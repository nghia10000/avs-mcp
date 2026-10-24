# avs-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Code, Claude Desktop, Cursor, …) build and check **Aurora Vision Studio** programs: it searches the filter documentation and the official examples installed on your machine, loads `.avproj` projects headlessly to catch errors, and turns a machine vision job into a **task spec** with ground truth, so results can be checked automatically.

> Community project — not affiliated with or endorsed by Aurora Vision / Zebra Technologies. It ships no Aurora Vision files; it reads the documentation and tools of **your own licensed installation**.

[Tiếng Việt bên dưới](#tiếng-việt)

## Requirements

- Windows, Aurora Vision Studio 5.x Professional + Aurora Vision Library documentation (tested with 5.6)
- Python 3.12+ (standard library only, nothing to `pip install`)

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

Install paths are detected under `C:\Program Files\Aurora Vision`; override with `AVS_DIR` (Studio folder), `AVL_DOC_DIR` (Library `Documentation` folder) and `AVS_EXAMPLES_DIR` (Studio examples in `C:\ProgramData\Aurora Vision\...`).

## Tools

| Tool | What it does |
|---|---|
| `avs_search_filters` | Search filters by keyword in the installed AVL documentation |
| `avs_filter_doc` | Syntax, parameters, defaults and errors of one filter or data type |
| `avs_search_examples` | How a filter / formula is actually written in the official `.avcode` examples |
| `avs_validate` | Load an `.avproj` with `AuroraVisionAvexeGenerator.exe` (no GUI) and report errors |
| `avs_open` | Open a project in Studio so you can press Run |
| `avs_task_types` | List step types for task specs |
| `avs_task_new` | Create a task folder skeleton |
| `avs_task_check` | Report missing / TODO information in a task spec |
| `avs_evaluate` | Compare the program's `results.csv` with `expected.csv` |

Prompt: **`describe_task`** — the assistant interviews you and writes the task spec.

## Task spec

A machine vision job is described by a folder, not by a chat message:

```
my_task/
├── task.toml          what to do: common sections + one [[steps]] entry per step
├── annotations.json   what you drew on the images (lines, points, rects, circles, polygons)
├── expected.csv       ground truth per image (caliper / CMM values, OK/NG)
└── images/            sample images, including the worst ones
```

`task.toml` has three layers:

1. **Common sections** for every job: `[task]`, `[conditions]`, `[calibration]`, `[output]`, `[expected]`, optional `[[features]]`.
2. **Step types**, each with its own fields: `measure`, `locate`, `count`, `defect`, `classify`, `read`, `color`, `measure3d`, `preprocess`, `decide`, `custom`.
3. **Steps chained** with `after`, each marked `method = "rule" | "deep" | "hybrid"` — e.g. `locate (rule) → preprocess (rule) → defect (deep) → measure (rule) → decide`.

Only what must be machine-checked is structured (coordinates, tolerances, ground truth, output columns); everything else is free text the assistant reads. Step types are data files in [`spec/types/`](spec/types) — add one to support a new kind of job. See [`spec/README.md`](spec/README.md).

### Workflow

1. `describe_task` → task folder filled in, `avs_task_check` clean.
2. The assistant writes the `.avcode` program; it appends one row per image to `results.csv`; `avs_validate` passes.
3. You press Run in Studio (`avs_open`).
4. `avs_evaluate` → error vs tolerance, escapes / false rejects → the assistant fixes the program → repeat.

## Example

[`examples/hole_distance`](examples/hole_distance) — center-to-center distance of two holes in a backlit plate: task spec, annotations, expected values and a validated program (`ThresholdToRegion → RegionHoles → FitCircleToEdges → PointToPointDistance → SaveText`).

## Limitations / roadmap

- [ ] **`avs_run`** — run a project headlessly and collect its results (no headless runner is installed with Studio; Executor or a generated .NET interface are the candidates). Until then you press Run.
- [ ] Local annotation UI that writes the task folder (draw measurements, ROIs and defects on images).
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

MCP server giúp trợ lý AI viết và kiểm tra chương trình **Aurora Vision Studio**: tra filter trong tài liệu và các ví dụ chính hãng có trên máy, nạp project không cần mở giao diện để bắt lỗi, và mô tả bài toán machine vision bằng một **thư mục task** (`task.toml` + ảnh có vẽ đánh dấu + `expected.csv` đáp án) để kết quả được so tự động.

- Cài: clone repo, thêm vào `.mcp.json` như trên. Cần Python 3.12+, không cần cài thêm thư viện.
- Mô tả bài toán: gọi prompt `describe_task`, trợ lý sẽ hỏi từng ý (đo gì, chi tiết trông thế nào, ảnh thay đổi ra sao, dung sai, đáp án mẫu…) rồi tạo thư mục task.
- Bài toán phức tạp = chuỗi bước, mỗi bước một loại (`measure`, `defect`, …) và ghi rõ `rule` / `deep`.
- Chưa có: tự chạy project không cần giao diện (`avs_run`) và giao diện vẽ đánh dấu — xem roadmap.
