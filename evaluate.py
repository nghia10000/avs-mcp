"""evaluate.py — compare results.csv written by the AVS program with expected.csv.

expected.csv may have a 'split' column: rows marked 'holdout' are reported separately, so accuracy on
images the program was not tuned on is visible. Numeric result columns that are not in expected.csv
(measured features such as a gap brightness) get a margin report: how far each NG image is from the
range of the OK images, and which images sit close to the boundary.
"""

import csv
import math
import os
import random

from task_spec import inside, load_task, read_csv, step_columns

OK_NG = {"OK", "NG"}
SPLIT = "split"


def to_float(s):
    try:
        v = float(str(s).strip())
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def stem(k):
    return os.path.splitext(os.path.basename(str(k).replace("\\", "/")))[0].lower()


def is_holdout(row):
    return (row.get(SPLIT) or "").strip().lower() == "holdout"


def fmt(v):
    return f"{v:.4g}"


def cell(row, c):
    """Stripped text of a CSV cell; '' when missing (rows shorter than the header give None, longer ones a list
    under the None key)."""
    v = row.get(c)
    return v.strip() if isinstance(v, str) else ""


def verdict_values(rows, c):
    """True when column c holds OK/NG verdicts: every filled cell is OK or NG (blank = not marked) and one is filled."""
    vals = {cell(r, c) for r in rows} - {""}
    return bool(vals) and vals <= OK_NG


def task_files(task_dir, results=None, expected=None, key=None):
    spec = load_task(task_dir) if os.path.exists(os.path.join(task_dir, "task.toml")) else {}
    out_sec = spec.get("output", {})
    results = os.path.join(task_dir, results or out_sec.get("results_csv", "results.csv"))
    expected = os.path.join(task_dir, expected or spec.get("expected", {}).get("file", "expected.csv"))
    return spec, results, expected, key or out_sec.get("key", "image")


def compare(keys, exp, res, cols, tol):
    """Lines and failed flag for the expected columns over the given images. A blank expected cell means "no ground
    truth for this image in this column" (e.g. no measurement on an NG part, an image not marked yet): skipped."""
    out, failed = [], False
    if not keys:
        return out, failed
    for c in cols:
        pairs = [(k, exp[k][c], res[k].get(c)) for k in keys if cell(exp[k], c)]
        if not pairs:
            out.append(f"\n[{c}] no expected values for these images")
            continue
        if all(r is None for _, _, r in pairs):
            out.append(f"\n[{c}] not in results.csv")
            failed = True
            continue
        nums = [(k, to_float(e), to_float(r)) for k, e, r in pairs]
        if all(e is not None for _, e, _ in nums):
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
                    if not over:
                        worst = max(abs_e)
                        out.append(f"  margin: worst error uses {worst / tol[c]:.0%} of the tolerance")
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
    return out, failed


