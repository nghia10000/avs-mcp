"""python -m unittest discover -s tests   (from the repo root)"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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
