"""python -m unittest discover -s tests   (from the repo root)"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import avs_mcp  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "hole_distance")


def call(tool, **args):
    r = avs_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": args}})["result"]
    return r["content"][0]["text"], r["isError"]


def same_version_runtime(folder):
    """A fake Runtime folder whose name carries the version of the detected Studio (run_project refuses a mismatch)."""
    return os.path.join(folder, f"Aurora Vision Studio {avs_mcp.version_of(avs_mcp.AVS_DIR)} Runtime")


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Protocol(unittest.TestCase):
    def test_initialize_and_lists(self):
        r = avs_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertIn("prompts", r["result"]["capabilities"])
        tools = avs_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertIn("avs_evaluate", [t["name"] for t in tools])
        self.assertIsNone(avs_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_prompt(self):
        r = avs_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                            "params": {"name": "describe_task", "arguments": {"task_dir": "C:/x"}}})
        self.assertIn("C:/x", r["result"]["messages"][0]["content"]["text"])
        r = avs_mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "nope"}})
        self.assertIn("error", r)

    def test_stdio(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "avs_mcp.py")], capture_output=True, text=True,
                           encoding="utf-8", timeout=30,
                           input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        self.assertEqual(json.loads(p.stdout)["id"], 1)

    def test_unknown_tool(self):
        self.assertTrue(call("nope")[1])

    def test_null_params_keep_the_id(self):
        for method in ("initialize", "tools/call", "prompts/get", "resources/read"):
            r = avs_mcp.handle({"jsonrpc": "2.0", "id": 7, "method": method, "params": None})
            self.assertEqual(r["id"], 7, method)
        lines = [{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": None}, "not json", [1, 2],
                 "[" * 100000, {"jsonrpc": "2.0", "id": 6, "method": "ping"}]          # still alive after deep JSON
        p = subprocess.run([sys.executable, os.path.join(ROOT, "avs_mcp.py")], capture_output=True, text=True,
                           encoding="utf-8", timeout=30,
                           input="".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))
        out = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(out[0]["id"], 5)
        self.assertTrue(out[0]["result"]["isError"])                                 # no tool name: tool error
        self.assertEqual(out[1]["error"]["code"], -32700)                            # parse error
        self.assertEqual(out[2]["error"]["code"], -32600)                            # not a request object
        self.assertEqual(out[3]["error"]["code"], -32700)                            # nesting too deep
        self.assertEqual(out[4]["id"], 6)


class Spec(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_types(self):
        text, err = call("avs_task_types")
        for t in ("measure", "defect", "classify", "read", "decide", "custom"):
            self.assertIn(f"- {t} ", text)

    def test_new_then_check_reports_todos(self):
        text, err = call("avs_task_new", task_dir=self.dir, step_types=["locate", "defect", "decide"], name="Demo")
        self.assertFalse(err, text)
        self.assertTrue(call("avs_task_new", task_dir=self.dir, step_types=["measure"])[1])   # no overwrite
        text, err = call("avs_task_check", task_dir=self.dir)
        self.assertTrue(err)
        self.assertIn("still TODO", text)
        self.assertIn("no images", text)
        self.assertNotIn("'name' still TODO", text)
        self.assertIn("no ground truth", text)
        write(os.path.join(self.dir, "expected.csv"), "image,result\n")             # [expected] file left commented out
        text, _ = call("avs_task_check", task_dir=self.dir)
        self.assertNotIn("no ground truth", text)                                    # found like avs_evaluate does

    def test_unknown_type(self):
        text, err = call("avs_task_new", task_dir=self.dir, step_types=["teleport"])
        self.assertTrue(err)
        self.assertIn("measure", text)

    def test_example_is_complete(self):
        img_dir = os.path.join(EXAMPLE, "images")
        if not any(f.endswith(".png") for f in os.listdir(img_dir)):
            self.skipTest("example images are not in the repository; add your own to examples/hole_distance/images")
        text, err = call("avs_task_check", task_dir=EXAMPLE)
        self.assertFalse(err, text)
        self.assertIn("OK", text.splitlines()[0])

    def test_bad_references(self):
        os.makedirs(os.path.join(self.dir, "images"))
        write(os.path.join(self.dir, "images", "a.png"), "")
        write(os.path.join(self.dir, "task.toml"), """
