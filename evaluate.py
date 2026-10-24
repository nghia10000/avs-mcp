"""evaluate.py — compare results.csv written by the AVS program with expected.csv."""

import math
import os

from task_spec import load_task, read_csv, step_columns

OK_NG = {"OK", "NG"}


def to_float(s):
    try:
        v = float(str(s).strip())
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def stem(k):
    return os.path.splitext(os.path.basename(str(k).replace("\\", "/")))[0].lower()


def evaluate(task_dir, results=None, expected=None, key=None, tolerances=None):
    spec = load_task(task_dir) if os.path.exists(os.path.join(task_dir, "task.toml")) else {}
    out_sec = spec.get("output", {})
    results = os.path.join(task_dir, results or out_sec.get("results_csv", "results.csv"))
    expected = os.path.join(task_dir, expected or spec.get("expected", {}).get("file", "expected.csv"))
    key = key or out_sec.get("key", "image")
    tol = {}
    for st in spec.get("steps", []):
        if isinstance(st.get("tolerance"), (int, float)):
            for c in step_columns(st):
                tol[c] = float(st["tolerance"])
    tol.update(tolerances or {})

    for p in (results, expected):
        if not os.path.exists(p):
            return f"File not found: {p}", True
    res = {r.get(key): r for r in read_csv(results)}
    exp = {r.get(key): r for r in read_csv(expected)}
    if None in res or None in exp:
        return f"Key column '{key}' missing in results or expected CSV.", True
    # programs often write the file name without folder or extension
    by_stem = {stem(k): k for k in exp}
    res = {(k if k in exp else by_stem.get(stem(k), k)): r for k, r in res.items()}

    missing = [k for k in exp if k not in res]
    extra = [k for k in res if k not in exp]
    cols = [c for c in (next(iter(exp.values())).keys() if exp else []) if c != key]
    out = [f"{len(exp)} expected row(s), {len(res)} result row(s), {len(exp) - len(missing)} matched."]
    if missing:
        out.append(f"No result for {len(missing)} image(s): {', '.join(missing[:10])}")
    if extra:
        out.append(f"Results without ground truth (ignored): {len(extra)}")
    failed = bool(missing)

    for c in cols:
        pairs = [(k, exp[k][c], res[k].get(c)) for k in exp if k in res]
        if all(r is None for _, _, r in pairs):
            out.append(f"\n[{c}] not in results.csv")
            failed = True
            continue
        nums = [(k, to_float(e), to_float(r)) for k, e, r in pairs]
        if pairs and all(e is not None for _, e, _ in nums):
            errs = [(k, r - e) for k, e, r in nums if r is not None]
            bad_read = [k for k, _, r in nums if r is None]
            out.append(f"\n[{c}] numeric, {len(errs)} value(s)")
            if errs:
                abs_e = [abs(d) for _, d in errs]
                mean = sum(d for _, d in errs) / len(errs)
                out.append(f"  error: mean {mean:+.4f}, mean |e| {sum(abs_e) / len(abs_e):.4f}, max |e| {max(abs_e):.4f}")
                if c in tol:
                    over = [(k, d) for k, d in errs if abs(d) > tol[c]]
                    out.append(f"  tolerance ±{tol[c]}: {len(errs) - len(over)}/{len(errs)} within")
                    out += [f"    OUT {k}: error {d:+.4f}" for k, d in sorted(over, key=lambda x: -abs(x[1]))[:10]]
                    failed = failed or bool(over)
                worst = sorted(errs, key=lambda x: -abs(x[1]))[:3]
                out.append("  worst: " + ", ".join(f"{k} {d:+.4f}" for k, d in worst))
            if bad_read:
                out.append(f"  non-numeric result for: {', '.join(bad_read[:10])}")
                failed = True
            continue

        norm = [(k, str(e).strip(), str(r).strip() if r is not None else "") for k, e, r in pairs]
        wrong = [(k, e, r) for k, e, r in norm if e != r]
        out.append(f"\n[{c}] categorical: {len(norm) - len(wrong)}/{len(norm)} correct")
        if {e for _, e, _ in norm} <= OK_NG:
            esc = [k for k, e, r in norm if e == "NG" and r != "NG"]
            fr = [k for k, e, r in norm if e == "OK" and r != "OK"]
            n_ng = sum(1 for _, e, _ in norm if e == "NG")
            n_ok = len(norm) - n_ng
            out.append(f"  escapes (NG passed): {len(esc)}/{n_ng}" + (f" -> {', '.join(esc[:10])}" if esc else ""))
            out.append(f"  false rejects (OK rejected): {len(fr)}/{n_ok}" + (f" -> {', '.join(fr[:10])}" if fr else ""))
        out += [f"    {k}: expected {e!r}, got {r!r}" for k, e, r in wrong[:10]]
        failed = failed or bool(wrong)

    out.insert(0, "FAIL" if failed else "PASS")
    return "\n".join(out), False
