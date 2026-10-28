"""docs.py — full-text search (BM25) over every Aurora Vision document available on this machine.

Sources: the installed Aurora Vision Library documentation (functions, data types, Machine Vision Guide, technical
issues, getting started), the installed Aurora Deep Learning documentation, and the Studio manual converted from its
printable PDF by docs_ingest.py (<cache>/studio_*_sections.jsonl). Standard library only. The index is built on the
first search and kept in <cache>/docs_index.json until a source folder changes.
"""

import glob
import html
import json
import math
import os
import re
from collections import Counter

CACHE = os.environ.get("AVS_DOCS_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "avs_docs_cache")
K1, B = 1.5, 0.75
_index = None


def html_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    m = re.search(r"(?is)<title>(.*?)</title>", t)
    title = html.unescape(m.group(1)).strip() if m else os.path.splitext(os.path.basename(path))[0]
    t = re.sub(r"(?s)<(script|style|nav|header|footer).*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>|</li>|</div>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("​", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    end = t.find("This article is valid for version")
    return title, t[:end] if end > 0 else t


def tokens(text):
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?", text):
        lw = w.lower()
        out.append(lw)
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", w)   # ScanSingleEdge -> scan single edge
        if len(parts) > 1:
            out += [p.lower() for p in parts]
    return out


def sources(doc_dir, dl_doc_dir):
    """[(label, kind, root)] of folders to index."""
    out = []
    for sub, label in (("functions", "AVL function"), ("datatypes", "AVL data type"),
                       ("machine_vision_guide", "Machine Vision Guide"), ("technical_issues", "AVL technical issues"),
                       ("getting_started", "AVL getting started")):
        if os.path.isdir(os.path.join(doc_dir, sub)):
            out.append((label, "html", os.path.join(doc_dir, sub)))
    if dl_doc_dir and os.path.isdir(dl_doc_dir):
        out.append(("Deep Learning", "html", dl_doc_dir))
    for p in sorted(glob.glob(os.path.join(CACHE, "studio_*_sections.jsonl"))):
        ver = re.search(r"studio_(.+)_sections", p).group(1)
        out.append((f"Studio {ver} manual", "jsonl", p))
    return out


def signature(srcs):
    return [[label, root, os.path.getmtime(root)] for label, _, root in srcs]


def build(srcs):
    docs = []
    for label, kind, root in srcs:
        if kind == "jsonl":
            with open(root, encoding="utf-8") as f:
                for line in f:
                    s = json.loads(line)
                    if len(s["text"]) > len(s["title"]) + 20:
                        docs.append({"src": label, "title": s["path"], "where": f"page {s['page']}", "text": s["text"]})
            continue
        for path in sorted(glob.glob(os.path.join(root, "**", "*.html"), recursive=True)):
            if label == "Deep Learning" and os.sep + "net" + os.sep in path:    # C# API pages: noise for Studio work
                continue
            title, text = html_text(path)
            if len(text) > 40:
                docs.append({"src": label, "title": title, "where": os.path.relpath(path, root), "text": text})
    df = Counter()
    for d in docs:
        tf = Counter(tokens(d["title"] + " " + d["title"] + " " + d["text"]))   # title counted twice
        d["tf"], d["len"] = tf, sum(tf.values())
        df.update(tf.keys())
    return {"docs": docs, "df": dict(df), "avgdl": sum(d["len"] for d in docs) / max(1, len(docs))}


def index(doc_dir, dl_doc_dir):
    global _index
    srcs = sources(doc_dir, dl_doc_dir)
    sig = signature(srcs)
    if _index is not None and _index["sig"] == sig:
        return _index
    cached = os.path.join(CACHE, "docs_index.json")
    if os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("sig") == sig:
                for d in data["docs"]:
                    d["tf"] = Counter(d["tf"])
                _index = data
                return _index
        except (OSError, ValueError, KeyError):
            pass
    _index = build(srcs)
    _index["sig"] = sig
    if os.path.isdir(CACHE):
        with open(cached, "w", encoding="utf-8") as f:
            json.dump(_index, f)
    return _index


def snippet(text, terms, width=420):
    low = text.lower()
    best, best_hits = 0, -1
    for i in range(0, max(1, len(text) - width), 60):
        hits = sum(low.count(t, i, i + width) for t in terms)
        if hits > best_hits:
            best, best_hits = i, hits
    s = text[best:best + width].replace("\n", " ")
    return ("…" if best else "") + s + ("…" if best + width < len(text) else "")


def search(query, doc_dir, dl_doc_dir, limit=8, source=""):
    ix = index(doc_dir, dl_doc_dir)
    q = list(dict.fromkeys(tokens(query)))
    if not q:
        return "Empty query."
    n, avgdl = len(ix["docs"]), ix["avgdl"]
    scored = []
    for i, d in enumerate(ix["docs"]):
        if source and source.lower() not in d["src"].lower():
            continue
        s = 0.0
        for t in q:
            f = d["tf"].get(t, 0)
            if f:
                idf = math.log(1 + (n - ix["df"][t] + 0.5) / (ix["df"][t] + 0.5))
                s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * d["len"] / avgdl))
        if s:
            scored.append((s, i))
    scored.sort(reverse=True)
    if not scored:
        return f"No document matches {query!r}. Sources: {', '.join(sorted({d['src'] for d in ix['docs']}))}."
    out = [f"{len(scored)} match(es) in {n} sections; top {min(limit, len(scored))} (read one with avs_docs_read id):"]
    for s, i in scored[:limit]:
        d = ix["docs"][i]
        out.append(f"\n[{i}] {d['src']} | {d['title']} ({d['where']})\n    {snippet(d['text'], q)}")
    return "\n".join(out)


def read(doc_id, doc_dir, dl_doc_dir, offset=0, length=6000):
    ix = index(doc_dir, dl_doc_dir)
    if not 0 <= doc_id < len(ix["docs"]):
        return f"No section {doc_id}; ids come from avs_docs_search.", True
    d = ix["docs"][doc_id]
    text = d["text"][offset:offset + length]
    more = f"\n\n[{len(d['text']) - offset - length} more characters: offset={offset + length}]" \
        if offset + length < len(d["text"]) else ""
    return f"{d['src']} | {d['title']} ({d['where']})\n\n{text}{more}", False