[task]
name = "x"
images = "images/"
[output]
results_csv = "r.csv"
key = "image"
[[steps]]
id = "m"
type = "measure"
method = "magic"
after = ["ghost"]
from = "a"
to = "b"
column = "d"
unit = "mm"
tolerance = 0.1
""")
        text, err = call("avs_task_check", task_dir=self.dir)
        self.assertTrue(err)
        self.assertIn("rule, deep or hybrid", text)
        self.assertIn("unknown step 'ghost'", text)
        self.assertIn("mm_per_px", text)


class Evaluate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write(os.path.join(self.dir, "task.toml"), """
[task]
name = "e"
images = "images/"
[output]
results_csv = "results.csv"
key = "image"
[expected]
file = "expected.csv"
[[steps]]
id = "m"
type = "measure"
method = "rule"
from = "a"
to = "b"
column = "d"
unit = "px"
tolerance = 0.5
[[steps]]
id = "v"
type = "decide"
method = "rule"
rule = "x"
column = "result"
""")
        write(os.path.join(self.dir, "expected.csv"),
              "image,d,result\na.png,10.0,OK\nb.png,20.0,NG\nc.png,30.0,OK\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_pass(self):
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\nc,30.4,OK\n")
        text, _ = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("PASS"), text)
        self.assertIn("within 3/3", text)

    def test_fail(self):
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na.png,10.9,OK\nb.png,20.0,OK\n")
        text, _ = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("FAIL"), text)
        self.assertIn("No result for 1", text)
        self.assertIn("WRONG a.png", text)
        self.assertIn("escapes (NG passed): 1/1", text)

    def test_missing_file(self):
        self.assertTrue(call("avs_evaluate", task_dir=self.dir)[1])

    def test_no_holdout_warning(self):
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\nc,30.4,OK\n")
        self.assertIn("No holdout images", call("avs_evaluate", task_dir=self.dir)[0])

    def test_fail_is_an_error(self):                                                 # agents key on isError
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.9,OK\nb,20.0,NG\nc,30.0,OK\n")
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("FAIL") and err, text)
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\nc,30.4,OK\n")
        self.assertFalse(call("avs_evaluate", task_dir=self.dir)[1])

    def test_blank_expected_cells_are_skipped(self):
        # no measurement on the NG part, c not marked yet: blanks are "no ground truth", not values to match
        write(os.path.join(self.dir, "expected.csv"), "image,d,result\na.png,10.0,OK\nb.png,,NG\nc.png,30.0,\n")
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,5,NG\nc,30.4,OK\n")
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("PASS") and not err, text)
        self.assertIn("[d] numeric, 2 image(s)", text)                              # tolerance applied, b skipped
        self.assertIn("[result] categorical: 2/2 correct", text)                    # c skipped
        self.assertIn("escapes (NG passed): 0/1", text)                             # OK/NG report kept

    def test_short_rows_and_no_ground_truth(self):
        write(os.path.join(self.dir, "expected.csv"), "image,d,result\na.png,10.0,OK\nb.png,20.0,NG\nc.png\n")
        write(os.path.join(self.dir, "results.csv"), "image,d,result,gap\na,10.1,OK,1\nb,19.8,NG,9\nc,30.4,OK,2\n")
        text, err = call("avs_evaluate", task_dir=self.dir)                          # c.png row has no cells: None
        self.assertTrue(text.startswith("PASS") and not err, text)
        self.assertIn("Margins for [result]", text)
        write(os.path.join(self.dir, "expected.csv"), "image,d,result\na.png,,\nb.png,,\nc.png,,\n")
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("FAIL") and err, text)
        self.assertIn("No ground truth", text)

    def test_all_holdout_does_not_fail_on_empty_tune(self):
        write(os.path.join(self.dir, "expected.csv"),
              "image,d,result,split\na.png,10.0,OK,holdout\nb.png,20.0,NG,holdout\nc.png,30.0,OK,holdout\n")
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\nc,30.4,OK\n")
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("PASS") and not err, text)
        self.assertNotIn("not in results.csv", text)

    def test_split_keeps_tune_images(self):
        write(os.path.join(self.dir, "expected.csv"), "image,result\na.png,OK\nb.png,OK\nc.png,NG\nd.png,NG\ne.png,\n")
        text, err = call("avs_split", task_dir=self.dir, fraction=0.9)
        self.assertFalse(err, text)
        rows = avs_mcp.task_spec.read_csv(os.path.join(self.dir, "expected.csv"))
        for v in ("OK", "NG"):                                                       # never a whole class held out
            self.assertIn("tune", [r["split"] for r in rows if r["result"] == v])

    def test_run_refuses_results_outside_the_task(self):
        victim = os.path.join(tempfile.mkdtemp(), "victim.csv")
        write(victim, "keep me")
        write(os.path.join(self.dir, "task.toml"), f'[task]\nname = "e"\n[output]\nresults_csv = {json.dumps(victim)}\n')
        proj = os.path.join(self.dir, "p.avproj")
        write(proj, "<AuroraVisionProject />")
        with unittest.mock.patch.object(avs_mcp, "RUNTIME_DIR", same_version_runtime(self.dir)), \
                unittest.mock.patch("os.path.exists", lambda p, real=os.path.exists: True if p.endswith(
                    "AuroraVisionConsole.exe") else real(p)):
            text, err = call("avs_run", project=proj, task_dir=self.dir)
        self.assertTrue(err)
        self.assertIn("inside the task folder", text)
        self.assertTrue(os.path.exists(victim))                                      # not deleted
        shutil.rmtree(os.path.dirname(victim))
        write(os.path.join(self.dir, "task.toml"), '[task]\nname = "e"\n[output]\nresults_csv = "expected.csv"\n')
        with unittest.mock.patch.object(avs_mcp, "RUNTIME_DIR", same_version_runtime(self.dir)), \
                unittest.mock.patch("os.path.exists", lambda p, real=os.path.exists: True if p.endswith(
                    "AuroraVisionConsole.exe") else real(p)):
            text, err = call("avs_run", project=proj, task_dir=self.dir)
        self.assertTrue(err)
        self.assertIn("ground-truth file", text)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "expected.csv")))      # ground truth kept

    def test_run_reports_a_failed_evaluation(self):
        proj = os.path.join(self.dir, "p.avproj")
        write(proj, "<AuroraVisionProject />")

        def fake_run(cmd, **kw):                                                     # generator, then the program
            if cmd[0].endswith("AuroraVisionAvexeGenerator.exe"):
                write(cmd[-1], "exe")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.9,OK\nb,20.0,NG\nc,30.0,OK\n")
            return subprocess.CompletedProcess(cmd, 0, "Program successfully finished.", "")
        real_exists = os.path.exists
        with unittest.mock.patch.object(avs_mcp.subprocess, "run", fake_run), \
                unittest.mock.patch("os.path.exists", lambda p: True if p.endswith("AuroraVisionConsole.exe")
                                    else real_exists(p)):
            text, err = call("avs_run", project=proj, task_dir=self.dir)
        self.assertIn("FAIL", text)
        self.assertIn("WRONG a", text)
        self.assertTrue(err, text)                                                   # a failing run is an error

    def test_rows_longer_than_the_header(self):
        write(os.path.join(self.dir, "expected.csv"), "image,d,result\na.png,10.0,OK,extra\nb.png,20.0,NG\n")
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\n")
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("PASS") and not err, text)
        self.assertFalse(call("avs_split", task_dir=self.dir, fraction=0.5)[1])

    def test_split_refuses_an_expected_file_outside(self):
        out = tempfile.mkdtemp()
        write(os.path.join(out, "v.csv"), "image,result\na.png,OK\nb.png,NG\n")
        write(os.path.join(self.dir, "task.toml"), f'[task]\nname = "e"\n[expected]\nfile = {json.dumps(os.path.join(out, "v.csv"))}\n')
        text, err = call("avs_split", task_dir=self.dir)
        self.assertTrue(err)
        self.assertNotIn("split", open(os.path.join(out, "v.csv"), encoding="utf-8").read())
        shutil.rmtree(out)


class Margins(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write(os.path.join(self.dir, "expected.csv"),
              "image,result\n" + "".join(f"ok{i}.png,OK\n" for i in range(4)) + "ng0.png,NG\nng1.png,NG\nng2.png,NG\n")
        # gap: OK 1..2, ng0 just above, ng1 far above, ng2 inside the OK range
        write(os.path.join(self.dir, "results.csv"),
              "image,result,gap,area\nok0,OK,1.0,50\nok1,OK,1.5,52\nok2,OK,2.0,51\nok3,OK,1.2,49\n"
              "ng0,NG,2.3,50\nng1,NG,9.0,50\nng2,NG,1.4,51\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_margin_report(self):
        text, err = call("avs_evaluate", task_dir=self.dir)
        self.assertFalse(err, text)
        self.assertIn("gap: OK 1..2, NG 1.4..9", text)
        lines = text.splitlines()
        ng0 = next(l for l in lines if l.strip().startswith("ng0.png"))
        self.assertIn("CLOSE", ng0)
        self.assertNotIn("CLOSE", next(l for l in lines if l.strip().startswith("ng1.png")))
        self.assertIn("not separated by any measured value): ng2.png", text)
        self.assertIn("Only 4 OK image(s)", text)

    def test_split_then_holdout_section(self):
        text, err = call("avs_split", task_dir=self.dir, fraction=0.5, seed=1)
        self.assertFalse(err, text)
        rows = avs_mcp.task_spec.read_csv(os.path.join(self.dir, "expected.csv"))
        hold = [r for r in rows if r["split"] == "holdout"]
        self.assertEqual(sum(r["result"] == "OK" for r in hold), 2)       # OK/NG ratio kept
        self.assertEqual(sum(r["result"] == "NG" for r in hold), 2)
        self.assertEqual(call("avs_split", task_dir=self.dir, fraction=0.5, seed=1)[0], text)   # repeatable
        text, _ = call("avs_evaluate", task_dir=self.dir)
        self.assertIn("=== TUNE images (3)", text)
        self.assertIn("=== HOLDOUT images (4)", text)
        self.assertNotIn("No holdout images", text)

    def test_split_needs_expected(self):
        os.remove(os.path.join(self.dir, "expected.csv"))
        self.assertTrue(call("avs_split", task_dir=self.dir)[1])


class Annotate(unittest.TestCase):
    PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg==")

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "images"))
        for n in ("a.png", "b.png"):
            with open(os.path.join(self.dir, "images", n), "wb") as f:
                f.write(self.PNG)
        write(os.path.join(self.dir, "expected.csv"), "image,width\na.png,12\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def get(self, url):
        with urllib.request.urlopen(url) as r:
            return r.read()

    def post(self, url, body):
        req = urllib.request.Request(url + "api/save", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return json.loads(e.read())

    def test_foreign_requests_are_refused(self):
        url = re.search(r"http://\S+/", call("avs_annotate", task_dir=self.dir, open_browser=False)[0]).group(0)
        port = urllib.parse.urlsplit(url).port

        def status(path, headers, body=None):
            req = urllib.request.Request(url + path, body, headers, method="POST" if body is not None else "GET")
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                with e:
                    return e.code
        save = json.dumps({"images": {}, "image_info": {"a.png": {"verdict": "NG"}}}).encode()
        self.assertEqual(status("api/task", {"Host": f"rebind.example:{port}"}), 403)          # DNS rebinding
        self.assertEqual(status("api/save", {"Origin": "http://evil.example", "Content-Type": "text/plain"},
                                save), 403)                                                   # cross-site POST
        self.assertEqual(status("api/remove?name=a.png", {"Sec-Fetch-Site": "cross-site"}, b""), 403)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "images", "a.png")))
        self.assertNotIn("NG", open(os.path.join(self.dir, "expected.csv"), encoding="utf-8").read())
        self.assertEqual(status("api/save", {"Origin": f"http://127.0.0.1:{port}",                # the page itself
                                             "Sec-Fetch-Site": "same-origin"}, save), 200)
        self.assertEqual(status("api/task", {}), 200)
        for bad in ("-1", "abc"):                                                    # no hang, no traceback
            self.assertEqual(status("api/save", {"Content-Length": bad}, b""), 400)

    def test_task_paths_stay_inside(self):
        write(os.path.join(self.dir, "task.toml"), '[task]\nname = "a"\n[expected]\nfile = "task.toml"\n')
        with self.assertRaises(ValueError):                                          # Save would overwrite it
            avs_mcp.annotate.task_paths(self.dir)
        write(os.path.join(self.dir, "task.toml"), '[task]\nname = "a"\n[expected]\nfile = "../escaped.csv"\n')
        with self.assertRaises(ValueError):
            avs_mcp.annotate.task_paths(self.dir)
        write(os.path.join(self.dir, "task.toml"), '[task]\nname = "a"\nimages = "C:/Windows"\n')
        with self.assertRaises(ValueError):
            avs_mcp.annotate.task_paths(self.dir)

    def test_page_task_and_save(self):
        text, err = call("avs_annotate", task_dir=self.dir, open_browser=False)
        self.assertFalse(err, text)
        url = re.search(r"http://\S+/", text).group(0)
        self.assertIn(b"AVS Annotate", self.get(url))
        state = json.loads(self.get(url + "api/task"))
        self.assertEqual(state["images"], ["a.png", "b.png"])
        self.assertEqual(state["verdict_col"], "result")
        self.assertEqual(self.get(url + "img/a.png"), self.PNG)

        r = self.post(url, {
            "images": {"b.png": [{"kind": "rect", "points": [[0, 0], [1, 1]], "class": "scratch", "label": "long scratch"}]},
            "image_info": {"a.png": {"verdict": "OK"}, "b.png": {"verdict": "NG", "note": "x", "levels": [0, 40]}},
            "renders": {"b.png": "data:image/png;base64," + base64.b64encode(self.PNG).decode()}})
        self.assertTrue(r["ok"], r)
        with open(os.path.join(self.dir, "annotations.json"), encoding="utf-8") as f:
            ann = json.load(f)
        self.assertEqual(ann["images"]["b.png"][0]["class"], "scratch")
        self.assertEqual(ann["image_info"]["b.png"]["levels"], [0, 40])
        rows = {r["image"]: r for r in avs_mcp.task_spec.read_csv(os.path.join(self.dir, "expected.csv"))}
        self.assertEqual((rows["a.png"]["width"], rows["a.png"]["result"], rows["b.png"]["result"]), ("12", "OK", "NG"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "annotated", "b.png")))

        r = self.post(url, {"images": {"a.png": [{"kind": "blob", "points": []}]}})
        self.assertFalse(r["ok"])
        self.assertIn("unknown kind", r["message"])

    def test_upload(self):
        shutil.rmtree(os.path.join(self.dir, "images"))              # page opens on an empty task
        text, err = call("avs_annotate", task_dir=self.dir, open_browser=False)
        self.assertFalse(err, text)
        url = re.search(r"http://\S+/", text).group(0)

        def up(name, data):
            req = urllib.request.Request(url + "api/upload?name=" + urllib.parse.quote(name), data, method="POST")
            try:
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    return json.loads(e.read())

        self.assertEqual(up("cam 1.png", self.PNG)["message"], "added")
        self.assertEqual(up("cam 1.png", self.PNG)["message"], "already in the task")
        r = up("cam 1.png", self.PNG + b"x")                           # same name, other content
        self.assertEqual(r["name"], "cam 1_1.png")
        self.assertFalse(up("..\\evil.txt", b"x")["ok"])
        self.assertEqual(up("..\\..\\up.bmp", b"BM")["name"], "up.bmp")   # folders in the name are dropped
        self.assertEqual(json.loads(self.get(url + "api/task"))["images"], ["cam 1.png", "cam 1_1.png", "up.bmp"])

    def test_remove(self):
        url = re.search(r"http://\S+/", call("avs_annotate", task_dir=self.dir, open_browser=False)[0]).group(0)
        self.post(url, {"images": {"a.png": [{"kind": "point", "points": [[0, 0]], "label": ""}]},
                        "image_info": {"a.png": {"verdict": "NG"}, "b.png": {"verdict": "OK"}},
                        "renders": {"a.png": "data:image/png;base64," + base64.b64encode(self.PNG).decode()}})

        def rm(name):
            req = urllib.request.Request(url + "api/remove?name=" + urllib.parse.quote(name), b"", method="POST")
            try:
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    return json.loads(e.read())

        self.assertTrue(rm("a.png")["ok"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".removed", "a.png")))       # moved, not deleted
        self.assertFalse(os.path.exists(os.path.join(self.dir, "annotated", "a.png")))
        with open(os.path.join(self.dir, "annotations.json"), encoding="utf-8") as f:
            ann = json.load(f)
        self.assertNotIn("a.png", ann["images"])
        self.assertEqual(list(ann["image_info"]), ["b.png"])
        rows = avs_mcp.task_spec.read_csv(os.path.join(self.dir, "expected.csv"))
        self.assertEqual([r["image"] for r in rows], ["b.png"])
        self.assertFalse(rm("a.png")["ok"])                                                  # no longer in the task
        self.assertFalse(rm("../task.toml")["ok"])
        self.assertEqual(json.loads(self.get(url + "api/task"))["images"], ["b.png"])


def rpc(method, params=None):
    return avs_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


UI_INIT = {"protocolVersion": "2025-06-18",
           "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}}}


class McpApp(unittest.TestCase):
    """The annotation page as an MCP App: resource, app-only tools, host capability check."""
    PNG = Annotate.PNG

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "images"))
        for n in ("a.png", "b.png"):
            with open(os.path.join(self.dir, "images", n), "wb") as f:
                f.write(self.PNG)

    def tearDown(self):
        rpc("initialize", {"protocolVersion": "2025-06-18"})          # back to a host without MCP Apps
        shutil.rmtree(self.dir)

    def app(self, tool, **args):
        return rpc("tools/call", {"name": tool, "arguments": dict(task_dir=self.dir, **args)})["result"]

    def test_host_without_ui(self):
        caps = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]["capabilities"]
        self.assertIn("resources", caps)
        tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
        self.assertFalse([n for n in tools if n.startswith("avs_ui_")])          # not shown to the model
        self.assertEqual(tools["avs_annotate"]["_meta"]["ui"]["resourceUri"], "ui://avs/annotate.html")
        text, err = call("avs_annotate", task_dir=self.dir, open_browser=False)
        self.assertNotIn("appear in the chat", text)

    def test_host_with_ui(self):
        rpc("initialize", UI_INIT)
        tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
        app_tools = [n for n in tools if n.startswith("avs_ui_")]
        self.assertEqual(len(app_tools), 5)
        for n in app_tools:
            self.assertEqual(tools[n]["_meta"]["ui"]["visibility"], ["app"])
        res = rpc("resources/list")["result"]["resources"]
        self.assertEqual(res[0]["uri"], "ui://avs/annotate.html")
        page = rpc("resources/read", {"uri": "ui://avs/annotate.html"})["result"]["contents"][0]
        self.assertEqual(page["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("ui/initialize", page["text"])
        self.assertIn("error", rpc("resources/read", {"uri": "ui://avs/nope.html"}))
        text, err = call("avs_annotate", task_dir=self.dir, open_browser=False)
        self.assertFalse(err, text)
        self.assertIn("should appear in the chat", text)

    def test_browser_fallback_when_host_does_not_render(self):
        # Claude Code announces MCP Apps but never shows the page: no app-tool call -> open the browser after a delay
        rpc("initialize", UI_INIT)
        opened = []
        with unittest.mock.patch.object(avs_mcp.annotate, "UI_FALLBACK_S", 0.2), \
             unittest.mock.patch.object(avs_mcp.annotate.webbrowser, "open", opened.append):
            call("avs_annotate", task_dir=self.dir)
            time.sleep(0.5)
            self.assertEqual(len(opened), 1)                     # nothing rendered it
            opened.clear()
            call("avs_annotate", task_dir=self.dir)
            self.app("avs_ui_task")                              # the page connected in time
            time.sleep(0.5)
            self.assertEqual(opened, [])

    def test_app_tools(self):
        rpc("initialize", UI_INIT)
        r = self.app("avs_ui_task")
        self.assertFalse(r["isError"], r)
        self.assertEqual(r["structuredContent"]["images"], ["a.png", "b.png"])
        img = self.app("avs_ui_image", name="a.png")["structuredContent"]
        self.assertEqual((img["mimeType"], base64.b64decode(img["data"])), ("image/png", self.PNG))
        self.assertTrue(self.app("avs_ui_image", name="../task.toml")["isError"])

        r = self.app("avs_ui_upload", name="c.png", data=base64.b64encode(self.PNG + b"c").decode())
        self.assertEqual(r["structuredContent"]["name"], "c.png")
        r = self.app("avs_ui_save", images={"c.png": [{"kind": "point", "points": [[0, 0]], "label": "dot"}]},
                     image_info={"a.png": {"verdict": "OK"}, "c.png": {"verdict": "NG"}}, renders={})
        self.assertFalse(r["isError"], r)
        rows = {r["image"]: r["result"] for r in avs_mcp.task_spec.read_csv(os.path.join(self.dir, "expected.csv"))}
        self.assertEqual(rows, {"a.png": "OK", "c.png": "NG"})
        self.assertFalse(self.app("avs_ui_remove", name="c.png")["isError"])
        self.assertEqual(self.app("avs_ui_task")["structuredContent"]["images"], ["a.png", "b.png"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".removed", "c.png")))


class Docs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (avs_mcp.docs.CACHE, avs_mcp.docs._index)
        avs_mcp.docs.CACHE, avs_mcp.docs._index = os.path.join(self.dir, "cache"), None
        os.makedirs(os.path.join(self.dir, "cache"))
        os.makedirs(os.path.join(self.dir, "doc", "functions"))
        write(os.path.join(self.dir, "doc", "functions", "ScanSingleEdge.html"),
              "<html><title>ScanSingleEdge</title><body><p>Locates the strongest edge along a scan path.</p></body></html>")
        with open(os.path.join(self.dir, "cache", "studio_9.9_sections.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"title": "Coalescing operator", "path": "Formulas > Coalescing operator", "page": 7,
                                "text": "Coalescing operator: a ?? b returns b when a is Nil (an optional value)."}) + "\n")

    def tearDown(self):
        avs_mcp.docs.CACHE, avs_mcp.docs._index = self.saved
        shutil.rmtree(self.dir)

    def test_search_and_read(self):
        doc = os.path.join(self.dir, "doc")
        text = avs_mcp.docs.search("nil optional coalescing", doc, None)
        self.assertIn("Studio 9.9 manual | Formulas > Coalescing operator (page 7)", text)
        self.assertIn("scan single edge", " ".join(avs_mcp.docs.tokens("ScanSingleEdge")))   # camel case split
        self.assertIn("ScanSingleEdge", avs_mcp.docs.search("strongest edge", doc, None, source="function"))
        doc_id = int(re.search(r"\[(\d+)\] Studio", text).group(1))
        body, err = avs_mcp.docs.read(doc_id, doc, None)
        self.assertFalse(err)
        self.assertIn("a ?? b returns b", body)
        self.assertTrue(avs_mcp.docs.read(99, doc, None)[1])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "cache", "docs_index.json")))   # cached for next start

    def test_studio_filter_pages(self):
        doc = os.path.join(self.dir, "doc")
        avs_mcp.docs.search("edge", doc, None)                  # index built before the pages exist
        pages = os.path.join(self.dir, "cache", "studio_filters")
        os.makedirs(os.path.join(pages, "1DEdgeDetection"))
        write(os.path.join(pages, "AllFilters.html"),
              '<html><title>All Filters</title><body><a href="1DEdgeDetection/ScanSingleEdge.html">inScanPath</a>'
              " list of every filter, long enough to be indexed if it were not skipped</body></html>")
        write(os.path.join(pages, "1DEdgeDetection", "ScanSingleEdge.html"),
              "<html><head><title>ScanSingleEdge (ScanEdges Single) - Aurora Vision</title></head><body>"
              '<ul id="main-navigation"><li><a>Machine Vision Guide</a></li></ul>'
              "<h1>ScanSingleEdge (ScanEdges Single)</h1><table>\n<tr>\n<td><img alt='in'/></td>\n<td>inScanPath</td>\n"
              "<td>Path</td>\n<td>Path along which the scan is performed</td>\n</tr>\n<tr>\n<td></td><td>inScanWidth</td>"
              "<td>Integer</td><td>1 - </td><td>Width of the scan field in pixels</td></tr></table>"
              "<p>This article is valid for version 9.9.1</p></body></html>")
        text = avs_mcp.docs.search("inScanPath ScanSingleEdge", doc, None)   # new source folder -> index rebuilt
        self.assertRegex(text, r"\[\d+\] Studio filter \| ScanSingleEdge \(ScanEdges Single\) \(1DEdgeDetection")
        self.assertNotIn("All Filters", text)
        doc_id = int(re.search(r"\[(\d+)\] Studio filter", text).group(1))
        body, _ = avs_mcp.docs.read(doc_id, doc, None)
        self.assertIn("inScanPath | Path | Path along which the scan is performed |", body)  # one row per line
        self.assertNotIn("Machine Vision Guide", body)                                    # site menu dropped
        self.assertNotIn("valid for version", body)
        self.assertNotIn("- Aurora Vision", body)                                         # <head> title dropped
        os.makedirs(os.path.join(doc, "functions", "1DEdgeDetection"))
        write(os.path.join(doc, "functions", "1DEdgeDetection", "ScanSingleEdge.html"),
              "<html><body><p>Locates the strongest edge (C++).</p></body></html>")
        with unittest.mock.patch.object(avs_mcp, "FUNC_DIR", os.path.join(doc, "functions")), \
                unittest.mock.patch.object(avs_mcp, "_index", None):
            page = avs_mcp.filter_doc("ScanSingleEdge")
        self.assertTrue(page.startswith("Studio Filter Reference (1DEdgeDetection"))
        self.assertIn("inScanWidth | Integer | 1 - | Width of the scan field", page)
        self.assertIn("AVL library function", page)                                      # C++ page appended
        self.assertIn("strongest edge", page)


PROGRAM = """//:USETYPE WORKERTASK
public Main()
{

section PROCESS:
\t//:HIDDENPORT[inScanWidth] TRUE
\t//:HIDDENPORT[diagSamplingStep] TRUE
\tScanSingleEdge
\t(
\t\tinImage: con_93c89ba6_a3bb_400e_8f0f_0196254989fa
\t\tinScanWidth: 5
\t\tinEdgeScanParams:
\t\t\tEdgeScanParams
\t\t\t(
\t\t\t\tEdgeTransition: BrightToDark
\t\t\t)
\t\tinEdgeSelection: Best
\t\toutEdge.Point: con_c62e1232_1a2d_41d3_a390_f15fe2b28fca
\t\tdiagSamplingStep: Nil
\t)

\tReadFilmstrip
\t{
\t\tImage: Image
\t}
\t(
\t\toutImage: con_93c89ba6_a3bb_400e_8f0f_0196254989fa
\t)

\tLoadObject<Region>
\t(
\t\tinFile: "a.avdata"
\t\toutObject: con_46a919f8_a37a_4886_a02a_27790199c231
\t)

\tMyMacro
\t(
\t\tinPrivate: 1
\t)

\texpression
\t{
\t\tReal in__A__ as A
\t}
\t(
\t\tin__A__: 1.000000
\t)
}

