# CLAUDE.md — Adobe-Clawback

Instructions for Claude Code and any other coding agent working in this repository.

## What this is
A Python script that bulk-downloads every PDF in an Adobe Creative Cloud account to a local folder. A manifest records what has been pulled, so re-runs fetch only new or changed files.

## Read at session start
- `README.md`, sections "How it works" and "Requirements".

## Read when the task needs it
- `CONTRIBUTING.md` before opening a pull request.
- The rest of `README.md` (status, open issues) when changing behavior.

## Commands
```bash
./setup.sh                                   # one-time: virtual environment, dependencies, Chromium
source .venv/bin/activate
python adobe_pdf_downloader.py --list        # discover only, no downloads
python adobe_pdf_downloader.py               # download all PDFs
python adobe_pdf_downloader.py --reconcile   # disk-only check, no browser
```

## Rules
- GitHub `main` is the source of truth. Fetch and fast-forward before working. Never force-push or rewrite history.
- This repository is public. Never commit secrets, credentials, tokens, or personal data.
- Commit only what you changed.
- Python 3.10 or newer; the code uses `str | None` union syntax.
- Never commit the browser profile, bearer tokens, account identifiers, the run manifest, or downloaded files.
- The `x-api-key` value in the script is a public client identifier, not a secret. Read the README section on it before changing it.

## Before you finish
- There is no test suite. Say plainly what you ran and what you could not verify.
- Commit and push, then confirm the local branch matches `origin/main`.
