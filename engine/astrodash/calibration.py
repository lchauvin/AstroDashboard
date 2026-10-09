"""The global dark/bias library (`<root>\\.Calibration\\<build date>\\…`) and matching.

PixInsight master darks/biases carry exposure and camera but not gain, offset,
readout mode or set temperature. Temperature comes from the `-10.0C` folder or
the file name; readout/gain/offset are inferred from the raw frames of the same
library build that share its temperature (and exposure, for darks). When that
inference is impossible the match is reported as *unverified*, never as OK.

In readout modes that ignore gain/offset (config `gain_ignored_readouts`, e.g.
"Linearity HDR") frames carry gain = offset = None, so matching compares
readout + exposure + temperature only. Readout modes must always match.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .frames import Frame


@dataclass
class Master:
    kind: str                 # "dark" | "bias"
    build: str                # library build folder name (usually a date)
    build_date: date | None
    path: str
    file: str
    temp: float | None
    exposure: float | None
    instrume: str | None
    gain: float | None
    offset: float | None
    readout: str | None
    inferred: bool            # readout/gain/offset came from raw frames of the build
    suspect: str | None = None  # why this master should not be trusted (e.g. bias too short)
    sources: int = 0          # raw input frames identified by DATE-OBS..DATE-END
    exposure_from: str = "header"

    def as_dict(self, today: date) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "build": self.build,
            "ageDays": (today - self.build_date).days if self.build_date else None,
            "file": self.file,
            "temp": self.temp,
            "exposure": self.exposure,
            "instrume": self.instrume,
            "gain": self.gain,
            "offset": self.offset,
            "readout": self.readout,
            "settingsKnown": self.inferred,
            "suspect": self.suspect,
            "sources": self.sources,
            "exposureFrom": self.exposure_from,
        }


@dataclass
class RawGroup:
    kind: str
    build: str
    folder: str
    exposure: float | None
    temp: float | None
    gain: float | None
    offset: float | None
    readout: str | None
    binning: int | None
    instrume: str | None
    frames: int
    suspect: str | None = None


def _build_date(name: str) -> date | None:
    try:
        return date.fromisoformat(name[:10])
    except ValueError:
        return None


def _close(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


class DarkLibrary:
    def __init__(self, frames: list[Frame], temp_tol: float, exp_tol: float, min_bias_exposure: float = 0.0):
        self.temp_tol = temp_tol
        self.exp_tol = exp_tol
        self.min_bias_exposure = min_bias_exposure
        self.masters: list[Master] = []
        self.raw: list[RawGroup] = []
        self.unreadable = 0

        raw_by_build: dict[str, list[Frame]] = defaultdict(list)
        master_frames: list[Frame] = []
        for f in frames:
            if f.status == "unreadable":
                self.unreadable += 1
                continue
            if f.kind in ("MASTERDARK", "MASTERBIAS"):
                master_frames.append(f)
            elif f.kind in ("DARK", "BIAS", "DARKFLAT"):
                raw_by_build[f.rel_parts[0]].append(f)

        groups: dict[tuple, int] = Counter()
        for build, items in raw_by_build.items():
            for f in items:
                folder = "/".join(f.rel_parts[1:-1])
                key = (f.kind, build, folder, f.exposure, f.set_temp, f.gain, f.offset, f.readout, f.binning, f.instrume)
                groups[key] += 1
        for key, n in sorted(groups.items(), key=lambda kv: (kv[0][1], str(kv[0][0]), kv[0][3] or 0)):
            kind, build, folder, exposure, temp, gain, offset, readout, binning, instrume = key
            group = RawGroup(kind.lower(), build, folder, exposure, temp, gain, offset, readout, binning, instrume, n)
            if group.kind == "bias":
                group.suspect = self._short_bias_reason(exposure)
            self.raw.append(group)

        for f in master_frames:
            kind = "dark" if f.kind == "MASTERDARK" else "bias"
            build = f.rel_parts[0]
            raw = raw_by_build.get(build, [])
            # Inputs are matched by time, so they are found even if filed in another build folder.
            inputs = self._inputs([r for items in raw_by_build.values() for r in items], kind, f)
            exposure, exposure_from = f.exposure, "header"
            if inputs:
                # The master's own inputs, found by time: exact settings and exposure.
                combos = {(r.gain, r.offset, r.readout) for r in inputs}
                settings = next(iter(combos)) if len(combos) == 1 else None
                exposure = statistics.median(r.exposure for r in inputs if r.exposure is not None)
                exposure_from = f"{len(inputs)} input frames"
            else:
                settings = self._infer_settings(raw, kind, f)
                frame_exp = f.header.get("XISF-FRAMEEXP")
                if kind == "bias" and isinstance(frame_exp, (int, float)):
                    # WBPP writes EXPTIME = 0 on master biases; the XISF property is the sub exposure.
                    exposure, exposure_from = float(frame_exp), "XISF FrameExposureTime"
            gain, offset, readout = settings or (None, None, None)
            suspect = None
            if kind == "bias":
                if exposure is not None:
                    suspect = self._short_bias_reason(exposure)
                else:
                    candidates = [r.exposure for r in raw if r.kind == "BIAS" and _close(r.set_temp, f.set_temp, self.temp_tol)]
                    reasons = [self._short_bias_reason(e) for e in candidates]
                    if candidates and all(reasons):
                        suspect = reasons[0]
            self.masters.append(Master(
                kind=kind,
                build=build,
                build_date=_build_date(build),
                path=f.path,
                file=f.rel_parts[-1],
                temp=f.set_temp,
                exposure=exposure,
                instrume=f.instrume,
                gain=gain,
                offset=offset,
                readout=readout,
                inferred=settings is not None,
                suspect=suspect,
                sources=len(inputs),
                exposure_from=exposure_from,
            ))
        self.masters.sort(key=lambda m: (m.build_date or date.min, m.kind, m.temp or 0, m.exposure or 0), reverse=True)

    @staticmethod
    def _inputs(raw: list[Frame], kind: str, master: Frame) -> list[Frame]:
        """Raw frames of the build shot between the master's DATE-OBS and DATE-END."""
        start, end = master.time_utc, master.time_end_utc
        if start is None or end is None:
            return []
        want = "DARK" if kind == "dark" else "BIAS"
        slack = timedelta(seconds=2)
        return [r for r in raw if r.kind == want and r.time_utc and start - slack <= r.time_utc <= end + slack]

    def _short_bias_reason(self, exposure: float | None) -> str | None:
        if exposure is None or not self.min_bias_exposure or exposure >= self.min_bias_exposure:
            return None
        shown = f"{exposure:g} s" if exposure >= 0.001 else f"{exposure * 1e6:g} µs" if exposure > 0 else "0 s"
        return f"bias exposure {shown} is below {self.min_bias_exposure:g} s"

    def _infer_settings(self, raw: list[Frame], kind: str, master: Frame):
        want = "DARK" if kind == "dark" else "BIAS"
        candidates = [
            f for f in raw
            if f.kind == want
            and _close(f.set_temp, master.set_temp, self.temp_tol)
            and (kind == "bias" or _close(f.exposure, master.exposure, self.exp_tol))
        ]
        combos = {(f.gain, f.offset, f.readout) for f in candidates}
        return next(iter(combos)) if len(combos) == 1 else None

    def match_dark(self, exposure, gain, offset, readout, set_temp, instrume) -> dict[str, Any]:
        """Best master dark for a light group, with a status the UI can show."""
        verified: list[Master] = []
        unverified: list[Master] = []
        for m in self.masters:
            if m.kind != "dark":
                continue
            if not _close(m.exposure, exposure, self.exp_tol) or not _close(m.temp, set_temp, self.temp_tol):
                continue
            if m.instrume and instrume and m.instrume != instrume:
                continue
            if not m.inferred:
                unverified.append(m)
            elif readout and m.readout and m.readout != readout:
                continue
            elif m.gain == gain and m.offset == offset:   # both None in gain-ignoring modes
                verified.append(m)
        if verified:
            return {"status": "ok", "master": verified[0]}
        if unverified:
            return {"status": "unverified", "master": unverified[0]}
        raw = [
            g for g in self.raw
            if g.kind == "dark"
            and _close(g.exposure, exposure, self.exp_tol)
            and _close(g.temp, set_temp, self.temp_tol)
            and g.gain == gain and g.offset == offset
            and not (readout and g.readout and g.readout != readout)
        ]
        if raw:
            return {"status": "raw-only", "master": None, "raw": raw[0]}
        return {"status": "missing", "master": None}

    def match_flat_calibration(self, readout, gain, offset, instrume) -> dict[str, Any] | None:
        """A bias (master or raw) or raw dark-flats in the library at the flats' own settings.

        Temperature is not compared: for flat-length exposures it barely matters.
        Trusted candidates win over suspect ones; a suspect-only match says so."""
        found: list[dict[str, Any]] = []
        for m in self.masters:
            if (
                m.kind == "bias" and m.inferred and m.readout == readout
                and m.gain == gain and m.offset == offset
                and not (m.instrume and instrume and m.instrume != instrume)
            ):
                found.append({"source": "library master bias", "name": f"{m.build}/{m.file}", "suspect": m.suspect})
        for g in self.raw:
            if (
                g.kind in ("bias", "darkflat") and g.readout == readout
                and g.gain == gain and g.offset == offset
                and not (g.instrume and instrume and g.instrume != instrume)
            ):
                found.append({
                    "source": f"library raw {g.kind}",
                    "name": f"{g.build}/{g.folder} ({g.frames} frames)",
                    "suspect": g.suspect,
                })
        trusted = [c for c in found if not c["suspect"]]
        return (trusted or found or [None])[0]

    def as_dict(self, today: date) -> dict[str, Any]:
        builds: dict[str, dict[str, Any]] = {}
        for m in self.masters:
            b = builds.setdefault(m.build, {"build": m.build, "ageDays": None, "masters": [], "raw": []})
            b["ageDays"] = (today - m.build_date).days if m.build_date else None
            b["masters"].append(m.as_dict(today))
        for g in self.raw:
            b = builds.setdefault(g.build, {"build": g.build, "ageDays": None, "masters": [], "raw": []})
            if b["ageDays"] is None and _build_date(g.build):
                b["ageDays"] = (today - _build_date(g.build)).days
            b["raw"].append({
                "kind": g.kind, "folder": g.folder, "exposure": g.exposure, "temp": g.temp,
                "gain": g.gain, "offset": g.offset, "readout": g.readout, "binning": g.binning,
                "instrume": g.instrume, "frames": g.frames, "suspect": g.suspect,
            })
        for b in builds.values():
            b["masters"].sort(key=lambda m: (m["kind"], m["temp"] or 0, m["exposure"] or 0))
        return {
            "builds": sorted(builds.values(), key=lambda b: b["build"], reverse=True),
            "minBiasExposure": self.min_bias_exposure,
            "suspectMasters": [m.as_dict(today) for m in self.masters if m.suspect],
            "unreadable": self.unreadable,
        }
