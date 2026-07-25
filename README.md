# cdjprep

**EN**: Prepares a raw music library for rekordbox import and USB playback on the Pioneer
CDJ-900 (original, 2010). Pipeline: `raw files → cdjprep → staging folder → rekordbox → USB`.
It never writes a Pioneer database and never touches the USB stick — that's rekordbox's job.
Every decision is research-backed and traceable to [RESEARCH.md](RESEARCH.md) (D1–D17, with sources).

**RU**: Готовит сырую музыкальную библиотеку к импорту в rekordbox и игре с USB на Pioneer
CDJ-900 (оригинал, 2010). Пайплайн: `сырьё → cdjprep → выходная папка → rekordbox → USB`.
Pioneer-базу не пишет, флешку не трогает — это делает rekordbox. Каждое решение обосновано
в [RESEARCH.md](RESEARCH.md) (D1–D17, с источниками).

---

## Features (EN)

### Audit & safety
- Probes every file (codec, sample rate, bit depth, bitrate, channels, duration, tags) and
  **fully decodes it** — truncated and corrupt files are caught and rejected, never reaching the output.
- Torrent leftovers (`*.part`) are skipped silently. Source files are **never modified or deleted**.
- Idempotent: a second run skips everything already done (manifest keyed by size/mtime/config);
  `--force` reprocesses. `--dry-run` prints the full plan and writes nothing.
- Parallel across all CPU cores with live progress output.

### Format handling (per official CDJ-900 spec)
- Playable as-is → **copied bit-exact** (no generation loss): MP3 (MPEG-1/2 L3 in-spec),
  AAC-LC, WAV/AIFF 16/24-bit PCM at 44.1/48 kHz with plain headers.
- Everything else → **AIFF 16-bit/44.1 kHz** (24-bit target configurable): FLAC, ALAC,
  Ogg/Opus/WMA, hi-res rates, 32-bit float WAV, AIFF-C, MPEG-2.5 MP3.
- Detects the traps the CDJ rejects with E-8304/E-8305 even when ffprobe hides them:
  **WAVE_FORMAT_EXTENSIBLE headers** (raw fmt-chunk byte parsing — ffprobe resolves the GUID
  and lies), AIFF-C containers, DRM-protected AAC.
- Resampling via swr with enlarged filter (filter_size 256); **TPDF-HP dither**
  (`triangular_hp`, shibata available) applied only when bit depth actually decreases.
- Conversion verified in tests: 16→16 conversions are PCM-bit-identical; dither noise ≤ 2 LSB.

### Tags & display
- Normalizes tags to **ID3v2.3** on MP3 and AIFF (best common denominator for rekordbox /
  Serato / Windows), MP4 atoms for M4A; carries tags across conversion from FLAC/ALAC/etc.
- Missing tags recovered from `Artist - Album (Year)/NN. Track` folder structure;
  empty critical fields (artist/title) are flagged.
- **Cyrillic transliterated to Latin** in tags and filenames — the CDJ-900 screen renders only
  A–Z/0–9 and shows `?` for everything else. Originals preserved in `TXXX ORIG TITLE/ARTIST/ALBUM`.
- Embedded artwork re-encoded to **JPEG ≤ 800×800** (Pioneer display limit) on conversion.

### Analysis (written into tags, audio never altered)
- **BPM** via aubio with octave-error correction (85 → 170 for a 160–180 BPM library) → `TBPM`.
- **Musical key** via libkeyfinder (the Mixxx-maintained, EDM-oriented algorithm) → `TKEY`
  (rekordbox reads TKEY; note: rekordbox ignores TBPM and always re-analyzes BPM itself).
- **Loudness** (LUFS-I, LRA) and **true peak** (BS.1770 4× oversampled) → `TXXX` tags + report.
- Warn-only checks, per research consensus (nothing is "fixed" destructively): clipping
  (> 0 dBTP), hot masters (above −1 dBTP), DC offset, leading silence > 2 s, mono files.

### Duplicates (report-only, never deletes)
- Layer 1: exact decoded-PCM hash — certain duplicates across containers/tag edits.
- Layer 2: Chromaprint acoustic fingerprint (catches FLAC-vs-MP3 of the same master;
  7-second duration gate as per AcoustID).
