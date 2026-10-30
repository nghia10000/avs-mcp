"""ports.py — real Aurora Vision Studio port names of a filter, mined from the official .avcode examples.

The installed documentation describes AVL C++ functions, whose parameters differ from the Studio filter ports
(ScanSingleEdge: inScanMap in C++, inScanPath in Studio; outputs used as outEdge.Point; hidden ports). The example
programs shipped with Studio are proven syntax for this Studio version, so every filter call in them is collected:

    //:HIDDENPORT[inScanWidth] TRUE
    ScanSingleEdge                      or  LoadObject<Region>
    {                                   optional configuration block (e.g. ReadFilmstrip { Image: Image })
        Image: Image
    }
    (
        inScanWidth: 5
        inEdgeScanParams:               multi-line value: a structure written on the following, deeper lines
            EdgeScanParams
            (
                EdgeTransition: BrightToDark
            )
        outEdge.Point: con_...          sub-output connected
    )

Calls of macros defined in the same example folder and formula blocks (expression) are skipped: their ports are the
example's own. Standard library
only; the catalog is built on the first query and kept for the life of the process.
"""

import difflib
import glob
import os
import re
from collections import Counter

CALL = re.compile(r"^([A-Za-z_]\w*)(?:<(.+)>)?$")
PORT = re.compile(r"^([A-Za-z_][\w.]*)\s*:\s*(.*)$")
HIDDEN = re.compile(r"^//:HIDDENPORT\[([^\]]+)\]\s*TRUE")
MACRO = re.compile(r"^(?:public\s+|private\s+)?([A-Za-z_]\w*)\s*\(", re.M)
CONNECTION = re.compile(r"^(?:\w+_)?con_[0-9a-f]{8}_[0-9a-f_]+$")
_cache = {}


def indent(line):
    return len(line) - len(line.lstrip(" \t"))


def kind(port, config=False):
    if config:
        return "config"
    for prefix in ("in", "out", "diag"):
        if port.startswith(prefix) and port[len(prefix):][:1].isupper():
            return prefix
    return "other"


def compact(lines):
    """Lines of a multi-line value -> 'EdgeScanParams(ProfileInterpolation: Quadratic4, ...)'."""
    s = ""
    for ln in (x.strip() for x in lines):
        if not ln:
            continue
        if ln in ("(", ")", "{", "}") or s.endswith(("(", "{")) or not s:
            s += ln
        else:
            s += ", " + ln
    return s


def body(lines, i, ind, close):
    """Entries of a '(' / '{' block whose opener is lines[i]; returns ([(port, value, line_no)], index after close)."""
    out, j = [], i + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() == close and indent(ln) == ind:
            return out, j + 1
        m = PORT.match(ln.strip())
        if m and indent(ln) == ind + 1:
            value, k = m.group(2).strip(), j + 1
            if not value:                                # value continues on the deeper lines below
                while k < len(lines) and (not lines[k].strip() or indent(lines[k]) > ind + 1):
                    k += 1
                value = compact(lines[j + 1:k])
            out.append((m.group(1), value, j + 1))
            j = k
            continue
        j += 1
    return out, j


def calls(text):
    """[(filter, type_arg, [(port, value, line_no, is_config)], hidden_ports, line_no)] of one .avcode file."""
    lines = text.splitlines()
    out, hidden, i = [], set(), 0
    while i < len(lines):
        s = lines[i].strip()
        h = HIDDEN.match(s)
        if h:
            hidden.add(h.group(1))
            i += 1
            continue
        if s.startswith("//"):
            i += 1
            continue
        m = CALL.match(s)
        if not m or indent(lines[i]) == 0:
            if s:
                hidden = set()                           # //:HIDDENPORT lines belong to the call right below them
            i += 1
            continue
        ind, j, ports = indent(lines[i]), i + 1, []
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].strip() == "{" and indent(lines[j]) == ind:
            cfg, j = body(lines, j, ind, "}")
            ports += [(p, v, n, True) for p, v, n in cfg]
            while j < len(lines) and not lines[j].strip():
                j += 1
        if j < len(lines) and lines[j].strip() == "(" and indent(lines[j]) == ind:
            args, j = body(lines, j, ind, ")")
            ports += [(p, v, n, False) for p, v, n in args]
            out.append((m.group(1), m.group(2), ports, hidden, i + 1))
            i = j
        else:
            i += 1
        hidden = set()
    return out