public MyMacro(Integer inPrivate)
{
}
"""


class FilterPorts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Examples", "Demo"))
        write(os.path.join(self.dir, "Examples", "Demo", "demo.avcode"), PROGRAM)
        write(os.path.join(self.dir, "Examples", "Demo", "other.avcode"),
              "public Main()\n{\n\tScanSingleEdge\n\t(\n\t\tinScanWidth: 40\n\t\tinPointAnchor: 0.000000\n\t)\n}\n")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_parse(self):
        text, err = avs_mcp.ports.filter_ports("ScanSingleEdge", self.dir)
        self.assertFalse(err, text)
        self.assertIn("2 call(s) in 2 example file(s)", text)
        self.assertRegex(text, r"inScanWidth\s+in\s+2/2\s+1\s+5; 40")             # both values, hidden once
        self.assertRegex(text, r"outEdge.Point\s+out\s+1/2\s+-")                  # connection is not a sample value
        self.assertIn("EdgeScanParams(EdgeTransition: BrightToDark)", text)       # multi-line structure value
        self.assertRegex(text, r"diagSamplingStep\s+diag\s+1/2\s+1\s+Nil")
        self.assertIn(os.path.join("Examples", "Demo", "demo.avcode") + ":8", text)
        self.assertRegex(text, r"inPointAnchor\s+in\s+1/2\s+0.000000")
        self.assertLess(text.index("inImage"), text.index("inScanWidth"))         # program order kept

    def test_config_type_args_and_skips(self):
        text = avs_mcp.ports.filter_ports("readfilmstrip", self.dir)[0]           # case-insensitive
        self.assertRegex(text, r"Image\s+config\s+1/1\s+Image")
        text = avs_mcp.ports.filter_ports("LoadObject<Region>", self.dir)[0]
        self.assertIn("LoadObject<Region> x1", text)
        self.assertIn('"a.avdata"', text)
        self.assertTrue(avs_mcp.ports.filter_ports("MyMacro", self.dir)[1])       # the example's own macro
        self.assertTrue(avs_mcp.ports.filter_ports("expression", self.dir)[1])

    def test_not_found(self):
        text, err = avs_mcp.ports.filter_ports("ScanSingleEdg", self.dir)
        self.assertTrue(err)
        self.assertIn("ScanSingleEdge", text)                                      # close name suggested
        self.assertIn("avs_filter_info", text)
        self.assertTrue(avs_mcp.ports.filter_ports("X", os.path.join(self.dir, "none"))[1])


HMI_CODE = """extern public Integer ext_con_00000000_0000_4000_8000_000000000001 = 10

