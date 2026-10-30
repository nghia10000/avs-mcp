"""verify_filters.py — check every catalogued filter's name and port names against the installed Studio.

One program calls every filter in filters_catalog.json with each input set to its AVL default (else Nil) and every
output connected; AuroraVisionAvexeGenerator loads it (no GUI, no license needed) and logs every unknown filter,
input or output and every literal of the wrong type, so a single run checks thousands of filters. A control call with
a planted wrong port and wrong value must be reported, or the run is refused. The per-filter verdict goes to
<cache>/filters_verified.json, which catalog.py merges into the catalog:

    {"ScanSingleEdge": {"status": "ok"}, "Foo": {"status": "missing"},
     "Bar": {"status": "ports", "bad": ["input inX"]}, "Baz": {"status": "ok", "bad_default": ["inY"]}}

Nil for a required input is accepted at load time, so this proves names and default literals, not results.

    python verify_filters.py [--chunk 4000]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from avs_mcp import AVS_DIR                                   # noqa: E402
from catalog import OUT as CATALOG, VERIFIED, read_json      # noqa: E402

PROJECT = ('<AuroraVisionProject Name="verify" Version="5.6.6" Revision="79833" Product="Professional" '
           'StartUpProgramName="Main">\n  <Files>\n    <Program Path="Program.avcode" />\n  </Files>\n'
           '</AuroraVisionProject>\n')
MISSING = re.compile(r"Filter or macrofilter '([^'<]+)(?:<[^']*>)?' does not exist")
BAD_PORT = re.compile(r"(Input|Output) '([^']+)' not found in filter \"[^\"]*\((\d+)\)[^\"]+\"")
BAD_VALUE = re.compile(r'Unable to initialize input "(\w+)" of filter "\((\d+)\)')
BAD_SOURCE = re.compile(r'Found invalid data source for input "(\w+)" of filter "(\w+)')
UNTYPED = re.compile(r"non-generic instance of the generic filter \"(\w+)_NonGeneric\"")
CONTROL = {"name": "RegionArea", "ports": [{"kind": "in", "name": "inRegion", "default": "5"},
                                           {"kind": "in", "name": "inPlantedBogus"}]}
CONTROL_RESULT = {"status": "ports", "bad": ["input inPlantedBogus"], "bad_default": ["inRegion"]}


def program(recs, typed=(), skip=(), where=None):
    """The .avcode text; where (if given) gets {line number: (call index, port)} for each default written."""
    lines, n = ["public Main()", "{"], 0
    for i, r in enumerate(recs):
        generic = "generic" in r or r["name"] in typed
        lines += ["\t" + r["name"] + ("<Integer>" if generic else ""), "\t("]
        for p in r["ports"]:
            if p["kind"] == "inout":                          # Studio 5.6 writes ioX as inX + outX
                n += 1
                lines += [f"\t\tin{p['name'][2:]}: Nil",
                          f"\t\tout{p['name'][2:]}: con_7e000001_0000_4000_8000_{n:012x}"]
            elif p["kind"] == "in":
                lines.append(f"\t\t{p['name']}: {p.get('default', 'Nil')}")
            else:
                n += 1
                lines.append(f"\t\t{p['name']}: con_7e000001_0000_4000_8000_{n:012x}")
        lines += ["\t)", ""]
    return "\n".join(lines[:-1] + ["}", ""])


def check(recs, typed=()):
    """Load one program calling recs (plus a control call with a planted wrong port) -> per-filter verdicts."""
    recs = list(recs) + [CONTROL]
    gen = os.path.join(AVS_DIR, "AuroraVisionAvexeGenerator.exe")
    skip = set()                                              # defaults the .avcode parser rejects: written as Nil
    while True:
        where = {}
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "verify.avproj"), "w", encoding="utf-8") as f:
                f.write(PROJECT)
            with open(os.path.join(tmp, "Program.avcode"), "w", encoding="utf-8") as f:
                f.write(program(recs, typed, skip, where))
            r = subprocess.run([gen, "-overwrite", "-no-logo", os.path.join(tmp, "verify.avproj"),
                                os.path.join(tmp, "x.avexe")], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=1800)
        log = r.stdout + r.stderr
        if "couldn't be loaded" not in log and "No program modules" not in log:
            break
        m = re.search(r"couldn't be loaded\. Line: (\d+)", log)
        if not m or int(m.group(1)) not in where or len(skip) > 300:
            raise RuntimeError("the generated program did not parse:\n" + log[-2000:])
        skip.add(where[int(m.group(1))])
    out =[{"status": "ok"} for _ in recs]                    # by the call's position in Main
    pos = {}
    for i, x in enumerate(recs):
        pos.setdefault(x["name"], []).append(i)
    for status, names in (("missing", MISSING.findall(log)), ("untyped", UNTYPED.findall(log))):
        for name in names:
            for i in pos.get(name, ()):
                out[i] = {"status": status}
    for io, port, idx in sorted(set(BAD_PORT.findall(log))):
        v = out[int(idx)]
        v["status"] = "ports"
        v.setdefault("bad", []).append(f"{io.lower()} {port}")
    for port, idx in sorted(set(BAD_VALUE.findall(log))):
        out[int(idx)].setdefault("bad_default", []).append(port)
    for port, name in sorted(set(BAD_SOURCE.findall(log))):
        for i in pos.get(name, ()):
            if any(p["name"] == port for p in recs[i]["ports"]):
                out[i].setdefault("bad_default", []).append(port)
    for i, port in skip:
        out[i].setdefault("bad_default", []).append(port)
    control = out.pop()
    out = {x["name"]: v for x, v in zip(recs, out)}
    if control != CONTROL_RESULT:
        raise RuntimeError("the control call's planted wrong port was not reported; the log format changed:\n"
                           + log[-2000:])
    other = [ln for ln in log.splitlines() if "[Error]" in ln and not MISSING.search(ln)
             and not BAD_PORT.search(ln) and not UNTYPED.search(ln) and not BAD_VALUE.search(ln)
             and not BAD_SOURCE.search(ln)]
    return out, other, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=4000)
    a = ap.parse_args()
    recs = list(read_json(CATALOG).values())
    result, others = {}, []
    for i in range(0, len(recs), a.chunk):
        res, other, log = check(recs[i:i + a.chunk])
        result.update(res)
        others += other
    untyped = {k for k, v in result.items() if v["status"] == "untyped"}
    if untyped:                                               # generic, though the page does not say so
        res, other, log = check([r for r in recs if r["name"] in untyped], typed=untyped)
        for k, v in res.items():
            result[k] = dict(v, generic=True)
        others += other
    with open(VERIFIED, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0)
    count = {}
    for v in result.values():
        count[v["status"]] = count.get(v["status"], 0) + 1
    print(count)
    bad = sum(len(v.get("bad_default", [])) for v in result.values())
    print(f"defaults rejected: {bad} in {sum('bad_default' in v for v in result.values())} filters")
    print(f"{len(others)} other errors")
    for ln in others[:30]:
        print("  ", ln[:220])


if __name__ == "__main__":
    main()