- Layer 3: normalized artist+title match; **VIP/remix/edit tokens veto merging**
  (DnB version families are never called duplicates), "(Original Mix)" treated as neutral.

### Output & reporting
- Configurable filename template (`{track}. {artist} - {title}`; `{bpm}` available when BPM
  analysis is enabled), release-folder or flat layout. Naming-only config changes rename
  existing outputs in place — no re-encoding.
- All paths FAT32-safe ASCII, illegal characters stripped, path length checked against the
  256-char rekordbox/PRO DJ LINK limit, nesting kept far below the CDJ's 8-level cap.
- `report.json` (machine-readable, fate of every source file) + human summary: counts,
  rejects with reasons, warnings, duplicate groups, output size vs a target USB volume.

### Menu-bar app (🎧)
- Pick input/output folders (saved to config.toml), run with **live progress in the menu bar**,
  completion notification with summary.
- Dry-run plan opens as text; quick access to the output folder and last report.
- **Launch at login** toggle (LaunchAgent).

### Project
- Single TOML config ([config.toml](config.toml)); console launcher `./cdjprep`.
- 51-check test harness (`tests/`): synthetic trap files for every researched failure mode,
  numeric audio-integrity proofs, idempotency and FAT32-safety checks.
- `install.sh` sets up a fresh Mac in one command; keyfinder-cli built from vendored
  source (GPLv3, license included).

---

## Фичи (RU)

### Аудит и безопасность
- Каждый файл прощупывается (кодек, частота, битность, битрейт, каналы, длительность, теги)
  и **полностью декодируется** — обрезанные и битые файлы отлавливаются и в выход не попадают.
- Хвосты торрентов (`*.part`) пропускаются молча. Исходники **никогда не изменяются и не удаляются**.
- Идемпотентность: повторный запуск пропускает уже сделанное (манифест по размеру/mtime/конфигу);
  `--force` — переобработать. `--dry-run` печатает полный план и ничего не пишет.
- Параллельная обработка на всех ядрах с живым прогрессом.

### Форматы (по официальному спеку CDJ-900)
- Играбельное → **копия бит-в-бит** (без потери поколения): MP3 (MPEG-1/2 L3 в спеке),
  AAC-LC, WAV/AIFF 16/24 бит PCM на 44.1/48 кГц с обычными заголовками.
- Всё остальное → **AIFF 16 бит / 44.1 кГц** (24 бита — опция в конфиге): FLAC, ALAC,
  Ogg/Opus/WMA, hi-res частоты, 32-битный float WAV, AIFF-C, MPEG-2.5 MP3.
- Ловит ловушки, на которых CDJ падает с E-8304/E-8305, даже когда ffprobe их прячет:
  **заголовки WAVE_FORMAT_EXTENSIBLE** (разбор байтов fmt-чанка — ffprobe резолвит GUID
  и «врёт»), контейнеры AIFF-C, AAC с DRM.
- Ресемплинг swr с увеличенным фильтром (filter_size 256); **TPDF-HP дизеринг**
  (`triangular_hp`, есть shibata) — только когда битность реально понижается.
- Конверсия проверена тестами: 16→16 даёт бит-идентичный PCM; шум дизеринга ≤ 2 LSB.

### Теги и экран
- Теги нормализуются в **ID3v2.3** на MP3 и AIFF (общий знаменатель rekordbox / Serato /
  Windows), MP4-атомы для M4A; при конверсии теги переносятся из FLAC/ALAC и т.д.
- Отсутствующие теги восстанавливаются из структуры `Artist - Album (Year)/NN. Track`;
  пустые критичные поля (артист/название) помечаются.
- **Кириллица транслитерируется в латиницу** в тегах и именах файлов — экран CDJ-900 умеет
  только A–Z/0–9, всё остальное показывает как `?`. Оригиналы сохраняются в
  `TXXX ORIG TITLE/ARTIST/ALBUM`.
- Обложки при конверсии пережимаются в **JPEG ≤ 800×800** (лимит дисплеев Pioneer).

