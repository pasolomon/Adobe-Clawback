# Adobe Clawback

Bulk-download every PDF in your **Adobe Creative Cloud** account to a local folder, with a manifest that tracks what you've already pulled so re-runs only fetch new or changed files.

Adobe's web UI has no "download everything" button. If you've accumulated hundreds of PDFs in Cloud Documents over the years and want a local copy — for backup, migration off Creative Cloud, or just because — clicking each file by hand isn't reasonable. This script signs in once via a browser window, then talks directly to the same storage API the Creative Cloud Home web app uses.

> **One developer's account, ~876 PDFs, ran end-to-end.** Resumes cleanly, skips files that haven't changed since the last run, and reconciles local deletions.

## Status

Working but rough. It does what I needed and it's been pushed up here in the hope that it's useful to someone else. **Contributions welcome** — see [Open issues / wanted contributions](#open-issues--wanted-contributions) below.

## How it works

1. **Playwright** launches a real Chromium window using a persistent profile in `~/.adobe_pdf_downloader/chrome_profile/`. First run, you sign in to Adobe in that window. Subsequent runs reuse the saved session — no re-login unless Adobe expires it.
2. The script captures your IMS bearer token directly from `window.adobeIMS.getAccessToken()` in the page context.
3. It auto-detects your account's **root URN** (`urn:aaid:sc:US:...`) by listening for the first `/links?assetId=...` request the SPA fires after sign-in. The URN is then cached in `manifest.json` so future runs don't need to detect it again.
4. It hits the storage discovery endpoint:
   ```
   GET <regional-host>/content/storage/id/<root_urn>/:page?type=application/pdf&limit=500
   ```
   This single paginated walk returns every PDF in your entire Cloud Documents tree (recursive, not just the root folder).
5. For each PDF, it downloads the bytes via `<regional-host>/content/storage/id/<assetId>`. Large files that exceed the direct-asset response limit fall back to a `block_download` descriptor → signed blobstore URL.
6. Downloads stream through `urllib.request` on a worker thread (atomic `.part` → final rename), so big files don't buffer through Playwright's IPC channel.
7. Every successful file gets recorded in `manifest.json` with `sha256`, sizes, modification time, etag, local path, and a status field (`downloaded` / `failed` / `missing_locally` / `deleted_remotely`).

### About the `x-api-key` header

The script sends `x-api-key: CCHomeWeb1` — this is the **public client identifier** that Adobe's own Creative Cloud Home web app sends from every user's browser. It's not a credential and not a secret; you can see the same value in your browser's Network panel any time you visit `adobe.com/files/cloud-documents`. Per [Adobe's own developer docs](https://developer.adobe.com/developer-console/docs/guides/authentication/APIKeyAuthentication/), an API key only identifies the calling application and cannot authenticate a user. Your actual authentication is the IMS bearer token captured from your signed-in session.

## Requirements

- **Python 3.10 or newer** (the code uses PEP 604 union syntax: `str | None`).
- **macOS, Linux, or Windows.** Developed on macOS; Linux should be fine; Windows is untested — see [Open issues](#open-issues--wanted-contributions).
- **~150 MB of disk** for the Playwright Chromium build, plus however much your PDFs total.
- An **active Adobe Creative Cloud account** with PDFs you want to back up.

## Install

```bash
git clone https://github.com/pasolomon/Adobe-Clawback.git
cd adobe-clawback
./setup.sh
```

`setup.sh` creates `.venv/`, installs `playwright` from `requirements.txt`, and downloads the Chromium browser binary.

## Usage

Always activate the venv first:

```bash
cd adobe-clawback
source .venv/bin/activate
```

Then:

```bash
# Download everything (resumes / catches up on subsequent runs)
python adobe_pdf_downloader.py

# Just list what's in your account, no downloads
python adobe_pdf_downloader.py --list

# Reconcile manifest against disk (no Chrome, no network)
# Useful if you deleted some files locally and want the manifest to reflect that.
python adobe_pdf_downloader.py --reconcile

# Manually override the root URN (rarely needed; only if auto-detection fails)
python adobe_pdf_downloader.py --root urn:aaid:sc:US:00000000-0000-0000-0000-000000000000
```

### First run

A Chromium window opens pointing at `https://www.adobe.com/files/cloud-documents`. Sign in with your Adobe credentials in that window. The script watches in the background and proceeds automatically once it detects:
- `window.adobeIMS.isSignedInUser()` returns true, **and**
- It has captured your root URN from a `/links?assetId=urn:aaid:sc:US:...` request.

Default sign-in timeout is 600 seconds (10 minutes). After sign-in, downloads begin and the window stays open until the run finishes.

### Subsequent runs

The persistent Chromium profile keeps you signed in, so the window appears, immediately registers you as authenticated, and discovery + download starts within a second or two. If Adobe has expired your session you'll be prompted to sign in again.

## Output layout

```
adobe-clawback/
├── downloads/                    # all your PDFs land here
│   ├── Some Document.pdf
│   ├── Another File.pdf
│   └── ...
├── manifest.json                 # state file (do not commit; in .gitignore)
├── adobe_pdf_downloader.py
├── setup.sh
├── requirements.txt
├── README.md
├── LICENSE
└── .gitignore
```

Filenames are sanitized (`/`, `\`, `:`, `<`, `>`, `"`, `|`, `?`, `*` → `_`) and capped at 200 characters. Collisions get ` (1)`, ` (2)`, etc. appended.

## Manifest schema

`manifest.json` is the source of truth for what's been downloaded. Top-level shape:

```json
{
  "version": 3,
  "created_at": "2025-...",
  "root_urn": "urn:aaid:sc:US:...",
  "regional_host": "https://platform-cs-edge-va6.adobe.io",
  "files": {
    "urn:aaid:sc:US:<asset-id>": {
      "id": "urn:aaid:sc:US:<asset-id>",
      "name": "Some Document.pdf",
      "adobe_path": "/Folder/Subfolder/Some Document.pdf",
      "size_remote": 123456,
      "size_local": 123456,
      "modified": "2024-...",
      "etag": "...",
      "local_path": "downloads/Some Document.pdf",
      "sha256": "abc123...",
      "downloaded_at": "2025-...",
      "last_seen_remote": "2025-...",
      "status": "downloaded"
    }
  },
  "runs": [ { "started_at": "...", "ended_at": "...", "mode": "...", "discovered": 0, "downloaded": 0, "skipped": 0, "failed": [] } ],
  "last_run": { "...": "..." }
}
```

`status` values:

| Status               | Meaning                                                                       |
|----------------------|-------------------------------------------------------------------------------|
| `downloaded`         | File is on disk and matches the remote.                                       |
| `failed`             | Last attempt errored out. `last_error` field has details.                     |
| `missing_locally`    | Manifest says it was downloaded, but the file is no longer on disk.           |
| `deleted_remotely`   | File no longer appears in the Adobe listing on the most recent run.           |

If `manifest.json` ever gets corrupted (interrupted write, etc.), it's automatically backed up to `manifest.corrupt.<timestamp>.json` and a fresh one is started — your downloads are not deleted, but they'll be re-hashed on the next run.

## Re-run behaviour

A file is **skipped** when all of these are true:
- It exists in the manifest with `status == "downloaded"`,
- The file at `local_path` still exists on disk,
- The remote `modified` timestamp matches the manifest entry's `modified`.

Otherwise it gets re-downloaded. So:
- New files in Adobe → downloaded.
- File modified in Adobe (different `modified` timestamp) → re-downloaded, overwriting the local copy.
- File deleted from disk → re-downloaded (unless you also `--reconcile`, in which case the manifest is updated to `missing_locally` first).
- File deleted from Adobe → kept locally, manifest entry flips to `deleted_remotely`.

## Troubleshooting

**"Couldn't auto-detect root URN"**
The script tries three strategies: listening for a `/links?assetId=...` request, parsing it from a `/files/id/<urn>` URL, and using the cached value in the manifest. If all three fail, navigate into any folder in the open Chromium window — the URL will contain the URN — or run with `--root urn:aaid:sc:US:...` once.

**Sign-in window timed out**
Default is 10 minutes. If you need longer, edit `SIGN_IN_TIMEOUT_S` near the top of the script.

**`401` after a long-running download**
The script catches 401s and refreshes the IMS token from `window.adobeIMS.getAccessToken()` automatically. If it still fails, your session has expired entirely — close the script (Ctrl+C) and re-run; you'll be prompted to sign in again.

**`responsetoolarge` for a particular file**
Handled automatically: the script falls back to `:block_download` to get a signed blobstore URL and streams from there.

**`429` / 5xx**
Exponential backoff with `Retry-After` honored where present. If you're seeing sustained throttling, slow your re-runs down.

## Open issues / wanted contributions

In rough priority order:

1. **Windows + Linux testing.** Developed on macOS only.
2. **Headless mode after first sign-in.** Currently always runs headful. Once a session is cached, there's no reason the browser needs to be visible.
3. **Concurrent downloads.** Sequential is simple but slow for many small files. Cap at ~4 concurrent to stay polite.
4. **Progress bar.** `rich` or `tqdm` would be a big UX upgrade, currently it just prints lines.
5. **More file types.** Code is hard-coded to `type=application/pdf`. The same endpoint will serve `image/*`, `application/illustrator`, `application/photoshop`, etc. — a `--type` flag (or "everything") would make this much more useful.
6. **Resume partial downloads.** `.part` files are deleted on error; HTTP `Range` requests + checksums could resume.
7. **Tests.** None currently. Mocking Adobe's API is non-trivial but valuable.
8. **Better edge-case handling for non-US accounts / non-`platform-cs-edge-va6` regions.** Discovery should work via the `/links` rel walk, but only `va6` has been observed in practice.
9. **Docker / single-binary build** for users who don't want to install Python.

If you're picking one up, an issue or draft PR before you start saves us both time.

## Privacy / data handling

- The script runs entirely on your machine.
- Your Adobe credentials are entered in the Chromium window — they go to Adobe, not to this code.
- The IMS bearer token lives in memory during a run and is never written to disk by this script. (Playwright's persistent profile stores Adobe's session cookies in `~/.adobe_pdf_downloader/chrome_profile/`, same as any browser would.)
- `manifest.json` contains your account's root URN, regional host, and full list of file paths/names. **Do not commit it to a public repo.** The supplied `.gitignore` excludes it.

## Disclaimer

This tool uses your own credentials to download your own files via the same endpoints Adobe's web app uses. It does not bypass authentication, scrape other users' content, or violate any access controls. That said:

- It is not affiliated with or endorsed by Adobe Inc.
- Adobe could change their API at any time and break it.
- Your account's terms of service apply. Use within them.
- No warranty — see [LICENSE](LICENSE).

## License

[MIT](LICENSE) — see file for full text.
