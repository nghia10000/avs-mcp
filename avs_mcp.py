"""avs_mcp.py — MCP server (stdio) connecting an AI assistant to Aurora Vision Studio.

Python standard library only (3.11+). Tools:
  avs_search_filters  search filters in the AVL documentation installed on this machine
  avs_filter_doc      syntax, parameters and description of one filter or data type
  avs_search_examples how a filter / formula is written in the official .avcode examples
  avs_filter_ports    real Studio port names of a filter (+ sample values), mined from the official examples
  avs_validate        load a project with AuroraVisionAvexeGenerator.exe (no GUI) and report errors
  avs_run             run a project headlessly with Aurora Vision Runtime (+ evaluate a task)
  avs_open            open a project in Aurora Vision Studio
  avs_task_types      list step types a task spec can use
  avs_task_new        create a task folder (task.toml, annotations.json, images/)
  avs_task_check      check a task spec for missing information
  avs_annotate        local web page to draw defects / mark OK-NG on the task images
  avs_evaluate        compare results.csv written by the program with expected.csv (+ margins, holdout)
  avs_split           mark part of the images as holdout (not for tuning)
Prompt:
  describe_task       interview the user and write the task spec

Paths are auto-detected; override with AVS_DIR, AVS_RUNTIME_DIR, AVL_DOC_DIR and AVS_EXAMPLES_DIR.
"""

import glob
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_spec  # noqa: E402
import annotate  # noqa: E402
import docs  # noqa: E402
import ports  # noqa: E402
from annotate import annotate_tool  # noqa: E402
from evaluate import evaluate, split  # noqa: E402

VERSION = "0.3.0"
AV_ROOT = r"C:\Program Files\Aurora Vision"


def latest(pattern):
    """Newest install matching pattern (by version number in the folder name)."""
    hits = glob.glob(os.path.join(AV_ROOT, pattern))
    key = lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p))]
    return max(hits, key=key) if hits else ""


AVS_DIR = os.environ.get("AVS_DIR") or latest("Aurora Vision Studio 5.* Professional") or latest("Aurora Vision Studio 5.*")
RUNTIME_DIR = os.environ.get("AVS_RUNTIME_DIR") or latest("Aurora Vision Studio 5.* Runtime")
DOC_DIR = os.environ.get("AVL_DOC_DIR") or os.path.join(latest("Aurora Vision Library 5.*"), "Documentation")
FUNC_DIR = os.path.join(DOC_DIR, "functions")
TYPE_DIR = os.path.join(DOC_DIR, "datatypes")
EXAMPLES_DIR = os.environ.get("AVS_EXAMPLES_DIR") or os.path.join(
    r"C:\ProgramData\Aurora Vision", os.path.basename(AVS_DIR))
DL_DOC_DIR = os.environ.get("AVS_DL_DOC_DIR") or os.path.join(latest("Aurora Deep Learning 5.*"), "Documentation")

# ------------------------------------------------------------------ documentation

def page_text(path):
    """Documentation HTML -> plain text without the page menu and footer."""
    with open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    t = re.sub(r"(?s)<(script|style).*?</\1>", "", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>|</li>|</div>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\u200b", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    end = t.find("This article is valid for version")
    if end > 0:
        t = t[:end]
    start = t.find("Header:")              # function page: starts at Header
    if start < 0:
        start = t.find("Description")       # data type page
    return t[start:] if start > 0 else t


_index = None

def doc_index():
    """{name: (path, category, summary)} for every function in functions/."""
    global _index
    if _index is not None:
        return _index
    if not os.path.isdir(FUNC_DIR):
        raise FileNotFoundError(f"AVL documentation not found at {DOC_DIR}; set AVL_DOC_DIR.")
    _index = {}
    for cat in sorted(os.listdir(FUNC_DIR)):
        d = os.path.join(FUNC_DIR, cat)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".html") or fn == "index.html":
                continue
            p = os.path.join(d, fn)
            summary = ""
            try:
                lines = page_text(p).split("\n")
                for i, ln in enumerate(lines):          # summary is the line after "Module:" + module name
                    if ln.startswith("Module:") and i + 2 < len(lines):
                        summary = lines[i + 2] if lines[i + 1].strip() else lines[i + 3]
                        break
            except OSError:
                pass
            _index[fn[:-5]] = (p, cat, summary)
    return _index