def margins(keys, exp, res, verdict_col, features):
    """How well each measured feature separates OK from NG images (by the expected verdict)."""
    v = lambda k: cell(exp[k], verdict_col)                     # blank = not marked
    ok = [k for k in keys if v(k) == "OK"]
    ng = [k for k in keys if v(k) == "NG"]
    if not ok or not ng or not features:
        return []
    out = [f"\nMargins for [{verdict_col}] over {len(ok)} OK / {len(ng)} NG image(s) "
           f"(features: numeric result columns not in the expected file)"]
    stats = {}
    for f in features:
        okv = [v for v in (to_float(res[k].get(f)) for k in ok) if v is not None]
        ngv = [v for v in (to_float(res[k].get(f)) for k in ng) if v is not None]
        if not okv or not ngv:
            continue
        lo, hi = min(okv), max(okv)
        high = sorted(ngv)[len(ngv) // 2] >= sorted(okv)[len(okv) // 2]   # NG side: above or below the OK range
        spread = hi - lo
        stats[f] = (lo, hi, high, spread)
        beyond = sum(1 for v in ngv if (v > hi if high else v < lo))
        out.append(f"  {f}: OK {fmt(lo)}..{fmt(hi)}, NG {fmt(min(ngv))}..{fmt(max(ngv))}; "
                   f"NG {'above' if high else 'below'} the OK range: {beyond}/{len(ngv)}")
    if not stats:
        return []

    def gap(f, k):
        v = to_float(res[k].get(f))
        if v is None:
            return None
        lo, hi, high, _ = stats[f]
        return v - hi if high else lo - v

    def score(f, g):                                  # margin in units of the OK spread, so features compare
        lo, hi, _, spread = stats[f]
        return g / max(spread, 1e-3 * max(1.0, abs(lo), abs(hi)))

    rows, unexplained = [], []
    for k in ng:
        hits = sorted(((score(f, gap(f, k)), gap(f, k), f) for f in stats
                       if gap(f, k) is not None and gap(f, k) > 0), reverse=True)
        if hits:
            rows.append((hits[0][0], k, hits))
        else:
            unexplained.append(k)
    rows.sort()
    out.append("  NG images, weakest first: every feature outside the OK range "
               "(margin; in units of the OK spread when it is > 0):")
    for s, k, hits in rows[:10]:
        parts = []
        for hs, g, f in hits[:3]:
            lo, hi, high, spread = stats[f]
            rel = f" = {g / spread:.2g}x spread" if spread > 0 else ""
            parts.append(f"{f} {fmt(to_float(res[k][f]))} (OK {'max' if high else 'min'} {fmt(hi if high else lo)}, "
                         f"margin {fmt(g)}{rel})")
        warn = "  <- CLOSE to the OK range" if s < 2 else ""
        out.append(f"    {k}: " + "; ".join(parts) + warn)
    if unexplained:
        out.append(f"  NG images inside the OK range of every feature (not separated by any measured value): "
                   f"{', '.join(unexplained[:10])}")
    if len(ok) < 10:
        out.append(f"  Only {len(ok)} OK image(s): the real OK range is probably wider than measured here, "
                   "so small margins are likely to fail on new images.")
    out.append("  Margins are measured on the images used for tuning; check the holdout images and add OK/NG "
               "samples where the margin is small before trusting a threshold.")
    return out


def evaluate(task_dir, results=None, expected=None, key=None, tolerances=None):
    spec, results, expected, key = task_files(task_dir, results, expected, key)
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
    exp_cols = list(next(iter(exp.values())).keys()) if exp else []
    cols = [c for c in exp_cols if c not in (key, SPLIT, None)]      # None: cells beyond the header
    matched = [k for k in exp if k in res]
    tune = [k for k in matched if not is_holdout(exp[k])]
    hold = [k for k in matched if is_holdout(exp[k])]
    n_hold = sum(1 for r in exp.values() if is_holdout(r))

    out = [f"{len(exp)} expected row(s), {len(res)} result row(s), {len(matched)} matched."]
    if missing:
        out.append(f"No result for {len(missing)} image(s): {', '.join(missing[:10])}")
    if extra:
        out.append(f"Results without ground truth (ignored): {len(extra)}")
    failed = bool(missing)
    if not any(cell(exp[k], c) for k in matched for c in cols):
        out.append("No ground truth: every expected cell of the matched images is blank, so nothing can be checked. "
                   "Mark the images (avs_annotate) or fill expected.csv.")
        failed = True

    if n_hold:
        out.append(f"\n=== TUNE images ({len(tune)}): use these to develop and set thresholds ===")
    else:
        out.append("No holdout images (expected.csv has no 'split' = holdout rows): every image is used for tuning, "
                   "so accuracy on new images is unknown. Create a split with avs_split.")
    lines, f1 = compare(tune, exp, res, cols, tol)
    out += lines
    failed = failed or f1

    verdict_cols = [c for c in cols if verdict_values(exp.values(), c)]
    res_cols = list(next(iter(res.values())).keys()) if res else []
    features = [c for c in res_cols if c not in exp_cols and c not in (key, None)
                and any(to_float(r.get(c)) is not None for r in res.values())]
    for vc in verdict_cols:
        out += margins(tune, exp, res, vc, features)

    if hold:
        out.append(f"\n=== HOLDOUT images ({len(hold)}): not for tuning; this is the estimate for new images ===")
        lines, f2 = compare(hold, exp, res, cols, tol)
        out += lines
        failed = failed or f2

    out.insert(0, "FAIL" if failed else "PASS")
    return "\n".join(out), failed          # FAIL is an error result, so agents that check isError do not stop early


def split(task_dir, fraction=0.3, seed=0, expected=None, key=None):
    """Mark about `fraction` of the images in expected.csv as holdout, keeping the OK/NG ratio."""
    spec, _, expected, key = task_files(task_dir, None, expected, key)
    if not inside(expected, task_dir) or not expected.lower().endswith(".csv"):   # split rewrites this file
        return f"The expected file must be a .csv inside the task folder, got {os.path.realpath(expected)}.", True
    if not os.path.exists(expected):
        return f"File not found: {expected}. Mark the images first (avs_annotate).", True
    if not 0 < fraction < 1:
        return "fraction must be between 0 and 1.", True
    rows = read_csv(expected)
    if not rows or key not in rows[0]:
        return f"{expected} has no '{key}' column.", True
    fields = [c for c in rows[0].keys() if c is not None]           # None: cells beyond the header (dropped)
    if SPLIT not in fields:
        fields.append(SPLIT)
    verdict = next((c for c in fields if c not in (key, SPLIT) and verdict_values(rows, c)), None)
    groups = {}
    for r in rows:
        groups.setdefault(cell(r, verdict) if verdict else "", []).append(r)
    rng = random.Random(seed)
    out = []
    for g, rs in sorted(groups.items()):
        rs = sorted(rs, key=lambda r: r[key])
        rng.shuffle(rs)
        n = min(round(len(rs) * fraction), len(rs) - 1) if len(rs) > 1 else 0   # keep >= 1 of each kind to tune on
        for i, r in enumerate(rs):
            r[SPLIT] = "holdout" if i < n else "tune"
        out.append(f"{g or 'all'}: {n} holdout / {len(rs) - n} tune")
    with open(expected, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    held = sorted(r[key] for r in rows if r[SPLIT] == "holdout")
    return (f"Wrote '{SPLIT}' column to {expected} ({'; '.join(out)}).\n"
            f"Holdout images: {', '.join(held) or 'none'}.\n"
            "Develop and set thresholds on the tune images only; read the holdout section of avs_evaluate as the "
            "estimate for new images, and do not tune on it."), False