### Анализ (пишется в теги, звук не трогается)
- **BPM** через aubio с коррекцией октавной ошибки (85 → 170 для библиотеки 160–180) → `TBPM`.
- **Тональность** через libkeyfinder (алгоритм для электронной музыки, поддерживается
  командой Mixxx) → `TKEY` (rekordbox читает TKEY; TBPM rekordbox игнорирует и всегда
  анализирует BPM сам).
- **Громкость** (LUFS-I, LRA) и **true peak** (BS.1770, 4× оверсемплинг) → `TXXX`-теги + отчёт.
- Только предупреждения, без «лечения» (по консенсусу из рисёрча): клиппинг (> 0 dBTP),
  горячие мастера (выше −1 dBTP), DC offset, тишина в начале > 2 с, моно-файлы.

### Дубликаты (только отчёт, никогда не удаляет)
- Слой 1: точный хеш декодированного PCM — стопроцентные дубли сквозь контейнеры и правки тегов.
- Слой 2: акустический отпечаток Chromaprint (ловит FLAC-vs-MP3 одного мастера;
  допуск по длительности 7 с, как у AcoustID).
- Слой 3: совпадение нормализованных артист+название; **токены VIP/remix/edit блокируют
  слияние** (семьи версий в DnB никогда не объявляются дублями), «(Original Mix)» нейтрален.

### Выход и отчётность
- Настраиваемый шаблон имён (`{track}. {artist} - {title}`; поле `{bpm}` доступно, если включён
  BPM-анализ), раскладка по папкам релизов или плоская. Смена только именования переименовывает
  готовые файлы на месте — без переконвертации.
- Все пути — ASCII, безопасные для FAT32, нелегальные символы вырезаны, длина пути проверяется
  против лимита 256 символов rekordbox/PRO DJ LINK, вложенность сильно ниже лимита CDJ в 8 уровней.
- `report.json` (машиночитаемый, судьба каждого исходника) + человеческая сводка: счётчики,
  отказы с причинами, предупреждения, группы дублей, размер выхода против целевой флешки.

### Приложение в menu bar (🎧)
- Выбор входной/выходной папки (сохраняется в config.toml), запуск с **живым прогрессом
  в баре**, уведомление со сводкой по окончании.
- План dry-run открывается текстом; быстрый доступ к выходной папке и последнему отчёту.
- Галка **автозапуска при входе** (LaunchAgent).

### Проект
- Один TOML-конфиг ([config.toml](config.toml)); консольный запуск `./cdjprep`.
- Тестовый стенд на 51 проверку (`tests/`): синтетические файлы-ловушки на каждый найденный
  в рисёрче отказ, численные доказательства целостности звука, идемпотентность, FAT32-безопасность.
- `install.sh` разворачивает всё на чистом маке одной командой; keyfinder-cli собирается
  из вендоренного исходника (GPLv3, лицензия приложена).

---

## Install / Установка (Apple Silicon)

```bash
git clone https://github.com/mashtaler-the-creator/dj-preparator-recordbox-pioneer.git ~/cdj-prep \
  && cd ~/cdj-prep && ./install.sh
```

Requires only [Homebrew](https://brew.sh) (Xcode Command Line Tools come with it). /
Нужен только [Homebrew](https://brew.sh) (Xcode Command Line Tools ставятся вместе с ним).

## Run / Запуск

```bash
# menu-bar app / приложение в menu bar
nohup .venv/bin/python cdjprep_app.py >/dev/null 2>&1 &

# console / консоль
./cdjprep --dry-run   # plan only / только план
./cdjprep             # process / обработка
```

Report / отчёт: `<output folder>/.cdjprep/report.json` (also via the 🎧 menu / доступен из меню 🎧).

## After processing — rekordbox / После обработки — rekordbox

1. Preferences → Analysis: mode **Normal**, BPM Range — top option (~98–195) /
   режим **Normal**, BPM Range — верхний диапазон (~98–195).
2. Import the staging folder, spot-check beatgrids on breaks, use Analysis Lock /
   импортируй выходную папку, проверь сетки на ломаных треках, включай Analysis Lock.
3. USB stick: FAT32, MBR, single partition; export via rekordbox only /
   флешка: FAT32, MBR, один раздел; экспорт — только через rekordbox.