def search_filters(query, limit=30):
    words = [w.lower() for w in query.split() if w]
    if not words:
        return "Give a keyword."
    hits = []
    for name, (_, cat, summary) in doc_index().items():
        hay = f"{name} {cat} {summary}".lower()
        if all(w in hay for w in words):
            score = sum(3 if w in name.lower() else 1 for w in words)
            hits.append((-score, name, cat, summary))
    hits.sort()
    if not hits:
        return (f"No filter matches '{query}'. Only AVL library functions are indexed; "
                "in Studio a C++ function Foo_Direct is usually the filter Foo.")
    out = [f"{len(hits)} match(es), showing {min(limit, len(hits))}:"]
    out += [f"- {n}  [{c}]  {s}" for _, n, c, s in hits[:limit]]
    return "\n".join(out)


def filter_doc(name):
    studio = docs.studio_filter_page(name) if name else None
    if studio:                                  # Studio port names; the AVL page adds C++ defaults
        _, text = docs.html_text(studio)
        out = f"Studio Filter Reference ({os.path.relpath(studio, docs.studio_filters_dir())}):\n\n{text}"
        avl = doc_index().get(name) if os.path.isdir(FUNC_DIR) else None
        if avl:
            out += "\n\n---- AVL library function (C++ parameter names, defaults) ----\n\n" + page_text(avl[0])
        return out
    idx = doc_index()
    if name in idx:
        return page_text(idx[name][0])
    tp = os.path.join(TYPE_DIR, name + ".html")
    if os.path.exists(tp):
        return page_text(tp)
    near = [n for n in idx if name.lower() in n.lower()][:15]
    return f"No page for '{name}'." + (" Similar: " + ", ".join(near) if near else "")


def search_examples(query, limit=5, context=12):
    """.avcode files of the bundled Examples/Tutorial containing every word; shows the first hit in each."""
    words = [w.lower() for w in query.split() if w]
    if not words:
        return "Give a keyword (filter name, formula function, data type...)."
    files = glob.glob(os.path.join(EXAMPLES_DIR, "**", "*.avcode"), recursive=True)
    if not files:
        return f"No .avcode examples under {EXAMPLES_DIR}; set AVS_EXAMPLES_DIR."
    hits = []
    for p in files:
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        low = text.lower()
        if all(w in low for w in words):
            hits.append((-sum(low.count(w) for w in words), p, text))
    hits.sort()
    if not hits:
        return f"No example uses '{query}' ({len(files)} files searched)."
    out = [f"{len(hits)} example file(s) of {len(files)}, showing {min(limit, len(hits))}:"]
    for _, p, text in hits[:limit]:
        lines = text.splitlines()
        i = next(i for i, ln in enumerate(lines) if words[0] in ln.lower())
        lo, hi = max(0, i - 2), min(len(lines), i + context)
        out.append(f"\n== {os.path.relpath(p, EXAMPLES_DIR)} (line {i + 1})")
        out += lines[lo:hi]
    return "\n".join(out)

# ------------------------------------------------------------------ Studio

