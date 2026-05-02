#!/usr/bin/env python3
"""Adobe PDF Downloader (API-based).

Walks Adobe's Creative Cloud cloud-documents account, downloads every PDF
into ./downloads, and maintains ./manifest.json so you can later compare
what was downloaded vs. deleted locally vs. deleted on Adobe.

First run: a Chrome window opens. Sign in to Adobe in that window. The
session is saved in a persistent profile under
~/.adobe_pdf_downloader/ and reused on subsequent runs.

Discovery uses Adobe's storage API:
    GET /content/storage/id/<root_urn>/:page?type=application/pdf
which returns every PDF in the entire cloud-documents tree (paginated).
The root URN is auto-detected by listening for the page's initial /links
request, then cached in the manifest.

Usage:
    python adobe_pdf_downloader.py --list      # discover only, no downloads
    python adobe_pdf_downloader.py             # discover + download
    python adobe_pdf_downloader.py --reconcile # update manifest from disk only
    python adobe_pdf_downloader.py --root <urn> # override root URN
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

try:
    from playwright.async_api import (
        async_playwright,
        BrowserContext,
        Page,
        APIRequestContext,
        Request,
        TimeoutError as PWTimeout,
    )
except ImportError:
    sys.stderr.write(
        "Missing dependency: playwright\n"
        "Run ./setup.sh, then `source .venv/bin/activate`.\n"
    )
    sys.exit(1)


HERE = Path(__file__).parent.resolve()
DOWNLOAD_DIR = HERE / "downloads"
MANIFEST_PATH = HERE / "manifest.json"

PROFILE_DIR = Path.home() / ".adobe_pdf_downloader" / "chrome_profile"

CC_FILES_URL = "https://www.adobe.com/files/cloud-documents"
URN_RE = re.compile(r"urn:aaid:sc:[A-Z]+:[a-f0-9-]{36}")
LINKS_PATH = "platform-cs-edge.adobe.io/links"
SIGN_IN_TIMEOUT_S = 600
DISCOVERY_TIMEOUT_S = 90

API_KEY = "CCHomeWeb1"
LINKS_HOST = "https://platform-cs-edge.adobe.io"
PAGE_LIMIT = 500


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {
            "version": 3,
            "created_at": utcnow(),
            "root_urn": None,
            "regional_host": None,
            "files": {},
            "runs": [],
        }
    try:
        m = json.loads(MANIFEST_PATH.read_text())
    except json.JSONDecodeError:
        ts = int(datetime.now().timestamp())
        backup = MANIFEST_PATH.with_name(f"manifest.corrupt.{ts}.json")
        MANIFEST_PATH.rename(backup)
        print(f"WARN: manifest was corrupt; backed up to {backup.name}; starting fresh.")
        return load_manifest()
    m.setdefault("root_urn", None)
    m.setdefault("regional_host", None)
    return m


def save_manifest(m: dict[str, Any]) -> None:
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(MANIFEST_PATH)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_filename(name: str) -> str:
    name = name.replace("\x00", "")
    name = re.sub(r'[/\\:<>"|?*]', "_", name).strip()
    return name[:200] or "untitled.pdf"


def unique_path(dir_: Path, name: str) -> Path:
    base = safe_filename(name)
    p = dir_ / base
    if not p.exists():
        return p
    stem, ext = os.path.splitext(base)
    i = 1
    while True:
        candidate = dir_ / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate
        i += 1


# --------------------------------------------------------------------------
# Browser + auth
# --------------------------------------------------------------------------

async def launch_browser(playwright_):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return await playwright_.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        accept_downloads=True,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


async def sign_in_and_resolve_root(
    context: BrowserContext, manifest: dict[str, Any], override_root: str | None
) -> tuple[Page, str]:
    """Open cloud-documents, wait for sign-in, return the root URN.

    Strategy:
      1. Listen for /links?assetId=urn:aaid:sc:... requests; the first one
         the SPA fires after sign-in is for the user's root folder.
      2. Failing that, parse the URN out of the URL when the user
         navigates into any folder (URL contains /files/id/<urn>, and
         then we look up its ancestors).
      3. Failing that, use --root or the cached value in the manifest.
    """
    page = context.pages[0] if context.pages else await context.new_page()

    discovered: dict[str, str | None] = {"root": None}

    def on_request(req: Request) -> None:
        if discovered["root"]:
            return
        if LINKS_PATH not in req.url:
            return
        m = re.search(r"assetId=([^&]+)", req.url)
        if not m:
            return
        urn = unquote(m.group(1))
        if URN_RE.fullmatch(urn):
            print(f"  [auto] captured candidate root URN: {urn}")
            discovered["root"] = urn

    context.on("request", on_request)

    print(f"Navigating to {CC_FILES_URL} ...")
    await page.goto(CC_FILES_URL, wait_until="domcontentloaded")
    print("If prompted, sign in to Adobe in the Chrome window.")

    async def wait_for_ims() -> None:
        deadline = asyncio.get_event_loop().time() + SIGN_IN_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            try:
                signed_in = await page.evaluate(
                    "() => !!(window.adobeIMS && window.adobeIMS.isSignedInUser && window.adobeIMS.isSignedInUser())"
                )
                if signed_in:
                    return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise RuntimeError("Timed out waiting for Adobe sign-in (window.adobeIMS).")

    if override_root:
        await wait_for_ims()
        return page, override_root

    deadline = asyncio.get_event_loop().time() + SIGN_IN_TIMEOUT_S
    last_url = ""
    while asyncio.get_event_loop().time() < deadline:
        url = page.url
        if url != last_url:
            print(f"  URL: {url}")
            last_url = url

        # Path 2: URL contains /files/id/<urn> (any sub-folder the user navigated into)
        m = re.search(r"/files/id/(urn:aaid:sc:[^/?#]+)", url)
        if m and not discovered["root"]:
            discovered["root"] = m.group(1)
            print(f"  [auto] captured URN from URL: {discovered['root']}")

        # Path 1 result (auto-captured via request listener)
        if discovered["root"]:
            try:
                signed_in = await page.evaluate(
                    "() => !!(window.adobeIMS && window.adobeIMS.isSignedInUser && window.adobeIMS.isSignedInUser())"
                )
            except Exception:
                signed_in = False
            if signed_in:
                return page, discovered["root"]

        await asyncio.sleep(2)

    # Path 3: cached value
    if manifest.get("root_urn"):
        print(f"  using cached root URN from manifest: {manifest['root_urn']}")
        return page, manifest["root_urn"]
    raise RuntimeError(
        "Couldn't auto-detect root URN. Pass --root urn:aaid:sc:US:... once and we'll cache it."
    )


async def get_token(page: Page) -> str:
    return await page.evaluate(
        """
        async () => {
            if (!window.adobeIMS || !window.adobeIMS.getAccessToken) {
                throw new Error('window.adobeIMS not ready');
            }
            const t = window.adobeIMS.getAccessToken();
            if (!t) throw new Error('no access token');
            return (typeof t === 'object') ? t.token : t;
        }
        """
    )


# --------------------------------------------------------------------------
# Streaming helpers (stdlib urllib so big files don't buffer through Playwright)
# --------------------------------------------------------------------------

class _Unauthorized(Exception):
    pass


class _Throttled(Exception):
    def __init__(self, status: int, retry_after: int | None):
        super().__init__(f"throttled status={status}")
        self.status = status
        self.retry_after = retry_after


class _ResponseTooLarge(Exception):
    pass


def _stream_with_headers(url: str, dest: Path, headers: dict[str, str]) -> int:
    """Run on a worker thread. Stream a URL to ``dest`` atomically."""
    req = urllib.request.Request(url, headers=headers)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            written = 0
            with tmp.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
        tmp.replace(dest)
        return written
    except urllib.error.HTTPError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        if e.code == 401:
            raise _Unauthorized() from e
        if e.code == 429 or 500 <= e.code < 600:
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                ra_int = int(ra) if ra else None
            except ValueError:
                ra_int = None
            raise _Throttled(e.code, ra_int) from e
        if e.code == 400:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if "responsetoolarge" in body.lower():
                raise _ResponseTooLarge() from e
            raise RuntimeError(f"GET {url} -> 400: {body}") from e
        raise RuntimeError(f"GET {url} -> {e.code}: {e.reason}") from e


async def _stream_unauthed(url: str, dest: Path) -> int:
    """Signed blobstore URLs need no auth. Stream on a worker thread."""
    loop = asyncio.get_running_loop()
    for attempt in range(4):
        try:
            return await loop.run_in_executor(None, _stream_with_headers, url, dest, {})
        except _Throttled as e:
            wait = e.retry_after or (2 ** attempt)
            print(f"    blobstore throttled ({e.status}), sleeping {wait}s ...")
            await asyncio.sleep(wait)
            continue
    raise RuntimeError(f"GET {url}: throttle retries exhausted")


# --------------------------------------------------------------------------
# Adobe API
# --------------------------------------------------------------------------

class AdobeApi:
    def __init__(self, request: APIRequestContext, page: Page):
        self.request = request
        self.page = page
        self._token: str | None = None
        self.regional_host: str | None = None

    async def _headers(self, accept: str = "application/json") -> dict[str, str]:
        if self._token is None:
            self._token = await get_token(self.page)
        return {
            "Authorization": f"Bearer {self._token}",
            "x-api-key": API_KEY,
            "Accept": accept,
        }

    async def _refresh_token(self) -> None:
        self._token = await get_token(self.page)

    async def _get(self, url: str, *, json_resp: bool) -> Any:
        last_err = None
        for attempt in range(3):
            r = await self.request.get(url, headers=await self._headers(
                "application/json" if json_resp else "*/*"
            ))
            if r.status == 401:
                await self._refresh_token()
                continue
            if r.status == 429 or 500 <= r.status < 600:
                last_err = f"{r.status}"
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status >= 400:
                body = ""
                try:
                    body = (await r.text())[:300]
                except Exception:
                    pass
                raise RuntimeError(f"GET {url} -> {r.status}: {body}")
            return await r.json() if json_resp else await r.body()
        raise RuntimeError(f"GET {url}: retries exhausted ({last_err})")

    async def discover_regional_host(self, root_urn: str) -> str:
        url = f"{LINKS_HOST}/links?assetId={root_urn}"
        data = await self._get(url, json_resp=True)
        page_link = (data.get("_links") or {}).get(
            "http://ns.adobe.com/adobecloud/rel/page"
        )
        if not page_link:
            raise RuntimeError("links response missing 'page' rel")
        href = page_link[0]["href"] if isinstance(page_link, list) else page_link["href"]
        host = "https://" + urlparse(href).netloc
        self.regional_host = host
        return host

    async def list_pdfs(self, root_urn: str) -> list[dict[str, Any]]:
        """One paginated walk; type=application/pdf returns PDFs from all
        sub-folders, not just direct children."""
        if not self.regional_host:
            raise RuntimeError("regional_host not set")
        url = (
            f"{self.regional_host}/content/storage/id/{root_urn}"
            f"/:page?limit={PAGE_LIMIT}&type=application/pdf"
        )
        results: list[dict[str, Any]] = []
        page_no = 0
        while url:
            page_no += 1
            data = await self._get(url, json_resp=True)
            children = data.get("children") or []
            print(f"  page {page_no}: +{len(children)} (total {len(results) + len(children)})")
            for c in children:
                if c.get("dc:format") != "application/pdf":
                    continue
                results.append({
                    "id": c["repo:assetId"],
                    "name": c.get("repo:name") or "untitled.pdf",
                    "adobe_path": c.get("repo:path") or "",
                    "size": c.get("repo:size"),
                    "modified": c.get("repo:modifyDate"),
                    "etag": c.get("repo:etag"),
                })
            next_link = (data.get("_links") or {}).get("next")
            url = next_link.get("href") if next_link else None
        return results

    async def download_to(self, urn: str, dest: Path) -> int:
        """Stream the asset bytes to ``dest``. Returns bytes written.

        Uses stdlib urllib so the response is streamed straight to disk
        instead of being buffered through Playwright's IPC channel.
        """
        if not self.regional_host:
            raise RuntimeError("regional_host not set")
        # First try the direct asset URL.
        direct_url = f"{self.regional_host}/content/storage/id/{urn}"
        try:
            return await self._stream_authed(direct_url, dest)
        except _ResponseTooLarge:
            pass
        # Fallback: ask for a block-download descriptor (small JSON), then
        # stream the signed blobstore URL.
        bd_url = f"{self.regional_host}/content/storage/id/{urn}/:block_download"
        descriptor = await self._get_json_descriptor(bd_url)
        href = descriptor.get("href")
        if not href:
            raise RuntimeError(f"block_download missing 'href': {descriptor}")
        return await _stream_unauthed(href, dest)

    async def _stream_authed(self, url: str, dest: Path) -> int:
        token = self._token or await get_token(self.page)
        self._token = token
        loop = asyncio.get_running_loop()
        for attempt in range(4):
            try:
                return await loop.run_in_executor(
                    None, _stream_with_headers, url, dest,
                    {
                        "Authorization": f"Bearer {token}",
                        "x-api-key": API_KEY,
                        "Accept": "*/*",
                    },
                )
            except _Unauthorized:
                token = await get_token(self.page)
                self._token = token
                continue
            except _Throttled as e:
                wait = e.retry_after or (2 ** attempt)
                print(f"    throttled (HTTP {e.status}), sleeping {wait}s ...")
                await asyncio.sleep(wait)
                continue
        raise RuntimeError(f"GET {url}: retries exhausted (auth/throttle)")

    async def _get_json_descriptor(self, url: str) -> dict[str, Any]:
        headers = await self._headers("application/vnd.adobecloud.download+json")
        for attempt in range(3):
            r = await self.request.get(url, headers=headers)
            if r.status == 401:
                await self._refresh_token()
                headers = await self._headers("application/vnd.adobecloud.download+json")
                continue
            if r.status == 429 or 500 <= r.status < 600:
                wait = 2 ** attempt
                print(f"    block_download throttled ({r.status}), sleeping {wait}s ...")
                await asyncio.sleep(wait)
                continue
            if r.status >= 400:
                body = ""
                try:
                    body = (await r.text())[:300]
                except Exception:
                    pass
                raise RuntimeError(f"block_download {url} -> {r.status}: {body}")
            return await r.json()
        raise RuntimeError(f"block_download {url}: retries exhausted")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

async def main_async(args) -> dict[str, Any]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    if args.reconcile:
        return reconcile_local(manifest)

    run = {
        "started_at": utcnow(),
        "mode": "list" if args.list else "download",
        "discovered": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": [],
    }

    async with async_playwright() as pw:
        context = await launch_browser(pw)
        try:
            page, root_urn = await sign_in_and_resolve_root(
                context, manifest, args.root
            )
            print(f"Root URN: {root_urn}")

            api = AdobeApi(context.request, page)
            host = await api.discover_regional_host(root_urn)
            print(f"Regional host: {host}")

            # Cache for future runs
            manifest["root_urn"] = root_urn
            manifest["regional_host"] = host
            save_manifest(manifest)

            print("Listing PDFs ...")
            pdfs = await api.list_pdfs(root_urn)
            run["discovered"] = len(pdfs)
            print(f"\nDiscovered {len(pdfs)} PDF(s).")

            if args.list:
                for p in pdfs:
                    size = p.get("size") or 0
                    print(f"  {p['adobe_path']}  ({size:,} bytes)")
                run["ended_at"] = utcnow()
                return run

            for i, p in enumerate(pdfs, 1):
                fid = p["id"]
                name = p["name"]
                existing = manifest["files"].get(fid)
                already = (
                    existing
                    and existing.get("status") == "downloaded"
                    and existing.get("local_path")
                    and (HERE / existing["local_path"]).exists()
                    and existing.get("modified") == p.get("modified")
                )
                if already:
                    existing["last_seen_remote"] = utcnow()
                    run["skipped"] += 1
                    if i % 25 == 0:
                        print(f"  [{i}/{len(pdfs)}] skipped (cached): {name}")
                    continue
                try:
                    print(f"  [{i}/{len(pdfs)}] DOWNLOAD: {p['adobe_path']}  ({p.get('size'):,} bytes)" if p.get("size") else f"  [{i}/{len(pdfs)}] DOWNLOAD: {p['adobe_path']}")
                    if existing and existing.get("local_path") and (HERE / existing["local_path"]).exists():
                        dest = HERE / existing["local_path"]
                    else:
                        dest = unique_path(DOWNLOAD_DIR, name)
                    t0 = time.monotonic()
                    written = await api.download_to(fid, dest)
                    elapsed = time.monotonic() - t0
                    if written > 1_000_000 and elapsed > 0:
                        print(f"    {written:,} bytes in {elapsed:.1f}s ({written/elapsed/1e6:.1f} MB/s)")
                    entry = {
                        "id": fid,
                        "name": name,
                        "adobe_path": p["adobe_path"],
                        "size_remote": p.get("size"),
                        "size_local": dest.stat().st_size,
                        "modified": p.get("modified"),
                        "etag": p.get("etag"),
                        "local_path": str(dest.relative_to(HERE)),
                        "sha256": sha256_file(dest),
                        "downloaded_at": utcnow(),
                        "last_seen_remote": utcnow(),
                        "status": "downloaded",
                    }
                    manifest["files"][fid] = entry
                    save_manifest(manifest)
                    run["downloaded"] += 1
                except Exception as e:
                    print(f"    FAILED: {e}")
                    run["failed"].append({"id": fid, "name": name, "error": str(e)})
                    entry = manifest["files"].setdefault(fid, {
                        "id": fid,
                        "name": name,
                        "adobe_path": p["adobe_path"],
                    })
                    entry["status"] = "failed"
                    entry["last_error"] = str(e)
                    entry["last_seen_remote"] = utcnow()
                    save_manifest(manifest)

            seen_ids = {p["id"] for p in pdfs}
            for fid, entry in manifest["files"].items():
                if fid not in seen_ids and entry.get("status") in ("downloaded", "failed"):
                    if entry.get("status") != "deleted_remotely":
                        entry["status"] = "deleted_remotely"
                        entry["deleted_remotely_at"] = utcnow()
                rel = entry.get("local_path")
                if rel and not (HERE / rel).exists() and entry.get("status") == "downloaded":
                    entry["status"] = "missing_locally"
                    entry["missing_locally_at"] = utcnow()
        finally:
            await context.close()

    run["ended_at"] = utcnow()
    manifest["runs"].append(run)
    manifest["last_run"] = run
    save_manifest(manifest)
    return run


def reconcile_local(manifest: dict[str, Any]) -> dict[str, Any]:
    run = {"started_at": utcnow(), "mode": "reconcile-disk", "changes": 0}
    for fid, entry in manifest["files"].items():
        rel = entry.get("local_path")
        if not rel:
            continue
        local = HERE / rel
        if entry.get("status") == "downloaded" and not local.exists():
            entry["status"] = "missing_locally"
            entry["missing_locally_at"] = utcnow()
            run["changes"] += 1
        elif entry.get("status") == "missing_locally" and local.exists():
            entry["status"] = "downloaded"
            run["changes"] += 1
    run["ended_at"] = utcnow()
    manifest["runs"].append(run)
    manifest["last_run"] = run
    save_manifest(manifest)
    print(f"Reconciliation updated {run['changes']} entries.")
    return run


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list", action="store_true", help="discover only, no download")
    parser.add_argument("--reconcile", action="store_true", help="update manifest from disk")
    parser.add_argument("--root", help="override root URN (urn:aaid:sc:US:...)")
    args = parser.parse_args()
    try:
        result = asyncio.run(main_async(args))
        print("\n=== Summary ===")
        print(json.dumps(result, indent=2, default=str))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