public Main()
{
	ThresholdToRegion
	(
		inMaxValue: ext_con_00000000_0000_4000_8000_000000000001
		outRegion: con_00000000_0000_4000_8000_000000000002
	)
}

//:USETYPE EVENTHANDLER
public OnClick() step
{
}
"""

HMI_XML = """<HMI RelativeDPI="1">
  <ClientSize>400, 300</ClientSize>
  <Controls>
    <GroupBox>
      <Location>4, 4</Location>
      <Name>groupBox1</Name>
      <Size>300, 200</Size>
      <TabIndex>0</TabIndex>
      <Text>Result</Text>
      <Controls>
        <Label>
          <Location>4, 20</Location>
          <Name>label1</Name>
          <Size>100, 23</Size>
          <TabIndex>0</TabIndex>
          <Text>-</Text>
          <Binding>
            <Input Property="Text" Macro="Main" Variable="con_00000000_0000_4000_8000_000000000002" VariantIndex="0" />
          </Binding>
        </Label>
        <NumericUpDown>
          <Location>4, 50</Location>
          <Maximum>255</Maximum>
          <Name>numericUpDown1</Name>
          <Size>100, 23</Size>
          <TabIndex>1</TabIndex>
          <Binding>
            <Output Property="Value" Variable="ext_con_00000000_0000_4000_8000_000000000001" />
          </Binding>
        </NumericUpDown>
        <ImpulseButton>
          <Location>4, 80</Location>
          <Name>button1</Name>
          <Size>100, 23</Size>
          <TabIndex>2</TabIndex>
          <Binding>
            <Event Name="Click" Handler="OnClick" />
          </Binding>
        </ImpulseButton>
      </Controls>
    </GroupBox>
  </Controls>
