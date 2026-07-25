#!/bin/bash
# cdjprep installer for a fresh Mac (Apple Silicon, Homebrew required).
# Sets up: brew audio tools, Python venv, keyfinder-cli build.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v brew >/dev/null; then
    echo "Сначала поставь Homebrew: https://brew.sh" >&2
    exit 1
fi

echo "==> brew: ffmpeg, aubio, chromaprint, libkeyfinder"
brew install -q ffmpeg aubio chromaprint libkeyfinder

echo "==> Python venv + зависимости (mutagen, rumps)"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet mutagen rumps

echo "==> Сборка keyfinder-cli (vendor/keyfinder-cli, GPLv3)"
mkdir -p bin
clang++ -O2 -std=c++17 vendor/keyfinder-cli/keyfinder_cli.cpp -o bin/keyfinder-cli \
    -I/opt/homebrew/include -L/opt/homebrew/lib \
    -lkeyfinder -lavcodec -lavformat -lavutil -lswresample

echo "==> Проверка"
./bin/keyfinder-cli 2>&1 | head -1 || true
.venv/bin/python -c "import mutagen, rumps; print('python deps OK')"

cat <<'EOF'

Готово. Дальше:
  запустить приложение в menu bar:   nohup .venv/bin/python cdjprep_app.py >/dev/null 2>&1 &
  или консольно:                     ./cdjprep --dry-run   и   ./cdjprep
Входную/выходную папку задай в меню приложения (или в config.toml).
Автозапуск при входе включается галкой в меню приложения.
EOF
