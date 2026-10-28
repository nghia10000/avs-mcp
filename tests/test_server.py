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
        self.assertIn("3/3 within", text)

    def test_fail(self):
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na.png,10.9,OK\nb.png,20.0,OK\n")
        text, _ = call("avs_evaluate", task_dir=self.dir)
        self.assertTrue(text.startswith("FAIL"), text)
        self.assertIn("No result for 1", text)
        self.assertIn("OUT a.png", text)
        self.assertIn("escapes (NG passed): 1/1", text)

    def test_missing_file(self):
        self.assertTrue(call("avs_evaluate", task_dir=self.dir)[1])

    def test_no_holdout_warning(self):
        write(os.path.join(self.dir, "results.csv"), "image,d,result\na,10.1,OK\nb,19.8,NG\nc,30.4,OK\n")
        self.assertIn("No holdout images", call("avs_evaluate", task_dir=self.dir)[0])


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
        self.assertIn("avs_filter_doc", text)
        self.assertTrue(avs_mcp.ports.filter_ports("X", os.path.join(self.dir, "none"))[1])


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


if __name__ == "__main__":
    unittest.main()