</HMI>
"""


PAGE = """<h1>ScanExactlyNEdges (ScanEdges ExactlyN)</h1>
<div class="user_level_icon_block" ><a href="x"><img src="u.png" class="o" /> Basic</a></div>
<table><tr><th>Module:</th><td style="p">MetrologyBasic</td></tr></table>
<p>Locates edges.</p>
<table class="hor_zebra_table">
<tr class="odd"><td><img alt="Input value"/></td><td>inImage</td><td colspan="2"><a>Image</a></td><td>Input image</td></tr>
<tr class="even"><td><img alt="Input value"/></td><td>inMaxDistance</td><td><a>Real</a><a>*</a></td>
<td>0.0 - <img src="../../img/infinity.png" /></td><td>Maximal distance</td></tr>
<tr class="odd"><td><img alt="Input will be modified"/></td><td>ioArray</td><td colspan="2"><a>Integer</a><a>Array</a></td><td>A</td></tr>
<tr class="even"><td><img alt="Output value"/></td><td>outEdges</td><td colspan="2"><a>Edge1D</a><a>Array</a><a>?</a></td><td>Found</td></tr>
</table>"""


class Catalog(unittest.TestCase):
    def setUp(self):
        import catalog
        self.c = catalog
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_cpp_defaults_become_avcode_literals(self):
        v = self.c.avcode_value
        self.assertEqual(v("0.6f"), "0.6")
        self.assertEqual(v("Selection::​Best"), "Best")
        self.assertEqual(v("SelectionBest"), "Best")
        self.assertEqual(v("atl::NIL"), "Nil")
        self.assertEqual(v("true"), "True")
        self.assertEqual(v("1.0", "Double"), "1.0D")            # Studio rejects 1.0 for a Double
        self.assertEqual(v("15", "Double"), "15.0D")
        self.assertEqual(v("100L", "Long"), "100L")
        self.assertEqual(v("100", "Integer"), "100")
        self.assertEqual(v("DoublePrecision_64Bit_LittleEndian.0f"), "DoublePrecision_64Bit_LittleEndian")
        self.assertEqual(v("EdgeScanParams ( SmoothingStdDev: 0.6f MinMagnitude: 5.0f )"),
                         "EdgeScanParams ( SmoothingStdDev: 0.6 MinMagnitude: 5.0 )")

    def test_page_ports(self):
        d = os.path.join(self.tmp, "1DEdgeDetection")
        os.makedirs(d)
        write(os.path.join(d, "ScanExactlyNEdges.html"), PAGE)
        r = self.c.parse_page(os.path.join(d, "ScanExactlyNEdges.html"))
        self.assertEqual((r["title"], r["module"], r["level"], r["summary"]),
                         ("ScanEdges ExactlyN", "MetrologyBasic", "Basic", "Locates edges."))
        p = {x["name"]: x for x in r["ports"]}
        self.assertEqual(list(p), ["inImage", "inMaxDistance", "ioArray", "outEdges"])
        self.assertEqual((p["inMaxDistance"]["optional"], p["inMaxDistance"]["range"]), (True, "0.0 - inf"))
        self.assertEqual(p["ioArray"]["kind"], "inout")
        self.assertEqual((p["outEdges"]["type"], p["outEdges"]["array"], p["outEdges"]["conditional"]),
                         ("Edge1D", 1, True))

    def fake_catalog(self):
        recs = {"FitCircleToEdges": {
            "name": "FitCircleToEdges", "title": "", "level": "Basic", "module": "M", "path": ["Shape Fitting"],
            "summary": "Finds a circle matching detected edge points.", "applications": "", "doc_category": "x",
            "ports": [{"kind": "in", "name": "inImage", "type": "Image", "desc": ""},
                      {"kind": "in", "name": "inEdgeSelection", "type": "Selection", "desc": "", "default": "Best"},
                      {"kind": "in", "name": "inNew57", "type": "Integer", "desc": "", "default": "1"},
                      {"kind": "out", "name": "outCircle", "type": "Circle2D", "desc": "", "conditional": True}],
            "verified": {"status": "ports", "bad": ["input inNew57"]}},
            "AuroraOnly57": {"name": "AuroraOnly57", "title": "", "level": "Basic", "module": "M", "path": ["X"],
                             "summary": "Fits a circle, newer.", "applications": "", "doc_category": "x",
                             "ports": [], "verified": {"status": "missing"}}}
        for n in (1, 2):
            recs[f"DL_DetectAnomalies{n}"] = {
                "name": f"DL_DetectAnomalies{n}", "title": "", "level": "Basic", "module": "DL", "path": ["DeepLearning"],
                "summary": f"Executes a Detect Anomalies {n} model on a single input image.", "applications": "",
                "doc_category": "DeepLearning", "ports": [], "verified": {"status": "ok"}}
        path = os.path.join(self.tmp, "filters_catalog.json")
        write(path, json.dumps(recs))
        p = unittest.mock.patch.object(self.c, "OUT", path)
        p.start()
        self.addCleanup(p.stop)
        self.c._loaded.clear()
        self.addCleanup(self.c._loaded.clear)

    def test_info_leaves_out_ports_missing_from_the_installed_studio(self):
        self.fake_catalog()
        text, err = self.c.filter_info("fitcircletoedges")
        self.assertFalse(err)
        self.assertIn("inNew57 (not in installed Studio)", text)
        call_text = text[text.index("FitCircleToEdges\n("):]
        self.assertNotIn("inNew57", call_text)
        self.assertIn("inEdgeSelection: Best", call_text)
        self.assertIn("outCircle: con_...", call_text)
        text, err = self.c.filter_info("FitCircleToEdgez")
        self.assertTrue(err)
        self.assertIn("FitCircleToEdges", text)

    def test_search_by_task_skips_filters_missing_from_studio(self):
        self.fake_catalog()
        text = self.c.search("fit circles edges")               # plural and camel-case name words still match
        self.assertIn("FitCircleToEdges", text)
        self.assertNotIn("AuroraOnly57", text)
        self.assertIn("AuroraOnly57", self.c.search("circle", include_missing=True))

    def test_search_by_name_with_digits(self):
        self.fake_catalog()
        for q in ("anomaly2", "Anomaly 2", "DL_DetectAnomalies2", "detectanomalies2", "anomaly detection 2"):
            first = self.c.search(q).splitlines()[1]
            self.assertIn("DL_DetectAnomalies2", first, q)
        self.assertNotIn("DL_DetectAnomalies2 ", self.c.search("anomaly 1"))

    def test_not_built(self):
        with unittest.mock.patch.object(self.c, "OUT", os.path.join(self.tmp, "none.json")):
            self.assertIsNone(self.c.search("circle"))
            text, err = self.c.filter_info("X")
        self.assertTrue(err)
        self.assertIn("catalog.py", text)


class VerifyFilters(unittest.TestCase):
    """The AvexeGenerator log -> per-filter verdicts, with the control call that must be reported."""
    LOG = ('<root>\t[Error]\tFilter or macrofilter \'Gone\' does not exist or there is no valid license for it.\n'
           '<root>\t[Error]\tInput \'inNew\' not found in filter "Main::PROCESS → (1)Same".\n'
           '<root>\t[Error]\tUnable to initialize input "inK" of filter "(2)Same" instantiated in macrofilter "M".\n'
           '<root>\t[Error]\tInput \'inPlantedBogus\' not found in filter "Main::PROCESS → (3)RegionArea".\n'
           '<root>\t[Error]\tUnable to initialize input "inRegion" of filter "(3)RegionArea" instantiated in macrofilter'
           ' "M".\n')
    RECS = [{"name": "Gone", "ports": []},
            {"name": "Same", "ports": [{"kind": "in", "name": "inNew"}]},
            {"name": "Other", "ports": [{"kind": "in", "name": "inK", "default": "1"}]}]

    def run_check(self, log):
        import verify_filters
        done = subprocess.CompletedProcess([], 0, log, "")
        with unittest.mock.patch.object(verify_filters.subprocess, "run", return_value=done):
            return verify_filters.check(self.RECS)

    def test_verdicts_follow_the_call_index(self):
        out, other, _ = self.run_check(self.LOG)
        self.assertEqual(out["Gone"], {"status": "missing"})
        self.assertEqual(out["Same"], {"status": "ports", "bad": ["input inNew"]})
        self.assertEqual(out["Other"], {"status": "ok", "bad_default": ["inK"]})      # (2) is Other, not Same
        self.assertEqual(other, [])

    def test_a_silent_log_is_refused(self):
        with self.assertRaises(RuntimeError):                    # the planted errors were not reported
            self.run_check("Loading source program...\nDone.\n")


class Hmi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Examples", "Demo"))
        write(os.path.join(self.dir, "Examples", "Demo", "demo.avhmi"), HMI_XML)
        self.proj = os.path.join(self.dir, "app.avproj")
        write(self.proj, '<AuroraVisionProject Name="app"><Files><Program Path="app.avcode" />'
                         '<HMI Path="app.avhmi" /></Files></AuroraVisionProject>')
        write(os.path.join(self.dir, "app.avcode"), HMI_CODE)
        write(os.path.join(self.dir, "app.avhmi"), HMI_XML)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_catalog(self):
        text, err = avs_mcp.hmi.hmi_controls("", self.dir)
        self.assertFalse(err, text)
        self.assertIn("NumericUpDown", text)
        self.assertIn("Input Text", text)
        text, err = avs_mcp.hmi.hmi_controls("numericupdown", self.dir)              # case-insensitive
        self.assertFalse(err, text)
        self.assertIn("inside: GroupBox", text)                                      # nested in a container
        self.assertIn("Maximum", text)
        self.assertIn("<NumericUpDown>", text)                                       # real example snippet
        self.assertIn("Event Click", avs_mcp.hmi.hmi_controls("ImpulseButton", self.dir)[0])
        self.assertTrue(avs_mcp.hmi.hmi_controls("NumericUpDwn", self.dir)[1])

    def test_check_ok(self):
        text, err = avs_mcp.hmi.hmi_check(self.proj)
        self.assertFalse(err, text)
        self.assertIn("3 binding(s)", text)

    def test_check_problems(self):
        bad = (HMI_XML.replace("000000000002", "0000000000ff")                       # not a connection of Main
               .replace('Handler="OnClick"', 'Handler="Nope"')
               .replace("<Name>button1</Name>", "<Name>label1</Name>"))
        write(os.path.join(self.dir, "app.avhmi"), bad)
        write(os.path.join(self.dir, "app.avcode"), HMI_CODE.replace("inMaxValue: ext_con", "inMaxValue: 5 //"))
        text, err = avs_mcp.hmi.hmi_check(self.proj)
        self.assertTrue(err)
        self.assertIn("0000000000ff is not a connection of macro Main", text)
        self.assertIn("declared but no filter input uses it", text)
        self.assertIn("handler macro 'Nope'", text)
        self.assertIn("'label1' used 2 times", text)

    def test_layout_warnings(self):
        bottom = ("    <ProgramControlBox>\n      <Anchor>Bottom, Left</Anchor>\n      <Location>4, 230</Location>\n"
                  "      <Name>box1</Name>\n      <Size>200, 60</Size>\n      <TabIndex>9</TabIndex>\n"
                  "    </ProgramControlBox>\n    <Label>\n      <Location>350, 4</Location>\n      <Name>far</Name>\n"
                  "      <Size>100, 23</Size>\n      <TabIndex>8</TabIndex>\n    </Label>\n  </Controls>\n</HMI>")
        write(os.path.join(self.dir, "app.avhmi"), HMI_XML.replace("  </Controls>\n</HMI>", bottom))
        text, err = avs_mcp.hmi.hmi_check(self.proj)
        self.assertFalse(err, text)                                                  # warnings, not errors
        self.assertIn("Bottom-anchored ProgramControlBox 'box1' is below Top-anchored GroupBox 'groupBox1'", text)
        self.assertIn("less than 274 px high", text)                                 # 300 - (230 - 204)
        self.assertIn("Label 'far' (350,4 100x23) reaches outside HMI (400x300)", text)
        write(os.path.join(self.dir, "app.avhmi"), HMI_XML.replace("<Location>4, 50</Location>",
                                                                   "<Location>4, 30</Location>"))
        self.assertIn("Label 'label1' overlaps NumericUpDown 'numericUpDown1' in GroupBox 'groupBox1'",
                      avs_mcp.hmi.hmi_check(self.proj)[0])

    def test_property_and_width_warnings(self):
        write(os.path.join(self.dir, "hmi_controls.json"), json.dumps({"controls": {"ImpulseButton": {
            "assembly": "HMI.Runtime.dll", "base": "Button", "public": True, "properties": [
                {"name": "Text", "type": "String", "hmi": True, "port": True, "default": "",
                 "description": "Caption.", "values": ""}]}}}))
        xml = (HMI_XML.replace("<Name>button1</Name>", "<Name>button1</Name>\n          <BackColor>Red</BackColor>")
               .replace("<Location>4, 80</Location>", "<Anchor>Top, Right</Anchor>\n          <Location>150, 20</Location>"))
        write(os.path.join(self.dir, "app.avhmi"), xml)
        with unittest.mock.patch.object(avs_mcp.hmi, "CACHE", self.dir):
            text, err = avs_mcp.hmi.hmi_check(self.proj, os.path.join(self.dir, "Examples"))
            self.assertIn("Caption.", avs_mcp.hmi.hmi_controls("ImpulseButton", self.dir)[0])   # real API shown
        self.assertFalse(err, text)
        self.assertIn("ImpulseButton 'button1' sets BackColor: not an AVS property", text)
        self.assertIn("Right-anchored ImpulseButton 'button1' is right of Left-anchored Label 'label1'", text)
        self.assertIn("px wide (designed 300)", text)

    def test_no_hmi(self):
        write(self.proj, '<AuroraVisionProject><Files><Program Path="app.avcode" /></Files></AuroraVisionProject>')
        self.assertTrue(avs_mcp.hmi.hmi_check(self.proj)[1])


def png_gray(path, w, h, white):
    """8-bit gray PNG, white where white(x, y)."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(255 if white(x, y) else 0 for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class InstalledHmi(unittest.TestCase):
    def test_examples_pass_check(self):
        import glob
        projects = []
        for p in glob.glob(os.path.join(avs_mcp.EXAMPLES_DIR, "**", "*.avproj"), recursive=True):
            with open(p, encoding="utf-8-sig") as f:
                if "<HMI " in f.read():
                    projects.append(p)
        self.assertTrue(projects)
        for p in projects:
            text, err = call("avs_hmi_check", project=p)
            self.assertFalse(err, f"{p}: {text}")
        self.assertIn("PassFailIndicator", call("avs_hmi_controls")[0])


