# NBS-Minecraft-RangeFixer

[中文说明](README.md) | English

A conservative `.nbs` converter for fixing notes outside the playable Minecraft note block range.

This tool is designed for Minecraft note block music, NBS preprocessing, and server-side music systems.  
Instead of simply clamping notes to the boundary or rounding pitches, it tries to preserve the original melody, harmony, and layer relationships as much as possible.

---

## Features

### Conservative Range Fixing

The default strategy focuses on preserving the original song:

- Global base transposition
- Layer-aware register preservation
- Octave folding for out-of-range notes
- Minimal changes to original chords
- Avoids aggressive chord re-arrangement

The main goal is:

```text
Change as little as possible.
Fix only what needs to be fixed.
````

---

### Instrument Substitution

Optional similar-instrument substitution.

When a note cannot be played naturally in the Minecraft note block range, the converter can try using another instrument with a similar tone but a different register.

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --instrument-substitution
```

Useful for:

* Notes that are too low
* Notes that are too high
* Harp / Guitar / Bass / Flute / Bell register compensation
* Cases where octave folding sounds too sudden

---

### Style Repair

Optional style repair for sudden melodic jumps.

It attempts to fix notes that become awkward after conversion, especially when octave folding creates unnatural leaps.

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --style-repair
```

Recommended:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --style-repair \
  --style-repair-jump 16 \
  --style-repair-strength 0.8
```

---

### Phrase Repair

Phrase repair is designed for this situation:

```text
Note A is already inside the valid range, so it is not changed.
Note B is outside the range, so it gets folded.
After conversion, A and B sound very awkward together.
```

With phrase repair enabled, nearby notes may move together by octaves to preserve the melodic line.

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --phrase-repair
```

Recommended combination:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --phrase-repair-radius 2 \
  --phrase-repair-jump 9 \
  --phrase-repair-move-clean-penalty 3.2
```

---

### Mega Chord Enhancement

Optional mega chord enhancement.

This feature adds low-volume helper notes to selected repaired notes, making the sound thicker and closer to the original arrangement.

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --mega-chord
```

Recommended mild settings:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --mega-chord \
  --mega-chord-width 1 \
  --mega-chord-max-added-per-tick 6 \
  --mega-chord-velocity 0.30
```

Do not enable this too aggressively on songs with many layers, or the result may become muddy.

---

## Minecraft Note Block Range

Minecraft note blocks use:

```text
Minecraft note: 0 ~ 24
```

This gives 25 semitone steps.

In NBS key values, the common safe range is:

```text
NBS key: 33 ~ 57
```

Mapping:

```text
NBS key 33 -> Minecraft note 0
NBS key 45 -> Minecraft note 12
NBS key 57 -> Minecraft note 24
```

This converter tries to keep normal pitched notes within this range.

---

## Requirements

Python 3.9 or newer is recommended.

No third-party Python packages are required.

---

## Installation

Clone or download this repository:

```bash
git clone https://github.com/your-name/NBS-Minecraft-RangeFixer.git
cd NBS-Minecraft-RangeFixer
```

Repository structure:

```text
NBS-Minecraft-RangeFixer/
├─ nbs_minecraft_range_converter_experimental_v4_batch.py
├─ README.md
├─ README.zh-CN.md
└─ LICENSE
```

---

## Basic Usage

Convert a single file:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs
```

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py song.nbs fixed_song.nbs
```

---

## Recommended Usage

Stable recommended configuration:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair
```

More complete configuration:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --phrase-repair-radius 2 \
  --phrase-repair-jump 9 \
  --phrase-repair-move-clean-penalty 3.2
```

With mild mega chord enhancement:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --mega-chord \
  --mega-chord-width 1 \
  --mega-chord-max-added-per-tick 6 \
  --mega-chord-velocity 0.30
```

---

## Batch Conversion

Convert all `.nbs` files in a folder:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch
```

If no output folder is provided, the converter will create:

```text
songs_converted/
```

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs --batch
```

---

## Recursive Batch Conversion

Convert `.nbs` files inside subfolders:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch --recursive
```

Example input:

```text
songs/
├─ A/
│  └─ song1.nbs
└─ B/
   └─ song2.nbs
```

Example output:

```text
converted_songs/
├─ A/
│  └─ song1.nbs
└─ B/
   └─ song2.nbs
```

---

## Overwrite Existing Files

By default, existing output files are skipped.

To overwrite existing files:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch --overwrite
```

---

## In-Place Batch Conversion

In-place conversion is supported but not recommended unless you have backups.

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs songs --batch --batch-in-place
```

By default, `.bak` backup files will be created.

To disable backups:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs songs --batch --batch-in-place --no-backup
```

Use this carefully.

---

## Common Options

### `--instrument-substitution`

Enables similar-instrument substitution.

Useful when pitch folding alone sounds unnatural.

---

### `--instrument-substitution-profile`

Controls how aggressive instrument substitution is.

Common values:

```text
safe
wide
```

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --instrument-substitution-profile wide
```

