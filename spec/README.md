# Task spec format

## task.toml

```toml
[task]          # name, images, notes
[conditions]    # position, rotation, lighting, variants
[calibration]   # mm_per_px, reference — required when a step uses unit = "mm"
[output]        # results_csv, key, destination, on_missing
[expected]      # file = "expected.csv"

[[features]]    # optional: id, looks_like, count, confused_with

[[steps]]       # one per step
id = "..."
type = "measure"            # a file in types/
method = "rule"             # rule | deep | hybrid
after = ["align"]           # steps this one needs
# ...fields of the type
```

Fields and their meaning are defined in [`common.toml`](common.toml) and [`types/*.toml`](types). `avs_task_new` writes a skeleton with a `TODO` for every required field; `avs_task_check` lists what is still missing. Unknown fields are allowed and reported as warnings — they are kept as free information.

Result columns come from each step's `column` / `columns`. The program writes them to `results_csv`, one row per image, keyed by `output.key`; `expected.csv` uses the same columns.

## annotations.json

```json
{
  "images": {
    "img_001.png": [
      {"kind": "line", "points": [[200, 251], [520, 252]], "step": "hole_distance", "label": "center to center"},
      {"kind": "rect", "points": [[100, 150], [600, 350]], "step": "scratch", "label": "roi"},
      {"kind": "polygon", "points": [[310, 200], [330, 205], [325, 220]], "label": "scratch"}
    ]
  }
}
```

`kind`: `point`, `line`, `rect` (two corners), `circle` (center + point on the circle), `polygon`. `step` / `feature` link the drawing to the spec; `label` is free (defect class, `roi`, …). Coordinates are image pixels.

## expected.csv

```
image,distance_px,result
img_001.png,319.93,OK
img_002.png,321.40,NG
```

Numeric columns are compared against the step `tolerance`; columns with only `OK` / `NG` also report escapes (NG passed) and false rejects (OK rejected); other columns must match exactly. Image names may differ by folder or extension between results and expected.

## Adding a step type

Create `types/<name>.toml`:

```toml
type = "<name>"
summary = "One line: what this type covers."
default_method = "rule"

[fields.<field>]
required = true
description = "What the user must tell."
example = "a realistic value"
```

No code change is needed.
