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

<!-- tool-discovery:begin -->
## Tool discovery protocol (added 2026-10-05; additive, does not override anything above)

Before saying something cannot be done, find out what tools you actually have.

1. At the start of any task, list the available tools and MCP servers. In Claude, deferred tools show up by name only and must be loaded with ToolSearch before they can be called. In other agents, use the equivalent tool or MCP listing.
2. The cloud or sandbox shell is a different machine from Peter's Mac. For local files, git, ssh, scp or osascript, use Desktop Commander (or the local device shell). Do not conclude "no access" from the sandbox.
3. For a problem on a live website, open the live URL in a real browser first. Read the console and network requests and take a screenshot before reading code. Describe what you see, then diagnose.
4. For servers and caches, use the Cloudways tools or `ssh cloudways` as described in RUNBOOK.md (if this repo has one).
5. Report "cannot do it" only after naming the specific tools you tried and the exact error each returned. If an action is denied by a permission or safety check, report it and stop. Do not retry it by another route.
6. Repo rules still apply: GitHub is the source of truth, `peter-knowledge-base.json` never goes into any repo, and nothing in a repo may contain secrets.
<!-- tool-discovery:end -->
