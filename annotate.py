"""annotate.py — local web page to draw on a task's images and mark each image OK / NG.

Writes into the task folder:
  annotations.json  shapes per image ("images") + verdict, note and display levels per image ("image_info")
  expected.csv      key column + verdict column (OK / NG), other columns kept
  annotated/        each marked image rendered with its shapes, so the assistant can look at what was drawn

Standalone: python annotate.py <task_dir> [--port N] [--no-browser]
"""

import base64
import csv
import http.server
import json
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_spec  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "annotate.html")
MIME = {".png": "image/png", ".bmp": "image/bmp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".tif": "image/tiff", ".tiff": "image/tiff"}
VERDICT_TYPES = ("defect", "decide", "classify")
_servers = {}                                         # task_dir -> (server, url)


def clean(v, default):
    return default if v is None or task_spec.is_todo(v) else v


def task_paths(task_dir):
    spec = task_spec.load_task(task_dir) if os.path.exists(os.path.join(task_dir, "task.toml")) else {}
    img_dir = os.path.join(task_dir, str(clean(spec.get("task", {}).get("images"), "images/")))
    images = sorted(f for f in os.listdir(img_dir) if f.lower().endswith(task_spec.IMAGE_EXT)) \
        if os.path.isdir(img_dir) else []
    key = clean(spec.get("output", {}).get("key"), "image")
    exp_file = clean(spec.get("expected", {}).get("file"), "expected.csv")
    steps = spec.get("steps", [])
    verdict_col = next((st["column"] for st in steps if st.get("type") in VERDICT_TYPES
                        and isinstance(st.get("column"), str) and not task_spec.is_todo(st["column"])), "result")
    classes = []
    for st in steps:
        for c in st.get("classes", []) if isinstance(st.get("classes"), list) else []:
            if isinstance(c, str) and not task_spec.is_todo(c) and c not in classes:
                classes.append(c)
    return {"spec": spec, "img_dir": img_dir, "images": images, "key": key, "exp_file": exp_file,
            "verdict_col": verdict_col, "classes": classes,
            "steps": [st.get("id") for st in steps if isinstance(st.get("id"), str) and not task_spec.is_todo(st["id"])],
            "features": [ft.get("id") for ft in spec.get("features", []) if isinstance(ft.get("id"), str)]}


def load_annotations(task_dir):
    path = os.path.join(task_dir, "annotations.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"images": {}}


def read_expected(path, key):
    if not os.path.exists(path):
        return [], {}
    rows = task_spec.read_csv(path)
    return (list(rows[0].keys()) if rows else [key]), {r.get(key): r for r in rows}


def task_state(task_dir):
    p = task_paths(task_dir)
    ann = load_annotations(task_dir)
    _, exp = read_expected(os.path.join(task_dir, p["exp_file"]), p["key"])
    classes = list(p["classes"])
    for items in ann.get("images", {}).values():
        for it in items:
            if it.get("class") and it["class"] not in classes:
                classes.append(it["class"])
    info = ann.get("image_info", {})
    for img, row in exp.items():                      # verdicts typed into expected.csv by hand
        v = (row.get(p["verdict_col"]) or "").strip()
        if img in p["images"] and v in ("OK", "NG") and not info.get(img, {}).get("verdict"):
            info.setdefault(img, {})["verdict"] = v
    name = clean(p["spec"].get("task", {}).get("name"), os.path.basename(os.path.abspath(task_dir)))
    return {"name": name, "task_dir": os.path.abspath(task_dir), "images": p["images"], "key": p["key"],
            "verdict_col": p["verdict_col"], "expected_file": p["exp_file"], "classes": classes,
            "steps": p["steps"], "features": p["features"],
            "annotations": ann.get("images", {}), "image_info": info}


def save(task_dir, body):
    p = task_paths(task_dir)
    known = set(p["images"])
    shapes = {img: items for img, items in (body.get("images") or {}).items() if img in known and items}
    for img, items in shapes.items():
        for it in items:
            if it.get("kind") not in task_spec.ANNOTATION_KINDS:
                raise ValueError(f"{img}: unknown kind {it.get('kind')!r}")
    info = {img: {k: v for k, v in i.items() if v not in ("", None, {})}
            for img, i in (body.get("image_info") or {}).items() if img in known}
    info = {img: i for img, i in info.items() if i}

    ann = load_annotations(task_dir)
    ann["images"], ann["image_info"] = shapes, info
    with open(os.path.join(task_dir, "annotations.json"), "w", encoding="utf-8") as f:
        json.dump(ann, f, indent=2, ensure_ascii=False)

    key, col = p["key"], p["verdict_col"]
    exp_path = os.path.join(task_dir, p["exp_file"])
    fields, rows = read_expected(exp_path, key)
    verdicts = {img: i["verdict"] for img, i in info.items() if i.get("verdict") in ("OK", "NG")}
    n_exp = 0
    if verdicts or rows:
        fields = fields or [key]
        if col not in fields:
            fields.append(col)
        for img in p["images"]:
            if img in verdicts:
                rows.setdefault(img, {key: img})[col] = verdicts[img]
            elif img in rows:
                rows[img][col] = ""
        ordered = [rows[img] for img in p["images"] if img in rows] + \
                  [r for k, r in rows.items() if k not in known]
        ordered = [r for r in ordered if any((r.get(c) or "").strip() for c in fields if c != key)]
        with open(exp_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(ordered)
        n_exp = len(ordered)

    out_dir = os.path.join(task_dir, "annotated")
    renders = {img: d for img, d in (body.get("renders") or {}).items() if img in known}
    if os.path.isdir(out_dir):                        # drop renders of images that are no longer marked
        for f in os.listdir(out_dir):
            if f.endswith(".png") and not any(os.path.splitext(img)[0] + ".png" == f for img in renders):
                os.remove(os.path.join(out_dir, f))
    if renders:
        os.makedirs(out_dir, exist_ok=True)
    for img, data in renders.items():
        with open(os.path.join(out_dir, os.path.splitext(img)[0] + ".png"), "wb") as f:
            f.write(base64.b64decode(data.split(",", 1)[-1]))

    n_ng = sum(1 for v in verdicts.values() if v == "NG")
    return (f"Saved {sum(len(v) for v in shapes.values())} shape(s) on {len(shapes)} image(s); "
            f"verdicts {len(verdicts) - n_ng} OK / {n_ng} NG in {p['exp_file']} ({n_exp} row(s)); "
            f"{len(renders)} image(s) in annotated/.")


def make_handler(task_dir):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):                 # stdout belongs to the MCP protocol
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, code, obj):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                with open(PAGE, "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/task":
                return self.send_json(200, task_state(task_dir))
            if path.startswith("/img/"):
                from urllib.parse import unquote
                name = unquote(path[5:])
                p = task_paths(task_dir)
                if name in p["images"]:
                    with open(os.path.join(p["img_dir"], name), "rb") as f:
                        return self.send(200, f.read(), MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))
            self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/save":
                return self.send(404, b"not found", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                self.send_json(200, {"ok": True, "message": save(task_dir, body)})
            except Exception as e:
                self.send_json(400, {"ok": False, "message": f"{type(e).__name__}: {e}"})

    return Handler


def serve(task_dir, port=0, open_browser=True):
    """Start (or reuse) the page for a task folder in a background thread; returns its URL."""
    task_dir = os.path.abspath(task_dir)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"Task folder not found: {task_dir}")
    if task_dir not in _servers:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(task_dir))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _servers[task_dir] = (server, f"http://127.0.0.1:{server.server_address[1]}/")
    url = _servers[task_dir][1]
    if open_browser:
        webbrowser.open(url)
    return url


def annotate_tool(task_dir, open_browser=True):
    p = task_paths(task_dir)
    if not p["images"]:
        return f"No images in {p['img_dir']}. Put the sample images there first.", True
    url = serve(task_dir, open_browser=open_browser)
    return (f"Annotation page for {len(p['images'])} image(s): {url}\n"
            f"Ask the user to mark each image OK/NG, draw every defect (rect/polygon/line/point/circle) with a class and a "
            f"description, then press Save. Verdicts go to {p['exp_file']} (column '{p['verdict_col']}'), shapes to "
            f"annotations.json, rendered images to annotated/ — open those PNGs to see exactly what was marked."), False


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task_dir")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    print(serve(a.task_dir, a.port, not a.no_browser), flush=True)
    threading.Event().wait()
