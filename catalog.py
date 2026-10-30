"""catalog.py — one record per Aurora Vision Studio filter, built from the downloaded Filter Reference and toolbox.xml.

Sources (all local; nothing here is committed):
  <cache>/studio_filters/<Category>/<Filter>.html   Studio port table, module, level, summary (filters_fetch.py)
  <AVS_DIR>/toolbox.xml                              the Studio toolbox tree: category / group / subgroup / tags
  <cache>/filters_verified.json                      port names checked by the AvexeGenerator (verify_filters.py)

    python catalog.py            build <cache>/filters_catalog.json and print a summary
Standard library only.
"""

import difflib
import glob
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

from docs import CACHE

PAGES = os.path.join(CACHE, "studio_filters")
OUT = os.path.join(CACHE, "filters_catalog.json")
VERIFIED = os.path.join(CACHE, "filters_verified.json")

ROW = re.compile(r"<tr class=\"(?:odd|even)\">(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
ALT = re.compile(r'alt="([^"]+)"')
KINDS = {"Input value": "in", "Output value": "out", "Diagnostic input": "diag", "Diagnostic output": "diag",
         "Input will be modified": "inout"}


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def text(s):
    s = re.sub(r'<img[^>]*infinity[^>]*>', "inf", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def port_type(cell):
    """'<a>Real</a><a>*</a>' -> ('Real', optional, array depth, conditional)."""
    links = [text(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", cell, re.S)] or [text(cell)]
    base = [x for x in links if x not in ("*", "Array", "?")]
    return {"type": "".join(base) or text(cell), "optional": "*" in links, "array": links.count("Array"),
            "conditional": "?" in links}


def parse_page(path):
    t = read_text(path)
    name = os.path.splitext(os.path.basename(path))[0]
    rec = {"name": name, "doc_category": os.path.basename(os.path.dirname(path))}
    m = re.search(r"<h1>(.*?)</h1>", t, re.S)
    title = text(m.group(1)) if m else name
    m = re.search(r"\((.+)\)$", title)
    rec["title"] = m.group(1) if m else ""                    # 'ScanEdges ExactlyN': Studio's group + variant
    m = re.search(r"user_level_icon_block.*?</a>", t, re.S)
    rec["level"] = text(re.sub(r".*<img[^>]*>", "", m.group(0), flags=re.S)) if m else ""
    m = re.search(r"Module:</th>\s*<td[^>]*>(.*?)</td>", t, re.S)
    rec["module"] = text(m.group(1)) if m else ""
    crumbs = re.findall(r'&raquo;\s*<a href="[^"]*">(.*?)</a>', t)
    rec["path"] = [text(c) for c in crumbs[1:-1]]
    i = t.find("</table>", t.find("Module:")) if "Module:" in t else t.find("</h1>")
    m = re.search(r"<p>(.*?)</p>", t[i:], re.S)
    rec["summary"] = text(m.group(1)) if m else ""
    m = re.search(r'Applications</h3>\s*<div class="helpSection" >(.*?)</div>', t, re.S)
    rec["applications"] = text(m.group(1)) if m else ""
    ports = []
    table = t[t.find('class="hor_zebra_table"'):]
    table = table[:table.find("</table>")]
    for row in ROW.findall(table):
        cells = CELL.findall(row)
        alt = ALT.search(cells[0]) if cells else None
        if not alt or len(cells) < 4:
            continue
        p = {"kind": KINDS.get(alt.group(1), alt.group(1)), "name": text(cells[1])}
        p.update(port_type(cells[2]))
        p["range"] = text(cells[3]) if len(cells) > 4 else ""    # 4 cells: the type spans the range column
        p["desc"] = text(cells[-1])
        ports.append(p)
    rec["ports"] = ports
    m = re.search(r"defined using the type variable <b>(\w+)</b> which represents ([^<.]*)", t)
    if m or "GenericFilters.html" in t:
        rec["generic"] = m.group(2).strip() if m else "?"     # call as Name<Type>
    return rec


def toolbox(avs_dir):
    """Filter or filter-group name -> {category, group, subgroup, tip, tags} from the Studio toolbox."""
    path = os.path.join(avs_dir, "toolbox.xml")
    if not os.path.exists(path):
        return {}
    out = {}
    for cat in ET.parse(path).getroot().iter("Category"):
        for grp in cat.iter("Group"):
            for sub in grp.iter("SubGroup"):
                for item in sub.iter("Item"):
                    key = item.get("Filter") or item.get("Group")
                    out.setdefault(key, {"category": cat.get("Name"), "group": grp.get("Name"),
                                         "subgroup": sub.get("Name"), "tip": grp.get("Tip") or cat.get("Tip"),
                                         "tags": grp.get("Tags") or ""})
    return out


def avcode_value(cpp, studio_type=""):
    """AVL C++ default -> .avcode literal: 0.6f -> 0.6, Selection::Best -> Best, NIL -> Nil, true -> True.

    In .avcode a Double literal needs D (1.0D) and a Long one L (100L); without it the value is rejected."""
    v = cpp.replace("​", "").replace('\\"', '"').strip()
    v = re.sub(r"\batl::|\bavl::", "", v)
    v = re.sub(r"(\d)\.f\b", r"\1.0", v)
    v = re.sub(r"(\d)[fDL]\b", r"\1", v)
    v = re.sub(r"^\((-?[\d.]+)\)$", r"\1", v)                 # (3) -> 3
    v = re.sub(r"([A-Za-z])\.0$", r"\1", v)                   # doc typo: LittleEndian.0f
    v = re.sub(r"^Selection(Best|First|Last)$", r"\1", v)     # doc typo: SelectionBest
    v = re.sub(r"\b\w+::(\w+)", r"\1", v)
    v = re.sub(r"\bNIL\b", "Nil", v)
    v = re.sub(r"\b(true|false)\b", lambda m: m.group(1).title(), v)
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        if studio_type == "Double":
            v += "" if "." in v else ".0"
            v += "D"
        elif studio_type == "Long":
            v += "L"
    return v


def avl_defaults(doc_dir):
    """AVL function name -> {parameter: C++ default} from the installed C++ documentation."""
    out = {}
    for path in glob.glob(os.path.join(doc_dir, "functions", "*", "*.html")):
        t = read_text(path)
        d = {}
        for row in ROW.findall(t):
            cells = CELL.findall(row)
            if len(cells) == 6 and ALT.search(cells[0]):
                default = text(cells[4])
                if default:
                    d[text(cells[1])] = default
        out[os.path.splitext(os.path.basename(path))[0]] = d
    return out


def build(avs_dir, doc_dir=""):
    recs = {}
    for path in sorted(glob.glob(os.path.join(PAGES, "*", "*.html"))):
        if os.path.basename(path) == "index.html":
            continue
        r = parse_page(path)
        recs[r["name"]] = r
    tb = toolbox(avs_dir)
    for r in recs.values():
        group = r["title"].split(" ")[0] if r["title"] else ""
        hit = tb.get(r["name"]) or tb.get(group) or tb.get(r["name"].split("_")[0])
        if hit:
            r["toolbox"] = hit
    defaults = avl_defaults(doc_dir) if doc_dir else {}
    for r in recs.values():
        d = defaults.get(r["name"]) or {}
        for p in r["ports"]:
            if p["kind"] == "in" and p["name"] in d:
                p["default"] = avcode_value(d[p["name"]], p["type"])
    verified = read_json(VERIFIED) if os.path.exists(VERIFIED) else {}
    for name, v in verified.items():
        if name in recs:
            recs[name]["verified"] = v
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=0)
    return recs


# ------------------------------------------------------------------ lookups for the MCP server

_loaded = {}
HOW = "Build it with: python catalog.py && python verify_filters.py && python catalog.py (in tools/avs_mcp)."


def load():
    if not os.path.exists(OUT):
        return None
    sig = os.path.getmtime(OUT)
    if _loaded.get("sig") != sig:
        _loaded["sig"], _loaded["recs"] = sig, read_json(OUT)
    return _loaded["recs"]


def status(r):
    v = r.get("verified")
    if not v:
        return "not checked against the installed Studio"
    if v["status"] == "missing":
        return "NOT AVAILABLE in the installed Studio (newer filter in the 5.7 reference)"
    s = "name and port names checked against the installed Studio (AvexeGenerator)"
    if v["status"] == "ports":
        s = "exists in the installed Studio, but these ports from the 5.7 reference do not: " + ", ".join(v["bad"])
    return s


def type_text(p):
    t = p["type"] + "Array" * p.get("array", 0)
    return t + ("*" if p.get("optional") else "") + ("?" if p.get("conditional") else "")


def ports_absent(r):
    return {b.split(" ", 1)[1] for b in r.get("verified", {}).get("bad", [])}


def call_template(r):
    """A call that loads in the installed Studio: verified defaults written, outputs as comments."""
    v = r.get("verified", {})
    generic = "generic" in r or v.get("generic")
    absent, bad_default = ports_absent(r), set(v.get("bad_default", []))
    lines = [r["name"] + ("<T>" if generic else ""), "("]
    for p in r["ports"]:
        if p["name"] in absent:
            continue
        if p["kind"] == "inout":
            lines += [f"\tin{p['name'][2:]}: ...", f"\tout{p['name'][2:]}: con_..."]
        elif p["kind"] == "in":
            val = p["default"] if "default" in p and p["name"] not in bad_default else "..."
            lines.append(f"\t{p['name']}: {val}")
        else:
            lines.append(f"\t{p['name']}: con_...")
    return "\n".join(lines + [")"])


def filter_info(name, examples_dir=""):
    recs = load()
    if recs is None:
        return "The filter catalog is not built. " + HOW, True
    key = re.sub(r"<.*>$", "", (name or "").strip())
    r = recs.get(key) or {n.lower(): x for n, x in recs.items()}.get(key.lower())
    if r is None:
        near = difflib.get_close_matches(key, list(recs), n=8, cutoff=0.6)
        return (f"No filter '{name}' in the catalog of {len(recs)} filters."
                + (f" Close names: {', '.join(near)}." if near else "")
                + " Search by task with avs_search_filters."), True
    v = r.get("verified", {})
    tb = r.get("toolbox", {})
    out = [f"{r['name']}" + (f"  ({r['title']})" if r["title"] else ""),
           f"Module {r['module']} | level {r['level']} | " + " > ".join(r["path"])
           + (f" | toolbox: {tb['category']} > {tb['group']} > {tb['subgroup']}" if tb else ""),
           f"Status: {status(r)}", "", r["summary"]]
    if r["applications"]:
        out.append("Applications: " + r["applications"])
    if "generic" in r or v.get("generic"):
        out.append(f"Generic: write {r['name']}<Type> (T = {r.get('generic', 'any valid type')}), e.g. "
                   f"{r['name']}<Integer>; with ports.py examples showing the usual type arguments.")
    absent, bad_default = ports_absent(r), set(v.get("bad_default", []))
    rows = [("port", "kind", "type", "range", "default", "description")]
    for p in r["ports"]:
        default = p.get("default", "")
        if default and p["name"] in bad_default:
            default += " (REJECTED by Studio)"
        elif default and v:
            default += " ✓"
        rows.append((p["name"] + (" (not in installed Studio)" if p["name"] in absent else ""), p["kind"],
                     type_text(p), p.get("range", ""), default, p.get("desc", "")))
    w = [min(max(len(x[c]) for x in rows), 40) for c in range(5)]
    out += [""] + ["  ".join(f"{x[c]:<{w[c]}}" for c in range(5)) + "  " + x[5] for x in rows]
    if examples_dir:
        import ports as ex
        cat, _ = ex.catalog(examples_dir)
        e = cat.get(r["name"])
        if e:
            out += ["", f"Used in {e['calls']} official example call(s), e.g. {', '.join(e['refs'])}"
                    + (f"; type arguments: {', '.join(t for t, _ in e['types'].most_common(4))}" if e["types"] else "")]
            for port, d in e["ports"].items():
                if d["values"]:
                    out.append(f"  {port}: " + "; ".join(f"{val} (x{c})" for val, c in d["values"].most_common(3)))
    out += ["", "Call (inputs left at their default can be omitted; ✓ = literal accepted by the installed Studio):",
            call_template(r), "",
            "Type marks: * optional (Nil allowed), ? conditional output (may be Nil), Array = array. "
            "In .avcode a Double literal is written 1.0D and a Long 100L. Check the whole program with avs_validate."]
    return "\n".join(out), False


def search(query, limit=20, include_missing=False):
    """Studio filters for a task: name, toolbox group and tip, summary, applications and port descriptions."""
    recs = load()
    if recs is None:
        return None
    stem = lambda w: re.sub(r"(es|s)$", "", w) if len(w) > 4 else w      # blobs -> blob, boxes -> box
    words = [stem(w) for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 1]
    if not words:
        return "Give a few words, e.g. 'fit circle edges', 'count blobs', 'read barcode'."
    camel = lambda s: re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s).replace("_", " ").lower()
    hits = []
    for r in recs.values():
        if not include_missing and r.get("verified", {}).get("status") == "missing":
            continue
        tb = r.get("toolbox", {})
        fields = [(camel(r["name"] + " " + r["title"]), 4),
                  (camel(" ".join(r["path"]) + " " + " ".join(tb.get(k, "") or "" for k in
                                                              ("category", "group", "subgroup"))), 2),
                  (camel(r["summary"] + " " + r["applications"]), 2), (camel(tb.get("tip", "") or ""), 1),
                  (camel(" ".join(p["name"] + " " + p.get("desc", "") for p in r["ports"])), 0.5)]
        score, matched = 0.0, 0
        for w in words:
            got = [wt for f, wt in fields if re.search(r"\b" + re.escape(w), f)]
            if got:
                matched += 1
                score += max(got)
        if matched == len(words) or (len(words) > 2 and matched >= len(words) - 1):
            level = {"Basic": 0.3, "Advanced": 0.1}.get(r["level"], 0) - 2 * r["name"].endswith("_Deprecated")
            hits.append((-(score + matched * 3 + level), r["name"], r))
    hits.sort()
    if not hits:
        return f"No Studio filter matches '{query}'. Try fewer or other words."
    out = [f"{len(hits)} Studio filter(s) for '{query}', best {min(limit, len(hits))}:"]
    for _, n, r in hits[:limit]:
        mark = "" if r.get("verified", {}).get("status") != "missing" else "  [NOT in installed Studio]"
        out.append(f"- {n}  [{' > '.join(r['path'][-1:])}, {r['level']}]  {r['summary']}{mark}")
    out.append("Details, ports, defaults and a ready call: avs_filter_info.")
    return "\n".join(out)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    from avs_mcp import AVS_DIR, DOC_DIR
    recs = build(AVS_DIR, DOC_DIR)
    kinds = {}
    for r in recs.values():
        for p in r["ports"]:
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"{len(recs)} filters, {sum(kinds.values())} ports {kinds}")
    print("no ports:", sum(1 for r in recs.values() if not r["ports"]))
    ins = [p for r in recs.values() for p in r["ports"] if p["kind"] == "in"]
    print("inputs with a default:", sum("default" in p for p in ins), "/", len(ins))
    print("in toolbox:", sum(1 for r in recs.values() if "toolbox" in r))
    print("modules:", len({r['module'] for r in recs.values()}), "levels:", {r['level'] for r in recs.values()})
