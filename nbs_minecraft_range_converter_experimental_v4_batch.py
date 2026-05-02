
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nbs_minecraft_range_converter_experimental_v3.py

把 .nbs 文件里超出 Minecraft 音符盒 2 八度限制的音符尽量无损地转回可播放范围。

核心策略：
1. 默认 ensemble 模式：先找全曲基准移调，再让每个 layer 只做 ±12 八度级别的寄存器调整
2. 仍越界的音符按 ±12 半音做八度折叠，尽量保持音名不变
3. 默认不做同 tick 和弦重排，只做保守的寄存器保持，避免把原曲改诡异
4. 打击乐轨默认归中，避免无意义的音高越界警告
5. 同 tick 重复音去重，可选限制同一 tick 和弦数量
6. 支持 NBS v0 classic 和 OpenNBS v1~v5 的基础读写
7. 可选实验功能：相似乐器代偿、风格修补、邻音陪跑修补、超级大和弦加厚

默认模式是 nbs-safe：
- NBS key 必须落在 33~57
- 33 = F#3
- 57 = F#5
- 对应 Minecraft note block blockstate note=0~24

用法：
  python nbs_minecraft_range_converter.py input.nbs output.nbs
  python nbs_minecraft_range_converter_experimental_v3.py input.nbs output.nbs --arrangement preserve --max-chord-notes 24
  python nbs_minecraft_range_converter.py input.nbs output.nbs --print-ranges
  python nbs_minecraft_range_converter.py input.nbs output.nbs --mode instrument-audible
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import struct
from pathlib import Path
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


# =========================
# Minecraft / NBS 音高常量
# =========================

# OpenNBS 的 NBS key：0 = A0, 87 = C8
# Minecraft 原版音符盒在 NBS 里的安全导出范围：33~57，即 F#3~F#5。
NBS_MC_LOW = 33
NBS_MC_HIGH = 57
NBS_MC_CENTER = 45  # F#4

# Minecraft note block 的 blockstate note：0~24
MC_NOTE_LOW = 0
MC_NOTE_HIGH = 24

NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def nbs_key_to_midi(key: int) -> int:
    # NBS key 0 = A0 = MIDI 21
    return key + 21


def midi_to_name(midi_note: int) -> str:
    name = NOTE_NAMES_SHARP[midi_note % 12]
    octave = midi_note // 12 - 1
    return f"{name}{octave}"


def nbs_key_name(key: int) -> str:
    return midi_to_name(nbs_key_to_midi(key))


def mc_blockstate_note_from_nbs_key(key: int) -> int:
    return key - NBS_MC_LOW


def mc_pitch_value_from_blockstate_note(note: int) -> float:
    # Minecraft Wiki 公式：2 ^ ((use count - 12) / 12)
    return 2 ** ((note - 12) / 12)


@dataclass(frozen=True)
class InstrumentInfo:
    nbs_id: int
    nbs_name: str
    mc_name: str
    block: str
    java_sound_event: str
    audible_low_key: Optional[int]
    audible_high_key: Optional[int]
    percussion: bool = False

    @property
    def audible_range_text(self) -> str:
        if self.audible_low_key is None or self.audible_high_key is None:
            return "无固定音高"
        return f"{nbs_key_name(self.audible_low_key)}~{nbs_key_name(self.audible_high_key)}"


# NBS 0~15 vanilla instruments.
# 注意：
# - nbs-safe 模式写出时仍统一压到 key 33~57。
# - instrument-audible 模式会按 Minecraft 实际听感音区处理，例如 Bass 是 F#1~F#3。
INSTRUMENTS: Dict[int, InstrumentInfo] = {
    0: InstrumentInfo(0, "Piano / Harp", "harp", "Air / other blocks", "block.note_block.harp", 33, 57),
    1: InstrumentInfo(1, "Double Bass", "bass", "Wood", "block.note_block.bass", 9, 33),
    2: InstrumentInfo(2, "Bass Drum", "basedrum", "Stone", "block.note_block.basedrum", None, None, True),
    3: InstrumentInfo(3, "Snare Drum", "snare", "Sand", "block.note_block.snare", None, None, True),
    4: InstrumentInfo(4, "Click / Hi-hat", "hat", "Glass", "block.note_block.hat", None, None, True),
    5: InstrumentInfo(5, "Guitar", "guitar", "Wool", "block.note_block.guitar", 21, 45),
    6: InstrumentInfo(6, "Flute", "flute", "Clay", "block.note_block.flute", 45, 69),
    7: InstrumentInfo(7, "Bell", "bell", "Gold Block", "block.note_block.bell", 57, 81),
    8: InstrumentInfo(8, "Chime", "chime", "Packed Ice", "block.note_block.chime", 57, 81),
    9: InstrumentInfo(9, "Xylophone", "xylophone", "Bone Block", "block.note_block.xylophone", 57, 81),
    10: InstrumentInfo(10, "Iron Xylophone", "iron_xylophone", "Iron Block", "block.note_block.iron_xylophone", 33, 57),
    11: InstrumentInfo(11, "Cow Bell", "cow_bell", "Soul Sand", "block.note_block.cow_bell", 45, 69),
    12: InstrumentInfo(12, "Didgeridoo", "didgeridoo", "Pumpkin", "block.note_block.didgeridoo", 9, 33),
    13: InstrumentInfo(13, "Bit", "bit", "Emerald Block", "block.note_block.bit", 33, 57),
    14: InstrumentInfo(14, "Banjo", "banjo", "Hay Bale", "block.note_block.banjo", 33, 57),
    15: InstrumentInfo(15, "Pling", "pling", "Glowstone", "block.note_block.pling", 33, 57),

    # Minecraft 26.1 / OpenNBS v3.12 beta 新增的铜质小号系列。
    # 只有支持 NBS v6 的新版 OpenNBS / 播放器才能把它们作为 vanilla instruments 正常表达。
    # 默认写出仍使用 mc-1.21 兼容模式，会把 16~19 重映射到 Harp，避免旧读取器崩溃。
    16: InstrumentInfo(16, "Trumpet", "trumpet", "Copper Block", "block.note_block.trumpet", 33, 57),
    17: InstrumentInfo(17, "Exposed Trumpet", "trumpet_exposed", "Exposed Copper Block", "block.note_block.trumpet_exposed", 33, 57),
    18: InstrumentInfo(18, "Weathered Trumpet", "trumpet_weathered", "Weathered Copper Block", "block.note_block.trumpet_weathered", 33, 57),
    19: InstrumentInfo(19, "Oxidized Trumpet", "trumpet_oxidized", "Oxidized Copper Block", "block.note_block.trumpet_oxidized", 33, 57),
}

# 写出兼容目标。NBS 格式用 vanilla_instrument_count 判断“默认乐器”和“自定义乐器”的分界。
# 这个字段写错时，Note Block Studio 很容易把 ID 10~15 当成 custom instrument，
# 进而在 blocks_set_instruments 里读到 undefined。
INSTRUMENT_SET_INFO = {
    "classic-10": {"vanilla_count": 10, "min_version": 0, "max_id": 9},
    "mc-1.21": {"vanilla_count": 16, "min_version": 5, "max_id": 15},
    "mc-26.1": {"vanilla_count": 20, "min_version": 6, "max_id": 19},
}


# =========================
# NBS 数据结构
# =========================

@dataclass
class NBSHeader:
    version: int = 0
    vanilla_instrument_count: int = 10
    song_length: int = 0
    layer_count: int = 0

    song_name: str = ""
    song_author: str = ""
    original_author: str = ""
    description: str = ""
    tempo: int = 1000
    auto_saving: int = 0
    auto_saving_duration: int = 0
    time_signature: int = 4
    minutes_spent: int = 0
    left_clicks: int = 0
    right_clicks: int = 0
    note_blocks_added: int = 0
    note_blocks_removed: int = 0
    midi_schematic_filename: str = ""

    loop_on: int = 0
    max_loop_count: int = 0
    loop_start_tick: int = 0


@dataclass
class Note:
    tick: int
    layer: int
    instrument: int
    key: int
    velocity: int = 100
    panning: int = 100
    pitch: int = 0

    # 统计用，不写入文件
    original_key: int = field(default=0, repr=False)
    original_pitch: int = field(default=0, repr=False)
    chosen_shift: int = field(default=0, repr=False)
    folded_semitones: int = field(default=0, repr=False)
    substituted_instrument: bool = field(default=False, repr=False)
    substitution_from: int = field(default=-1, repr=False)
    substitution_target_abs_key: int = field(default=0, repr=False)


@dataclass
class Layer:
    name: str = ""
    lock: int = 0
    volume: int = 100
    panning: int = 100


@dataclass
class CustomInstrument:
    name: str
    sound_file: str
    sound_key: int = 45
    press_piano_key: int = 0


@dataclass
class NBSFile:
    header: NBSHeader
    notes: List[Note]
    layers: List[Layer]
    custom_instruments: List[CustomInstrument]


# =========================
# 二进制读写
# =========================

class BinReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError(f"Unexpected EOF at {self.pos}, need {n} bytes")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return struct.unpack("<B", self.read(1))[0]

    def i8(self) -> int:
        return struct.unpack("<b", self.read(1))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.read(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def str(self) -> str:
        length = self.i32()
        if length < 0:
            raise ValueError(f"Invalid string length: {length}")
        raw = self.read(length)
        return raw.decode("utf-8", errors="replace")


class BinWriter:
    def __init__(self):
        self.buf = bytearray()

    def bytes(self) -> bytes:
        return bytes(self.buf)

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    def u8(self, v: int) -> None:
        self.write(struct.pack("<B", int(v) & 0xFF))

    def i8(self, v: int) -> None:
        self.write(struct.pack("<b", int(v)))

    def u16(self, v: int) -> None:
        self.write(struct.pack("<H", int(v) & 0xFFFF))

    def i16(self, v: int) -> None:
        v = max(-32768, min(32767, int(v)))
        self.write(struct.pack("<h", v))

    def i32(self, v: int) -> None:
        self.write(struct.pack("<i", int(v)))

    def str(self, s: str) -> None:
        raw = (s or "").encode("utf-8")
        self.i32(len(raw))
        self.write(raw)


def read_nbs(path: str) -> NBSFile:
    with open(path, "rb") as f:
        data = f.read()

    r = BinReader(data)
    first_short = r.u16()
    header = NBSHeader()

    if first_short == 0:
        header.version = r.u8()
        header.vanilla_instrument_count = r.u8()
        if header.version >= 3:
            header.song_length = r.u16()
        else:
            header.song_length = 0
        header.layer_count = r.u16()
    else:
        # Classic v0
        header.version = 0
        header.vanilla_instrument_count = 10
        header.song_length = first_short
        header.layer_count = r.u16()

    header.song_name = r.str()
    header.song_author = r.str()
    header.original_author = r.str()
    header.description = r.str()
    header.tempo = r.u16()
    header.auto_saving = r.u8()
    header.auto_saving_duration = r.u8()
    header.time_signature = r.u8()
    header.minutes_spent = r.i32()
    header.left_clicks = r.i32()
    header.right_clicks = r.i32()
    header.note_blocks_added = r.i32()
    header.note_blocks_removed = r.i32()
    header.midi_schematic_filename = r.str()

    if header.version >= 4:
        header.loop_on = r.u8()
        header.max_loop_count = r.u8()
        header.loop_start_tick = r.u16()

    notes: List[Note] = []
    tick = -1

    while True:
        jump_ticks = r.u16()
        if jump_ticks == 0:
            break
        tick += jump_ticks

        layer = -1
        while True:
            jump_layers = r.u16()
            if jump_layers == 0:
                break
            layer += jump_layers

            instrument = r.u8()
            key = r.u8()
            velocity = 100
            panning = 100
            pitch = 0

            if header.version >= 4:
                velocity = r.u8()
                panning = r.u8()
                pitch = r.i16()

            notes.append(
                Note(
                    tick=tick,
                    layer=layer,
                    instrument=instrument,
                    key=key,
                    velocity=velocity,
                    panning=panning,
                    pitch=pitch,
                    original_key=key,
                    original_pitch=pitch,
                )
            )

    layers: List[Layer] = []
    if r.remaining() > 0:
        for _ in range(header.layer_count):
            if r.remaining() <= 0:
                break
            name = r.str()
            lock = 0
            volume = 100
            panning = 100
            if header.version >= 4:
                lock = r.u8()
            if r.remaining() > 0:
                volume = r.u8()
            if header.version >= 2 and r.remaining() > 0:
                panning = r.u8()
            layers.append(Layer(name=name, lock=lock, volume=volume, panning=panning))

    # 补齐 layer 信息，避免后续索引炸掉
    while len(layers) < header.layer_count:
        layers.append(Layer())

    custom_instruments: List[CustomInstrument] = []
    if r.remaining() > 0:
        try:
            count = r.u8()
            for _ in range(count):
                name = r.str()
                sound_file = r.str()
                sound_key = r.u8() if r.remaining() > 0 else 45
                press = r.u8() if r.remaining() > 0 else 0
                custom_instruments.append(CustomInstrument(name, sound_file, sound_key, press))
        except EOFError:
            # 有些老/损坏文件结尾可能不完整，前面的音符仍然能处理。
            pass

    return NBSFile(header=header, notes=notes, layers=layers, custom_instruments=custom_instruments)


def write_nbs(song: NBSFile, path: str) -> None:
    w = BinWriter()
    h = song.header

    # 根据音符/层信息修正 layer_count 和 song_length
    max_layer_from_notes = max((n.layer for n in song.notes), default=-1) + 1
    h.layer_count = max(h.layer_count, len(song.layers), max_layer_from_notes)
    if h.version >= 3 or h.version == 0:
        h.song_length = max(h.song_length, max((n.tick for n in song.notes), default=-1) + 1)

    if h.version == 0:
        w.u16(h.song_length)
        w.u16(h.layer_count)
    else:
        w.u16(0)
        w.u8(h.version)
        w.u8(h.vanilla_instrument_count)
        if h.version >= 3:
            w.u16(h.song_length)
        w.u16(h.layer_count)

    w.str(h.song_name)
    w.str(h.song_author)
    w.str(h.original_author)
    w.str(h.description)
    w.u16(h.tempo)
    w.u8(h.auto_saving)
    w.u8(h.auto_saving_duration)
    w.u8(h.time_signature)
    w.i32(h.minutes_spent)
    w.i32(h.left_clicks)
    w.i32(h.right_clicks)
    w.i32(h.note_blocks_added)
    w.i32(h.note_blocks_removed)
    w.str(h.midi_schematic_filename)

    if h.version >= 4:
        w.u8(h.loop_on)
        w.u8(h.max_loop_count)
        w.u16(h.loop_start_tick)

    # 写 note blocks，按 NBS delta 编码
    notes_by_tick: Dict[int, List[Note]] = defaultdict(list)
    for note in song.notes:
        notes_by_tick[note.tick].append(note)

    last_tick = -1
    for tick in sorted(notes_by_tick):
        tick_jump = tick - last_tick
        if tick_jump <= 0:
            continue
        if tick_jump > 65535:
            raise ValueError(f"Tick jump too large for NBS format: {tick_jump}")
        w.u16(tick_jump)
        last_tick = tick

        layer_notes = sorted(notes_by_tick[tick], key=lambda n: n.layer)
        last_layer = -1
        for n in layer_notes:
            layer_jump = n.layer - last_layer
            if layer_jump <= 0:
                # 正常 NBS 不应同 tick 同 layer 多个 note。
                # 如果真的出现，移动到下一层，避免写坏。
                n.layer = last_layer + 1
                layer_jump = 1
            if layer_jump > 65535:
                raise ValueError(f"Layer jump too large for NBS format: {layer_jump}")
            w.u16(layer_jump)
            last_layer = n.layer

            w.u8(n.instrument)
            w.u8(max(0, min(87, n.key)))

            if h.version >= 4:
                w.u8(max(0, min(100, n.velocity)))
                w.u8(max(0, min(200, n.panning)))
                w.i16(max(-32768, min(32767, n.pitch)))

        w.u16(0)  # end layers for this tick

    w.u16(0)  # end notes

    # 写 layers
    layers = list(song.layers)
    while len(layers) < h.layer_count:
        layers.append(Layer())

    for layer in layers[:h.layer_count]:
        w.str(layer.name)
        if h.version >= 4:
            w.u8(layer.lock)
        w.u8(max(0, min(100, layer.volume)))
        if h.version >= 2:
            w.u8(max(0, min(200, layer.panning)))

    # 写 custom instruments。
    # 虽然规范说 Custom instruments 部分可选，但这里始终写入 count 字节；
    # 一些 GameMaker 系读取器在写了 layers 后如果直接 EOF，会把 custom count 读成 undefined。
    custom_count = max(0, min(240, len(song.custom_instruments)))
    w.u8(custom_count)
    for ci in song.custom_instruments[:custom_count]:
        w.str(ci.name)
        w.str(ci.sound_file)
        w.u8(max(0, min(87, ci.sound_key)))
        w.u8(1 if ci.press_piano_key else 0)

    with open(path, "wb") as f:
        f.write(w.bytes())


# =========================
# 转换逻辑
# =========================

@dataclass
class ConvertStats:
    total_notes: int = 0
    changed_notes: int = 0
    folded_notes: int = 0
    shifted_layers: int = 0
    dropped_notes: int = 0
    deduped_notes: int = 0
    centered_percussion: int = 0
    out_before: int = 0
    out_after: int = 0
    global_shift: int = 0
    chord_revoiced_notes: int = 0
    chord_revoiced_ticks: int = 0
    instrument_substituted_notes: int = 0
    style_repaired_notes: int = 0
    phrase_repaired_notes: int = 0
    phrase_repaired_windows: int = 0
    mega_chord_added_notes: int = 0
    mega_chord_layers: int = 0
    layer_shifts: Dict[int, int] = field(default_factory=dict)


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def parse_int_range(s: str) -> Tuple[int, int]:
    if ":" not in s:
        raise argparse.ArgumentTypeError("范围格式应该像 -24:24")
    a, b = s.split(":", 1)
    lo, hi = int(a), int(b)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def is_percussion(instrument: int) -> bool:
    info = INSTRUMENTS.get(instrument)
    return bool(info and info.percussion)



def instrument_pitch_offset(instrument: int) -> int:
    """
    返回某个 Minecraft/NBS 乐器相对 Harp/Piano 的实际听感偏移。

    例：
    - Harp 的安全听感范围是 33~57，offset=0
    - Guitar 的实际听感范围是 21~45，offset=-12
    - Bass/Didgeridoo 是 9~33，offset=-24
    - Flute/Cow Bell 是 45~69，offset=+12
    - Bell/Chime/Xylophone 是 57~81，offset=+24

    用这个值可以实现“同一个目标音高，换一个天然更低/更高的乐器来承接”。
    """
    info = INSTRUMENTS.get(instrument)
    if not info or info.percussion or info.audible_low_key is None:
        return 0
    return int(info.audible_low_key - NBS_MC_LOW)


# 相似乐器替代表。
# 数字越小越像；这个不是玄学绝对值，只是用于排序。
# 默认 similar 模式只会在这个表里挑，避免把钢琴突然变成牛铃这种邪门东西。
SIMILAR_INSTRUMENT_COSTS: Dict[int, Dict[int, float]] = {
    0:  {0: 0.00, 15: 0.28, 13: 0.48, 10: 0.58, 14: 0.70, 5: 0.95, 6: 1.05, 7: 1.35, 8: 1.35, 9: 1.35, 1: 1.55, 12: 1.65},
    15: {15: 0.00, 0: 0.28, 13: 0.42, 10: 0.55, 14: 0.70, 5: 1.00, 6: 1.05, 7: 1.35, 8: 1.35, 9: 1.35},
    13: {13: 0.00, 15: 0.42, 0: 0.48, 10: 0.65, 14: 0.85, 6: 1.05, 7: 1.25, 8: 1.25, 9: 1.25},
    10: {10: 0.00, 0: 0.58, 15: 0.55, 13: 0.65, 9: 0.72, 7: 1.10, 8: 1.10, 11: 1.20},
    14: {14: 0.00, 5: 0.45, 0: 0.70, 15: 0.70, 1: 0.95, 13: 0.85},
    5:  {5: 0.00, 14: 0.45, 1: 0.70, 12: 0.85, 0: 0.95, 15: 1.00},
    1:  {1: 0.00, 12: 0.45, 5: 0.70, 14: 0.95, 0: 1.55},
    12: {12: 0.00, 1: 0.45, 5: 0.85, 14: 1.05, 0: 1.65},
    6:  {6: 0.00, 11: 0.65, 0: 1.05, 15: 1.05, 7: 1.10, 8: 1.10, 13: 1.05},
    11: {11: 0.00, 6: 0.65, 7: 0.85, 8: 0.85, 9: 0.90, 10: 1.20},
    7:  {7: 0.00, 8: 0.38, 9: 0.55, 11: 0.85, 10: 1.10, 6: 1.10, 0: 1.35},
    8:  {8: 0.00, 7: 0.38, 9: 0.55, 11: 0.85, 10: 1.10, 6: 1.10, 0: 1.35},
    9:  {9: 0.00, 7: 0.55, 8: 0.55, 10: 0.72, 11: 0.90, 0: 1.35},
}


def substitution_cost(old_instrument: int, new_instrument: int, profile: str) -> float:
    if old_instrument == new_instrument:
        return 0.0
    if profile == "wide":
        # wide 模式允许跨音色，但仍然稍微偏向同类偏移/同类发声。
        old_offset = instrument_pitch_offset(old_instrument)
        new_offset = instrument_pitch_offset(new_instrument)
        return 0.75 + abs(old_offset - new_offset) / 24.0 * 0.45
    return SIMILAR_INSTRUMENT_COSTS.get(old_instrument, {}).get(new_instrument, 999.0)


@dataclass(frozen=True)
class InstrumentSubstitutionChoice:
    instrument: int
    key: int
    audible_key: int
    cost: float


def choose_instrument_substitution(
    old_instrument: int,
    target_abs_key: int,
    previous_key_for_layer: Optional[int],
    profile: str = "similar",
    max_cost: float = 1.35,
    max_instrument_id: int = 15,
) -> Optional[InstrumentSubstitutionChoice]:
    """
    用相似乐器代偿音域。

    target_abs_key 是“希望听起来像哪个 NBS key”的绝对音高。
    对候选乐器 inst，有：
        实际听感 = 写入 key + instrument_pitch_offset(inst)
    所以：
        写入 key = target_abs_key - offset
    如果写入 key 落在 Minecraft blockstate 可用的 33~57，就可以不用八度折叠，靠换乐器保留音高。
    """
    if is_percussion(old_instrument):
        return None

    choices: List[InstrumentSubstitutionChoice] = []
    for inst in range(0, max_instrument_id + 1):
        if is_percussion(inst):
            continue
        info = INSTRUMENTS.get(inst)
        if not info or info.audible_low_key is None:
            continue

        tcost = substitution_cost(old_instrument, inst, profile)
        if tcost > max_cost:
            continue

        key = int(target_abs_key - instrument_pitch_offset(inst))
        if not (NBS_MC_LOW <= key <= NBS_MC_HIGH):
            continue

        audible = key + instrument_pitch_offset(inst)
        pitch_error = abs(audible - target_abs_key)

        cost = tcost + pitch_error * 2.0 + abs(key - NBS_MC_CENTER) * 0.012
        if previous_key_for_layer is not None:
            cost += abs(key - previous_key_for_layer) * 0.018

        choices.append(InstrumentSubstitutionChoice(inst, key, audible, cost))

    if not choices:
        return None

    return min(choices, key=lambda c: (c.cost, c.instrument != old_instrument, abs(c.key - NBS_MC_CENTER)))


def target_range_for_note(note: Note, mode: str, percussion_mode: str) -> Tuple[int, int]:
    if is_percussion(note.instrument):
        if percussion_mode == "center":
            return (NBS_MC_CENTER, NBS_MC_CENTER)
        if percussion_mode == "fold":
            return (NBS_MC_LOW, NBS_MC_HIGH)
        # keep 模式不会真正使用这个范围，但给个安全值
        return (0, 87)

    if mode == "instrument-audible":
        info = INSTRUMENTS.get(note.instrument)
        if info and info.audible_low_key is not None and info.audible_high_key is not None:
            return (info.audible_low_key, info.audible_high_key)

    # 默认 OpenNBS / 原版导出安全范围
    return (NBS_MC_LOW, NBS_MC_HIGH)


def fit_by_octave(key: int, low: int, high: int) -> Tuple[int, int]:
    """
    把 key 用 ±12 半音折叠进 [low, high]。
    返回：(新 key, 折叠的半音数绝对值)
    """
    candidates = []
    for oct_shift in range(-10, 11):
        k = key + oct_shift * 12
        if low <= k <= high:
            candidates.append(k)

    if candidates:
        center = (low + high) / 2
        best = min(candidates, key=lambda k: (abs(k - key), abs(k - center)))
        return best, abs(best - key)

    # 理论上 25 半音范围一定能折叠进去；单点范围/异常范围才会到这里
    best = clamp(key, low, high)
    return best, abs(best - key)


def note_importance(note: Note, song: NBSFile, duration_weight: float = 1.0) -> float:
    layer_volume = 100
    if 0 <= note.layer < len(song.layers):
        layer_volume = song.layers[note.layer].volume

    velocity = note.velocity if note.velocity is not None else 100

    # 0.15 是保底，防止小音量音符完全失去影响
    weight = 0.15 + (velocity / 100.0) * (layer_volume / 100.0)

    if is_percussion(note.instrument):
        weight *= 0.35

    return weight * duration_weight


def build_duration_weights(notes: List[Note]) -> Dict[int, float]:
    """
    NBS 没有真正的 note duration。
    这里用“同 layer 下一颗音出现前的 tick 距离”估计重要度。
    长音稍微更重要，但封顶，避免一个长空白把权重拉爆。
    """
    result: Dict[int, float] = {}
    by_layer: Dict[int, List[Note]] = defaultdict(list)
    for n in notes:
        by_layer[n.layer].append(n)

    for layer_notes in by_layer.values():
        layer_notes.sort(key=lambda n: n.tick)
        for i, n in enumerate(layer_notes):
            if i + 1 < len(layer_notes):
                dt = max(1, layer_notes[i + 1].tick - n.tick)
            else:
                dt = 1
            dt = min(dt, 32)
            result[id(n)] = 1.0 + math.log2(dt + 1) * 0.18
    return result


def source_key_for_note(note: Note, bake_fine_pitch: bool) -> int:
    if not bake_fine_pitch:
        return note.key
    # 把 pitch cents 烘焙到最近半音，然后后续会清零 fine pitch
    return clamp(round_half_up(note.key + note.pitch / 100.0), 0, 87)


def score_shift_for_notes(
    layer_notes: List[Note],
    song: NBSFile,
    shift: int,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    duration_weights: Dict[int, float],
    shift_penalty: float,
    non_octave_shift_penalty: float,
) -> float:
    score = 0.0
    total_weight = 0.0

    for n in layer_notes:
        if is_percussion(n.instrument) and percussion_mode == "keep":
            continue

        low, high = target_range_for_note(n, mode, percussion_mode)
        raw = source_key_for_note(n, bake_fine_pitch) + shift
        fitted, folded = fit_by_octave(raw, low, high)

        w = note_importance(n, song, duration_weights.get(id(n), 1.0))
        total_weight += w

        outside = 0 if low <= raw <= high else 1

        # 折叠一个八度比硬夹好很多，所以惩罚较低。
        # 但折叠太多仍然说明这个 shift 不理想。
        score += w * (
            outside * 1.2
            + (folded / 12.0) * 2.5
            + abs(fitted - raw) * 0.015
        )

    if total_weight <= 0:
        return 0.0

    # 整体移调不是免费：越少越好。
    score += total_weight * abs(shift) * shift_penalty

    # 非 12 半音倍数的移调会改变原曲调性，稍微惩罚。
    if shift % 12 != 0:
        score += total_weight * non_octave_shift_penalty

    return score


def choose_best_shift_for_layer(
    layer_notes: List[Note],
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    duration_weights: Dict[int, float],
    shift_min: int,
    shift_max: int,
    shift_penalty: float,
    non_octave_shift_penalty: float,
) -> int:
    best_shift = 0
    best_score = float("inf")

    for shift in range(shift_min, shift_max + 1):
        score = score_shift_for_notes(
            layer_notes,
            song,
            shift,
            mode,
            percussion_mode,
            bake_fine_pitch,
            duration_weights,
            shift_penalty,
            non_octave_shift_penalty,
        )
        if score < best_score:
            best_score = score
            best_shift = shift

    return best_shift



def choose_best_global_shift(
    notes: List[Note],
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    duration_weights: Dict[int, float],
    shift_min: int,
    shift_max: int,
    shift_penalty: float,
    non_octave_shift_penalty: float,
) -> int:
    """
    给整首歌找一个共同的半音移调。
    这样所有 layer 的音级关系会一起变，不会出现 A 轨 +5、B 轨 -7 这种把和声撕碎的情况。
    """
    pitched = [
        n for n in notes
        if not (is_percussion(n.instrument) and percussion_mode == "keep")
    ]
    if not pitched:
        return 0

    return choose_best_shift_for_layer(
        pitched,
        song,
        mode,
        percussion_mode,
        bake_fine_pitch,
        duration_weights,
        shift_min,
        shift_max,
        shift_penalty,
        non_octave_shift_penalty,
    )


def choose_best_octave_locked_shift_for_layer(
    layer_notes: List[Note],
    song: NBSFile,
    global_shift: int,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    duration_weights: Dict[int, float],
    shift_min: int,
    shift_max: int,
    shift_penalty: float,
) -> int:
    """
    ensemble 模式下，每个 layer 只能在全曲基准移调的基础上做 ±12 的八度调整。
    这会保留和弦/旋律的音级关系，只改变声部所在八度。
    """
    candidates = []
    for octaves in range(-8, 9):
        shift = global_shift + octaves * 12
        if shift_min <= shift <= shift_max:
            candidates.append(shift)

    if not candidates:
        candidates = [clamp(global_shift, shift_min, shift_max)]

    best_shift = candidates[0]
    best_score = float("inf")

    for shift in candidates:
        # octave-locked 模式下非八度额外惩罚已经不需要了；global_shift 决定调性。
        score = score_shift_for_notes(
            layer_notes,
            song,
            shift,
            mode,
            percussion_mode,
            bake_fine_pitch,
            duration_weights,
            shift_penalty,
            non_octave_shift_penalty=0.0,
        )

        # layer 额外偏移越小越好，避免伴奏突然挪得太离谱。
        score += abs(shift - global_shift) * 0.04 * max(1, len(layer_notes))

        if score < best_score:
            best_score = score
            best_shift = shift

    return best_shift



def median_int(values: List[int]) -> float:
    if not values:
        return float(NBS_MC_CENTER)
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


def fit_by_octave_prefer_register(
    key: int,
    low: int,
    high: int,
    preferred_center: float,
    previous_key: Optional[int] = None,
    center_weight: float = 0.85,
    raw_weight: float = 0.22,
    previous_weight: float = 0.75,
) -> Tuple[int, int]:
    """
    保守寄存器折叠：
    - 只在同音名的不同八度候选里选，不改音级；
    - 参考该 layer 的目标寄存器，避免低声部忽然跑到高声部；
    - 参考同 layer 上一颗音，避免旋律线突然跳八度。

    这和 smart 版的 tick 级和弦重排不同：它不会为了“协和”改同一拍里的上下关系。
    """
    candidates = all_octave_candidates(key, low, high)
    if not candidates:
        best = clamp(key, low, high)
        return best, abs(best - key)

    preferred_center = max(low, min(high, preferred_center))

    def cost(k: int) -> float:
        c = abs(k - preferred_center) * center_weight
        c += abs(k - key) * raw_weight
        if previous_key is not None:
            jump = abs(k - previous_key)
            c += jump * previous_weight
            if jump > 12:
                c += (jump - 12) * previous_weight * 1.25
        return c

    best = min(candidates, key=lambda k: (cost(k), abs(k - key), k))
    return best, abs(best - key)


def compute_preserve_layer_centers(
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    global_shift: int,
    strength: float = 0.62,
) -> Dict[int, float]:
    """
    给每个 layer 计算一个“应该待在哪个寄存器”的中心值。

    重点：只用它在同音名的不同八度之间做选择，不会改变音级。
    这样 14 轨 NBS 会尽量保留原本谁在上、谁在下，而不是每个 tick 乱重排。
    """
    by_layer: Dict[int, List[int]] = defaultdict(list)
    for n in song.notes:
        if is_percussion(n.instrument):
            continue
        low, high = target_range_for_note(n, mode, percussion_mode)
        raw = source_key_for_note(n, bake_fine_pitch) + global_shift
        folded, _ = fit_by_octave(raw, low, high)
        by_layer[n.layer].append(folded)

    if not by_layer:
        return {}

    medians = {layer: median_int(keys) for layer, keys in by_layer.items()}
    ordered_layers = sorted(medians, key=lambda layer: (medians[layer], layer))
    count = len(ordered_layers)

    centers: Dict[int, float] = {}
    for rank, layer in enumerate(ordered_layers):
        if count == 1:
            rank_center = float(NBS_MC_CENTER)
        else:
            rank_center = NBS_MC_LOW + (NBS_MC_HIGH - NBS_MC_LOW) * (0.08 + 0.84 * rank / (count - 1))

        natural_center = medians[layer]
        centers[layer] = natural_center * (1.0 - strength) + rank_center * strength

    return centers


def all_octave_candidates(key: int, low: int, high: int) -> List[int]:
    """
    返回所有和 key 同音名、落在 [low, high] 的候选 key。
    25 个半音范围内通常会有 2~3 个候选。
    """
    out = []
    for oct_shift in range(-10, 11):
        k = key + oct_shift * 12
        if low <= k <= high:
            out.append(k)
    out.sort(key=lambda k: (abs(k - key), k))
    return out


def interval_dissonance_cost(a: int, b: int) -> float:
    """
    简易协和度评分。不是古典和声引擎，但对 NBS 压 2 八度很实用：
    - 纯五、三/六度、纯四比较稳
    - 大二/小七还行
    - 小二/大七、三全音很刺，强惩罚
    """
    semitone = abs(a - b)
    pc = semitone % 12

    base = {
        0: 0.05,   # 同音/八度。可接受，但太多会糊，另有密集惩罚。
        1: 1.10,
        2: 0.22,
        3: 0.04,
        4: 0.03,
        5: 0.08,
        6: 0.85,
        7: 0.00,
        8: 0.04,
        9: 0.04,
        10: 0.20,
        11: 1.05,
    }[pc]

    if semitone == 0:
        base += 0.08
    elif semitone <= 1:
        base += 0.90
    elif semitone <= 2:
        base += 0.20

    return base


def chord_candidate_cost(
    note: Note,
    candidate_key: int,
    raw_key: int,
    desired_key: float,
    selected: Dict[int, int],
    ordered_notes: List[Note],
    note_index: int,
    previous_by_layer: Dict[int, int],
    song: NBSFile,
    duration_weights: Dict[int, float],
    voice_leading_weight: float,
    vertical_harmony_weight: float,
    spread_weight: float,
) -> float:
    # 个体保真：尽量靠近折叠前目标，也尽量靠近期望寄存器。
    cost = abs(candidate_key - raw_key) * 0.08
    cost += abs(candidate_key - desired_key) * 0.11 * spread_weight

    # 同 layer 连续性：减少“上一拍在下面、下一拍突然飞到上面”的抽搐感。
    prev = previous_by_layer.get(note.layer)
    if prev is not None:
        jump = abs(candidate_key - prev)
        cost += jump * 0.06 * voice_leading_weight
        if jump > 12:
            cost += (jump - 12) * 0.18 * voice_leading_weight

    self_weight = note_importance(note, song, duration_weights.get(id(note), 1.0))

    # 垂直和声：同一 tick 的音尽量别撞小二度/三全音，也别上下声部交叉。
    for other_index, other in enumerate(ordered_notes):
        if other_index == note_index:
            continue
        if id(other) not in selected:
            continue

        other_key = selected[id(other)]
        other_weight = note_importance(other, song, duration_weights.get(id(other), 1.0))
        pair_weight = max(0.25, min(1.4, (self_weight + other_weight) * 0.5))

        cost += interval_dissonance_cost(candidate_key, other_key) * vertical_harmony_weight * pair_weight

        # 原本更低的声部，不要突然跑到更高声部上面去。
        if other_index < note_index and other_key > candidate_key:
            cost += 4.0 * vertical_harmony_weight
        elif other_index > note_index and other_key < candidate_key:
            cost += 4.0 * vertical_harmony_weight

        # 超密集堆叠会糊，尤其 14 轨同时响的时候。
        if abs(candidate_key - other_key) <= 1:
            cost += 0.75 * vertical_harmony_weight

    return cost


def revoice_tick_chords(
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    bake_fine_pitch: bool,
    duration_weights: Dict[int, float],
    voice_leading_weight: float,
    vertical_harmony_weight: float,
    spread_weight: float,
) -> Tuple[int, int]:
    """
    对同一个 tick 的多音进行联合重排。
    目的：在 33~57 这么窄的范围里，尽量保持低音/中音/高音顺序和协和度。
    返回：(被改动的音符数, 被重排的 tick 数)
    """
    by_tick: Dict[int, List[Note]] = defaultdict(list)
    for n in song.notes:
        by_tick[n.tick].append(n)

    previous_by_layer: Dict[int, int] = {}
    changed_notes = 0
    changed_ticks = 0

    for tick in sorted(by_tick):
        tick_notes = by_tick[tick]
        pitched = [
            n for n in tick_notes
            if not is_percussion(n.instrument)
        ]

        # 单音只做连续性优化意义很小；多音才是重点。
        if len(pitched) < 2:
            for n in sorted(tick_notes, key=lambda x: x.layer):
                if not is_percussion(n.instrument):
                    previous_by_layer[n.layer] = n.key
            continue

        raw_by_id: Dict[int, int] = {}
        order_by_id: Dict[int, int] = {}
        candidates_by_id: Dict[int, List[int]] = {}

        usable: List[Note] = []
        for n in pitched:
            low, high = target_range_for_note(n, mode, percussion_mode)
            source_key = source_key_for_note(n, bake_fine_pitch)

            # raw 用于“离原本折叠目标多远”的成本；
            # order_source 用于判断原曲上下声部关系，不能带 layer 的 ±12 八度偏移，
            # 否则低音轨升八度后会被误认为高音声部。
            raw = source_key + n.chosen_shift
            order_source = source_key

            candidates = all_octave_candidates(raw, low, high)
            if not candidates:
                fitted, _ = fit_by_octave(raw, low, high)
                candidates = [fitted]

            # 限制候选数量，避免 14 轨时组合优化爆炸；实际通常 2~3 个够了。
            candidates = sorted(set(candidates), key=lambda k: (abs(k - raw), k))[:4]
            raw_by_id[id(n)] = raw
            order_by_id[id(n)] = order_source
            candidates_by_id[id(n)] = candidates
            usable.append(n)

        if len(usable) < 2:
            continue

        # 按“原曲音高关系”排序，而不是 layer 号排序。layer 号经常不是声部高低。
        ordered = sorted(usable, key=lambda n: (order_by_id[id(n)], n.layer, n.instrument))
        m = len(ordered)

        # 目标寄存器：把同一 tick 的多音尽量摊开到 2 八度里，避免全部折到一坨。
        desired_by_id: Dict[int, float] = {}
        for i, n in enumerate(ordered):
            low, high = target_range_for_note(n, mode, percussion_mode)
            if m == 1:
                desired = (low + high) / 2
            else:
                # 10%~90% 区间，别老贴边。
                desired = low + (high - low) * (0.10 + 0.80 * i / (m - 1))
            desired_by_id[id(n)] = desired

        selected: Dict[int, int] = {}

        # 初始化：低到高依次挑候选，尽量接近目标寄存器。
        for i, n in enumerate(ordered):
            raw = raw_by_id[id(n)]
            desired = desired_by_id[id(n)]
            candidates = candidates_by_id[id(n)]
            best = min(
                candidates,
                key=lambda k: (
                    abs(k - desired) * 1.15
                    + abs(k - raw) * 0.35
                    + (abs(k - previous_by_layer[n.layer]) * 0.20 if n.layer in previous_by_layer else 0.0)
                )
            )
            selected[id(n)] = best

        # 坐标下降微调：每次只改一个音，综合考虑和其他音的关系。
        for _ in range(3):
            any_changed = False
            for i, n in enumerate(ordered):
                raw = raw_by_id[id(n)]
                desired = desired_by_id[id(n)]
                candidates = candidates_by_id[id(n)]

                old = selected[id(n)]
                best = min(
                    candidates,
                    key=lambda k: chord_candidate_cost(
                        n,
                        k,
                        raw,
                        desired,
                        selected,
                        ordered,
                        i,
                        previous_by_layer,
                        song,
                        duration_weights,
                        voice_leading_weight,
                        vertical_harmony_weight,
                        spread_weight,
                    )
                )
                if best != old:
                    selected[id(n)] = best
                    any_changed = True

            if not any_changed:
                break

        tick_changed = False
        for n in ordered:
            new_key = selected[id(n)]
            if n.key != new_key:
                n.key = new_key
                changed_notes += 1
                tick_changed = True

        if tick_changed:
            changed_ticks += 1

        # 更新连续性状态。打击乐不参与。
        for n in sorted(tick_notes, key=lambda x: x.layer):
            if not is_percussion(n.instrument):
                previous_by_layer[n.layer] = n.key

    return changed_notes, changed_ticks



def apply_style_repair(
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    jump_threshold: int = 14,
    passes: int = 2,
    strength: float = 1.0,
) -> int:
    """
    风格修补：只在同音名的不同八度候选里选择，不改和弦音名。

    它专门处理这种情况：
    - 某个 layer 前后都很平稳，中间一颗因为折叠突然飞出去；
    - 或者压进 2 八度后，局部旋律线出现很突兀的大跳。

    注意：这不是 AI 作曲，只是保守的上下文平滑器。
    """
    if passes <= 0:
        return 0

    by_layer: Dict[int, List[Note]] = defaultdict(list)
    for n in song.notes:
        if not is_percussion(n.instrument):
            by_layer[n.layer].append(n)

    changed = 0
    for _ in range(passes):
        pass_changed = 0
        for layer, notes in by_layer.items():
            notes.sort(key=lambda n: (n.tick, n.key, n.instrument))
            if len(notes) < 3:
                continue

            layer_center = median_int([n.key for n in notes])
            for i, n in enumerate(notes):
                prev_n = notes[i - 1] if i > 0 else None
                next_n = notes[i + 1] if i + 1 < len(notes) else None
                if prev_n is None and next_n is None:
                    continue

                low, high = target_range_for_note(n, mode, percussion_mode)
                candidates = all_octave_candidates(n.key, low, high)
                if len(candidates) <= 1:
                    continue

                old_key = n.key
                old_jump = 0
                if prev_n is not None:
                    old_jump = max(old_jump, abs(old_key - prev_n.key))
                if next_n is not None:
                    old_jump = max(old_jump, abs(next_n.key - old_key))

                # 不突兀就别碰，避免“越修越怪”。
                if old_jump < jump_threshold and n.folded_semitones <= 12:
                    continue

                def local_cost(k: int) -> float:
                    cost = abs(k - layer_center) * 0.20
                    cost += abs(k - old_key) * 0.38  # 改动越小越好
                    if prev_n is not None:
                        j = abs(k - prev_n.key)
                        cost += j * 0.65 * strength
                        if j > 12:
                            cost += (j - 12) * 0.95 * strength
                    if next_n is not None:
                        j = abs(next_n.key - k)
                        cost += j * 0.65 * strength
                        if j > 12:
                            cost += (j - 12) * 0.95 * strength
                    return cost

                best = min(candidates, key=lambda k: (local_cost(k), abs(k - old_key)))
                if best != old_key and local_cost(best) + 0.60 < local_cost(old_key):
                    n.key = best
                    n.folded_semitones = max(n.folded_semitones, abs(best - old_key))
                    changed += 1
                    pass_changed += 1

        if pass_changed == 0:
            break

    return changed



def _raw_intended_key_for_repair(note: Note) -> int:
    """
    邻音陪跑修补用的“原本想表达的音高”。

    original_key 是输入 NBS 的 key；chosen_shift 是前面自动选择的全局/分轨移调。
    这里不用当前 note.key，因为当前 key 已经可能被折叠了。
    """
    base = note.original_key if note.original_key is not None else note.key
    return int(base + note.chosen_shift)


def _note_changed_by_range_process(note: Note) -> bool:
    return bool(
        note.folded_semitones > 0
        or note.substituted_instrument
        or note.key != (note.original_key + note.chosen_shift)
    )


def _melody_interval_cost(
    actual_interval: int,
    desired_interval: int,
    contour_weight: float,
    large_jump_threshold: int,
) -> float:
    """
    比较修补后的相邻音程和原曲相邻音程。

    重点不是绝对追求小跳，而是尽量保留原本 A->B 的方向和距离。
    比如原曲 A->B 是 +1，但折叠后变成 -11，就应该强惩罚；
    如果把 A 也陪着下移一八度，实际又回到 +1，则惩罚接近 0。
    """
    err = abs(actual_interval - desired_interval)
    cost = err * 0.72

    # 对“小幅旋律运动反向”强惩罚。
    # 这正是“前一个音 A 没改，后一个 B 折下去后听着突兀”的常见来源。
    if desired_interval != 0 and actual_interval != 0:
        if (desired_interval > 0) != (actual_interval > 0) and abs(desired_interval) <= 7:
            cost += contour_weight * (7 + abs(desired_interval))

    # 过大的跳进额外惩罚，但别完全禁止，原曲也可能就是大跳。
    if abs(actual_interval) > large_jump_threshold:
        cost += (abs(actual_interval) - large_jump_threshold) * 0.55

    return cost


def _collect_phrase_repair_allowed_indices(
    notes: List[Note],
    jump_threshold: int,
    radius: int,
) -> set:
    """
    只允许“有问题附近”的音陪跑，避免整条旋律被 DP 慢慢带歪。
    """
    allowed = set()
    n_count = len(notes)

    for i, n in enumerate(notes):
        triggered = _note_changed_by_range_process(n)

        if i > 0:
            prev = notes[i - 1]
            desired = _raw_intended_key_for_repair(n) - _raw_intended_key_for_repair(prev)
            actual = n.key - prev.key
            if abs(actual - desired) >= jump_threshold:
                triggered = True
            if desired != 0 and actual != 0 and (desired > 0) != (actual > 0) and abs(desired) <= 7:
                triggered = True

        if i + 1 < n_count:
            nxt = notes[i + 1]
            desired = _raw_intended_key_for_repair(nxt) - _raw_intended_key_for_repair(n)
            actual = nxt.key - n.key
            if abs(actual - desired) >= jump_threshold:
                triggered = True
            if desired != 0 and actual != 0 and (desired > 0) != (actual > 0) and abs(desired) <= 7:
                triggered = True

        if triggered:
            lo = max(0, i - radius)
            hi = min(n_count, i + radius + 1)
            for j in range(lo, hi):
                allowed.add(j)

    return allowed


def apply_phrase_repair(
    song: NBSFile,
    mode: str,
    percussion_mode: str,
    radius: int = 2,
    jump_threshold: int = 9,
    strength: float = 1.0,
    move_clean_penalty: float = 3.2,
    contour_weight: float = 1.25,
    passes: int = 1,
) -> Tuple[int, int]:
    """
    邻音陪跑修补 / Phrase repair。

    它解决这种情况：
        A 在范围内，所以没改；
        B 越界，所以被折叠；
        结果 A->B 的旋律方向/距离突然变得很怪。

    做法：
    - 不改音名，只允许同音名 ±12 八度候选；
    - 不只修 B，也允许 B 前后的 A/C 在小窗口内“陪跑”；
    - 用动态规划保留原曲相邻音程和旋律方向；
    - 只在有折叠/替换/突兀跳进附近启用，避免全曲漂移。
    """
    if radius <= 0 or passes <= 0:
        return (0, 0)

    total_changed = 0
    windows = 0

    for _ in range(passes):
        pass_changed = 0
        by_layer: Dict[int, List[Note]] = defaultdict(list)
        for n in song.notes:
            if not is_percussion(n.instrument):
                by_layer[n.layer].append(n)

        for layer, layer_notes in by_layer.items():
            notes = sorted(layer_notes, key=lambda n: (n.tick, n.key, n.instrument))
            if len(notes) < 2:
                continue

            allowed = _collect_phrase_repair_allowed_indices(notes, jump_threshold, radius)
            if not allowed:
                continue
            windows += 1

            layer_center = median_int([n.key for n in notes])
            candidates_per_i: List[List[int]] = []
            for i, n in enumerate(notes):
                low, high = target_range_for_note(n, mode, percussion_mode)
                if i not in allowed:
                    candidates = [n.key]
                else:
                    candidates = all_octave_candidates(n.key, low, high)
                    if not candidates:
                        candidates = [n.key]
                    if n.key not in candidates:
                        candidates.append(n.key)
                        candidates.sort(key=lambda k: (abs(k - n.key), k))
                candidates_per_i.append(candidates)

            # 动态规划：每个 layer 一条旋律线，选择每个音的八度位置。
            dp: List[Dict[int, Tuple[float, Optional[int]]]] = []

            for i, n in enumerate(notes):
                current: Dict[int, Tuple[float, Optional[int]]] = {}
                raw = _raw_intended_key_for_repair(n)
                changed_by_converter = _note_changed_by_range_process(n)

                for k in candidates_per_i[i]:
                    # 单音代价：越少动越好；没被转换器动过的“干净音”陪跑成本更高。
                    move = abs(k - n.key)
                    base_cost = 0.0
                    if move:
                        base_cost += move * (0.15 if changed_by_converter else move_clean_penalty)
                    base_cost += abs(k - layer_center) * 0.055
                    base_cost += abs(k - raw) * 0.035

                    # 对音量/长音大的音更谨慎，避免主旋律被陪跑过头。
                    imp = note_importance(n, song, 1.0)
                    if move:
                        base_cost += move * max(0.0, imp - 0.75) * 0.55

                    if i == 0:
                        current[k] = (base_cost, None)
                    else:
                        prev_n = notes[i - 1]
                        desired_interval = _raw_intended_key_for_repair(n) - _raw_intended_key_for_repair(prev_n)
                        best_cost = None
                        best_prev = None
                        for prev_k, (prev_cost, _) in dp[i - 1].items():
                            actual_interval = k - prev_k
                            trans = _melody_interval_cost(
                                actual_interval,
                                desired_interval,
                                contour_weight=contour_weight * strength,
                                large_jump_threshold=max(12, jump_threshold + 3),
                            ) * strength

                            # 同 tick 内同 layer 几乎不会出现；如果有，少惩罚一点，避免把和弦当旋律误杀。
                            if n.tick == prev_n.tick:
                                trans *= 0.35

                            cost = prev_cost + base_cost + trans
                            if best_cost is None or cost < best_cost:
                                best_cost = cost
                                best_prev = prev_k
                        current[k] = (float(best_cost), best_prev)
                dp.append(current)

            if not dp:
                continue

            last_key = min(dp[-1], key=lambda k: dp[-1][k][0])
            chosen = [0] * len(notes)
            chosen[-1] = last_key
            for i in range(len(notes) - 1, 0, -1):
                prev_key = dp[i][chosen[i]][1]
                if prev_key is None:
                    prev_key = notes[i - 1].key
                chosen[i - 1] = prev_key

            for n, new_key in zip(notes, chosen):
                if new_key != n.key:
                    n.key = int(new_key)
                    n.folded_semitones = max(n.folded_semitones, abs(new_key - _raw_intended_key_for_repair(n)))
                    total_changed += 1
                    pass_changed += 1

        if pass_changed == 0:
            break

    return (total_changed, windows)

def apply_mega_chord(
    song: NBSFile,
    trigger: str = "changed",
    width: int = 2,
    max_added_per_tick: int = 10,
    velocity_factor: float = 0.42,
    layer_count: int = 8,
    color: str = "same",
) -> Tuple[int, int]:
    """
    超级大和弦：给被折叠/替换的关键音加低音量的幽灵音，模拟原曲厚度。

    它是“加厚/补泛音”，不是纠错。开大了会很爽，也可能糊成浆糊。
    trigger：
    - folded：只给发生八度折叠的音加
    - changed：给折叠或相似乐器替换过的音加
    - all：所有非打击乐都尝试加，最夸张，不建议默认用
    """
    if width <= 0 or max_added_per_tick <= 0 or layer_count <= 0:
        return (0, 0)

    base_layer = max([n.layer for n in song.notes], default=-1) + 1
    for i in range(layer_count):
        song.layers.append(Layer(name=f"MegaChord Ghost {i + 1}", volume=62, panning=100))

    existing_tick_layer = {(n.tick, n.layer) for n in song.notes}
    added_by_tick: Dict[int, int] = defaultdict(int)
    added_notes: List[Note] = []

    source_notes = list(song.notes)
    # 低音量补音优先围绕“变化过”的音，别给整首歌无限膨胀。
    for n in source_notes:
        if is_percussion(n.instrument):
            continue
        if trigger == "folded" and n.folded_semitones <= 0:
            continue
        if trigger == "changed" and n.folded_semitones <= 0 and not n.substituted_instrument:
            continue

        if added_by_tick[n.tick] >= max_added_per_tick:
            continue

        raw = source_key_for_note(n, True) + n.chosen_shift
        direction = 1 if raw > n.key else -1 if raw < n.key else 0

        if direction > 0:
            intervals = [12, 7, -12, -5]
        elif direction < 0:
            intervals = [-12, -7, 12, 5]
        else:
            intervals = [12, -12, 7, -5]

        made_for_note = 0
        for interval in intervals:
            if made_for_note >= width or added_by_tick[n.tick] >= max_added_per_tick:
                break
            key = n.key + interval
            if not (NBS_MC_LOW <= key <= NBS_MC_HIGH):
                continue

            inst = n.instrument
            if color == "bright" and interval > 0:
                # 稍微亮一点的泛音层，但不要离谱。
                inst = 15 if n.instrument in {0, 13, 10} else n.instrument
            elif color == "warm" and interval < 0:
                inst = 5 if n.instrument in {0, 15, 13, 10, 14} else n.instrument

            slot_found = None
            for slot in range(layer_count):
                layer = base_layer + slot
                if (n.tick, layer) not in existing_tick_layer:
                    slot_found = layer
                    break
            if slot_found is None:
                continue

            existing_tick_layer.add((n.tick, slot_found))
            ghost = Note(
                tick=n.tick,
                layer=slot_found,
                instrument=inst,
                key=key,
                velocity=clamp(round_half_up(n.velocity * velocity_factor), 1, 100),
                panning=n.panning,
                pitch=0,
                original_key=n.original_key,
                original_pitch=0,
                chosen_shift=n.chosen_shift,
                folded_semitones=0,
            )
            added_notes.append(ghost)
            added_by_tick[n.tick] += 1
            made_for_note += 1

    song.notes.extend(added_notes)
    return (len(added_notes), layer_count if added_notes else 0)


def count_out_of_range(song: NBSFile, mode: str, percussion_mode: str) -> int:
    count = 0
    for n in song.notes:
        if is_percussion(n.instrument) and percussion_mode == "keep":
            continue
        low, high = target_range_for_note(n, mode, percussion_mode)
        if not (low <= n.key <= high):
            count += 1
    return count


def apply_conversion(
    song: NBSFile,
    mode: str = "nbs-safe",
    percussion_mode: str = "center",
    bake_fine_pitch: bool = True,
    keep_fine_pitch: bool = False,
    shift_range: Tuple[int, int] = (-24, 24),
    shift_penalty: float = 0.035,
    non_octave_shift_penalty: float = 0.45,
    max_chord_notes: int = 0,
    drop_extreme_folds: int = 0,
    dedupe: bool = False,
    arrangement: str = "preserve",
    revoice_chords: bool = False,
    voice_leading_weight: float = 1.25,
    vertical_harmony_weight: float = 1.45,
    spread_weight: float = 1.0,
    instrument_substitution: bool = False,
    instrument_substitution_profile: str = "similar",
    instrument_substitution_max_cost: float = 1.35,
    style_repair: bool = False,
    style_repair_jump: int = 14,
    style_repair_passes: int = 2,
    style_repair_strength: float = 1.0,
    phrase_repair: bool = False,
    phrase_repair_radius: int = 2,
    phrase_repair_jump: int = 9,
    phrase_repair_strength: float = 1.0,
    phrase_repair_move_clean_penalty: float = 3.2,
    phrase_repair_passes: int = 1,
    mega_chord: bool = False,
    mega_chord_trigger: str = "changed",
    mega_chord_width: int = 2,
    mega_chord_max_added_per_tick: int = 10,
    mega_chord_velocity: float = 0.42,
    mega_chord_layers: int = 8,
    mega_chord_color: str = "same",
) -> Tuple[NBSFile, ConvertStats]:
    out = copy.deepcopy(song)
    stats = ConvertStats(total_notes=len(out.notes))

    stats.out_before = count_out_of_range(out, mode, percussion_mode)

    duration_weights = build_duration_weights(out.notes)

    by_layer: Dict[int, List[Note]] = defaultdict(list)
    for n in out.notes:
        by_layer[n.layer].append(n)

    shift_min, shift_max = shift_range

    # 1. 选择移调策略
    #
    # layer：
    #   旧策略。每个 layer 独立找最佳半音移调，容易让和弦上下轨变成不同调。
    # global：
    #   全曲只用一个共同移调，和声最稳，但某些声部可能被折叠得多。
    # ensemble：
    #   推荐。先找全曲共同移调，再允许每个 layer 只按 ±12 半音调整八度位置。
    #
    # 这个设计的核心是：允许换八度，但不允许不同 layer 各自乱改调性。
    if arrangement not in {"preserve", "layer", "global", "ensemble"}:
        arrangement = "preserve"

    if arrangement in {"preserve", "global", "ensemble"}:
        global_shift = choose_best_global_shift(
            out.notes,
            out,
            mode,
            percussion_mode,
            bake_fine_pitch,
            duration_weights,
            shift_min,
            shift_max,
            shift_penalty,
            non_octave_shift_penalty,
        )
        stats.global_shift = global_shift
    else:
        global_shift = 0
        stats.global_shift = 0

    preserve_centers = compute_preserve_layer_centers(
        out, mode, percussion_mode, bake_fine_pitch, global_shift
    ) if arrangement == "preserve" else {}

    for layer_idx, layer_notes in by_layer.items():
        layer_notes.sort(key=lambda x: (x.tick, x.layer, x.instrument, x.key))
        previous_key_for_layer: Optional[int] = None

        if arrangement == "layer":
            best_shift = choose_best_shift_for_layer(
                layer_notes,
                out,
                mode,
                percussion_mode,
                bake_fine_pitch,
                duration_weights,
                shift_min,
                shift_max,
                shift_penalty,
                non_octave_shift_penalty,
            )
        elif arrangement in {"global", "preserve"}:
            best_shift = global_shift
        else:
            best_shift = choose_best_octave_locked_shift_for_layer(
                layer_notes,
                out,
                global_shift,
                mode,
                percussion_mode,
                bake_fine_pitch,
                duration_weights,
                shift_min,
                shift_max,
                shift_penalty,
            )

        stats.layer_shifts[layer_idx] = best_shift
        if best_shift != 0:
            stats.shifted_layers += 1

        for n in layer_notes:
            old_key = n.key

            if is_percussion(n.instrument) and percussion_mode == "keep":
                continue

            low, high = target_range_for_note(n, mode, percussion_mode)

            raw = source_key_for_note(n, bake_fine_pitch) + best_shift

            if arrangement == "preserve" and not is_percussion(n.instrument):
                preferred_center = preserve_centers.get(layer_idx, (low + high) / 2)
                fitted, folded = fit_by_octave_prefer_register(
                    raw, low, high, preferred_center, previous_key_for_layer
                )
            else:
                fitted, folded = fit_by_octave(raw, low, high)

            # 可选：相似乐器代偿。
            # 只在 nbs-safe 下启用，因为它依赖 Minecraft blockstate key=33~57 的固定区间。
            # 它不会改音级，而是换一个天然更低/更高的乐器，使“实际听感音高”尽量等于原目标音高。
            if (
                instrument_substitution
                and mode == "nbs-safe"
                and not is_percussion(n.instrument)
                and folded > 0
            ):
                target_abs_key = source_key_for_note(n, bake_fine_pitch) + best_shift + instrument_pitch_offset(n.instrument)
                choice = choose_instrument_substitution(
                    n.instrument,
                    target_abs_key,
                    previous_key_for_layer,
                    profile=instrument_substitution_profile,
                    max_cost=instrument_substitution_max_cost,
                    max_instrument_id=15,
                )
                if choice is not None:
                    # 只有当代偿能比八度折叠更接近目标实际音高时才采用。
                    folded_audible = fitted + instrument_pitch_offset(n.instrument)
                    if abs(choice.audible_key - target_abs_key) <= abs(folded_audible - target_abs_key):
                        n.substitution_from = n.instrument
                        n.instrument = choice.instrument
                        fitted = choice.key
                        folded = 0
                        n.substituted_instrument = True
                        n.substitution_target_abs_key = target_abs_key
                        stats.instrument_substituted_notes += 1

            n.chosen_shift = best_shift
            n.folded_semitones = folded

            if is_percussion(n.instrument) and percussion_mode == "center":
                if n.key != NBS_MC_CENTER:
                    stats.centered_percussion += 1

            n.key = clamp(fitted, 0, 87)
            if not is_percussion(n.instrument):
                previous_key_for_layer = n.key

            if bake_fine_pitch and not keep_fine_pitch:
                n.pitch = 0
            elif not keep_fine_pitch:
                # 原版音符盒没有 fine pitch，默认清零，避免 NBS 播放和 Minecraft 实物不一致。
                n.pitch = 0

            if n.key != old_key or n.pitch != n.original_pitch:
                stats.changed_notes += 1
            if folded:
                stats.folded_notes += 1

    # 1.5 实验性同 tick 和弦联合重排。
    # 默认关闭：上一版问题就出在这里，它会为了局部协和破坏原曲声部关系。
    if revoice_chords:
        changed, ticks = revoice_tick_chords(
            out,
            mode,
            percussion_mode,
            bake_fine_pitch,
            duration_weights,
            voice_leading_weight,
            vertical_harmony_weight,
            spread_weight,
        )
        stats.chord_revoiced_notes = changed
        stats.chord_revoiced_ticks = ticks

    # 1.6 可选：风格修补。
    # 只在同音名八度候选里平滑突兀大跳，不重新编曲。
    if style_repair:
        stats.style_repaired_notes = apply_style_repair(
            out,
            mode,
            percussion_mode,
            jump_threshold=style_repair_jump,
            passes=style_repair_passes,
            strength=style_repair_strength,
        )

    # 1.7 可选：邻音陪跑修补。
    # 这个功能不是只修被折叠的那个音，而是允许前后几个同音名音符陪着换八度，
    # 专门修“A没越界没动，B越界折叠后 AB 连起来突兀”的问题。
    if phrase_repair:
        changed, windows = apply_phrase_repair(
            out,
            mode,
            percussion_mode,
            radius=phrase_repair_radius,
            jump_threshold=phrase_repair_jump,
            strength=phrase_repair_strength,
            move_clean_penalty=phrase_repair_move_clean_penalty,
            passes=phrase_repair_passes,
        )
        stats.phrase_repaired_notes = changed
        stats.phrase_repaired_windows = windows

    # 1.8 可选：超级大和弦加厚。
    # 放在丢极端音之前/之后都行；这里放前面，后续 max_chord_notes 仍可控总密度。
    if mega_chord:
        added, layers_added = apply_mega_chord(
            out,
            trigger=mega_chord_trigger,
            width=mega_chord_width,
            max_added_per_tick=mega_chord_max_added_per_tick,
            velocity_factor=mega_chord_velocity,
            layer_count=mega_chord_layers,
            color=mega_chord_color,
        )
        stats.mega_chord_added_notes = added
        stats.mega_chord_layers = layers_added

    # 2. 可选：丢弃折叠太夸张且重要度低的极端音
    if drop_extreme_folds > 0:
        kept = []
        for n in out.notes:
            if n.folded_semitones > drop_extreme_folds:
                # 重要音尽量留，边角料才扔
                imp = note_importance(n, out, duration_weights.get(id(n), 1.0))
                if imp < 0.55:
                    stats.dropped_notes += 1
                    continue
            kept.append(n)
        out.notes = kept

    # 3. 可选：同 tick 重复音去重。
    # 默认关闭，因为多音轨 NBS 里“同音重复”常用于加厚音色/音量；
    # 强行去掉会让 14 轨编曲听起来突然变薄。
    if dedupe:
        before = len(out.notes)
        dedup: Dict[Tuple[int, int, int], Note] = {}
        for n in out.notes:
            k = (n.tick, n.instrument, n.key)
            old = dedup.get(k)
            if old is None:
                dedup[k] = n
            else:
                old_imp = note_importance(old, out, duration_weights.get(id(old), 1.0))
                new_imp = note_importance(n, out, duration_weights.get(id(n), 1.0))
                if new_imp > old_imp:
                    dedup[k] = n
        out.notes = list(dedup.values())
        stats.deduped_notes += before - len(out.notes)

    # 4. 可选：限制同 tick 过密和弦
    if max_chord_notes and max_chord_notes > 0:
        before = len(out.notes)
        by_tick: Dict[int, List[Note]] = defaultdict(list)
        for n in out.notes:
            by_tick[n.tick].append(n)

        kept_all: List[Note] = []
        for tick, tick_notes in by_tick.items():
            if len(tick_notes) <= max_chord_notes:
                kept_all.extend(tick_notes)
                continue

            # 优先保留：打击乐、高权重、低音、高音，防止一刀切把骨架扔没。
            tick_notes_sorted = sorted(
                tick_notes,
                key=lambda n: (
                    1 if is_percussion(n.instrument) else 0,
                    note_importance(n, out, duration_weights.get(id(n), 1.0)),
                ),
                reverse=True,
            )

            forced = set()

            pitched = [n for n in tick_notes if not is_percussion(n.instrument)]
            if pitched:
                forced.add(id(min(pitched, key=lambda n: n.key)))
                forced.add(id(max(pitched, key=lambda n: n.key)))

            selected: List[Note] = []
            for n in tick_notes_sorted:
                if id(n) in forced and n not in selected:
                    selected.append(n)

            for n in tick_notes_sorted:
                if len(selected) >= max_chord_notes:
                    break
                if n not in selected:
                    selected.append(n)

            kept_all.extend(selected)

        out.notes = kept_all
        stats.dropped_notes += before - len(out.notes)

    stats.out_after = count_out_of_range(out, mode, percussion_mode)

    # 排序，方便稳定输出
    out.notes.sort(key=lambda n: (n.tick, n.layer, n.instrument, n.key))

    return out, stats



def normalize_for_writer(
    song: NBSFile,
    instrument_set: str = "mc-1.21",
    output_version: str = "auto",
    custom_instruments_mode: str = "remap",
    fallback_instrument: int = 0,
) -> Tuple[NBSFile, Dict[str, int]]:
    """
    修正 NBS 写出兼容性，避免 Note Block Studio / 播放器把乐器表读炸。

    重点修正：
    1. 新版 vanilla_instrument_count 必须覆盖实际使用的 vanilla instrument ID。
    2. 如果输入是 classic v0，但用了 ID 10~15，必须升级到新版格式，否则旧规则会把 10+ 当 custom。
    3. 默认把 custom / 目标版本不支持的乐器重映射到 fallback，保证 Minecraft 原版音符盒可播。
    """
    out = copy.deepcopy(song)
    info = INSTRUMENT_SET_INFO[instrument_set]
    vanilla_count = int(info["vanilla_count"])
    min_version = int(info["min_version"])
    max_id = int(info["max_id"])

    fallback_instrument = clamp(fallback_instrument, 0, max_id)

    h = out.header

    if output_version == "keep":
        # keep 也不能盲目保持 classic v0，否则 ID 10~15 会被旧读取器当 custom。
        if h.version == 0 and vanilla_count > 10:
            h.version = min_version
    elif output_version == "auto":
        h.version = max(h.version, min_version)
    else:
        h.version = int(output_version)
        if h.version < min_version:
            h.version = min_version

    if h.version >= 1:
        h.vanilla_instrument_count = vanilla_count
    else:
        h.vanilla_instrument_count = 10

    remapped = 0
    invalid_custom = 0

    valid_custom_start = vanilla_count
    valid_custom_end = vanilla_count + len(out.custom_instruments)

    for n in out.notes:
        old = n.instrument
        if 0 <= n.instrument <= max_id:
            continue

        if custom_instruments_mode == "keep" and valid_custom_start <= n.instrument < valid_custom_end:
            continue

        if n.instrument >= valid_custom_start:
            invalid_custom += 1
        n.instrument = fallback_instrument
        if n.instrument != old:
            remapped += 1

    if custom_instruments_mode == "remap":
        out.custom_instruments = []

    # 旧 classic-10 没有新乐器，强制映射 10+。
    if instrument_set == "classic-10":
        for n in out.notes:
            if n.instrument > 9:
                n.instrument = fallback_instrument
                remapped += 1
        if custom_instruments_mode == "remap":
            out.custom_instruments = []

    return out, {
        "writer_version": h.version,
        "vanilla_instrument_count": h.vanilla_instrument_count,
        "remapped_instruments": remapped,
        "invalid_custom_refs": invalid_custom,
        "custom_instruments_written": len(out.custom_instruments),
    }


# =========================
# 输出辅助
# =========================

def print_ranges() -> None:
    print("Minecraft / NBS 音符盒范围表")
    print("=" * 78)
    print(f"NBS 安全范围：key {NBS_MC_LOW}~{NBS_MC_HIGH} = {nbs_key_name(NBS_MC_LOW)}~{nbs_key_name(NBS_MC_HIGH)}")
    print(f"Minecraft blockstate note：0~24，NBS key = note + {NBS_MC_LOW}")
    print()
    print(f"{'ID':>2}  {'NBS Instrument':<18} {'MC':<16} {'方块':<18} {'实际听感范围':<16} {'Java sound event'}")
    print("-" * 120)
    for i in sorted(INSTRUMENTS):
        info = INSTRUMENTS[i]
        print(
            f"{i:>2}  "
            f"{info.nbs_name:<18} "
            f"{info.mc_name:<18} "
            f"{info.block:<24} "
            f"{info.audible_range_text:<16} "
            f"{info.java_sound_event}"
        )
    print()


def summarize_song(song: NBSFile) -> str:
    if not song.notes:
        return "空 NBS，没有音符。"

    min_key = min(n.key for n in song.notes)
    max_key = max(n.key for n in song.notes)
    ticks = max(n.tick for n in song.notes) + 1
    layers = max(n.layer for n in song.notes) + 1

    return (
        f"音符数={len(song.notes)}, ticks≈{ticks}, layers≈{layers}, "
        f"key范围={min_key}~{max_key} ({nbs_key_name(min_key)}~{nbs_key_name(max_key)})"
    )


def print_stats(stats: ConvertStats, song_before: NBSFile, song_after: NBSFile) -> None:
    print("转换完成")
    print("=" * 78)
    print("输入：", summarize_song(song_before))
    print("输出：", summarize_song(song_after))
    print()
    print(f"总音符数：{stats.total_notes}")
    print(f"转换前越界：{stats.out_before}")
    print(f"转换后越界：{stats.out_after}")
    print(f"改变音符：{stats.changed_notes}")
    print(f"发生八度折叠：{stats.folded_notes}")
    print(f"全曲基准移调：{stats.global_shift:+d} 半音")
    print(f"整体移调过的 layer：{stats.shifted_layers}")
    print(f"和弦重排音符：{stats.chord_revoiced_notes}")
    print(f"和弦重排 tick：{stats.chord_revoiced_ticks}")
    print(f"相似乐器代偿：{stats.instrument_substituted_notes}")
    print(f"风格修补音符：{stats.style_repaired_notes}")
    print(f"邻音陪跑修补音符：{stats.phrase_repaired_notes}")
    print(f"邻音陪跑处理 layer：{stats.phrase_repaired_windows}")
    print(f"超级和弦新增音符：{stats.mega_chord_added_notes}")
    print(f"超级和弦新增 layer：{stats.mega_chord_layers}")
    print(f"打击乐归中：{stats.centered_percussion}")
    print(f"重复音去重：{stats.deduped_notes}")
    print(f"丢弃音符：{stats.dropped_notes}")

    nonzero = {k: v for k, v in stats.layer_shifts.items() if v != 0}
    if nonzero:
        print()
        print("Layer 移调：")
        for layer, shift in sorted(nonzero.items()):
            print(f"  layer {layer}: {shift:+d} 半音")


def make_backup(path: str) -> str:
    base = path + ".bak"
    candidate = base
    i = 1
    while os.path.exists(candidate):
        candidate = f"{base}.{i}"
        i += 1
    with open(path, "rb") as src, open(candidate, "wb") as dst:
        dst.write(src.read())
    return candidate


# =========================
# 单文件 / 批量转换入口工具
# =========================

def convert_one_file(input_path: str, output_path: str, args: argparse.Namespace, *, quiet: bool = False) -> Tuple[bool, str]:
    """转换单个 NBS 文件。返回 (成功与否, 简短消息)。"""
    try:
        song = read_nbs(input_path)

        converted, stats = apply_conversion(
            song,
            mode=args.mode,
            percussion_mode=args.percussion,
            bake_fine_pitch=not args.no_bake_fine_pitch,
            keep_fine_pitch=args.keep_fine_pitch,
            shift_range=args.shift_search,
            shift_penalty=args.shift_penalty,
            non_octave_shift_penalty=args.non_octave_shift_penalty,
            max_chord_notes=args.max_chord_notes,
            drop_extreme_folds=args.drop_extreme_folds,
            dedupe=args.dedupe,
            arrangement=args.arrangement,
            revoice_chords=(args.revoice and not args.no_revoice),
            voice_leading_weight=args.voice_leading_weight,
            vertical_harmony_weight=args.vertical_harmony_weight,
            spread_weight=args.spread_weight,
            instrument_substitution=args.instrument_substitution,
            instrument_substitution_profile=args.instrument_substitution_profile,
            instrument_substitution_max_cost=args.instrument_substitution_max_cost,
            style_repair=args.style_repair,
            style_repair_jump=args.style_repair_jump,
            style_repair_passes=args.style_repair_passes,
            style_repair_strength=args.style_repair_strength,
            phrase_repair=args.phrase_repair,
            phrase_repair_radius=args.phrase_repair_radius,
            phrase_repair_jump=args.phrase_repair_jump,
            phrase_repair_strength=args.phrase_repair_strength,
            phrase_repair_move_clean_penalty=args.phrase_repair_move_clean_penalty,
            phrase_repair_passes=args.phrase_repair_passes,
            mega_chord=args.mega_chord,
            mega_chord_trigger=args.mega_chord_trigger,
            mega_chord_width=args.mega_chord_width,
            mega_chord_max_added_per_tick=args.mega_chord_max_added_per_tick,
            mega_chord_velocity=args.mega_chord_velocity,
            mega_chord_layers=args.mega_chord_layers,
            mega_chord_color=args.mega_chord_color,
        )

        converted_for_write, writer_info = normalize_for_writer(
            converted,
            instrument_set=args.instrument_set,
            output_version=args.output_version,
            custom_instruments_mode=args.custom_instruments,
            fallback_instrument=args.fallback_instrument,
        )

        if not quiet:
            print_stats(stats, song, converted_for_write)
            print()
            print("写出兼容性：")
            print(f"  NBS version: v{writer_info['writer_version']}")
            print(f"  vanilla_instrument_count: {writer_info['vanilla_instrument_count']}")
            print(f"  重映射不支持/custom 乐器音符: {writer_info['remapped_instruments']}")
            print(f"  写出的 custom instruments: {writer_info['custom_instruments_written']}")

        if args.dry_run:
            return True, "dry-run：未写出"

        output_parent = os.path.dirname(os.path.abspath(output_path))
        if output_parent:
            os.makedirs(output_parent, exist_ok=True)

        if os.path.abspath(output_path) == os.path.abspath(input_path) and not args.no_backup:
            backup = make_backup(input_path)
            if not quiet:
                print(f"已创建备份：{backup}")

        write_nbs(converted_for_write, output_path)
        return True, f"已写出：{output_path}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def iter_nbs_files(input_dir: Path, recursive: bool) -> List[Path]:
    pattern = "**/*.nbs" if recursive else "*.nbs"
    return sorted(p for p in input_dir.glob(pattern) if p.is_file())


def choose_batch_output_dir(input_dir: Path, output_arg: Optional[str]) -> Path:
    if output_arg:
        return Path(output_arg)
    return input_dir.with_name(input_dir.name + "_converted")


def batch_convert(input_arg: str, output_arg: Optional[str], args: argparse.Namespace) -> int:
    input_dir = Path(input_arg).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"错误：批量模式的输入必须是文件夹：{input_dir}", file=sys.stderr)
        return 2

    output_dir = choose_batch_output_dir(input_dir, output_arg).expanduser().resolve()
    if output_dir.exists() and output_dir.is_file():
        print(f"错误：批量输出路径不能是文件：{output_dir}", file=sys.stderr)
        return 2

    same_dir = output_dir == input_dir
    if same_dir and not args.batch_in_place:
        print("错误：批量输出目录不能和输入目录相同，除非显式加 --batch-in-place。", file=sys.stderr)
        print("建议：不写输出目录，让程序自动生成 xxx_converted 文件夹。", file=sys.stderr)
        return 2

    files = iter_nbs_files(input_dir, args.recursive)
    if not files:
        print(f"没有找到 .nbs 文件：{input_dir}")
        return 0

    if args.dry_run:
        print("dry-run：只分析，不写出文件。")

    print("批量转换配置：")
    print(f"  输入目录：{input_dir}")
    print(f"  输出目录：{output_dir}")
    print(f"  递归扫描：{'是' if args.recursive else '否'}")
    print(f"  文件数量：{len(files)}")
    print(f"  覆盖已有输出：{'是' if args.overwrite else '否'}")
    print()

    ok_count = 0
    skip_count = 0
    fail_count = 0
    report_lines: List[str] = []

    for index, src in enumerate(files, start=1):
        rel = src.relative_to(input_dir)
        if same_dir:
            dst = src
        else:
            dst = output_dir / rel

        if args.batch_suffix and not same_dir:
            dst = dst.with_name(dst.stem + args.batch_suffix + dst.suffix)

        if dst.exists() and not args.overwrite and not args.dry_run and not same_dir:
            skip_count += 1
            line = f"[{index}/{len(files)}] 跳过已存在：{rel}"
            print(line)
            report_lines.append("SKIP\t" + str(rel) + "\toutput exists")
            continue

        print(f"[{index}/{len(files)}] 转换：{rel}")
        success, message = convert_one_file(str(src), str(dst), args, quiet=not args.batch_verbose)
        if success:
            ok_count += 1
            print(f"    OK：{message}")
            report_lines.append("OK\t" + str(rel) + "\t" + message)
        else:
            fail_count += 1
            print(f"    失败：{message}")
            report_lines.append("FAIL\t" + str(rel) + "\t" + message)
            if not args.continue_on_error:
                print("遇到错误，已停止。可加 --continue-on-error 继续处理后续文件。")
                break

    print()
    print("批量转换完成：")
    print(f"  成功：{ok_count}")
    print(f"  跳过：{skip_count}")
    print(f"  失败：{fail_count}")

    if args.batch_report:
        report_path = Path(args.batch_report).expanduser().resolve()
    else:
        report_path = output_dir / "batch_report.tsv"

    if not args.dry_run:
        os.makedirs(report_path.parent, exist_ok=True)
        report_path.write_text("status\tfile\tmessage\n" + "\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"  报告：{report_path}")

    return 1 if fail_count else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="保守地把 NBS 音符转换到 Minecraft 音符盒可用音域，并可选开启相似乐器代偿/风格修补/邻音陪跑修补/超级和弦。"
    )
    parser.add_argument("input", help="输入 .nbs 文件")
    parser.add_argument("output", nargs="?", help="输出 .nbs 文件；不写则覆盖输入文件")
    parser.add_argument(
        "--mode",
        choices=["nbs-safe", "instrument-audible"],
        default="nbs-safe",
        help=(
            "nbs-safe：所有非打击乐压到 NBS key 33~57，最适合 OpenNBS / 原版导出；"
            "instrument-audible：按 Minecraft 各乐器实际听感范围处理。默认 nbs-safe。"
        ),
    )
    parser.add_argument(
        "--percussion",
        choices=["center", "fold", "keep"],
        default="center",
        help="打击乐处理：center=统一 key 45；fold=折进33~57；keep=不处理。默认 center。",
    )
    parser.add_argument(
        "--shift-search",
        type=parse_int_range,
        default=(-24, 24),
        help="每个 layer 自动移调搜索范围，格式 min:max，单位半音。默认 -24:24。",
    )
    parser.add_argument(
        "--shift-penalty",
        type=float,
        default=0.035,
        help="整体移调惩罚，越大越不愿意移调。默认 0.035。",
    )
    parser.add_argument(
        "--non-octave-shift-penalty",
        type=float,
        default=0.45,
        help="非 12 半音倍数移调的额外惩罚，越大越倾向只升降八度。默认 0.45。",
    )
    parser.add_argument(
        "--arrangement",
        choices=["preserve", "ensemble", "global", "layer"],
        default="preserve",
        help=(
            "多轨移调策略：preserve=保守推荐，统一移调+按 layer 保持寄存器，不重排和弦；"
            "ensemble=旧智能模式；global=所有轨完全同一个移调；layer=旧模式，每轨独立移调。默认 preserve。"
        ),
    )
    parser.add_argument(
        "--revoice",
        action="store_true",
        help="开启实验性同 tick 和弦联合重排。默认关闭；不建议多音轨 NBS 使用。",
    )
    parser.add_argument(
        "--no-revoice",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--instrument-substitution",
        action="store_true",
        help="开启相似乐器代偿：音高下/上不去时，尝试换成天然更低/更高的相似乐器来保留原音高。默认关闭。",
    )
    parser.add_argument(
        "--instrument-substitution-profile",
        choices=["similar", "wide"],
        default="similar",
        help="相似乐器代偿范围：similar=只选接近音色；wide=允许更大胆跨音色。默认 similar。",
    )
    parser.add_argument(
        "--instrument-substitution-max-cost",
        type=float,
        default=1.35,
        help="相似乐器最大替换成本，越大越敢换音色。默认 1.35。",
    )
    parser.add_argument(
        "--style-repair",
        action="store_true",
        help="开启风格修补：只修局部突兀大跳，在同音名八度里选更像原曲旋律线的位置。默认关闭。",
    )
    parser.add_argument(
        "--style-repair-jump",
        type=int,
        default=14,
        help="风格修补触发阈值：相邻跳进超过多少半音才考虑修。默认 14。",
    )
    parser.add_argument(
        "--style-repair-passes",
        type=int,
        default=2,
        help="风格修补迭代次数。默认 2。",
    )
    parser.add_argument(
        "--style-repair-strength",
        type=float,
        default=1.0,
        help="风格修补强度。越大越追求平滑，但也更可能变味。默认 1.0。",
    )
    parser.add_argument(
        "--phrase-repair",
        action="store_true",
        help="开启邻音陪跑修补：B 因越界折叠后，如果和 A/C 连起来突兀，允许 A/C 在同音名八度里陪着移动。默认关闭。",
    )
    parser.add_argument(
        "--phrase-repair-radius",
        type=int,
        default=2,
        help="邻音陪跑窗口半径。2 表示最多看前后各 2 个音。越大越像整句重排，默认 2。",
    )
    parser.add_argument(
        "--phrase-repair-jump",
        type=int,
        default=9,
        help="邻音陪跑触发阈值：转换后相邻音程与原曲相邻音程差多少半音才触发。默认 9。",
    )
    parser.add_argument(
        "--phrase-repair-strength",
        type=float,
        default=1.0,
        help="邻音陪跑强度。越大越追求旋律连续，越小越保守。默认 1.0。",
    )
    parser.add_argument(
        "--phrase-repair-move-clean-penalty",
        type=float,
        default=3.2,
        help="移动原本没越界音符的惩罚。越大越不愿意让 A/C 陪跑。默认 3.2。",
    )
    parser.add_argument(
        "--phrase-repair-passes",
        type=int,
        default=1,
        help="邻音陪跑修补迭代次数。默认 1，通常不要开太高。",
    )
    parser.add_argument(
        "--mega-chord",
        action="store_true",
        help="开启超级大和弦加厚：给折叠/替换过的关键音添加低音量幽灵音。默认关闭。",
    )
    parser.add_argument(
        "--mega-chord-trigger",
        choices=["folded", "changed", "all"],
        default="changed",
        help="超级和弦触发范围：folded=只给折叠音；changed=折叠或替换音；all=所有非打击乐。默认 changed。",
    )
    parser.add_argument(
        "--mega-chord-width",
        type=int,
        default=2,
        help="每个触发音最多添加几个幽灵音。默认 2。",
    )
    parser.add_argument(
        "--mega-chord-max-added-per-tick",
        type=int,
        default=10,
        help="每个 tick 最多新增几个幽灵音，防止糊成墙。默认 10。",
    )
    parser.add_argument(
        "--mega-chord-velocity",
        type=float,
        default=0.42,
        help="幽灵音音量倍率。默认 0.42。",
    )
    parser.add_argument(
        "--mega-chord-layers",
        type=int,
        default=8,
        help="超级和弦额外创建多少个 ghost layer。默认 8。",
    )
    parser.add_argument(
        "--mega-chord-color",
        choices=["same", "bright", "warm"],
        default="same",
        help="超级和弦音色：same=同乐器；bright=上方泛音偏亮；warm=下方泛音偏暖。默认 same。",
    )
    parser.add_argument(
        "--voice-leading-weight",
        type=float,
        default=1.25,
        help="声部连续性权重，越大越不愿意让同一 layer 突然跳八度。默认 1.25。",
    )
    parser.add_argument(
        "--vertical-harmony-weight",
        type=float,
        default=1.45,
        help="垂直和声权重，越大越避免同 tick 半音冲突/三全音/声部交叉。默认 1.45。",
    )
    parser.add_argument(
        "--spread-weight",
        type=float,
        default=1.0,
        help="同 tick 多音摊开权重，越大越不容易挤成一团。默认 1.0。",
    )
    parser.add_argument(
        "--keep-fine-pitch",
        action="store_true",
        help="保留 NBS v4+ 的 fine pitch。注意原版音符盒实物不支持细微音高。",
    )
    parser.add_argument(
        "--no-bake-fine-pitch",
        action="store_true",
        help="不要把 fine pitch 四舍五入烘焙到 key。默认会烘焙，然后清零 fine pitch。",
    )
    parser.add_argument(
        "--max-chord-notes",
        type=int,
        default=0,
        help="限制同一个 tick 最多保留多少个音。0=不限制。建议 16~32。",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="开启同 tick + 同乐器 + 同 key 重复音去重。默认关闭，保留多轨加厚效果。",
    )
    parser.add_argument(
        "--drop-extreme-folds",
        type=int,
        default=0,
        help="折叠超过多少半音且重要度较低时丢弃。0=不丢弃。建议 36 或 48。",
    )
    parser.add_argument(
        "--instrument-set",
        choices=["classic-10", "mc-1.21", "mc-26.1"],
        default="mc-1.21",
        help=(
            "写出时使用的原版乐器集合。"
            "classic-10=旧版 0~9；mc-1.21=0~15，兼容性最好；"
            "mc-26.1=0~19，包含铜质小号，需要支持 NBS v6 的新版 OpenNBS/播放器。默认 mc-1.21。"
        ),
    )
    parser.add_argument(
        "--output-version",
        choices=["auto", "5", "6", "keep"],
        default="auto",
        help="写出 NBS 版本。auto 会按 instrument-set 自动升级。默认 auto。",
    )
    parser.add_argument(
        "--custom-instruments",
        choices=["remap", "keep"],
        default="remap",
        help="custom/目标版本不支持的乐器：remap=映射到 fallback；keep=尽量保留。默认 remap。",
    )
    parser.add_argument(
        "--fallback-instrument",
        type=int,
        default=0,
        help="custom/不支持乐器的回退乐器 ID。默认 0=Harp/Piano。可用 15=Pling。",
    )
    parser.add_argument(
        "--print-ranges",
        action="store_true",
        help="打印 Minecraft / NBS 音符盒范围表。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只分析和打印统计，不写出文件。",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="覆盖输入文件时不创建 .bak 备份。",
    )


    parser.add_argument(
        "--batch",
        action="store_true",
        help="批量模式：把 input 当作文件夹，转换其中所有 .nbs。input 本身是文件夹时也会自动启用。",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="批量模式递归扫描子文件夹，并在输出目录保持原目录结构。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="批量模式下覆盖已存在的输出文件。默认跳过已存在文件。",
    )
    parser.add_argument(
        "--batch-in-place",
        action="store_true",
        help="允许批量原地覆盖输入目录中的文件。危险操作；默认禁止。会遵守 --no-backup。",
    )
    parser.add_argument(
        "--batch-suffix",
        default="",
        help="批量输出文件名后缀。例如 --batch-suffix _mc 会输出 song_mc.nbs。原地批量时忽略。",
    )
    parser.add_argument(
        "--batch-report",
        default="",
        help="批量转换报告路径。默认写到输出目录 batch_report.tsv。",
    )
    parser.add_argument(
        "--batch-verbose",
        action="store_true",
        help="批量模式下为每个文件打印完整统计。默认只打印简短进度。",
    )
    parser.add_argument(
        "--continue-on-error",
        dest="continue_on_error",
        action="store_true",
        default=True,
        help="批量模式遇到坏文件继续处理后续文件。默认开启。",
    )
    parser.add_argument(
        "--stop-on-error",
        dest="continue_on_error",
        action="store_false",
        help="批量模式遇到第一个错误就停止。",
    )

    args = parser.parse_args()

    if args.print_ranges:
        print_ranges()

    input_path = args.input
    output_path = args.output or args.input

    if args.batch or os.path.isdir(input_path):
        return batch_convert(input_path, args.output, args)

    success, message = convert_one_file(input_path, output_path, args, quiet=False)
    if success:
        if args.dry_run:
            print()
        print(message)
        return 0

    print(f"转换失败：{message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
