"""Access to the sibling astro-eval project (D:\\Python\\AstroSubQualityEvaluation).

astro-eval is put on sys.path rather than installed: its repository keeps
`Output/` and `staging/` next to the package, which setuptools refuses to build
(the same reason AstroMaster wires it by path). Only its measuring, scoring and
session-grouping modules are used — never `report`, `report_html` or the CLI's
report-writing paths, which write into the session folder.

The EvalConfig / ScoringWeights built here mirror astro-eval's CLI exactly
(same defaults, same astro_eval.toml keys and search order), so the numbers
match running `astro-eval <session> ` by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def ensure_on_path(astro_eval_path: Path) -> None:
    p = str(astro_eval_path)
    if not (astro_eval_path / "astro_eval" / "__init__.py").is_file():
        raise FileNotFoundError(f"astro-eval not found at {astro_eval_path} (set [diagnostics] astro_eval_path)")
    if p not in sys.path:
        sys.path.insert(0, p)


def worker_init(astro_eval_path: str) -> None:
    """ProcessPoolExecutor initializer: spawned workers need the path too."""
    ensure_on_path(Path(astro_eval_path))


# CLI defaults and their astro_eval.toml keys (astro_eval/cli.py main()).
_DEFAULTS: dict[str, Any] = {
    "bortle": 0, "focal_length": 250.0, "fwhm_threshold": 0.0, "ecc_threshold": 0.6,
    "star_fraction": 0.7, "snr_fraction": 0.5, "sigma_fwhm": 2.0, "sigma_noise": 2.5,
    "sigma_bg": 3.0, "sigma_residual": 3.0, "sigma_gradient": 2.0, "gradient_threshold": 0.0,
    "gradient_knee": 1.2, "min_score": 0.7, "min_relative_snr": 0.70,
    "min_region_transparency": 0.50, "min_deviation": 0.10, "detection_threshold": 5.0,
    "mode": "auto", "flat_target_level": 0.40, "flat_snr_target": 200.0, "flat_max_saturation": 0.01,
}
_CFG_MAP = {
    "bortle": "sky.bortle", "focal_length": "telescope.focal_length_mm",
    "fwhm_threshold": "rejection.fwhm_threshold_arcsec", "ecc_threshold": "rejection.ecc_threshold",
    "star_fraction": "rejection.star_count_fraction", "snr_fraction": "rejection.snr_fraction",
    "sigma_fwhm": "rejection.sigma_fwhm", "sigma_noise": "rejection.sigma_noise",
    "sigma_bg": "rejection.sigma_bg", "sigma_residual": "rejection.sigma_residual",
    "sigma_gradient": "rejection.sigma_gradient", "gradient_threshold": "rejection.gradient_threshold",
    "gradient_knee": "rejection.gradient_knee", "min_score": "rejection.min_score",
    "min_relative_snr": "rejection.min_relative_snr",
    "min_region_transparency": "rejection.min_region_transparency",
    "min_deviation": "rejection.min_deviation", "detection_threshold": "processing.detection_threshold",
    "mode": "processing.mode", "flat_target_level": "rejection.flat.target_level",
    "flat_snr_target": "rejection.flat.snr_target", "flat_max_saturation": "rejection.flat.max_saturation",
}


def build_config(session_dir: Path):
    """(EvalConfig, ScoringWeights, pixel_size_um, focal_length, config_path) as the CLI would build them."""
    from astro_eval.config_loader import find_config_file, load_config
    from astro_eval.metrics import EvalConfig, ScoringWeights

    path = find_config_file(session_dir)
    raw = load_config(path) if path else {}
    a = {k: raw.get(_CFG_MAP[k], v) for k, v in _DEFAULTS.items()}

    config = EvalConfig(
        bortle=a["bortle"], focal_length_mm=a["focal_length"], detection_threshold=a["detection_threshold"],
        fwhm_threshold_arcsec=a["fwhm_threshold"], ecc_threshold=a["ecc_threshold"],
        star_count_fraction=a["star_fraction"], snr_fraction=a["snr_fraction"], sigma_fwhm=a["sigma_fwhm"],
        sigma_noise=a["sigma_noise"], sigma_bg=a["sigma_bg"], sigma_residual=a["sigma_residual"],
        sigma_gradient=a["sigma_gradient"], gradient_threshold=a["gradient_threshold"],
        gradient_knee=a["gradient_knee"], min_score=a["min_score"], min_relative_snr=a["min_relative_snr"],
        min_region_transparency=a["min_region_transparency"], min_deviation=a["min_deviation"],
        mode=a["mode"], verbose=False,
        flat_target_level=raw.get("rejection.flat.target_level", a["flat_target_level"]),
        flat_snr_target=raw.get("rejection.flat.snr_target", a["flat_snr_target"]),
        flat_min_level=raw.get("rejection.flat.min_level", 0.10),
        flat_max_level=raw.get("rejection.flat.max_level", 0.85),
        flat_max_saturation=raw.get("rejection.flat.max_saturation", a["flat_max_saturation"]),
        flat_min_snr=raw.get("rejection.flat.min_snr", 0.0),
        flat_min_master_snr=raw.get("rejection.flat.min_master_snr", 100.0),
        flat_readout_mode=str(raw.get("rejection.flat.readout_mode", "Full Resolution")),
    )
    weights = ScoringWeights(
        star_fwhm=raw.get("scoring.star.weight_fwhm", 0.30),
        star_ecc=raw.get("scoring.star.weight_ecc", 0.25),
        star_stars=raw.get("scoring.star.weight_stars", 0.20),
        star_snr=raw.get("scoring.star.weight_snr", 0.00),
        star_psfsw=raw.get("scoring.star.weight_psfsw", 0.25),
        gas_snr=raw.get("scoring.gas.weight_snr", 0.30),
        gas_noise=raw.get("scoring.gas.weight_noise", 0.20),
        gas_bg=raw.get("scoring.gas.weight_bg", 0.15),
        gas_stars=raw.get("scoring.gas.weight_stars", 0.20),
        gas_psfsw=raw.get("scoring.gas.weight_psfsw", 0.15),
        flat_exposure=raw.get("scoring.flat.weight_exposure", 0.40),
        flat_snr=raw.get("scoring.flat.weight_snr", 0.30),
        flat_saturation=raw.get("scoring.flat.weight_saturation", 0.15),
        flat_smoothness=raw.get("scoring.flat.weight_smoothness", 0.15),
    )
    return config, weights, raw.get("camera.pixel_size_um"), a["focal_length"], (str(path) if path else None)
