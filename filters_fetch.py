"""filters_fetch.py — download the public Aurora Vision Studio Filter Reference into <cache>/studio_filters.

One HTML page per filter (with its Studio port table), fetched sequentially from docs.adaptive-vision.com. Pages are
Zebra's content: they stay in the local cache and are never committed. Already-downloaded pages are skipped, so an
interrupted run can be restarted. Standard library only.

    python filters_fetch.py [--delay 0.7] [--limit N]
"""

import argparse
import http.client
import os
import re
import sys
import time
import urllib.parse

from docs import CACHE

BASE = "https://docs.adaptive-vision.com/studio/filters/"
AGENT = "avs-mcp docs fetcher (personal offline index; sequential, rate-limited)"


_conn = None


def get(url):
    """GET over one kept-alive HTTPS connection (reconnects once if the server closed it)."""
    global _conn
    u = urllib.parse.urlsplit(url)
    for attempt in (0, 1):
        if _conn is None:
            _conn = http.client.HTTPSConnection(u.netloc, timeout=60)
        try:
            _conn.request("GET", u.path, headers={"User-Agent": AGENT})
            r = _conn.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException):
            _conn.close()
            _conn = None
            if attempt:
                raise
            continue
        if r.status != 200:
            raise OSError(f"HTTP {r.status}")
        return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.7, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads (0 = all)")
    ap.add_argument("--out", default=os.path.join(CACHE, "studio_filters"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    index = get(BASE + "AllFilters.html")
    with open(os.path.join(a.out, "AllFilters.html"), "wb") as f:
        f.write(index)
    text = index.decode("utf-8", "replace")
    pages = sorted(set(re.findall(r'href="([A-Za-z0-9_]+/[A-Za-z0-9_]+\.html)"', text)))
    ver = re.search(r"valid for version ([\d.]+)", text)
    print(f"{len(pages)} filter pages, version {ver.group(1) if ver else '?'}", flush=True)
    done = failed = 0
    for i, rel in enumerate(pages, 1):
        dest = os.path.join(a.out, *rel.split("/"))
        if os.path.exists(dest):
            continue
        time.sleep(a.delay)
        try:
            data = get(BASE + rel)
        except (OSError, http.client.HTTPException) as e:
            failed += 1
            print(f"[{i}/{len(pages)}] {rel}: {e}", file=sys.stderr, flush=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as f:
            f.write(data)
        os.replace(dest + ".part", dest)
        done += 1
        if done % 100 == 0:
            print(f"[{i}/{len(pages)}] {done} downloaded", flush=True)
        if a.limit and done >= a.limit:
            break
    print(f"done: {done} downloaded, {failed} failed", flush=True)


if __name__ == "__main__":
    main()
