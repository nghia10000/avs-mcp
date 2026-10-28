"""hmi.py — Aurora Vision Studio HMI (.avhmi) knowledge, mined from the official examples, and a binding check.

An .avhmi file is WinForms-like XML: <HMI><ClientSize/><Controls> with one element per control (Label, VideoBox,
ImpulseButton, PassFailIndicator...), each with Location/Size/Name/TabIndex/Text..., containers (Panel, GroupBox,
TabControl/TabPage, SplitContainer) nesting their own <Controls>. A control talks to the program through <Binding>:

    <Input Property="Image" Macro="Main" Variable="con_..." VariantIndex="0" />   program -> control
        Variable = a connection of macro Macro (an output port value, e.g. outImage: con_...)
    <Output Property="Value" Variable="ext_con_..." />                           control -> program
        Variable = a global declared in the .avcode:  extern public Real ext_con_... = 0.000000
        and used as a filter input (inMaxValue: ext_con_...)

The project lists the file in .avproj: <HMI Path="name.avhmi" />. Standard library only; the catalog is built on the
first query and kept for the life of the process.
"""

import difflib
import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter

LAYOUT = {"Location", "Size", "Name", "TabIndex", "Anchor", "Dock", "Controls", "Binding", "DesignerAttributes"}
_cache = {}
CACHE = os.environ.get("AVS_DOCS_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "avs_docs_cache")


def api():
    """{control: {assembly, base, public, properties: [{name, type, hmi, port, default, description, values}]}} written
    by hmi_reflect.ps1 from the public metadata of the installed HMI.*.dll, or {} when it has not been run."""
    path = os.path.join(CACHE, "hmi_controls.json")
    if not os.path.exists(path):
        return {}
    sig = os.path.getmtime(path)
    if _cache.get("api_sig") != sig:
        with open(path, encoding="utf-8-sig") as f:
            _cache["api"], _cache["api_sig"] = json.load(f).get("controls", {}), sig
    return _cache["api"]


def controls(parent):
    """Direct child controls of an element (the children of its <Controls>)."""
    box = parent.find("Controls")
    return list(box) if box is not None else []


def walk(parent, container="HMI"):
    for c in controls(parent):
        yield c, container
        yield from walk(c, c.tag)
        for page in c.findall("TabPages/*"):           # TabControl keeps its pages apart
            yield page, c.tag
            yield from walk(page, page.tag)


def snippet(e, limit=1400):
    s = ET.tostring(e, encoding="unicode").strip()
    s = re.sub(r"\n\s*\n", "\n", s)
    return s if len(s) <= limit else s[:limit] + "\n    ..."


def build(files, root):
    cat = {}
    for path in files:
        rel = os.path.relpath(path, root)
        try:
            tree = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for e, container in walk(tree):
            c = cat.setdefault(e.tag, {"n": 0, "files": set(), "props": {}, "bind": Counter(), "in": Counter(),
                                       "example": None, "ref": rel})
            c["n"] += 1
            c["files"].add(rel)
            c["in"][container] += 1
            for p in e:
                if p.tag in LAYOUT or len(p):
                    continue
                c["props"].setdefault(p.tag, Counter())[(p.text or "").strip()[:60]] += 1
            b = e.find("Binding")
            for x in (b if b is not None else []):
                c["bind"][f"{x.tag} {x.get('Property') or x.get('Name')}"] += 1   # Event: Name="Click"
            # keep the smallest instance that has a binding as the example (a bare one if none has)
            score = (b is None, len(ET.tostring(e)))
            if c["example"] is None or score < c["example"][0]:
                c["example"] = (score, snippet(e), rel)
    return cat


def catalog(examples_dir):
    files = glob.glob(os.path.join(examples_dir, "**", "*.avhmi"), recursive=True)
    sig = (examples_dir, len(files), max((os.path.getmtime(p) for p in files), default=0))
    if _cache.get("sig") != sig:
        _cache["sig"], _cache["cat"], _cache["files"] = sig, build(files, examples_dir), len(files)
    return _cache["cat"], _cache["files"]


RULES = ("Binding: <Input Property=\"P\" Macro=\"Main\" Variable=\"con_...\" VariantIndex=\"0\"/> shows a value of the "
         "program (a connection of that macro) in property P; <Output Property=\"P\" Variable=\"ext_con_...\"/> sends P to "
         "the program through a global 'extern public <Type> ext_con_... = <default>' used as a filter input. List the "
         "file in .avproj as <HMI Path=\"x.avhmi\"/>. Check with avs_hmi_check.")


def hmi_controls(name, examples_dir):
    cat, nfiles = catalog(examples_dir)
    ref = api()
    if not nfiles and not ref:
        return f"No .avhmi examples under {examples_dir} and no hmi_controls.json; set AVS_EXAMPLES_DIR.", True
    note = "" if ref else "\n(Run hmi_reflect.ps1 once to add the real property list of every control from HMI.*.dll.)"
    if not (name or "").strip():
        out = [f"{len(set(cat) | set(ref))} HMI control types; x = uses in {nfiles} example .avhmi files, then the "
               "most used bound properties:"]
        for t in sorted(set(cat) | set(ref), key=lambda t: (-cat[t]["n"] if t in cat else 0, t)):
            c = cat.get(t)
            b = ", ".join(k for k, _ in c["bind"].most_common(4)) if c else "(not in the examples)"
            out.append(f"  {t:<26} x{c['n'] if c else 0:<4} {b}")
        return "\n".join(out + ["", RULES, "Details of one control: avs_hmi_controls name=<type>."]) + note, False
    key = name.strip()
    known = set(cat) | set(ref)
    if key not in known:
        key = {k.lower(): k for k in known}.get(key.lower(), key)
    if key not in known:
        near = difflib.get_close_matches(key, list(known), n=8, cutoff=0.5)
        return (f"'{name}' is not an HMI control here. "
                + (f"Close: {', '.join(near)}. " if near else "") + "List all with an empty name."), True
    out, c, a = [], cat.get(key), ref.get(key)
    if c:
        out += [f"{key}: {c['n']} use(s) in {len(c['files'])} example file(s), e.g. {c['ref']}",
                "inside: " + ", ".join(f"{k} x{v}" for k, v in c["in"].most_common()),
                "bindings: " + (", ".join(f"{k} x{v}" for k, v in c["bind"].most_common()) or "none")]
    else:
        out.append(f"{key}: not used in the example HMIs")
    hmi_names = set()
    if a:
        hp = [p for p in a["properties"] if p.get("hmi")]
        hmi_names = {p["name"] for p in hp}
        out += ["", f"AVS properties ({a['assembly']}, base {a['base']}{'' if a.get('public', True) else ', internal'}"
                    "; 'bind' = shown as a program port by default, the examples bind others too):"]
        if not hp:
            out.append("  none of its own: only position and size; the examples set nothing else on it")
        for p in hp:
            extra = "; ".join(x for x in (f"default {p['default']}" if p["default"] else "",
                                          f"values {p['values']}" if p["values"] else "") if x)
            out.append(f"  {p['name']:<22} {p['type']:<16} {'bind' if p['port'] else '    '} "
                       f"{p['description']}{'  [' + extra + ']' if extra else ''}")
    if c:
        out += ["", "properties written in the examples (sample values):"]
        for p, vals in sorted(c["props"].items(), key=lambda kv: -sum(kv[1].values())):
            out.append(f"  {p:<24} " + "; ".join(f"{v} (x{n})" if n > 1 else str(v) for v, n in vals.most_common(3))
                       + ("" if not a or p in hmi_names else "  (WinForms base)"))
        out += ["", f"example ({c['example'][2]}):", c["example"][1]]
    out += ["", RULES + " Use the AVS properties plus the base properties the examples show for this control; "
                "avs_hmi_check warns about any other."]
    return "\n".join(out) + note, False


# ------------------------------------------------------------------ check

def macro_bodies(text):
    """{macro name: body text} of the macros defined in .avcode text."""
    starts = [(m.start(), m.group(1)) for m in
              re.finditer(r"^(?:public\s+|private\s+)?([A-Za-z_]\w*)\s*\(.*\)[ \t\w()]*$", text, re.M)]   # ") step(inRun)"
    return {name: text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
            for i, (pos, name) in enumerate(starts)}


def rect(e):
    try:
        x, y = (int(v) for v in e.findtext("Location", "0, 0").split(","))
        w, h = (int(v) for v in e.findtext("Size", "0, 0").split(","))
    except ValueError:
        return None
    return x, y, w, h


PAGED = {"TabControl", "MultiPanelControl"}     # their pages share one area on purpose


def anchors(e):
    return {a.strip() for a in (e.findtext("Anchor") or "Top, Left").split(",")}


TRAPS = ((1, "Top", "Bottom", "below", "high"), (0, "Left", "Right", "right of", "wide"))


def layout_warnings(parent, size, where="HMI"):
    """Sibling controls that overlap, controls reaching outside their container (size = its client w, h), and anchor
    traps: a control anchored Bottom (Right) only, below (right of) one anchored Top (Left), slides onto it when the
    HMI is shown smaller than its ClientSize, as Studio's HMI panel often is."""
    out, kids, traps = [], [(c, rect(c)) for c in controls(parent)], {}
    for i, (a, ra) in enumerate(kids):
        if ra is None:
            continue
        name = a.findtext("Name") or a.tag
        if size and (ra[0] < 0 or ra[1] < 0 or ra[0] + ra[2] > size[0] or ra[1] + ra[3] > size[1]):
            out.append(f"{a.tag} '{name}' ({ra[0]},{ra[1]} {ra[2]}x{ra[3]}) reaches outside {where} "
                       f"({size[0]}x{size[1]})")
        for b, rb in kids[i + 1:]:
            if rb is None or parent.tag in PAGED:
                continue
            bname = b.findtext("Name") or b.tag
            span = [min(ra[k] + ra[k + 2], rb[k] + rb[k + 2]) > max(ra[k], rb[k]) for k in (0, 1)]   # share x / y
            if all(span):
                out.append(f"{a.tag} '{name}' overlaps {b.tag} '{bname}' in {where}")
                continue
            for axis, near, far, rel, dim in TRAPS:
                if not size or not span[1 - axis]:
                    continue
                for (lo, rlo, nlo), (hi, rhi, nhi) in (((a, ra, name), (b, rb, bname)),
                                                        ((b, rb, bname), (a, ra, name))):
                    al, ah = anchors(lo), anchors(hi)
                    end = rhi[axis] + rhi[axis + 2]
                    # a control anchored both ways stretches: its far edge moves with the window, no trap
                    if rlo[axis] >= end and far in al and near not in al and near in ah and far not in ah:
                        t = size[axis] - (rlo[axis] - end)            # first size at which they touch
                        key = (lo.tag, nlo, axis)
                        if t > traps.get(key, (0,))[0]:
                            traps[key] = (t, f"{far}-anchored {lo.tag} '{nlo}' is {rel} {near}-anchored {hi.tag} "
                                             f"'{nhi}' in {where}: they overlap when it is shown less than {t} px "
                                             f"{dim} (designed {size[axis]}); anchor both {near} or move one aside")
        if a.find("Controls") is not None or a.tag in PAGED:
            out += layout_warnings(a, ra[2:], f"{a.tag} '{name}'")
    return out + [msg for _, msg in traps.values()]


def property_warnings(e, name, examples_dir):
    """Properties set on a control that are neither AVS properties of it (hmi_controls.json) nor used on that control
    in the examples: WinForms has them, but Studio may ignore them (BackColor on ProgramControlBox)."""
    ref, cat = api(), catalog(examples_dir)[0] if examples_dir else {}
    if e.tag not in ref and e.tag not in cat:
        return []
    allowed = {p["name"] for p in ref.get(e.tag, {}).get("properties", []) if p.get("hmi")}
    allowed |= set(cat.get(e.tag, {}).get("props", {}))
    return [f"{e.tag} '{name}' sets {p.tag}: not an AVS property of {e.tag} and never set on it in the examples; "
            f"Studio may ignore it" for p in e if p.tag not in LAYOUT and not len(p) and p.tag not in allowed]


def hmi_check(project, examples_dir=None):
    """Every binding of the project's HMI points at something that exists in its programs (errors); overlapping
    controls, anchor traps and properties Studio may ignore are reported as warnings."""
    project = os.path.abspath(project or "")
    if not project.lower().endswith(".avproj") or not os.path.exists(project):
        return "Give the path of an existing .avproj file.", True
    base = os.path.dirname(project)
    proj = ET.parse(project).getroot()
    hmis = [os.path.join(base, e.get("Path")) for e in proj.iter("HMI")]
    if not hmis:
        return "The project has no <HMI Path=...> entry.", True
    code = ""
    for e in proj.iter("Program"):
        with open(os.path.join(base, e.get("Path")), encoding="utf-8-sig") as f:
            code += f.read() + "\n"
    # ext_con_... or renamed (HMI3); optional types may have no default: extern public String? ext_con_...
    externs = set(re.findall(r"^extern\s+public\s+\S+\s+(\w+)\s*(?:=|$)", code, re.M))
    bodies = macro_bodies(code)
    problems, warnings, n = [], [], 0
    for h in hmis:
        if not os.path.exists(h):
            problems.append(f"{os.path.basename(h)}: file listed in .avproj does not exist")
            continue
        names = Counter()
        root = ET.parse(h).getroot()
        try:
            client = tuple(int(v) for v in root.findtext("ClientSize", "").split(","))
        except ValueError:
            client = None
        warnings += [f"{os.path.basename(h)}: {w}" for w in layout_warnings(root, client)]
        for e, _ in walk(root):
            nm = e.findtext("Name") or "?"
            names[nm] += 1
            warnings += [f"{os.path.basename(h)}: {w}" for w in property_warnings(e, nm, examples_dir)]
            b = e.find("Binding")
            for x in (b if b is not None else []):
                n += 1
                var = x.get("Variable", "")
                where = f"{e.tag} '{nm}' {x.tag} {x.get('Property')}"
                if x.tag == "Event":       # <Event Name="Click" Handler="Macro"/>: a //:USETYPE EVENTHANDLER macro
                    if x.get("Handler") not in bodies:
                        problems.append(f"{e.tag} '{nm}' Event {x.get('Name')}: handler macro "
                                        f"'{x.get('Handler')}' is not defined")
                elif x.tag == "Output":
                    if var not in externs:
                        problems.append(f"{where}: global {var} is not declared (extern public <Type> {var} = ...)")
                    elif not re.search(rf":\s*{var}\b", code):
                        problems.append(f"{where}: {var} is declared but no filter input uses it")
                else:
                    macro = x.get("Macro", "")
                    if macro not in bodies:
                        problems.append(f"{where}: macro '{macro}' is not defined")
                    elif not re.search(rf"\b{re.escape(var)}\b", bodies[macro]):
                        problems.append(f"{where}: {var} is not a connection of macro {macro}")
        problems += [f"control name '{k}' used {v} times" for k, v in names.items() if v > 1 and k != "?"]
    warn = ("\n\nLayout warnings (Location/Size/Anchor; not errors):\n" + "\n".join("  " + w for w in warnings)
            if warnings else "")
    if problems:
        return f"{len(problems)} problem(s) in {n} binding(s):\n" + "\n".join("  " + p for p in problems) + warn, True
    return f"OK: {n} binding(s) in {len(hmis)} HMI file(s) point at existing program values." + warn, False
