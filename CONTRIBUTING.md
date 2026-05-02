# Contributing

Thanks for your interest. This is a small utility, so the bar for contributions is "does it work, is the diff focused, and does it not regress the happy path."

## Before you start

- For anything non-trivial (new flags, refactors, new file types, concurrency, etc.), open an issue or a draft PR first so we can agree on the shape before you write the code.
- For typo / docs / small bug fixes, just send the PR.
- Check the [Open issues / wanted contributions](README.md#open-issues--wanted-contributions) list in the README — those are the things I'd most like help with.

## Dev setup

```bash
git clone https://github.com/<your-fork>/Adobe-Clawback.git
cd adobe-clawback
./setup.sh
source .venv/bin/activate
```

You'll need an Adobe account with at least one PDF in Cloud Documents to test against.

## Style / conventions

- **Python 3.10+.** PEP 604 (`str | None`) and PEP 585 (`list[str]`) are fine; we already use them.
- **Stdlib first.** The whole point of one-dependency design is keeping it that way. If you genuinely need a new package, justify it in the PR description.
- **Format with `ruff format` or `black`.** Either is fine; both produce nearly identical output for this codebase. No CI enforces it yet.
- **Keep functions small and the call graph readable.** `main_async` is the orchestrator; `AdobeApi` is the API client; helpers are at module level.
- **Don't print secrets.** Bearer tokens, URNs, full Adobe paths in error messages — use redaction or shortened forms.

## Testing

There's no automated test suite yet. For now:

- Run `python adobe_pdf_downloader.py --list` and confirm the file count matches what you see in `https://www.adobe.com/files/cloud-documents`.
- Run a full download against a small test account if you have one.
- Run `--reconcile` after deleting a few local files; confirm their manifest status flips to `missing_locally`.
- For changes touching network/retry logic, force conditions where possible (slow throttle a request with a proxy, kill the venv mid-download, corrupt the manifest, etc.) and confirm the script recovers cleanly.

A real test suite is a top-priority wanted contribution — see the README.

## Pull requests

- One concern per PR.
- Description should answer: what changed, why, and how you tested it.
- If you added or changed behaviour, update the relevant section of the README in the same PR.
- Don't commit `manifest.json`, `downloads/`, or anything else in `.gitignore`. (If your PR adds new generated files, put them in `.gitignore` too.)

## Code of conduct

Be reasonable. Disagree about technical choices freely; don't make it personal. If something feels off, open an issue and tag the maintainer rather than escalating in PR comments.