@unittest.skipUnless(os.path.isdir(avs_mcp.EXAMPLES_DIR), "Aurora Vision Studio examples not installed")
class InstalledPorts(unittest.TestCase):
    def test_scan_single_edge(self):
        text, err = call("avs_filter_ports", name="ScanSingleEdge")
        self.assertFalse(err, text)
        self.assertIn("inScanPath", text)
        self.assertIn("outEdge.Point", text)
        self.assertNotIn("inScanMap", text)                                        # the C++ name, not a Studio port


@unittest.skipUnless(os.path.isdir(avs_mcp.FUNC_DIR), "Aurora Vision Library documentation not installed")
class Installed(unittest.TestCase):
    def test_search_and_doc(self):
        self.assertIn("ThresholdToRegion", call("avs_search_filters", query="threshold region")[0])
        self.assertIn("inMaxValue", call("avs_filter_doc", name="ThresholdToRegion")[0])

    def test_examples(self):
        if not os.path.isdir(avs_mcp.EXAMPLES_DIR):
            self.skipTest("examples not installed")
        self.assertIn("SaveText", call("avs_search_examples", query="SaveText")[0])

    def test_validate_example(self):
        text, err = call("avs_validate", project=os.path.join(EXAMPLE, "HoleDistance.avproj"))
        self.assertFalse(err, text)