def build(files, root):
    macros = {}                                          # folder -> macro names defined by that example
    texts = {}
    for p in files:
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            texts[p] = f.read()
        macros.setdefault(os.path.dirname(p), set()).update(MACRO.findall(texts[p]))
    cat = {}
    for p in sorted(files):
        rel = os.path.relpath(p, root)
        for name, targ, ports, hidden, line in calls(texts[p]):
            if name == "expression" or name in macros[os.path.dirname(p)]:    # formula block: ports are its own
                continue
            f = cat.setdefault(name, {"calls": 0, "files": set(), "refs": [], "types": Counter(), "ports": {}})
            f["calls"] += 1
            f["files"].add(rel)
            if len(f["refs"]) < 2 and not any(r.startswith(rel + ":") for r in f["refs"]):
                f["refs"].append(f"{rel}:{line}")
            if targ:
                f["types"][targ] += 1
            seen, prev = set(), None
            for port, value, n, cfg in ports:
                if port in seen:
                    continue
                seen.add(port)
                d = f["ports"].get(port)
                if d is None:
                    d = {"n": 0, "hidden": 0, "values": Counter(), "ref": f"{rel}:{n}", "kind": kind(port, cfg),
                         "after": prev}
                    f["ports"][port] = d
                d["n"] += 1
                d["hidden"] += port in hidden
                if value and not CONNECTION.match(value) and not re.match(r"^(in|out)[A-Z]\w*$", value):
                    d["values"][value if len(value) <= 120 else value[:117] + "..."] += 1
                prev = port
    return cat


def catalog(examples_dir):
    files = glob.glob(os.path.join(examples_dir, "**", "*.avcode"), recursive=True)
    sig = (examples_dir, len(files), max((os.path.getmtime(p) for p in files), default=0))
    if _cache.get("sig") != sig:
        _cache["sig"], _cache["cat"], _cache["files"] = sig, build(files, examples_dir), len(files)
    return _cache["cat"], _cache["files"]


def ordered(ports):
    """Port names in program order: each new port goes right after the port it followed when first seen."""
    out = []
    for p, d in ports.items():
        after = d["after"]
        out.insert(out.index(after) + 1 if after in out else len(out), p)
    return out


def filter_ports(name, examples_dir):
    cat, nfiles = catalog(examples_dir)
    if not nfiles:
        return f"No .avcode examples under {examples_dir}; set AVS_EXAMPLES_DIR.", True
    key = re.sub(r"<.*>$", "", (name or "").strip())
    if key not in cat:
        low = {n.lower(): n for n in cat}
        key = low.get(key.lower(), key)
    if key not in cat:
        near = difflib.get_close_matches(key, list(cat), n=8, cutoff=0.6)
        near += sorted(n for n in cat if key and key.lower() in n.lower() and n not in near)[:8]
        return (f"'{name}' is not called in any of the {nfiles} example programs, so its Studio ports are unknown here. "
                + (f"Similar filters that are: {', '.join(near)}. " if near else "")
                + "Use avs_filter_info (all Studio ports, checked against the installed Studio) and confirm with "
                  "avs_validate."), True
    f = cat[key]
    out = [f"{key}: {f['calls']} call(s) in {len(f['files'])} example file(s); e.g. {', '.join(f['refs'])}"]
    if f["types"]:
        out.append("Type arguments seen: " + ", ".join(f"{key}<{t}> x{n}" for t, n in f["types"].most_common()))
    out.append("")
    rows = [("port", "kind", "seen", "hidden", "sample values")]
    for p in ordered(f["ports"]):
        d = f["ports"][p]
        vals = "; ".join(f"{v} (x{c})" if c > 1 else v for v, c in d["values"].most_common(3)) or "-"
        if d["n"] < f["calls"]:
            vals += f"   [e.g. {d['ref']}]"
        rows.append((p, d["kind"], f"{d['n']}/{f['calls']}", str(d["hidden"]) if d["hidden"] else "", vals))
    w = [max(len(r[c]) for r in rows) for c in range(4)]
    out += [f"{r[0]:<{w[0]}}  {r[1]:<{w[1]}}  {r[2]:>{w[2]}}  {r[3]:>{w[3]}}  {r[4]}" for r in rows]
    out += ["",
            "kind: in/out/diag from the name prefix, config = {...} block above the ports; a dotted port (outEdge.Point) "
            "is a sub-output connected on its own. seen = calls that list the port; hidden = calls with "
            "//:HIDDENPORT[port] TRUE. Values are literals (connections omitted). Only ports the examples use are "
            "listed, and filters never used in the examples are not covered — check those with avs_filter_doc / "
            "avs_docs_search and avs_validate."]
    return "\n".join(out), False
