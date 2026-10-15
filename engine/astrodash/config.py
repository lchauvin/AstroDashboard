"""astrodashboard.toml loader, and the .env file for secrets.

Search order: $ASTRODASH_CONFIG, then astrodashboard.toml at the project root
(one level above engine/). Every key has a default, so the file is optional.

Secrets (ASTROSPHERIC_API_KEY, TELESCOPIUS_API_KEY, ASTROCONTROLLER_TOKEN) stay out of the toml:
they come from the environment, or from a .env file at the project root.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FILTER_ALIASES = {
    "ha": "H", "h": "H", "halpha": "H", "h-alpha": "H", "h_alpha": "H",
    "oiii": "O", "o3": "O", "o-iii": "O", "o": "O",
    "sii": "S", "s2": "S", "s-ii": "S", "s": "S",
    "l": "L", "lum": "L", "luminance": "L", "clear": "L",
    "r": "R", "red": "R",
    "g": "G", "green": "G",
    "b": "B", "blue": "B",
}


@dataclass
class Config:
    data_root: Path = Path("G:/Astro")
    db_path: Path = PROJECT_ROOT / "var" / "astrodash.db"
    host: str = "127.0.0.1"
    port: int = 3007
    filter_aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FILTER_ALIASES))
    # Flats and lights further apart than this (focuser steps) get a warning.
    focus_tolerance_steps: int = 100
    # Master-dark matching tolerances.
    dark_temp_tolerance_c: float = 1.0
    dark_exposure_tolerance_s: float = 0.5
    scan_workers: int = 16
    # Sibling astro-eval checkout used for diagnostics, and its worker count (0 = cores - 1).
    astro_eval_path: Path = PROJECT_ROOT.parent / "AstroSubQualityEvaluation"
    diagnostics_workers: int = 0
    # Readout modes in which the camera ignores gain and offset (verified on the
    # QHY miniCam8M: bias level ~400 ADU and read noise ~1.4 ADU regardless of
    # gain 9/offset 100 vs gain 69/offset 1). Frames in these modes are grouped
    # and matched on readout + exposure + temperature only.
    gain_ignored_readouts: list[str] = field(default_factory=lambda: ["Linearity HDR"])
    # Readout mode flats must use (the IMX585 misbehaves for flats in HDR). None disables the check.
    flat_readout: str | None = "Full Resolution"
    # Bias frames shorter than this are suspect: on the QHY miniCam8M, 1 µs HDR biases
    # measured ~24.5 ADU temporal noise vs ~1.4 ADU for every other HDR frame.
    min_bias_exposure_s: float = 0.2

    def gain_ignored(self, readout: str | None) -> bool:
        return readout is not None and readout.casefold() in {r.casefold() for r in self.gain_ignored_readouts}


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> list[str]:
    """KEY=value lines into os.environ; a variable already set in the real environment wins.

    Blank lines and # comments are skipped, and matching quotes around a value are removed.
    Returns the names it set (never the values).
    """
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    candidates = [path] if path else []
    env = os.environ.get("ASTRODASH_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(PROJECT_ROOT / "astrodashboard.toml")
    for candidate in candidates:
        if candidate and candidate.is_file():
            _apply(cfg, tomllib.loads(candidate.read_text(encoding="utf-8")))
            break
    return cfg


def _apply(cfg: Config, raw: dict) -> None:
    data = raw.get("data", {})
    if "root" in data:
        cfg.data_root = Path(data["root"])
    if "focus_tolerance_steps" in data:
        cfg.focus_tolerance_steps = int(data["focus_tolerance_steps"])
    for alias, canonical in data.get("filter_aliases", {}).items():
        cfg.filter_aliases[alias.casefold()] = canonical
    server = raw.get("server", {})
    cfg.host = server.get("host", cfg.host)
    cfg.port = int(server.get("port", cfg.port))
    if "db_path" in server:
        cfg.db_path = Path(server["db_path"])
    diag = raw.get("diagnostics", {})
    if "astro_eval_path" in diag:
        cfg.astro_eval_path = Path(diag["astro_eval_path"])
    cfg.diagnostics_workers = int(diag.get("workers", cfg.diagnostics_workers))
    camera = raw.get("camera", {})
    if "gain_ignored_readouts" in camera:
        cfg.gain_ignored_readouts = list(camera["gain_ignored_readouts"])
    if "flat_readout" in camera:
        cfg.flat_readout = camera["flat_readout"] or None
    if "min_bias_exposure_s" in camera:
        cfg.min_bias_exposure_s = float(camera["min_bias_exposure_s"])
    darks = raw.get("darks", {})
    cfg.dark_temp_tolerance_c = float(darks.get("temp_tolerance_c", cfg.dark_temp_tolerance_c))
    cfg.dark_exposure_tolerance_s = float(darks.get("exposure_tolerance_s", cfg.dark_exposure_tolerance_s))