@unittest.skipUnless(os.path.exists(os.path.join(avs_mcp.RUNTIME_DIR, "AuroraVisionConsole.exe")),
                     "Aurora Vision Studio Runtime not installed")
class Run(unittest.TestCase):
    def test_run_example_and_evaluate(self):
        if not any(f.endswith(".png") for f in os.listdir(os.path.join(EXAMPLE, "images"))):
            self.skipTest("example images are not in the repository")
        text, err = call("avs_run", project=os.path.join(EXAMPLE, "HoleDistance.avproj"), task_dir=EXAMPLE)
        self.assertFalse(err, text)
        self.assertIn("FINISHED", text)
        self.assertIn("results.csv", text)
        self.assertIn("PASS", text)
        self.assertFalse(os.path.exists(os.path.join(EXAMPLE, ".avs_run.avexe")))

    def test_run_failure_is_reported(self):
        d = tempfile.mkdtemp()
        try:
            for f in ("HoleDistance.avproj", "Program.avcode"):
                shutil.copy(os.path.join(EXAMPLE, f), d)       # no images/ folder here
            text, err = call("avs_run", project=os.path.join(d, "HoleDistance.avproj"))
            self.assertTrue(err, text)
            self.assertIn("FAILED", text)
            self.assertIn("Directory not found", text)
        finally:
            shutil.rmtree(d)


class AnnotationValues(unittest.TestCase):
    """[expected] from_annotations: numbers from the drawn shapes."""

    def setUp(self):
        import task_spec
        self.ts = task_spec
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        os.makedirs(os.path.join(self.dir, "images"))
        for n in ("a.png", "b.png", "c.png"):
            write(os.path.join(self.dir, "images", n), "")
        write(os.path.join(self.dir, "task.toml"),
              '[task]\nname = "s"\nimages = "images/"\n[output]\nresults_csv = "results.csv"\nkey = "image"\n'
              '[expected]\nfile = "expected.csv"\n'
              'from_annotations = { split_x = "rect.left", split_y = "rect.center_y", d = "circle[hole].diameter" }\n'
              '[[steps]]\nid = "s"\ntype = "locate"\nmethod = "rule"\ntarget = "split"\naccuracy = "17 px"\n'
              'columns = ["split_x", "split_y"]\ntolerance = { split_x = 17 }\n')

    def test_shape_values(self):
        v = self.ts.shape_value
        self.assertEqual(v({"kind": "rect", "points": [[30, 10], [10, 50]]}, "left"), 10)
        self.assertEqual(v({"kind": "rect", "points": [[30, 10], [10, 50]]}, "center_y"), 30)
        self.assertEqual(v({"kind": "point", "points": [[3, 4]]}, "y"), 4)
        self.assertEqual(v({"kind": "line", "points": [[0, 0], [3, 4]]}, "length"), 5)
        self.assertEqual(v({"kind": "circle", "points": [[0, 0], [3, 4]]}, "diameter"), 10)
        self.assertEqual(self.ts.parse_shape_ref(" rect [ split ] . left "), ("rect", "split", "left"))
        for bad in ("rect.radius", "blob.x", "rect", "rect[x]"):
            with self.assertRaises(ValueError):
                self.ts.parse_shape_ref(bad)

    def test_derive_needs_exactly_one_shape(self):
        spec = self.ts.load_task(self.dir)
        shapes = {"a.png": [{"kind": "rect", "points": [[100, 10], [200, 30]]},
                            {"kind": "circle", "class": "hole", "points": [[5, 5], [5, 9]]},
                            {"kind": "circle", "class": "dust", "points": [[1, 1], [1, 2]]}],
                  "b.png": [{"kind": "rect", "points": [[1, 1], [2, 2]]}, {"kind": "rect", "points": [[3, 3], [4, 4]]}]}
        vals, notes = self.ts.derive_expected(spec, shapes)
        self.assertEqual(vals["a.png"], {"split_x": 100, "split_y": 20, "d": 8})      # class filter picks the hole
        self.assertNotIn("b.png", vals)                                              # two rects: ambiguous
        self.assertTrue(any("b.png: 2 rect shapes" in n for n in notes))

    def test_save_writes_the_values_and_clears_removed_ones(self):
        import annotate
        body = {"images": {"a.png": [{"kind": "rect", "points": [[100.5, 10], [200, 30]]}],
                           "b.png": [{"kind": "rect", "points": [[50, 0], [60, 10]]}]}, "image_info": {}}
        msg = annotate.save(self.dir, body)
        self.assertIn("Expected split_x, split_y, d from the shapes on 2 image(s)", msg)
        rows = {r["image"]: r for r in read_csv(os.path.join(self.dir, "expected.csv"))}
        self.assertEqual((rows["a.png"]["split_x"], rows["b.png"]["split_y"]), ("100.5", "5.0"))
        self.assertNotIn("c.png", rows)
        del body["images"]["b.png"]
        annotate.save(self.dir, body)
        rows = {r["image"]: r for r in read_csv(os.path.join(self.dir, "expected.csv"))}
        self.assertNotIn("b.png", rows)                                              # no shape, no ground truth left
        self.assertIn("split_x", annotate.task_state(self.dir)["from_annotations"])

    def test_check_reports_a_bad_reference(self):
        p = os.path.join(self.dir, "task.toml")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        write(p, text.replace('"rect.left"', '"rect.radius"'))
        text, err = self.ts.check_task(self.dir)
        self.assertTrue(err)
        self.assertIn("expected.from_annotations", text)

    def test_tolerance_number_or_table(self):
        spec = {"steps": [{"columns": ["x", "y"], "tolerance": 0.5}, {"column": "d", "tolerance": {"d": 2, "e": True}}]}
        self.assertEqual(self.ts.tolerances(spec), {"x": 0.5, "y": 0.5, "d": 2.0})


