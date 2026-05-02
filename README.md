# NBS-Minecraft-RangeFixer

中文说明 | [English](README.en-US.md)

一个用于修复 `.nbs` 文件中音符超出 Minecraft 音符盒音域范围问题的转换器。

它不是简单地把越界音符硬夹到边界，也不是粗暴四舍五入，而是尽量在不破坏原曲旋律和声部关系的情况下，将 NBS 音符压缩到 Minecraft 音符盒可播放范围内。

本项目适合用于：

- Minecraft 音符盒音乐转换
- NBS 文件批量修复
- Minecraft 服务器点歌系统预处理
- OpenNBS / Note Block Studio 文件音域适配
- 多音轨 NBS 的保守型修谱

---

## 项目特点

### 保守型音域修复

默认策略以“少改原曲”为核心：

- 全曲统一基准移调
- 按 layer 保持原本高低关系
- 越界音符优先按八度折叠
- 尽量不打乱原本和弦结构
- 避免激进重排导致歌曲变味

---

### 相似乐器代偿

可选开启相似乐器替换。

例如某些音符低不下去时，可以尝试换成更低沉的乐器来承接，而不是强行把音符折叠到奇怪的位置。

示例：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --instrument-substitution
````

适合处理：

* Bass 下潜不够
* Harp 音域不合适
* Guitar / Flute / Bell 等乐器需要音区代偿
* 直接折叠后听起来突兀的旋律

---

### AI 风格修补

可选开启局部风格修补。

它会尝试修复转换后突然出现的大跳、断裂、怪异跳音。

示例：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --style-repair
```

推荐参数：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --style-repair \
  --style-repair-jump 16 \
  --style-repair-strength 0.8
```

---

### 邻音陪跑修补

用于处理这种情况：

```text
前一个音 A 在范围内，所以没改
后一个音 B 超出范围，所以被折叠
结果 A 和 B 放一起突然非常突兀
```

开启后，程序会允许前后邻音在同音名八度内轻微陪跑，尽量保持旋律线顺滑。

示例：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --phrase-repair
```

推荐组合：

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

### 超级和弦增强

可选开启超级和弦。

它会给部分被修复过的关键音添加低音量辅助音，用来模拟原曲厚度。

示例：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs --mega-chord
```

推荐温和参数：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --mega-chord \
  --mega-chord-width 1 \
  --mega-chord-max-added-per-tick 6 \
  --mega-chord-velocity 0.30
```

注意：
超级和弦不建议一上来就开太猛，否则多音轨歌曲可能会变得很糊。

---

## Minecraft 音符盒范围说明

Minecraft 音符盒的标准可用音高不是无限的。

常见标准音符盒范围可以理解为：

```text
Minecraft note: 0 ~ 24
共 25 个半音点
```

在 NBS key 中，对应常用安全范围：

```text
NBS key: 33 ~ 57
```

也就是：

```text
33 -> Minecraft note 0
45 -> Minecraft note 12
57 -> Minecraft note 24
```

本工具默认会尽量把普通旋律音符处理到这个范围内。

---

## 安装

本项目只需要 Python。

推荐版本：

```text
Python 3.9+
```

不需要额外安装第三方库。

下载或克隆项目后，目录结构大概如下：

```text
NBS-Minecraft-RangeFixer/
├─ nbs_minecraft_range_converter_experimental_v4_batch.py
├─ README.md
└─ LICENSE
```

---

## 基础用法

转换单个文件：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs
```

例如：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py 千本樱.nbs 转换后_千本樱.nbs
```

---

## 推荐用法

比较稳的推荐配置：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair
```

更完整一点：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --phrase-repair-radius 2 \
  --phrase-repair-jump 9 \
  --phrase-repair-move-clean-penalty 3.2
```

如果想加一点厚度：

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

## 批量转换

转换整个文件夹里的所有 `.nbs`：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch
```

如果不写输出目录，会自动输出到：

```text
songs_converted/
```

例如：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs --batch
```

---

## 递归转换子目录

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch --recursive
```

示例结构：

```text
songs/
├─ A/
│  └─ song1.nbs
└─ B/
   └─ song2.nbs
```

输出后：

```text
converted_songs/
├─ A/
│  └─ song1.nbs
└─ B/
   └─ song2.nbs
```

---

## 覆盖已有文件

默认情况下，如果输出文件已经存在，程序会跳过。

如果需要覆盖：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs converted_songs --batch --overwrite
```

---

## 原地转换

不太推荐，但可以使用：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs songs --batch --batch-in-place
```

默认会创建 `.bak` 备份。

如果不想备份：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py songs songs --batch --batch-in-place --no-backup
```

注意：
原地覆盖有风险，建议先备份原始 NBS 文件。

---

## 常用参数说明

### `--instrument-substitution`

开启相似乐器代偿。

适合解决：

* 某个音符下不去
* 折叠后音色突兀
* 原曲音区和 Minecraft 音符盒音区不匹配

---

### `--instrument-substitution-profile`

控制乐器替换范围。

常见值：

```text
safe
wide
```

`safe` 更保守，`wide` 更激进。

示例：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --instrument-substitution-profile wide
```

