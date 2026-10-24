"""task_spec.py — task folder (task.toml + annotations.json + expected.csv): create and check.

Section and step-type definitions are data in spec/common.toml and spec/types/*.toml.
"""

import csv
import json
import os
import tomllib

SPEC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec")
IMAGE_EXT = (".png", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff")
ANNOTATION_KINDS = ("point", "line", "rect", "circle", "polygon")
TODO = "TODO"


def load_defs():
    with open(os.path.join(SPEC_DIR, "common.toml"), "rb") as f:
        common = tomllib.load(f)
    types = {}
    tdir = os.path.join(SPEC_DIR, "types")
    for fn in sorted(os.listdir(tdir)):
        if fn.endswith(".toml"):
            with open(os.path.join(tdir, fn), "rb") as f:
                t = tomllib.load(f)
            types[t["type"]] = t
    return common, types


def list_types():
    _, types = load_defs()
    out = ["Step types (spec/types/*.toml):"]
    for name, t in types.items():
        req = [k for k, v in t["fields"].items() if v.get("required")]
        out.append(f"- {name} [{t.get('default_method', 'rule')}]  {t['summary']}  required: {', '.join(req)}")
    return "\n".join(out)

# ------------------------------------------------------------------ new

def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def todo_value(example):
    """Placeholder of the same shape as the example, so the file stays valid TOML of the right type."""
    if isinstance(example, list):
        return [f"{TODO}: {x}" for x in example] or [TODO]
    return f"{TODO}: {example}"


def field_lines(fields, overrides=None):
    """Required fields get the override, the default or a TODO placeholder; optional ones are commented out."""
    lines = []
    for key, d in fields.items():
        lines.append(f"# {d['description']}")
        if overrides and key in overrides:
            lines.append(f"{key} = {toml_value(overrides[key])}")
        elif "default" in d:
            lines.append(f"{key} = {toml_value(d['default'])}")
        elif d.get("required"):
            lines.append(f"{key} = {toml_value(todo_value(d.get('example', '')))}")
        else:
            lines.append(f"# {key} = {toml_value(d.get('example', ''))}")
    return lines


def new_task(task_dir, step_types, name=""):
    common, types = load_defs()
    unknown = [t for t in step_types if t not in types]
    if unknown:
        return f"Unknown step type(s): {', '.join(unknown)}.\n{list_types()}", True
    if not step_types:
        return "Give at least one step type.\n" + list_types(), True
    path = os.path.join(task_dir, "task.toml")
    if os.path.exists(path):
        return f"{path} already exists; edit it or pick another folder.", True
    os.makedirs(os.path.join(task_dir, "images"), exist_ok=True)

    lines = ["# Task spec — fill every TODO, then run avs_task_check.", ""]
    for sec, sdef in common["sections"].items():
        lines += [f"# {sdef['summary']}", f"[{sec}]"]
        lines += field_lines(sdef["fields"], {"name": name} if sec == "task" and name else None)
        lines.append("")
    lines += [f"# {common['feature']['summary']}", "# [[features]]"]
    lines += ["# " + l for l in field_lines(common["feature"]["fields"])]
    lines.append("")
    for i, t in enumerate(step_types, 1):
        tdef = types[t]
        lines += [f"# Step {i}: {t} — {tdef['summary']}", "[[steps]]"]
        lines += field_lines(common["step"]["fields"], {
            "id": f"{TODO}: step{i}", "type": t, "method": tdef.get("default_method", "rule")})
        lines += field_lines(tdef["fields"])
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    ann = os.path.join(task_dir, "annotations.json")
    if not os.path.exists(ann):
        with open(ann, "w", encoding="utf-8") as f:
            json.dump({"images": {}}, f, indent=2)
    return (f"Created {path}, annotations.json and images/.\n"
            "Next: copy sample images into images/, fill the TODOs, add expected.csv, run avs_task_check."), False

# ------------------------------------------------------------------ check

def is_todo(v):
    if isinstance(v, list):
        return any(is_todo(x) for x in v)
    return isinstance(v, str) and v.strip().upper().startswith(TODO)


def check_fields(where, values, fields, errors, warnings):
    for key, d in fields.items():
        if key not in values:
            if d.get("required"):
                errors.append(f"{where}: missing '{key}' — {d['description']}")
        elif is_todo(values[key]):
            errors.append(f"{where}: '{key}' still TODO — {d['description']}")
    for key in values:
        if key not in fields:
            warnings.append(f"{where}: unknown field '{key}' (kept as free information)")


def step_columns(step):
    cols = []
    if isinstance(step.get("column"), str):
        cols.append(step["column"])
    if isinstance(step.get("columns"), list):
        cols += [c for c in step["columns"] if isinstance(c, str)]
    return cols


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_task(task_dir):
    path = os.path.join(task_dir, "task.toml")
    if not os.path.exists(path):
        return f"No task.toml in {task_dir}. Create one with avs_task_new.", True
    try:
        with open(path, "rb") as f:
            spec = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        return f"task.toml is not valid TOML: {e}", True

    common, types = load_defs()
    errors, warnings = [], []

    for sec, sdef in common["sections"].items():
        vals = spec.get(sec)
        if vals is None:
            if any(d.get("required") for d in sdef["fields"].values()):
                errors.append(f"missing section [{sec}] — {sdef['summary']}")
            else:
                warnings.append(f"no [{sec}] section — {sdef['summary']}")
            continue
        check_fields(f"[{sec}]", vals, sdef["fields"], errors, warnings)

    feature_ids = set()
    for i, ft in enumerate(spec.get("features", []), 1):
        check_fields(f"features[{i}]", ft, common["feature"]["fields"], errors, warnings)
        feature_ids.add(ft.get("id"))

    steps = spec.get("steps", [])
    if not steps:
        errors.append("no [[steps]] — add at least one step")
    ids, columns, uses_mm = [], {}, False
    for i, st in enumerate(steps, 1):
        where = f"steps[{i}] ({st.get('id', '?')})"
        t = st.get("type")
        if t not in types:
            errors.append(f"{where}: unknown type '{t}'. Known: {', '.join(types)}")
            continue
        fields = dict(common["step"]["fields"], **types[t]["fields"])
        check_fields(where, st, fields, errors, warnings)
        if st.get("method") not in ("rule", "deep", "hybrid") and not is_todo(st.get("method")):
            errors.append(f"{where}: method must be rule, deep or hybrid")
        if st.get("id") in ids:
            errors.append(f"{where}: duplicate id")
        ids.append(st.get("id"))
        for c in step_columns(st):
            if not is_todo(c):
                columns[c] = st.get("id")
        uses_mm = uses_mm or st.get("unit") == "mm"
    for i, st in enumerate(steps, 1):
        for dep in st.get("after", []) or []:
            if dep not in ids:
                errors.append(f"steps[{i}] ({st.get('id')}): 'after' refers to unknown step '{dep}'")
    if uses_mm and not spec.get("calibration", {}).get("mm_per_px"):
        errors.append("a step uses unit = 'mm' but [calibration] mm_per_px is not set")

    # images
    images = []
    img_setting = spec.get("task", {}).get("images", "images/")
    img_dir = os.path.join(task_dir, str(img_setting))
    if is_todo(img_setting):
        pass                                          # already reported as TODO
    elif os.path.isdir(img_dir):
        images = sorted(f for f in os.listdir(img_dir) if f.lower().endswith(IMAGE_EXT))
        if not images:
            errors.append(f"no images in {img_dir}")
    else:
        errors.append(f"image folder not found: {img_dir}")

    # annotations
    ann_path = os.path.join(task_dir, "annotations.json")
    n_ann = 0
    if os.path.exists(ann_path):
        try:
            with open(ann_path, encoding="utf-8") as f:
                ann = json.load(f)
            for img, items in ann.get("images", {}).items():
                if images and img not in images:
                    errors.append(f"annotations.json: image '{img}' not in the image folder")
                for it in items:
                    n_ann += 1
                    if it.get("kind") not in ANNOTATION_KINDS:
                        errors.append(f"annotations.json [{img}]: kind must be one of {', '.join(ANNOTATION_KINDS)}")
                    ref = it.get("step") or it.get("feature")
                    if ref and ref not in ids and ref not in feature_ids:
                        warnings.append(f"annotations.json [{img}]: '{ref}' is not a step or feature id")
        except (json.JSONDecodeError, AttributeError) as e:
            errors.append(f"annotations.json is invalid: {e}")
    if n_ann == 0:
        warnings.append("no annotations — drawing what to measure/inspect on 1-2 images removes most ambiguity")

    # expected.csv
    key = spec.get("output", {}).get("key", "image")
    exp_file = spec.get("expected", {}).get("file")
    n_exp = 0
    if exp_file and not is_todo(exp_file):
        exp_path = os.path.join(task_dir, exp_file)
        if not os.path.exists(exp_path):
            errors.append(f"expected file not found: {exp_path}")
        else:
            rows = read_csv(exp_path)
            n_exp = len(rows)
            head = rows[0].keys() if rows else []
            if key not in head:
                errors.append(f"{exp_file}: no key column '{key}'")
            missing = [c for c in columns if c not in head]
            if missing:
                warnings.append(f"{exp_file}: no column for {', '.join(missing)} — those results cannot be evaluated")
            unknown = [r[key] for r in rows if images and r.get(key) not in images]
            if unknown:
                errors.append(f"{exp_file}: {len(unknown)} row(s) name images not in the folder, e.g. {unknown[0]}")
    else:
        warnings.append("no ground truth ([expected] file) — results can only be checked by eye")

    summary = (f"task '{spec.get('task', {}).get('name', '?')}': {len(steps)} step(s) "
               f"[{', '.join(f'{s.get('id')}:{s.get('type')}/{s.get('method')}' for s in steps)}], "
               f"{len(images)} image(s), {n_ann} annotation(s), {n_exp} expected row(s), "
               f"result columns: {', '.join(columns) or '-'}")
    head = "OK — spec is complete." if not errors else f"{len(errors)} problem(s) to fix."
    out = [head, summary]
    out += ["", "Errors:"] + [f"- {e}" for e in errors] if errors else []
    out += ["", "Warnings:"] + [f"- {w}" for w in warnings] if warnings else []
    with open(path, encoding="utf-8") as f:
        out += ["", "task.toml:", f.read()]
    return "\n".join(out), bool(errors)


def load_task(task_dir):
    with open(os.path.join(task_dir, "task.toml"), "rb") as f:
        return tomllib.load(f)