def read_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class EvaluateFound(unittest.TestCase):
    """found column, silent failures, expected values from shapes, several versions."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        write(os.path.join(self.dir, "task.toml"),
              '[task]\nname = "f"\n[output]\nresults_csv = "results.csv"\nkey = "image"\n'
              '[expected]\nfile = "expected.csv"\nfrom_annotations = { x = "point.x" }\n'
              '[[steps]]\nid = "s"\ntype = "locate"\nmethod = "rule"\ntarget = "t"\naccuracy = "10 px"\n'
              'columns = ["x"]\ntolerance = 10\n')
        write(os.path.join(self.dir, "expected.csv"),
              "image,x,split\na.png,100,tune\nb.png,200,tune\nc.png,300,holdout\nd.png,,tune\n")
        write(os.path.join(self.dir, "annotations.json"),               # d.png has no expected x: the point gives it
              json.dumps({"images": {"d.png": [{"kind": "point", "points": [[400, 9]]}],
                                     "a.png": [{"kind": "point", "points": [[999, 9]]}]}}))   # a.png keeps 100

    def run_eval(self, rows):
        write(os.path.join(self.dir, "results.csv"), "image,x,found\n" + rows)
        return call("avs_evaluate", task_dir=self.dir)

    def test_wrong_and_not_found_are_told_apart(self):
        text, err = self.run_eval("a.png,105,True\nb.png,260,True\nc,,False\nd.png,401,True\n")
        self.assertTrue(err)
        self.assertIn("1 expected value(s) taken from the drawn shapes", text)
        self.assertIn("within 2/3 | WRONG but reported as found 1 | not found 0", text)   # tune: a, d ok; b wrong
        self.assertIn("WRONG b.png: error +60.0000", text)
        self.assertIn("within 0/1 | WRONG but reported as found 0 | not found 1", text)   # holdout c: not found
        self.assertNotIn("non-numeric", text)

    def test_all_within_passes(self):
        text, err = self.run_eval("a.png,101,True\nb.png,199,True\nc.png,305,True\nd.png,398,True\n")
        self.assertFalse(err, text)

    def test_compare_versions(self):
        for name, rows in (("v1", "a.png,150,True\nb.png,200,True\nc.png,,False\nd.png,400,True\n"),
                           ("v2", "a.png,101,True\nb.png,202,True\nc.png,301,True\n")):
            os.makedirs(os.path.join(self.dir, name, "output"))
            write(os.path.join(self.dir, name, "output", "results.csv"), "image,x,found\n" + rows)
        text, err = call("avs_evaluate", task_dir=self.dir,
                         compare=["v1/output/results.csv", "v2/output/results.csv"])
        self.assertFalse(err, text)
        self.assertRegex(text, r"a\.png\s+tune\s+\+50\.0!\s+\+1\.0")                # labels default to the project
        self.assertRegex(text, r"c\.png\s+hold\s+MISS\s+\+1\.0")
        self.assertRegex(text, r"d\.png\s+tune\s+\+0\.0\s+-")                         # v2 has no row for d
        self.assertIn("tune: v1: within 2/3, WRONG 1, not found 0", text)
        self.assertIn("v2: within 2/3, WRONG 0, not found 1", text)
        self.assertIn("HOLDOUT: v1: within 0/1, WRONG 0, not found 1", text)


class RunParams(unittest.TestCase):
    """avs_run: matching Studio/Runtime versions, globals per run, license retries, hints."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_pick_installs_pairs_versions(self):
        root = os.path.join(self.dir, "AV")
        for n in ("Aurora Vision Studio 5.6 Professional", "Aurora Vision Studio 5.6 Runtime",
                  "Aurora Vision Studio 5.7 Professional", "Aurora Vision Studio 5.7"):
            os.makedirs(os.path.join(root, n))
        with unittest.mock.patch.object(avs_mcp, "AV_ROOT", root):
            s, r = avs_mcp.pick_installs({})                                         # only 5.6 has a Runtime
            self.assertEqual((os.path.basename(s), os.path.basename(r)),
                             ("Aurora Vision Studio 5.6 Professional", "Aurora Vision Studio 5.6 Runtime"))
            s, r = avs_mcp.pick_installs({"AVS_VERSION": "5.7"})
            self.assertEqual((os.path.basename(s), r), ("Aurora Vision Studio 5.7 Professional", ""))
            os.makedirs(os.path.join(root, "Aurora Vision Studio 5.7 Runtime"))
            s, r = avs_mcp.pick_installs({})                                         # newest common version
            self.assertEqual(avs_mcp.version_of(s) + avs_mcp.version_of(r), "5.75.7")

    def test_mismatched_versions_are_refused(self):
        proj = os.path.join(self.dir, "p.avproj")
        write(proj, "<AuroraVisionProject />")
        with unittest.mock.patch.object(avs_mcp, "AVS_DIR", r"C:\x\Aurora Vision Studio 5.7 Professional"), \
                unittest.mock.patch.object(avs_mcp, "RUNTIME_DIR", r"C:\x\Aurora Vision Studio 5.6 Runtime"), \
                unittest.mock.patch("os.path.exists", lambda p, real=os.path.exists: True if p.endswith(
                    "AuroraVisionConsole.exe") else real(p)):
            text, err = call("avs_run", project=proj)
        self.assertTrue(err)
        self.assertIn("Studio 5.7 builds the program but Runtime 5.6 would run it", text)

    def make_project(self):
        os.makedirs(os.path.join(self.dir, "proj", "sub"))
        proj = os.path.join(self.dir, "proj", "p.avproj")
        write(proj, '<AuroraVisionProject><Files><Program Path="sub\\Program.avcode" />'
                    '<View Path="p.avview" /></Files></AuroraVisionProject>')
        code = ('extern public Directory ImageFolder = "C:\\\\old"\nextern public Real Gain = 1.5\n'
                'extern public Bool Debug = False\npublic Main()\n{\n}\n')
        write(os.path.join(self.dir, "proj", "sub", "Program.avcode"), code)
        write(os.path.join(self.dir, "proj", "p.avview"), "<View />")
        return proj, code

    def test_globals_are_set_on_a_copy(self):
        proj, code = self.make_project()
        seen = {}

        def fake_run(cmd, **kw):
            if cmd[0].endswith("AuroraVisionAvexeGenerator.exe"):
                src = os.path.join(os.path.dirname(cmd[-2]), "sub", "Program.avcode")
                with open(src, encoding="utf-8") as f:
                    seen["code"] = f.read()
                seen["view"] = os.path.exists(os.path.join(os.path.dirname(cmd[-2]), "p.avview"))
                write(cmd[-1], "exe")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "Program successfully finished.", "")
        with unittest.mock.patch.object(avs_mcp.subprocess, "run", fake_run), \
                unittest.mock.patch.object(avs_mcp, "RUNTIME_DIR", same_version_runtime(self.dir)), \
                unittest.mock.patch("os.path.exists", lambda p, real=os.path.exists: True if p.endswith(
                    "AuroraVisionConsole.exe") else real(p)):
            text, err = call("avs_run", project=proj,
                             params={"ImageFolder": "D:\\new imgs", "Gain": 2, "Debug": True})
            self.assertFalse(err, text)
            self.assertIn('extern public Directory ImageFolder = "D:\\\\new imgs"', seen["code"])
            self.assertIn("extern public Real Gain = 2", seen["code"])
            self.assertIn("extern public Bool Debug = True", seen["code"])
            self.assertTrue(seen["view"])
            self.assertIn("Globals for this run: ImageFolder = D:\\new imgs", text)
            with open(os.path.join(self.dir, "proj", "sub", "Program.avcode"), encoding="utf-8") as f:
                self.assertEqual(f.read(), code)                                     # original untouched
            text, err = call("avs_run", project=proj, params={"Folder": "x"})
        self.assertTrue(err)
        self.assertIn("Available: ImageFolder (Directory), Gain (Real), Debug (Bool)", text)

    def test_license_limit_is_retried_and_hints(self):
        calls = []

        def busy_then_ok(cmd, **kw):
            calls.append(cmd)
            out = "License process limit is exceeded." if len(calls) < 3 else "done"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        with unittest.mock.patch.object(avs_mcp.subprocess, "run", busy_then_ok), \
                unittest.mock.patch.object(avs_mcp.time, "sleep", lambda s: None):
            r = avs_mcp.run_aurora(["x.exe"])
        self.assertEqual((len(calls), r.stdout), (3, "done"))
        h = avs_mcp.hints("The library was compiled for use in Aurora Vision Studio 5.7.4, cannot load it in 5.7.3 "
                          "version.\n[IO Error] Cannot open file: 'C:\\o\\r.csv'. Error code: 13 [Permission denied].")
        self.assertIn("rebuild the DLL with the Studio 5.7.3 SDK", h)
        self.assertIn("C:\\o\\r.csv is locked", h)


if __name__ == "__main__":
    unittest.main()
