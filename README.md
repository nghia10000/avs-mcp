# avs-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Code, Claude Desktop, Cursor, …) build and check **Aurora Vision Studio** programs: it searches the filter documentation and the official examples installed on your machine, loads and **runs** `.avproj` projects headlessly, and turns a machine vision job into a **task spec** with ground truth, so the assistant can write → run → evaluate → fix on its own.

> Community project — not affiliated with or endorsed by Aurora Vision / Zebra Technologies. It ships no Aurora Vision files; it reads the documentation and tools of **your own licensed installation**.

[Tiếng Việt bên dưới](#tiếng-việt)

## Requirements

- Windows, Aurora Vision Studio 5.x Professional + Aurora Vision Library documentation (tested with 5.6)
- Aurora Vision Studio **Runtime** of the same version, for `avs_run` (optional — without it you press Run in Studio)
- Python 3.11+ (standard library only, nothing to `pip install`; tested with 3.11 and 3.14)

## Install

```bash
git clone https://github.com/nghia10000/avs-mcp.git
```

Claude Code — add to `.mcp.json` in your project (or `~/.claude.json`):

```json
{
  "mcpServers": {
    "avs": { "command": "python", "args": ["C:/path/to/avs-mcp/avs_mcp.py"] }
  }
}
```

Other MCP clients: run `python avs_mcp.py` over stdio.

Install paths are detected under `C:\Program Files\Aurora Vision`; override with `AVS_DIR` (Studio folder), `AVS_RUNTIME_DIR` (Runtime folder), `AVL_DOC_DIR` (Library `Documentation` folder) and `AVS_EXAMPLES_DIR` (Studio examples in `C:\ProgramData\Aurora Vision\...`).

## Tools

| Tool | What it does |
|---|---|
| `avs_search_filters` | Search filters by keyword in the installed AVL documentation |
| `avs_filter_doc` | Syntax, parameters, defaults and errors of one filter or data type; shows the **Studio Filter Reference** page (Studio port names) first when it has been downloaded (see below) |
| `avs_docs_search` / `avs_docs_read` | Full-text (BM25) search over all docs on this machine — AVL functions and data types, Machine Vision Guide, Deep Learning, and the **Studio manual** (formulas, Optional/Conditional, project files…) after `python docs_ingest.py all_chapters.pdf` (needs `pypdf` once; the PDF comes from docs.adaptive-vision.com and is not shipped), and the **Studio Filter Reference** (source `Studio filter`) after `python filters_fetch.py` |
| `avs_search_examples` | How a filter / formula is actually written in the official `.avcode` examples |
| `avs_filter_ports` | Real **Studio** port names of a filter (e.g. `ScanSingleEdge` → `inScanPath`, `outEdge.Point`), mined from every call in the official examples: in/out/diag, how often listed or hidden, sample values, file:line. The AVL docs give C++ parameter names, which often differ; filters never used in the examples are not covered |
| `avs_validate` | Load an `.avproj` with `AuroraVisionAvexeGenerator.exe` (no GUI) and report errors |
| `avs_run` | Run a project headlessly with the Runtime (`AuroraVisionConsole.exe`); with `task_dir` it clears the results CSV and evaluates afterwards |
| `avs_open` | Open a project in Studio so you can press Run |
| `avs_task_types` | List step types for task specs |
| `avs_task_new` | Create a task folder skeleton |
| `avs_task_check` | Report missing / TODO information in a task spec |
| `avs_annotate` | Local web page to mark each image OK / NG and draw defects or measurements on it (see below) |
| `avs_evaluate` | Compare the program's `results.csv` with `expected.csv`; margin report; tune vs holdout |
| `avs_split` | Mark part of the images as holdout (kept out of tuning) |

Prompt: **`describe_task`** — the assistant interviews you and writes the task spec.

### Studio Filter Reference (optional download)

The installed AVL docs describe C++ functions, whose parameters differ from Studio filter ports. The public Studio
Filter Reference on docs.adaptive-vision.com has one page per filter with its Studio port table. Download it once
(~3,750 pages, ~55 MB, sequential with a 0.7 s pause: about an hour; rerunning skips pages already there):

```bash
python filters_fetch.py
```

Pages go to `../avs_docs_cache/studio_filters` (or `$AVS_DOCS_CACHE/studio_filters`), outside the repo: they are
Zebra's content and are not shipped. The search index is rebuilt automatically on the next `avs_docs_search`. The
pages describe the current online version (5.7.x at the time of writing), which can differ slightly from an older
installed Studio.

## Task spec

A machine vision job is described by a folder, not by a chat message:

```
my_task/
├── task.toml          what to do: common sections + one [[steps]] entry per step
├── annotations.json   what you drew on the images (lines, points, rects, circles, polygons)
├── expected.csv       ground truth per image (caliper / CMM values, OK/NG)
├── annotated/         the images as you marked them (written by avs_annotate, read by the assistant)
└── images/            sample images, including the worst ones
```

### Annotation page

`avs_annotate` (or `python annotate.py <task_dir>`) opens a page on `127.0.0.1` where you:

- mark every image **OK / NG** and add a note — saved as the verdict column of `expected.csv`;
- draw each defect or measurement as a **rect, polygon, line, circle or point**, give it a **class** and a **description** of what exactly is wrong, and link it to a step or feature;
- drag **Black / White** levels to reveal faint defects in dark or bright areas — the levels are saved per image.

Save writes `annotations.json`, `expected.csv` (other columns are kept) and `annotated/*.png`, where every image is rendered with its levels, shapes, classes and descriptions. The assistant reads those PNGs, so it sees exactly what you marked instead of guessing from the raw image.

**Inside the chat (MCP Apps).** When the host announces the MCP Apps extension (`io.modelcontextprotocol/ui`), `avs_annotate` is rendered in the conversation instead of a browser tab: the server exposes the page as the resource `ui://avs/annotate.html` and five app-only tools (`avs_ui_task`, `avs_ui_image`, `avs_ui_save`, `avs_ui_upload`, `avs_ui_remove`, visibility `["app"]`, listed only to such hosts). The page detects where it runs: in the chat it talks JSON-RPC over `postMessage` and gets images as base64; in a browser tab it uses the local HTTP server as before. After Save it tells the assistant through `ui/update-model-context`. Hosts without MCP Apps keep the browser page. Some hosts announce the extension without rendering it — Claude Code (also the Code tab of the desktop app) runs the tool as text only; the Claude desktop chat and claude.ai render it — so if the page has not called back within 8 s, `avs_annotate` opens it in the browser anyway. To try the in-chat mode without a supporting host: `python tests/fake_app_host.py <task_dir>` runs a minimal host that loads the page in a sandboxed iframe and logs every message.

`task.toml` has three layers:

1. **Common sections** for every job: `[task]`, `[conditions]`, `[calibration]`, `[output]`, `[expected]`, optional `[[features]]`.
2. **Step types**, each with its own fields: `measure`, `locate`, `count`, `defect`, `classify`, `read`, `color`, `measure3d`, `preprocess`, `decide`, `custom`.
3. **Steps chained** with `after`, each marked `method = "rule" | "deep" | "hybrid"` — e.g. `locate (rule) → preprocess (rule) → defect (deep) → measure (rule) → decide`.

Only what must be machine-checked is structured (coordinates, tolerances, ground truth, output columns); everything else is free text the assistant reads. Step types are data files in [`spec/types/`](spec/types) — add one to support a new kind of job. See [`spec/README.md`](spec/README.md).

### Workflow

1. `describe_task` → task folder filled in, images marked with `avs_annotate`, `avs_task_check` clean.
2. The assistant writes the `.avcode` program; it appends one row per image to `results.csv`; `avs_validate` passes.
3. `avs_run` with `task_dir` → runs the program over the images and evaluates: error vs tolerance, escapes / false rejects.
4. The assistant fixes the program and repeats until it passes. (No Runtime? Press Run in Studio via `avs_open`, then `avs_evaluate`.)

### What the evaluation is compared against

There is no reference program for a real job. Ground truth is what **people** say: OK/NG judged by QC, caliper / CMM values, defects marked in `avs_annotate`. Passing on the sample images is not enough, so `avs_evaluate` also reports:

- **Margins.** Every numeric column the program writes to `results.csv` that is not in `expected.csv` (gap brightness, area, distance…) is treated as a measured feature. For each one: the range of the OK images, the range of the NG images, and for every NG image how far it is outside the OK range, in units of the OK spread. NG images close to the OK range are flagged `CLOSE`; NG images that no feature separates are listed. Few OK images → warning that the real OK range is wider.
- **Holdout.** `avs_split` adds a `split` column to `expected.csv` (`tune` / `holdout`, OK/NG ratio kept, repeatable with a seed). Develop and set thresholds on the tune images only; the holdout section is the estimate for new images.

Example (bottle-cap task, 9 images): all 9 correct, but the report shows the faint seal gap of one NG image only 0.68 above the highest OK value, about one OK spread — a threshold that passes here is unlikely to hold on new images without more samples.

Notes on `avs_run`: the `.avexe` is built next to the project because relative paths in the program resolve from its location; the Runtime returns exit code 0 even when the program fails, so success is read from its log.

## Example

[`examples/hole_distance`](examples/hole_distance) — center-to-center distance of two holes in a backlit plate: task spec, annotations, expected values and a program (`ThresholdToRegion → RegionHoles → FitCircleToEdges → PointToPointDistance → SaveText`). Run headlessly over 10 images it takes about 1 s and agrees with an independent Python sub-pixel measurement within 0.06 px. The sample images are not published; add your own backlit images with two holes to `images/`.

## Limitations / roadmap

- [x] `avs_run` — headless run with Aurora Vision Studio Runtime.
- [ ] Programs that wait for a camera, PLC or HMI input never finish under `avs_run`; use image folders (`EnumerateImages`, `inRepeat = False`) for testing.
- [x] Local annotation UI that writes the task folder (`avs_annotate`).
- [ ] Deep Learning helpers: export preprocessed training images, read model results.
- [ ] Studio 5.5 / 5.7 testing, Runtime-only installs.

## Development

```bash
python -m unittest discover -s tests
```

Tests that need Aurora Vision are skipped when it is not installed.

## License

MIT — see [LICENSE](LICENSE).

---

## Tiếng Việt

MCP server giúp trợ lý AI viết và kiểm tra chương trình **Aurora Vision Studio**: tra filter trong tài liệu và các ví dụ chính hãng có trên máy, nạp và **chạy** project không cần mở giao diện (`avs_run`, cần cài Runtime cùng phiên bản), và mô tả bài toán machine vision bằng một **thư mục task** (`task.toml` + ảnh có vẽ đánh dấu + `expected.csv` đáp án) để trợ lý tự viết → chạy → so đáp án → sửa.

- Cài: clone repo, thêm vào `.mcp.json` như trên. Cần Python 3.11+, không cần cài thêm thư viện.
- Tra cứu cổng (port) Studio của filter: chạy `python filters_fetch.py` một lần để tải Filter Reference công khai (~55 MB, không đưa vào repo).
- Mô tả bài toán: gọi prompt `describe_task`, trợ lý sẽ hỏi từng ý (đo gì, chi tiết trông thế nào, ảnh thay đổi ra sao, dung sai, đáp án mẫu…) rồi tạo thư mục task.
- Bài toán phức tạp = chuỗi bước, mỗi bước một loại (`measure`, `defect`, …) và ghi rõ `rule` / `deep`.
- Nếu ứng dụng chat hỗ trợ MCP Apps, trang đánh dấu hiện ngay trong khung chat; nếu không thì mở trên trình duyệt như cũ.
- Đánh dấu ảnh: `avs_annotate` mở trang web cục bộ — chọn OK/NG cho từng ảnh, khoanh lỗi (hộp, đa giác, đường, tròn, điểm) kèm loại lỗi và mô tả, kéo mức Black/White để thấy lỗi mờ. Lưu ra `annotations.json`, `expected.csv` và `annotated/*.png` để trợ lý xem đúng chỗ bạn khoanh.
- Chấm điểm: đáp án là của người (QC đánh OK/NG, số đo thước cặp/CMM, lỗi khoanh bằng `avs_annotate`). `avs_evaluate` ngoài đúng/sai còn báo **khoảng cách an toàn** (ảnh NG cách khoảng giá trị của ảnh OK bao xa, ảnh nào sát ngưỡng) và chấm riêng **ảnh để dành** (`avs_split` chia ảnh dùng để chỉnh / ảnh để kiểm tra).
- Chưa có: hỗ trợ Deep Learning — xem roadmap.