---

### `--style-repair`

开启风格修补。

用于修复局部跳音、断裂、突兀旋律。

---

### `--style-repair-jump`

控制多大的跳音会触发修补。

数值越小，越容易触发。

示例：

```bash
--style-repair-jump 16
```

---

### `--style-repair-strength`

控制修补强度。

示例：

```bash
--style-repair-strength 0.8
```

数值越大，修补越积极。

---

### `--phrase-repair`

开启邻音陪跑修补。

用于处理“前一个音没改，后一个音被折叠，结果两个音放一起很突兀”的情况。

---

### `--phrase-repair-radius`

控制邻音陪跑范围。

```text
1 = 只看前后 1 个音
2 = 默认推荐
3 = 更像整句修补，但可能更改味
```

---

### `--phrase-repair-jump`

控制触发邻音陪跑的跳跃阈值。

推荐：

```bash
--phrase-repair-jump 9
```

如果突兀点很多，可以尝试：

```bash
--phrase-repair-jump 7
```

---

### `--phrase-repair-move-clean-penalty`

控制程序有多不愿意移动原本没有越界的音。

推荐：

```bash
--phrase-repair-move-clean-penalty 3.2
```

更保守：

```bash
--phrase-repair-move-clean-penalty 4.5
```

更积极：

```bash
--phrase-repair-move-clean-penalty 2.2
```

---

### `--mega-chord`

开启超级和弦增强。

用于模拟更厚的声音。

---

### `--mega-chord-width`

控制超级和弦扩展宽度。

推荐：

```bash
--mega-chord-width 1
```

不建议太大。

---

### `--mega-chord-max-added-per-tick`

控制每个 tick 最多添加多少个辅助音。

推荐：

```bash
--mega-chord-max-added-per-tick 6
```

---

### `--mega-chord-velocity`

控制新增辅助音的音量。

推荐：

```bash
--mega-chord-velocity 0.30
```

---

## 推荐调参顺序

建议不要一上来开满所有功能。

推荐顺序：

```text
1. 先跑默认转换
2. 加 --instrument-substitution
3. 再加 --style-repair
4. 再加 --phrase-repair
5. 最后尝试 --mega-chord
```

比较稳的流程：

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

## 多音轨歌曲建议

如果一个 NBS 有很多音轨，例如 10 个、14 个甚至更多，不建议一开始就使用太激进的参数。

推荐：

```bash
python nbs_minecraft_range_converter_experimental_v4_batch.py input.nbs output.nbs \
  --instrument-substitution \
  --style-repair \
  --phrase-repair \
  --phrase-repair-radius 2 \
  --phrase-repair-jump 9
```

如果歌曲变得太厚、太糊，少开或不开：

```bash
--mega-chord
```

如果听起来还有突兀跳音，可以尝试：

```bash
--phrase-repair-jump 7
```

如果感觉被修过头，可以尝试：

```bash
--phrase-repair-move-clean-penalty 4.5
```

---

## 常见问题

### 为什么不用简单四舍五入？

因为四舍五入会直接改变音名，容易跑调。

例如原本是 C，四舍五入后可能变成 C# 或 D。
这会比八度折叠更容易破坏旋律。

---

### 为什么不用硬夹到 0 或 24？

因为硬夹会把一串越界旋律全部压成同一个边界音。

结果可能变成：

```text
F# F# F# F# F#
```

听起来会非常僵硬。

---

### 为什么默认不激进重排和弦？

因为多音轨 NBS 中，很多和弦和声部关系是原曲结构的一部分。

如果程序为了局部协和度强行重排，同一 tick 的音可能会变得更“数学正确”，但音乐上反而更怪。

所以本项目默认采用保守策略：

```text
宁可少改，也不要乱改。
```

---

### 超级和弦为什么会让歌变糊？

因为它会增加额外辅助音。

如果原曲本来就有很多音轨，再叠很多辅助音，就可能导致声音过厚、过挤。

建议先用小参数：

```bash
--mega-chord-width 1
--mega-chord-max-added-per-tick 6
--mega-chord-velocity 0.30
```

---

## 文件说明

```text
nbs_minecraft_range_converter_experimental_v4_batch.py
```

主程序，包含：

* 单文件转换
* 批量转换
* 音域修复
* 相似乐器代偿
* 风格修补
* 邻音陪跑
* 超级和弦

本仓库不包含额外自动测试某首歌的脚本。

---

## 免责声明

本工具只能尽量修复 NBS 文件在 Minecraft 音符盒范围内播放的问题。

由于 Minecraft 音符盒音域有限，某些复杂 MIDI / NBS 歌曲无法做到完全还原。
尤其是钢琴曲、大量和弦、多音轨、高低跨度极大的歌曲，转换后一定会有一定取舍。

这个工具的目标不是“完美还原”，而是：

```text
尽量不跑调
尽量不突兀
尽量保留原曲味道
尽量适合 Minecraft 播放
```