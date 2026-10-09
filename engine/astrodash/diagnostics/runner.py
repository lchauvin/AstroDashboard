"""Run astro-eval's session analysis on one night folder and return plain JSON.

Mirrors astro_eval.cli._run_session step for step — scan_session → group_session
→ per-frame compute_metrics (lights in auto mode, flats in flat mode) →
evaluate_session per light group and flat set → flat_set_summary /
calibration_summary — except that nothing is written: no CSV, no HTML.
"""

from __future__ import annotations

import math
import os
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import sky
from ..layout import COPY_DIRS, IGNORED_SESSION_DIRS
from .astro_eval_bridge import build_config, ensure_on_path, worker_init
from .flatmatch import compare as compare_flats

Progress = Callable[[str, int, int], None]

LIGHT_FIELDS = [
    "mode", "filter_name", "exptime", "obs_time", "altitude_deg",
    "background_median", "background_rms", "noise_mad",
    "background_gradient", "background_gradient_global", "background_gradient_local",
    "n_stars", "fwhm_median", "eccentricity_median", "psf_residual_median",
    "snr_weight", "psf_signal_weight", "wfwhm", "moffat_beta", "snr_estimate",
    "elongation_direction", "elongation_consistency",
    "transparency", "relative_snr", "region_transparency_min", "n_matched",
    "n_trails", "trail_length_fraction", "trail_type", "settings_note", "ref_transform",
]
FLAT_FIELDS = [
    "filter_name", "exptime", "obs_time", "level_adu", "level_fraction", "saturation_fraction",
    "bottom_clipping_fraction", "flat_snr", "flat_snr_rel", "smoothness_excess",
    "vignetting_depth", "histogram_peak", "histogram_fwhm",
]