def validate(project):
    project = os.path.abspath(project)
    if not project.lower().endswith(".avproj") or not os.path.exists(project):
        return "Give the path of an existing .avproj file.", True
    gen = os.path.join(AVS_DIR, "AuroraVisionAvexeGenerator.exe")
    if not os.path.exists(gen):
        return f"AuroraVisionAvexeGenerator.exe not found in {AVS_DIR}; set AVS_DIR.", True
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([gen, "-overwrite", "-no-logo", project, os.path.join(tmp, "check.avexe")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    log = (r.stdout + r.stderr).strip()
    code = r.returncode - (1 << 32) if r.returncode >= (1 << 31) else r.returncode   # Windows returns negatives as unsigned
    ok = code == 0
    head = "VALID: project loads without errors." if ok else f"ERRORS (exit {code})."
    note = ("\nNote: this only checks loading (filter names, types, connections, formulas). "
            "The program is not run, so results are not known.")
    return f"{head}\n\n{log}{note}", not ok


def run_project(project, task_dir=None, timeout=600):
    """Build an .avexe next to the project (relative paths resolve from there) and run it with AuroraVisionConsole."""
    project = os.path.abspath(project)
    if not project.lower().endswith(".avproj") or not os.path.exists(project):
        return "Give the path of an existing .avproj file.", True
    gen = os.path.join(AVS_DIR, "AuroraVisionAvexeGenerator.exe")
    console = os.path.join(RUNTIME_DIR, "AuroraVisionConsole.exe")
    if not os.path.exists(console):
        return ("AuroraVisionConsole.exe not found: install Aurora Vision Studio Runtime (same version as Studio) "
                "or set AVS_RUNTIME_DIR.", True)
    pdir = os.path.dirname(project)
    exe = os.path.join(pdir, ".avs_run.avexe")

    results = None
    if task_dir:
        spec = task_spec.load_task(task_dir)
        results = os.path.join(task_dir, spec.get("output", {}).get("results_csv", "results.csv"))
        if os.path.exists(results):
            os.remove(results)                          # programs append; start from an empty file

    before = {f: os.path.getmtime(os.path.join(pdir, f)) for f in os.listdir(pdir)}
    try:
        g = subprocess.run([gen, "-overwrite", "-no-logo", project, exe],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if g.returncode != 0 or not os.path.exists(exe):
            return f"Build failed (fix with avs_validate):\n{(g.stdout + g.stderr).strip()}", True
        try:
            r = subprocess.run([console, exe], cwd=pdir, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return (f"Program still running after {timeout} s and was stopped. "
                    "Check that EnumerateImages has inRepeat = False and no loop waits for a camera or HMI."), True
    finally:
        if os.path.exists(exe):
            os.remove(exe)

    log = "\n".join(ln for ln in (r.stdout + r.stderr).splitlines()
                    if ln.strip() and not set(ln.strip()) <= {"-"} and "loading:" not in ln)
    ok = "Program successfully finished" in log and "[Error]" not in log   # exit code is 0 even on failure
    changed = sorted(f for f in os.listdir(pdir)
                     if f != os.path.basename(exe) and os.path.getmtime(os.path.join(pdir, f)) != before.get(f))
    out = [("FINISHED" if ok else "FAILED") + f" — {os.path.basename(project)} with {os.path.basename(RUNTIME_DIR)}",
           "", log or "(no output)", "", "Files written in the project folder: " + (", ".join(changed) or "none")]
    if task_dir and ok:
        if results and os.path.exists(results):
            text, _ = evaluate(task_dir)
            out += ["", "avs_evaluate:", text]
        else:
            out += ["", f"Program did not write {results} — check the SaveText path."]
            ok = False
    return "\n".join(out), not ok


def studio_windows():
    """[(hwnd, title)] of visible top-level windows owned by AuroraVisionStudio.exe."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    def image_name(pid):
        h = kernel32.OpenProcess(0x1000, False, pid)    # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value) if ok else ""
        finally:
            kernel32.CloseHandle(h)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if user32.IsWindowVisible(hwnd) and n:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if image_name(pid.value).lower() == "auroravisionstudio.exe":
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def open_project(project, wait=90):
    project = os.path.abspath(project)
    if not os.path.exists(project):
        return f"File not found: {project}", True
    name = os.path.basename(project).lower()
    shows = lambda title: title.lower().rstrip("*").strip().endswith(name)

    # Studio allows one instance: a second launch only shows "already running" and exits 0.
    running = studio_windows()
    for hwnd, title in running:
        if shows(title):
            import ctypes
            ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return f"Already open in Studio: '{title}'. Brought the window to the front.", False
    if running:
        return (f"Aurora Vision Studio is already running ('{running[0][1]}') and allows only one instance, "
                f"so {project} was NOT opened. Ask the user to close Studio (or use File > Open) and call avs_open again."), True

    p = subprocess.Popen([os.path.join(AVS_DIR, "AuroraVisionStudio.exe"), project],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    for _ in range(wait):
        for _, title in studio_windows():
            if shows(title):
                return f"Opened in Studio: '{title}'.", False
        if p.poll() is not None:
            return f"Studio exited (code {p.returncode}) without opening {project}.", True
        time.sleep(1)
    titles = [t for _, t in studio_windows()]
    return (f"Studio started but no window shows {os.path.basename(project)} after {wait} s "
            f"(windows: {titles or 'none'}). It may be waiting on a dialog or license prompt."), True

# ------------------------------------------------------------------ prompt

DESCRIBE_TASK = """You are helping the user describe a machine vision task so that an Aurora Vision program can be built and checked automatically.

Task folder: {task_dir}

1. Call avs_task_types, then ask the user what the program must do and look at their sample images. Pick the step types; split hybrid jobs into steps (e.g. locate -> preprocess -> defect(deep) -> measure -> decide). Geometry that can be defined (position, rotation, ROI, size) is rule-based; appearance that can only be judged by eye goes to deep learning.
2. Call avs_task_new with the folder and the step types.
3. Interview the user one topic at a time; do not guess:
   - exactly what is measured or inspected (from where to where, which edge, which direction)
   - how each feature looks and what it can be confused with
   - how images vary (shift, rotation, lighting, variants); ask for the worst images
   - accuracy: tolerance, mm per pixel
   - for defects: classes, NG rule, things that look like defects but are OK, dataset size, escape vs false-reject priority
   - output: columns, OK/NG, destination
   - ground truth: caliper/CMM values or OK/NG per image -> expected.csv
4. Call avs_annotate and ask the user to mark every image OK/NG and draw each defect / measurement with a class and a description
   (it writes annotations.json, the verdict column of expected.csv and annotated/*.png). Open the annotated/*.png files and read
   every description before writing the program; ask about anything unclear. annotations.json format:
   {{"images": {{"<file>": [{{"kind": "line|point|rect|circle|polygon", "points": [[x, y], ...], "class": "", "label": "", "step": "<step id>"}}]}},
    "image_info": {{"<file>": {{"verdict": "OK|NG", "note": "", "levels": [black, white]}}}}}}
5. Fill task.toml (replace every TODO), then call avs_task_check and fix everything it reports.
   With enough images (>= ~10), call avs_split so part of them is held out; never tune on holdout images.
6. Build the program so it appends one row per image to output.results_csv (SaveText, CreateOrAppend), using the key column and the step columns; look up filters with avs_search_filters / avs_filter_doc, their Studio port names with avs_filter_ports and real .avcode syntax with avs_search_examples; check it with avs_validate.
   Also write the measured values the decision is based on (gap brightness, areas, distances...) as extra numeric columns:
   avs_evaluate then reports how far each NG image is from the OK range.
   Ship a <Project>.avview listed in the .avproj (<View Path="..."/>) with the key outputs already placed — input/result
   image with the overlays that explain it (regions, located points, coordinate system, aligned fitting fields, fitted
   shapes) and a TextView with the per-image result — and name them with //:LABELDATASOURCE, so pressing Run in Studio
   shows the images at once instead of the user dragging outputs into views.
7. Run it with avs_run (project + task_dir): it executes headlessly and evaluates the results. Fix the program and repeat until avs_evaluate passes; if the Runtime is not installed, ask the user to press Run (avs_open) and then call avs_evaluate.
8. Passing is not enough: report the margins to the user. NG images close to the OK range, or NG images no measured value
   separates, need more samples or the user's judgement — ask instead of moving a threshold to fit them.
"""

PROMPTS = [
    {"name": "describe_task",
     "description": "Interview the user and write a complete task spec (task.toml, annotations.json, expected.csv).",
     "arguments": [{"name": "task_dir", "description": "Folder for the task", "required": True}]},
]

# ------------------------------------------------------------------ MCP

def obj(props, required):
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
TOOLS = [
    {"name": "avs_search_filters",
     "description": "Search Aurora Vision filters/functions by keyword (name, category, description), e.g. 'fit segment', 'threshold rgb'.",
     "inputSchema": obj({"query": S, "limit": {"type": "integer", "default": 30}}, ["query"])},
    {"name": "avs_filter_doc",
     "description": "Full documentation of one filter or data type: syntax, parameters, defaults, description, possible errors. "
                    "When the Studio Filter Reference was downloaded (filters_fetch.py), shows its Studio port table "
                    "first, then the AVL C++ page.",
     "inputSchema": obj({"name": S}, ["name"])},
    {"name": "avs_docs_search",
     "description": "Full-text (BM25) search over all Aurora Vision documentation on this machine: AVL functions and data "
                    "types, Machine Vision Guide, technical issues, Deep Learning docs, and the Studio manual (formulas, "
                    "data types, Optional/Conditional, project files, HMI...) once converted with docs_ingest.py, and "
                    "the Studio Filter Reference (Studio port names) once downloaded with filters_fetch.py. Use it "
                    "before guessing syntax or what a parameter means; returns ids for avs_docs_read. source filters by "
                    "label, e.g. 'Studio filter', 'Studio', 'function', 'Deep Learning'.",
     "inputSchema": obj({"query": S, "limit": {"type": "integer", "default": 8}, "source": S}, ["query"])},
    {"name": "avs_docs_read",
     "description": "Full text of one section found by avs_docs_search (by id), in pages of `length` characters.",
     "inputSchema": obj({"id": {"type": "integer"}, "offset": {"type": "integer", "default": 0},
                         "length": {"type": "integer", "default": 6000}}, ["id"])},
    {"name": "avs_search_examples",
     "description": "Search the official .avcode Examples/Tutorials installed with Studio for a filter, formula function or syntax "
                    "(e.g. 'SaveText', 'toString', 'FitCircleToEdges') and show how it is written.",
     "inputSchema": obj({"query": S, "limit": {"type": "integer", "default": 5}}, ["query"])},
    {"name": "avs_filter_ports",
     "description": "Real Aurora Vision Studio port names of a filter (e.g. ScanSingleEdge -> inScanPath, outEdge.Point), "
                    "mined from every call in the official .avcode examples: in/out/diag kind, how often each port is "
                    "listed or hidden, sample literal values, example file:line. The AVL documentation (avs_filter_doc) "
                    "gives C++ parameter names that often differ from Studio ports; use this before writing a filter "
                    "call. Filters never used in the examples are not covered (close names are suggested).",
     "inputSchema": obj({"name": S}, ["name"])},
    {"name": "avs_validate",
     "description": "Load an .avproj with AuroraVisionAvexeGenerator (no GUI) and report errors. Use after every .avcode edit.",
     "inputSchema": obj({"project": S}, ["project"])},
    {"name": "avs_run",
     "description": "Run an .avproj headlessly with Aurora Vision Runtime (AuroraVisionConsole) until the program finishes, "
                    "e.g. after EnumerateImages has gone through a folder. Relative paths resolve from the project folder. "
                    "With task_dir: clears the task's results CSV first and runs avs_evaluate afterwards.",
     "inputSchema": obj({"project": S, "task_dir": S, "timeout": {"type": "integer", "default": 600}}, ["project"])},
    {"name": "avs_open",
     "description": "Open an .avproj in Aurora Vision Studio so the user can press Run. Waits until a Studio window shows the project; "
                    "errors if Studio is already running with another project (only one instance is allowed).",
     "inputSchema": obj({"project": S}, ["project"])},
    {"name": "avs_task_types",
     "description": "List the step types a task spec can use (measure, locate, count, defect, classify, read, ...) and their required fields.",
     "inputSchema": obj({}, [])},
    {"name": "avs_task_new",
     "description": "Create a task folder: task.toml skeleton with TODOs for the given step types, empty annotations.json, images/.",
     "inputSchema": obj({"task_dir": S, "step_types": {"type": "array", "items": S}, "name": S},
                        ["task_dir", "step_types"])},
    {"name": "avs_task_check",
     "description": "Check a task folder: missing/TODO fields, step references, images, annotations, expected.csv columns. Returns the spec.",
     "inputSchema": obj({"task_dir": S}, ["task_dir"])},
    {"name": "avs_annotate",
     "description": "Show the annotation page where the user marks each image of a task OK/NG and draws defects or "
                    "measurements (rect, polygon, line, circle, point) with a class and a description: inside the chat when the "
                    "host supports MCP Apps, otherwise in the browser. Save writes annotations.json, the verdict column of "
                    "expected.csv and annotated/*.png — read those PNGs to see exactly what the user marked.",
     "inputSchema": obj({"task_dir": S, "open_browser": {"type": "boolean", "default": True}}, ["task_dir"]),
     "_meta": {"ui": {"resourceUri": annotate.UI_URI}}},
    {"name": "avs_evaluate",
     "description": "Compare the program's results.csv with expected.csv of a task: numeric error vs tolerance, OK/NG escapes and "
                    "false rejects, reported separately for tune and holdout images. Numeric result columns not in expected.csv "
                    "(measured features) get a margin report: OK range, NG range, NG images closest to the OK range.",
     "inputSchema": obj({"task_dir": S, "results": S, "expected": S, "key": S,
                         "tolerances": {"type": "object", "additionalProperties": {"type": "number"}}}, ["task_dir"])},
    {"name": "avs_split",
     "description": "Mark about `fraction` of a task's images as holdout in expected.csv (column 'split', OK/NG ratio kept). "
                    "Tune the program on the other images only; avs_evaluate reports holdout accuracy separately.",
     "inputSchema": obj({"task_dir": S, "fraction": {"type": "number", "default": 0.3},
                         "seed": {"type": "integer", "default": 0}}, ["task_dir"])},
]


def call_tool(name, a):
    if name == "avs_search_filters":
        return search_filters(a.get("query", ""), int(a.get("limit", 30))), False
    if name == "avs_filter_doc":
        return filter_doc(a.get("name", "")), False
    if name == "avs_docs_search":
        return docs.search(a.get("query", ""), DOC_DIR, DL_DOC_DIR, int(a.get("limit", 8)), a.get("source", "")), False
    if name == "avs_docs_read":
        return docs.read(int(a.get("id", -1)), DOC_DIR, DL_DOC_DIR, int(a.get("offset", 0)), int(a.get("length", 6000)))
    if name == "avs_search_examples":
        return search_examples(a.get("query", ""), int(a.get("limit", 5))), False
    if name == "avs_filter_ports":
        return ports.filter_ports(a.get("name", ""), EXAMPLES_DIR)
    if name == "avs_validate":
        return validate(a.get("project", ""))
    if name == "avs_run":
        return run_project(a.get("project", ""), a.get("task_dir"), int(a.get("timeout", 600)))
    if name == "avs_open":
        return open_project(a.get("project", ""))
    if name == "avs_task_types":
        return task_spec.list_types(), False
    if name == "avs_task_new":
        return task_spec.new_task(a.get("task_dir", ""), a.get("step_types") or [], a.get("name", ""))
    if name == "avs_task_check":
        return task_spec.check_task(a.get("task_dir", ""))
    if name == "avs_annotate":
        return annotate_tool(a.get("task_dir", ""), bool(a.get("open_browser", True)), in_chat=CLIENT["ui"])
    if name == "avs_evaluate":
        return evaluate(a.get("task_dir", ""), a.get("results"), a.get("expected"), a.get("key"), a.get("tolerances"))
    if name == "avs_split":
        return split(a.get("task_dir", ""), float(a.get("fraction", 0.3)), int(a.get("seed", 0)))
    return f"Unknown tool: {name}", True


def get_prompt(name, args):
    if name != "describe_task":
        raise ValueError(f"Unknown prompt: {name}")
    text = DESCRIBE_TASK.format(task_dir=args.get("task_dir", "<ask the user>"))
    return {"description": PROMPTS[0]["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


CLIENT = {"ui": False}                                # does the host render MCP Apps (set by initialize)


def client_supports_ui(params):
    ext = ((params or {}).get("capabilities") or {}).get("extensions") or {}
    return annotate.UI_MIME in ((ext.get(annotate.UI_EXTENSION) or {}).get("mimeTypes") or [])


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        ver = msg.get("params", {}).get("protocolVersion", "2025-06-18")
        CLIENT["ui"] = client_supports_ui(msg.get("params"))
        result = {"protocolVersion": ver, "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                  "serverInfo": {"name": "avs", "version": VERSION}}
    elif method == "tools/list":
        # app-only tools serve the in-chat page; hosts without MCP Apps would show them to the model, so hide them there
        result = {"tools": TOOLS + (annotate.APP_TOOLS if CLIENT["ui"] else [])}
    elif method == "tools/call":
        p = msg.get("params", {})
        name, args = p.get("name"), p.get("arguments") or {}
        structured = None
        try:
            if name in annotate.APP_TOOL_NAMES:
                (text, structured), err = annotate.app_call(name, args), False
            else:
                text, err = call_tool(name, args)
        except Exception as e:  # tool errors go back to the assistant instead of killing the server
            text, err = f"{type(e).__name__}: {e}", True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
        if structured is not None:
            result["structuredContent"] = structured
    elif method == "resources/list":
        result = {"resources": [{"uri": annotate.UI_URI, "name": "avs_annotate_page", "mimeType": annotate.UI_MIME,
                                 "description": "Annotation page: mark images OK/NG and draw defects."}]}
    elif method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        if uri != annotate.UI_URI:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32002, "message": f"Resource not found: {uri}"}}
        result = {"contents": [annotate.ui_resource()]}
    elif method == "prompts/list":
        result = {"prompts": PROMPTS}
    elif method == "prompts/get":
        p = msg.get("params", {})
        try:
            result = get_prompt(p.get("name"), p.get("arguments") or {})
        except ValueError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(e)}}
    elif method == "ping":
        result = {}
    elif mid is None:
        return None                                   # notification (e.g. notifications/initialized)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