`safe` is more conservative.
`wide` allows more aggressive substitutions.

---

### `--style-repair`

Enables local style repair.

Useful for fixing sudden jumps or broken melodic lines.

---

### `--style-repair-jump`

Controls how large a jump must be before style repair is triggered.

Example:

```bash
--style-repair-jump 16
```

Lower values make repair more sensitive.

---

### `--style-repair-strength`

Controls repair strength.

Example:

```bash
--style-repair-strength 0.8
```

Higher values make the repair more active.

---

### `--phrase-repair`

Enables phrase repair.

Useful when one note is folded but nearby notes are not, causing an awkward melodic jump.

---

### `--phrase-repair-radius`

Controls how many nearby notes are considered.

```text
1 = only immediate neighbors
2 = recommended default
3 = more phrase-like repair, but may change the song more
```

---

### `--phrase-repair-jump`

Controls the jump threshold for phrase repair.

Recommended:

```bash
--phrase-repair-jump 9
```

If there are still many awkward jumps, try:

```bash
--phrase-repair-jump 7
```

---

### `--phrase-repair-move-clean-penalty`

Controls how reluctant the program is to move notes that were already valid.

Recommended:

```bash
--phrase-repair-move-clean-penalty 3.2
```

More conservative:

```bash
--phrase-repair-move-clean-penalty 4.5
```

More active:

```bash
--phrase-repair-move-clean-penalty 2.2
```

---

### `--mega-chord`

Enables mega chord enhancement.

This can make the result thicker, but it can also make dense songs muddy.

---

### `--mega-chord-width`

Controls the width of added helper notes.

Recommended:

```bash
--mega-chord-width 1
```

---

### `--mega-chord-max-added-per-tick`

Controls how many helper notes can be added per tick.

Recommended:

```bash
--mega-chord-max-added-per-tick 6
```

---

### `--mega-chord-velocity`

Controls the volume of added helper notes.

Recommended:

```bash
--mega-chord-velocity 0.30
```

---

## Recommended Tuning Order

Do not enable everything at once.

Recommended workflow:

```text
1. Run the default conversion
2. Enable instrument substitution
3. Enable style repair
4. Enable phrase repair
5. Try mega chord enhancement only if the song feels too thin
```

Example:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output_1.nbs

python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output_2.nbs \
  --instrument-substitution

python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output_3.nbs \
  --instrument-substitution \
  --style-repair

python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output_4.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair
```

---

## Tips for Songs with Many Layers

For songs with many layers, such as 10, 14, or more tracks, avoid overly aggressive settings at first.

Recommended:

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --phrase-repair-radius 2 \
  --phrase-repair-jump 9
```

If the song becomes too thick or muddy, disable:

```bash
--mega-chord
```

If awkward jumps remain, try:

```bash
--phrase-repair-jump 7
```

If the song sounds over-repaired, try:

```bash
--phrase-repair-move-clean-penalty 4.5
```

---

## FAQ

### Why not simply round the pitch?

Because rounding can change the note name.

For example, C may become C# or D.
That usually sounds worse than moving the same note name by octaves.

---

### Why not simply clamp notes to 0 or 24?

Because clamping can flatten an entire melody into the same boundary note.

Example:

```text
F# F# F# F# F#
```

That sounds stiff and unnatural.

---

### Why does the converter avoid aggressive chord rearrangement?

Because many NBS files use layers as part of the arrangement.

If the program aggressively reorders or rewrites chords, the result may become mathematically cleaner but musically worse.

This project follows a conservative idea:

```text
Better slightly imperfect than completely re-arranged.
```

---

### Why can mega chord make the song muddy?

Mega chord adds helper notes.

If the original song already has many layers, adding more notes can make the result too dense.

Recommended mild settings:

```bash
--mega-chord-width 1
--mega-chord-max-added-per-tick 6
--mega-chord-velocity 0.30
```

---

## Files

```text
nbs_minecraft_range_converter_experimental_v4_batch.py
```

Main converter.

Includes:

* Single-file conversion
* Batch conversion
* Range fixing
* Instrument substitution
* Style repair
* Phrase repair
* Mega chord enhancement

This repository does not include any song-specific auto-run scripts.

---

## Disclaimer

This tool cannot perfectly restore every song.

Minecraft note blocks have a limited pitch range.
Complex piano pieces, dense MIDI conversions, large chords, and songs with extreme pitch ranges will always require some compromise.

The goal of this tool is:

```text
Avoid obvious wrong notes.
Reduce awkward jumps.
Preserve the original song as much as possible.
Make NBS files more suitable for Minecraft playback.
```

---

## License

This project is licensed under the MIT License.