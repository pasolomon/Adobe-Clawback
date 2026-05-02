#!/usr/bin/env bash
# One-time setup for adobe_pdf_downloader.py
# Creates a local Python venv and installs Playwright + the Chromium browser.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium

echo
echo "Setup complete. To run:"
echo "    source .venv/bin/activate"
echo "    python adobe_pdf_downloader.py --list      # discover only, no downloads"
echo "    python adobe_pdf_downloader.py             # download all PDFs"
echo "    python adobe_pdf_downloader.py --reconcile # disk-only check, no Chrome"
