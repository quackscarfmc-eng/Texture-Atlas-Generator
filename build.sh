#!/usr/bin/env bash
# Build a single-file standalone executable for AntiX OS.
set -e

echo "==> Installing build/runtime deps (needs sudo)…"
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-pil python3-tk \
    python3-dev build-essential \
    libjpeg-dev zlib1g-dev libpng-dev \
    libtk-img tk-dev tcl-dev \
    libglib2.0-dev libffi-dev

echo "==> Installing Python wheels…"
pip install pillow tkinterdnd2 pyinstaller --break-system-packages

echo "==> Cleaning previous build…"
rm -rf build dist

echo "==> Building one-file executable…"
python3 -m PyInstaller \
    --onefile \
    --strip \
    --name atlas-packer \
    --collect-all tkinterdnd2 \
    atlas_packer.py

echo
echo "Done. Standalone binary: ./dist/atlas-packer"
echo "Run it with: ./dist/atlas-packer"
