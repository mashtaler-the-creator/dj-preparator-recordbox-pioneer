# cdjprep

Готовит сырую музыкальную библиотеку к импорту в rekordbox и игре с USB на Pioneer
CDJ-900 (оригинал 2010): всё неиграбельное конвертирует в AIFF 16/44.1 (с дизерингом),
играбельное копирует бит-в-бит, чистит теги (ID3v2.3, транслитерация кириллицы — экран
CDJ-900 показывает только A–Z/0–9), пишет BPM/key/громкость в теги, ловит битые файлы,
находит дубликаты (никогда не удаляет). Все решения обоснованы в [RESEARCH.md](RESEARCH.md)
(D1–D17, с источниками). Пайплайн: `сырьё → cdjprep → выходная папка → rekordbox → USB`.
Pioneer-базу не пишет, флешку не трогает — это делает rekordbox.

## Установка (свежий мак, Apple Silicon)

```bash
git clone <repo-url> ~/cdj-prep && cd ~/cdj-prep && ./install.sh
```

Нужен только [Homebrew](https://brew.sh). Скрипт ставит ffmpeg/aubio/chromaprint/libkeyfinder,
создаёт venv (mutagen, rumps) и собирает keyfinder-cli из vendor/.

## Использование

Приложение в menu bar (🎧):

```bash
nohup .venv/bin/python cdjprep_app.py >/dev/null 2>&1 &
```

В меню: входная/выходная папка, «Обработать библиотеку» (прогресс в баре),
«Показать план» (dry-run, ничего не пишет), отчёт, автозапуск при входе.

Консольно: `./cdjprep --dry-run` (план), `./cdjprep` (обработка), `./cdjprep --force`
(переобработать всё). Повторный запуск пропускает уже сделанное. Источники не изменяются
никогда; `*.part` пропускаются молча.

Настройки — [config.toml](config.toml). Тесты: `cd tests && ../.venv/bin/python make_traps.py && ../.venv/bin/python verify.py` (51 проверка).

## После обработки — в rekordbox

1. Preferences → Analysis: режим **Normal**, BPM Range — верхний диапазон (~98–195).
2. Импортируй выходную папку, проверь сетки на ломаных треках, включай Analysis Lock.
3. Флешка: FAT32, MBR, один раздел. Экспорт — только через rekordbox.
