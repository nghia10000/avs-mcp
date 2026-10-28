"""docs_ingest.py — turn the printable Aurora Vision Studio documentation (PDF) into sections avs_docs_search reads.

The PDF is not shipped (it is Zebra's documentation): download it from
https://docs.adaptive-vision.com/current/pdf/studio/all_chapters.pdf and run, once,

    python docs_ingest.py <all_chapters.pdf> [--out <cache dir>]

with pypdf installed (only this script needs it; the MCP server stays standard-library only). It writes
<cache dir>/studio_<version>_sections.jsonl — one JSON object per outline heading: {"title", "path", "page", "text"}.
Default cache dir: AVS_DOCS_CACHE, else ../avs_docs_cache next to this repo.
"""

import argparse
import json
import os
import re
import sys

DEFAULT_CACHE = os.environ.get("AVS_DOCS_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "avs_docs_cache")


def outline(reader):
    """Flat list of (title, page_index) in document order."""
    out = []

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
                continue
            try:
                out.append((it.title.strip(), reader.get_destination_page_number(it)))
            except Exception:
                pass
    walk(reader.outline)
    return out


def clean(text):
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def ingest(pdf, out_dir):
    import pypdf                                       # only needed here
    r = pypdf.PdfReader(pdf)
    pages = [clean(p.extract_text() or "") for p in r.pages]
    heads = outline(r)
    version = next((m.group(1) for t, _ in heads[:3] for m in [re.search(r"Studio (\d+\.\d+)", t)] if m), "unknown")

    # One continuous text with page offsets; each heading's section runs from its title (searched from its own
    # page on) to the next heading's title. Headings that cannot be found keep their page start.
    full, offsets = "", []
    for t in pages:
        offsets.append(len(full))
        full += t + "\n"
    # Look for a heading only on its own page and the next one: the same words appear elsewhere (tables of contents,
    # cross references), and one far match would push every later section past it.
    starts, pos = [], 0
    for title, p in heads:
        lo = max(pos, offsets[p])
        hi = offsets[p + 2] if p + 2 < len(offsets) else len(full)
        at = full.find(title, lo, hi)
        if at < 0:
            at = lo
        starts.append(at)
        pos = at
    sections, chapter = [], ""
    for i, (title, p) in enumerate(heads):
        end = starts[i + 1] if i + 1 < len(heads) else len(full)
        text = full[starts[i]:max(end, starts[i])].strip()
        if re.match(r"^\d+\. [A-Z][a-z]+( [A-Z][a-z]+)*$", title) and len(title) > 12:
            chapter = title
        sections.append({"title": title, "path": f"{chapter} > {title}" if chapter and chapter != title else title,
                         "page": p + 1, "text": text})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"studio_{version}_sections.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for s in sections:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return path, version, len(pages), len(sections), sum(len(s["text"]) for s in sections)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--out", default=DEFAULT_CACHE)
    a = ap.parse_args()
    path, version, n_pages, n_sec, n_chars = ingest(a.pdf, a.out)
    print(f"Studio {version}: {n_pages} pages -> {n_sec} sections, {n_chars:,} characters -> {path}")
    sys.exit(0)
