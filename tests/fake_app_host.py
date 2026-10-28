"""fake_app_host.py — minimal MCP Apps host for trying the in-chat annotation page without Claude.

Starts avs_mcp.py over stdio (announcing the io.modelcontextprotocol/ui extension), reads ui://avs/annotate.html with
resources/read and shows it in a sandboxed iframe (opaque origin, like a real host). The host page answers
ui/initialize, sends ui/notifications/tool-input with the task folder, forwards tools/call / resources/read to the
server (refusing tools whose visibility lacks "app") and logs every message to window.__log.

    python tests/fake_app_host.py <task_dir> [--port 8770]
"""

import argparse
import http.server
import json
import os
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOST_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Fake MCP Apps host</title>
<style>body{margin:0;font:13px Segoe UI,sans-serif;background:#0b0c0e;color:#ccc;display:flex;flex-direction:column;height:100vh}
#bar{padding:6px 10px;background:#222}#frame{border:1px solid #444;width:100%;height:400px;flex:none}
#log{flex:1;overflow:auto;white-space:pre;font:11px Consolas,monospace;padding:6px}</style></head><body>
<div id="bar">Fake MCP Apps host — task: <b id="task"></b> — display: <b id="mode">inline</b></div>
<iframe id="frame" sandbox="allow-scripts allow-forms allow-downloads"></iframe><div id="log"></div>
<script>
const TASK = __TASK__;
window.__log = []; window.__context = null;
const frame = document.getElementById("frame"); document.getElementById("task").textContent = TASK;
function log(dir, m) { window.__log.push({dir, m}); const s = JSON.stringify(m); document.getElementById("log").textContent += dir + " " + (s.length > 300 ? s.slice(0, 300) + "…" : s) + "\n"; }
async function rpc(method, params) {
  const r = await fetch("/rpc", {method: "POST", body: JSON.stringify({jsonrpc: "2.0", id: 1, method, params})});
  return r.json();
}
let tools = {};
function send(m) { log("host->view", m); frame.contentWindow.postMessage(m, "*"); }
window.addEventListener("message", async e => {
  if (e.source !== frame.contentWindow) return;
  const m = e.data; log("view->host", m);
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2026-01-26", hostInfo: {name: "fake-host", version: "0"},
      hostCapabilities: {serverTools: {}, serverResources: {}},
      hostContext: {theme: "dark", displayMode: "inline", availableDisplayModes: ["inline", "fullscreen"],
                    containerDimensions: {maxHeight: 760}}}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-input", params: {arguments: {task_dir: TASK}}});
  } else if (m.method === "ui/notifications/size-changed") {
    frame.style.height = m.params.height + "px";
  } else if (m.method === "ui/request-display-mode") {
    const mode = m.params.mode; document.getElementById("mode").textContent = mode;
    frame.style.height = mode === "fullscreen" ? "90vh" : "760px";
    send({jsonrpc: "2.0", id: m.id, result: {mode}});
  } else if (m.method === "ui/update-model-context") {
    window.__context = m.params; send({jsonrpc: "2.0", id: m.id, result: {}});
  } else if (m.method === "tools/call") {
    const t = tools[m.params.name];
    const vis = (t && t._meta && t._meta.ui && t._meta.ui.visibility) || ["model", "app"];
    if (!t || !vis.includes("app")) { send({jsonrpc: "2.0", id: m.id, error: {code: -32000, message: "tool not callable by the app"}}); return; }
    const r = await rpc("tools/call", m.params);
    send({jsonrpc: "2.0", id: m.id, result: r.result, error: r.error});
  } else if (m.method === "resources/read") {
    const r = await rpc("resources/read", m.params); send({jsonrpc: "2.0", id: m.id, result: r.result, error: r.error});
  } else if (m.id != null) {
    send({jsonrpc: "2.0", id: m.id, error: {code: -32601, message: "not supported by fake host: " + m.method}});
  }
});
(async () => {
  for (const t of (await rpc("tools/list", {})).result.tools) tools[t.name] = t;
  const a = tools["avs_annotate"];
  const uri = a && a._meta && a._meta.ui && a._meta.ui.resourceUri;
  const res = (await rpc("resources/read", {uri})).result.contents[0];
  log("host", {resource: uri, mimeType: res.mimeType, bytes: res.text.length});
  frame.srcdoc = res.text;
})();
</script></body></html>"""


class Server:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "avs_mcp.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.lock = threading.Lock()
        self.next_id = 1
        self.call("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "fake-host", "version": "0"},
                                 "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {
                                     "mimeTypes": ["text/html;profile=mcp-app"]}}}})

    def call(self, method, params):
        with self.lock:
            mid, self.next_id = self.next_id, self.next_id + 1
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
            self.p.stdin.flush()
            return json.loads(self.p.stdout.readline())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_dir")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    server = Server()
    page = HOST_PAGE.replace("__TASK__", json.dumps(os.path.abspath(a.task_dir)))

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.reply(json.dumps(server.call(req["method"], req.get("params") or {})).encode("utf-8"), "application/json")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"http://127.0.0.1:{a.port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