# astro-eval's advice to calibrate Full-Resolution flats with bias at the flats' own
# settings contradicts the intended workflow (HDR bias/darks); see PLAN / user decision.
_FLAT_SETTINGS_NOTE = "Flats use a different"


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 6)
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if hasattr(v, "item") and callable(v.item):        # numpy scalar
        return _clean(v.item())
    return v


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _frame_row(r, fields: list[str], data_root: Path, site: tuple[float, float] | None) -> dict[str, Any]:
    m = r.metrics
    row = {"file": m.filename, "path": _rel(m.filepath, data_root)}
    for f in fields:
        row[f] = _clean(getattr(m, f, None))
    row["score"] = _clean(r.score)
    row["rejected"] = bool(r.rejection.rejected)
    row["reasons"] = r.rejection.rejection_reasons
    row["warnings"] = list(m.warnings or [])
    row["sunAlt"] = None
    if site and m.obs_time:
        try:
            t = datetime.fromisoformat(str(m.obs_time)[:26])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            row["sunAlt"] = round(sky.sun_altitude(sky.julian_day(t), site[0], site[1]), 1)
        except ValueError:
            pass
    return row


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept = [r for r in rows if not r["rejected"]]

    def med(key: str):
        vals = [r[key] for r in kept if isinstance(r.get(key), (int, float))]
        return round(statistics.median(vals), 4) if vals else None

    reasons: dict[str, int] = {}
    for r in rows:
        for reason in r["reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    seconds = sum(r.get("exptime") or 0 for r in kept)
    return {
        "frames": len(rows), "kept": len(kept), "rejected": len(rows) - len(kept),
        "keptSeconds": seconds, "reasons": reasons,
        "median": {k: med(k) for k in ("fwhm_median", "eccentricity_median", "n_stars", "background_median",
                                         "noise_mad", "transparency", "snr_estimate", "score")},
    }


def run_session(
    session_dir: Path,
    data_root: Path,
    astro_eval_path: Path,
    site: tuple[float, float] | None = None,
    workers: int | None = None,
    progress: Progress | None = None,
    intended_flat_workflow: bool = True,
) -> dict[str, Any]:
    ensure_on_path(astro_eval_path)
    from astro_eval import __version__ as ae_version
    from astro_eval.cli import _odd_settings_result, _process_frame
    from astro_eval.scoring import evaluate_session
    from astro_eval.session import calibration_summary, flat_set_summary, group_session, scan_session

    progress = progress or (lambda *_: None)
    config, weights, pixel_size_um, focal_length, config_path = build_config(session_dir)

    progress("scanning", 0, 0)
    infos = scan_session(session_dir)
    # astro-eval only skips _REJECTED; drop astro-eval's "Accepted" copies and Snapshot
    # frames too, exactly as the Library does, so nothing is counted twice.
    skip = COPY_DIRS | IGNORED_SESSION_DIRS
    infos = {
        k: v for k, v in infos.items()
        if not any(part.casefold() in skip for part in Path(k).relative_to(session_dir).parts[:-1])
    }
    groups = group_session(infos.values())

    tasks = [(str(f.path), "auto") for g in groups.lights.values() for f in g.frames]
    tasks += [(str(f.path), "flat") for fs in groups.flats.values() for f in fs.frames]
    metrics: dict[str, Any] = {}
    errors: list[str] = []
    n_workers = workers or max(1, (os.cpu_count() or 2) - 1)
    progress("measuring", 0, len(tasks))
    if tasks:
        with ProcessPoolExecutor(max_workers=n_workers, initializer=worker_init,
                                 initargs=(str(astro_eval_path),)) as pool:
            futures = {
                pool.submit(_process_frame, (path, focal_length, pixel_size_um, mode, config)): path
                for path, mode in tasks
            }
            for done, fut in enumerate(as_completed(futures), 1):
                try:
                    m, name, err = fut.result()
                except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the run
                    m, name, err = None, Path(futures[fut]).name, f"{type(exc).__name__}: {exc}"
                if err:
                    errors.append(f"{name}: {err}")
                elif m is not None:
                    metrics[m.filepath] = m
                progress("measuring", done, len(tasks))

    progress("scoring", len(tasks), len(tasks))
    lights_out = []
    for key, g in groups.lights.items():
        ms = [metrics[str(f.path)] for f in g.frames if str(f.path) in metrics]
        main = [m for m in ms if m.filepath not in g.odd]
        if not main:
            continue
        stats, results = evaluate_session(main, config, weights)
        results += [_odd_settings_result(m, stats, config, weights, g.odd[m.filepath]) for m in ms if m.filepath in g.odd]
        rows = sorted((_frame_row(r, LIGHT_FIELDS, data_root, site) for r in results), key=lambda r: r["obs_time"] or "")
        lights_out.append({
            "key": key, "filter": g.filter, "target": g.target,
            "summary": _group_summary(rows), "frames": rows,
        })

    flats_out = []
    summaries: dict[str, dict] = {}
    for key, fs in groups.flats.items():
        ms = [metrics[str(f.path)] for f in fs.frames if str(f.path) in metrics]
        if not ms:
            continue
        _, results = evaluate_session(ms, config, weights)
        summaries[key] = flat_set_summary(fs, results, config)
        rows = sorted((_frame_row(r, FLAT_FIELDS, data_root, None) for r in results), key=lambda r: r["obs_time"] or "")
        flats_out.append({"key": key, "filter": fs.filter, "frames": rows})

    calibration = calibration_summary(groups, summaries) if (groups.lights or groups.flats) else None
    if calibration and intended_flat_workflow:
        for row in calibration["rows"]:
            row["issues"] = [i for i in row["issues"] if not i["text"].startswith(_FLAT_SETTINGS_NOTE)]
        calibration["common"] = [i for i in calibration["common"] if not i["text"].startswith(_FLAT_SETTINGS_NOTE)]
    for f in flats_out:
        f["summary"] = summaries.get(f["key"])

    # Pixel-level flat ↔ light comparison, per light group, with the flat set astro-eval chose.
    flat_match = []
    rows_by_light = {r["light"]: r for r in (calibration or {}).get("rows", [])}
    flats_by_key = {f["key"]: f for f in flats_out}
    for i, g in enumerate(lights_out):
        progress("flat check", i, len(lights_out))
        row = rows_by_light.get(g["key"])
        fs = flats_by_key.get(row["flat"]) if row and row.get("flat") else None
        if not fs:
            continue
        lights = sorted((x for x in g["frames"] if not x["rejected"]), key=lambda x: -(x["score"] or 0))
        flats = [x for x in fs["frames"] if not x["rejected"]] or fs["frames"]
        if len(lights) < 3 or len(flats) < 3:
            continue
        try:
            res = compare_flats([str(data_root / x["path"]) for x in lights], [str(data_root / x["path"]) for x in flats])
        except Exception as exc:  # noqa: BLE001 - informative check, never fatal
            res = {"verdict": "unknown", "notes": [f"Could not compare: {type(exc).__name__}: {exc}"]}
        flat_match.append({"light": g["key"], "filter": g["filter"], "flatSet": fs["key"], **res})

    return _clean({
        "astroEvalVersion": ae_version,
        "configPath": config_path,
        "measured": len(metrics),
        "errors": errors,
        "lights": lights_out,
        "flats": flats_out,
        "calibration": calibration,
        "flatMatch": flat_match,
    })
