"""The Library model: catalog target → revision → session → light/flat groups.

Built in memory from a ScanResult. Totals only ever count frames whose status
is "ok": rejected, quarantined, copies, Snapshot frames and frames in
excluded/non-conforming session folders are counted separately.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable

from . import sky
from .calibration import DarkLibrary
from .config import Config
from .frames import Frame, build_frame
from .layout import MASTER_FLAT_RE, parse_session_folder
from .scanner import ScanResult, TargetFolderInfo

FILTER_ORDER = ["L", "R", "G", "B", "H", "O", "S"]


def filter_sort_key(name: str | None) -> tuple:
    if name in FILTER_ORDER:
        return (0, FILTER_ORDER.index(name), "")
    return (1, 0, name or "")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def _median(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return statistics.median(vals) if vals else None


def _round(v: float | None, nd: int = 1) -> float | None:
    return round(v, nd) if v is not None else None


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


@dataclass
class Check:
    level: str          # ok | info | warn | bad
    code: str
    text: str
    detail: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "text": self.text, "detail": self.detail}


def resolve_rejected_copies(frames: list[Frame]) -> None:
    """astro-eval copies frames into _REJECTED (and Accepted). A _REJECTED copy whose
    original still sits in the parent folder marks that original rejected; the copy is
    then not counted twice."""
    by_dir_name: dict[tuple[str, str], Frame] = {}
    for f in frames:
        if f.location and f.status == "ok":
            parent = "/".join(f.rel_parts[:-1])
            by_dir_name[(f.target_folder or "", parent + "/" + f.rel_parts[-1].casefold())] = f
    for f in frames:
        loc = f.location
        if f.status != "rejected" or not loc or not loc.reject_parent:
            continue
        original = by_dir_name.get((f.target_folder or "", loc.reject_parent + "/" + f.rel_parts[-1].casefold()))
        if original is not None:
            original.status = "rejected"
            f.status = "copy"


class Library:
    def __init__(self, scan: ScanResult, cfg: Config, dismissals: dict[str, dict[str, Any]] | None = None):
        self.cfg = cfg
        # Issues the user chose to ignore, keyed by dark_key(); still reported, never counted.
        self.dismissals = dismissals or {}
        self.scanned_at = scan.finished_at
        self.scan_duration = scan.duration_s
        today = date.today()
        self.today = today

        frames = [build_frame(ff, cfg.filter_aliases, cfg.gain_ignored) for ff in scan.frames]
        resolve_rejected_copies(frames)
        self.frame_count = len(frames)
        # Headers of every light, keyed like diagnostics results ("Target/…/file.fits"), for the detectors.
        root = str(cfg.data_root).replace("\\", "/").rstrip("/") + "/"
        self.light_headers = {
            f.path.replace("\\", "/").removeprefix(root): f.header
            for f in frames if f.kind == "LIGHT" and f.area == "session"
        }
        self.unreadable = sum(1 for f in frames if f.status == "unreadable")
        # Kept for Planning: rig detection, and the raw biases that give the sky pedestal.
        self.ok_lights = [f for f in frames if f.kind == "LIGHT" and f.area == "session" and f.status == "ok"]
        self.library_frames = [f for f in frames if f.area == "library"]

        self.darks = DarkLibrary(
            [f for f in frames if f.area == "library"],
            cfg.dark_temp_tolerance_c,
            cfg.dark_exposure_tolerance_s,
            cfg.min_bias_exposure_s,
        )
        by_target: dict[str, list[Frame]] = defaultdict(list)
        for f in frames:
            if f.target_folder:
                by_target[f.target_folder].append(f)

        self.revisions: dict[str, dict[str, Any]] = {}
        for info in scan.targets:
            rev = self._build_revision(info, by_target.get(info.target.folder, []))
            self.revisions[rev["id"]] = rev

    # ------------------------------------------------------------------ revision

    def _build_revision(self, info: TargetFolderInfo, frames: list[Frame]) -> dict[str, Any]:
        t = info.target
        tj = info.target_json or {}
        sessions_frames: dict[str, list[Frame]] = defaultdict(list)
        for f in frames:
            if f.session_folder:
                sessions_frames[f.session_folder].append(f)

        master_flats = self._master_flats(info)
        sessions = []
        excluded = []
        for folder, items in sessions_frames.items():
            parsed = parse_session_folder(folder)
            if parsed.status in ("excluded", "nonconforming"):
                excluded.append({
                    "folder": folder,
                    "reason": "legacy flagged session" if parsed.status == "excluded" else "folder name is not NIGHT_<date>",
                    "frames": len(items),
                    "lights": sum(1 for f in items if f.kind == "LIGHT"),
                })
                continue
            only_quarantine = all(f.area == "quarantine" for f in items)
            if only_quarantine and not any(f.area == "session" for f in items):
                # quarantine of a session whose raw folder no longer exists: still show it
                pass
            sessions.append(self._build_session(folder, parsed, items))

        sessions.sort(key=lambda s: (s["date"] or "", s["folder"]))
        self._resolve_flats(sessions, master_flats)

        filters = self._filter_totals(sessions)
        lights_ok = [f for f in frames if f.kind == "LIGHT" and f.status == "ok"]
        equipment = sorted({
            (f.instrume or "?", f.telescop or "?", f.focallen or 0) for f in lights_ok
        })
        total_seconds = sum(s["totals"]["seconds"] for s in sessions)
        nights = [s for s in sessions if s["totals"]["lights"] > 0]
        panels = sorted({g["panel"] for s in sessions for g in s["lightGroups"] if g["panel"]}, key=lambda p: int(p) if p.isdigit() else 0)

        stage = self._stage(info, tj)
        if stage["value"] == "finished":
            # Finished targets were processed with whatever flats they had: the flat readout
            # rule only matters for data still to be processed, so it is not reported there.
            for s in sessions:
                for st in s["flatStatus"].values():
                    if st.get("readoutWrong"):
                        st["readoutAccepted"] = st.pop("readoutWrong")
        rev = {
            "id": slugify(t.folder),
            "folder": t.folder,
            "catalogId": tj.get("catalog_id") or t.catalog_id,
            "name": tj.get("name") or t.name,
            "revision": int(tj.get("revision") or t.revision),
            "groupId": slugify(tj.get("catalog_id") or t.catalog_id),
            "stage": stage,
            "legacyYears": info.legacy_years,
            "legacyStages": info.legacy_stages,
            "wbppMasters": info.wbpp_masters,
            "finishedFiles": info.finished_files,
            "hasPreview": info.finished_preview is not None,
            "previewPath": info.finished_preview,
            "panels": panels,
            "pointing": _pointing(lights_ok),
            "equipment": [{"instrume": i, "telescop": tel, "focalLength": fl} for i, tel, fl in equipment],
            "totals": {
                "seconds": total_seconds,
                "lights": sum(s["totals"]["lights"] for s in sessions),
                "rejected": sum(s["totals"]["rejected"] for s in sessions),
                "quarantined": sum(s["totals"]["quarantined"] for s in sessions),
                "nights": len(nights),
                "first": nights[0]["date"] if nights else None,
                "last": nights[-1]["date"] if nights else None,
            },
            "filters": filters,
            "sessions": sessions,
            "excludedSessions": excluded,
            "goals": tj.get("goals"),
            "notes": tj.get("notes"),
            "unreadable": sum(1 for f in frames if f.status == "unreadable"),
        }
        rev["checks"] = [c.as_dict() for c in self._checks(rev, frames)]
        return rev

    @staticmethod
    def _stage(info: TargetFolderInfo, tj: dict[str, Any]) -> dict[str, Any]:
        declared = tj.get("status")
        if info.finished_files:
            inferred = "finished"
        elif info.wbpp_masters or any(s.startswith("7.") for s in info.legacy_stages):
            inferred = "processed"
        else:
            inferred = "acquiring"
        return {"value": declared or inferred, "declared": bool(declared), "inferred": inferred}

    def _master_flats(self, info: TargetFolderInfo) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for path in info.master_flats:
            m = MASTER_FLAT_RE.search(path.replace("\\", "/").rsplit("/", 1)[-1])
            if not m:
                continue
            filt = self.cfg.filter_aliases.get(m["filter"].casefold(), m["filter"])
            d = m["date"]
            out[(filt, f"{d[:4]}-{d[4:6]}-{d[6:]}")] = path
        return out

    # ------------------------------------------------------------------ session

    def _build_session(self, folder: str, parsed, items: list[Frame]) -> dict[str, Any]:
        lights = [f for f in items if f.kind == "LIGHT"]
        flats = [f for f in items if f.kind == "FLAT"]
        ok_lights = [f for f in lights if f.status == "ok"]

        groups: dict[tuple, list[Frame]] = defaultdict(list)
        for f in lights:
            panel = f.location.panel if f.location else None
            groups[(panel, f.filter) + f.settings_key].append(f)

        light_groups = []
        for key, fs in groups.items():
            panel, filt, exposure, gain, offset, readout, binning, set_temp, instrume = key
            if all(f.status in ("copy", "ignored") for f in fs):
                continue  # Snapshot frames and astro-eval copies only
            ok = [f for f in fs if f.status == "ok"]
            dark = self.darks.match_dark(exposure, gain, offset, readout, set_temp, instrume) if ok else None
            dkey = dark_key(exposure, readout, gain, offset, set_temp, instrume)
            if dark and dark["status"] in ("missing", "raw-only") and dkey in self.dismissals:
                dark = {**dark, "status": "dismissed", "was": dark["status"], "note": self.dismissals[dkey].get("note")}
            light_groups.append({
                "panel": panel,
                "filter": filt,
                "exposure": exposure,
                "gain": gain,
                "offset": offset,
                "readout": readout,
                "binning": binning,
                "setTemp": set_temp,
                "instrume": instrume,
                "gainIgnored": self.cfg.gain_ignored(readout),
                "frames": len(ok),
                "seconds": sum(f.exposure or 0 for f in ok),
                "rejected": sum(1 for f in fs if f.status == "rejected"),
                "quarantined": sum(1 for f in fs if f.status == "quarantined"),
                "copies": sum(1 for f in fs if f.status in ("copy", "ignored")),
                "focpos": _median(f.focpos for f in ok),
                "dark": _dark_dict(dark, self.today) if dark else None,
                "darkKey": dkey,
            })
        light_groups.sort(key=lambda g: (int(g["panel"]) if g["panel"] and g["panel"].isdigit() else 0, filter_sort_key(g["filter"]), g["exposure"] or 0))

        flat_sets: dict[tuple, list[Frame]] = defaultdict(list)
        for f in flats:
            if f.status in ("ok", "rejected"):
                flat_sets[(f.filter, f.binning, f.exposure, f.gain, f.offset, f.readout)].append(f)
        flat_groups = [
            {
                "filter": k[0], "binning": k[1], "exposure": k[2], "gain": k[3], "offset": k[4], "readout": k[5],
                "frames": sum(1 for f in fs if f.status == "ok"),
                "rejected": sum(1 for f in fs if f.status == "rejected"),
                "focpos": _median(f.focpos for f in fs),
            }
            for k, fs in flat_sets.items()
        ]
        flat_groups.sort(key=lambda g: (filter_sort_key(g["filter"]), g["exposure"] or 0))

        # Bias / dark-flats shot with the session (used to calibrate its flats).
        cal_sets: dict[tuple, int] = defaultdict(int)
        for f in items:
            if f.kind in ("BIAS", "DARKFLAT", "DARK") and f.status == "ok":
                cal_sets[(f.kind, f.readout, f.gain, f.offset, f.exposure)] += 1
        cal_groups = [
            {"kind": k[0], "readout": k[1], "gain": k[2], "offset": k[3], "exposure": k[4], "frames": n}
            for k, n in cal_sets.items()
        ]

        nights = sorted({f.night for f in ok_lights if f.night})
        session_date = parsed.date or (nights[0] if nights else None)
        times = sorted(f.time_utc for f in ok_lights if f.time_utc)
        local_times = sorted(f.time_local for f in ok_lights if f.time_local)

        return {
            "id": slugify(folder),
            "folder": folder,
            "date": _iso(session_date),
            "naming": parsed.status,
            "headerNights": [_iso(n) for n in nights],
            "nightMismatch": bool(nights) and parsed.date is not None and nights != [parsed.date],
            "lightGroups": light_groups,
            "flatGroups": flat_groups,
            "calGroups": cal_groups,
            "flatStatus": {},      # filled by _resolve_flats
            "start": _iso(local_times[0]) if local_times else None,
            "end": _iso(local_times[-1]) if local_times else None,
            "conditions": self._conditions(ok_lights, times),
            "path": _session_path(items, folder),
            "site": _site(ok_lights),
            "totals": {
                "lights": len(ok_lights),
                "seconds": sum(f.exposure or 0 for f in ok_lights),
                "rejected": sum(1 for f in lights if f.status == "rejected"),
                "quarantined": sum(1 for f in lights if f.status == "quarantined"),
                "flats": sum(1 for f in flats if f.status == "ok"),
            },
        }

    def _resolve_flats(self, sessions: list[dict[str, Any]], master_flats: dict[tuple[str, str], str]) -> None:
        flats_by_session: dict[str, dict[str, list[dict]]] = {}
        for s in sessions:
            per_filter: dict[str, list[dict]] = defaultdict(list)
            for g in s["flatGroups"]:
                if g["frames"]:
                    per_filter[g["filter"]].append(g)
            flats_by_session[s["folder"]] = per_filter

        tol = self.cfg.focus_tolerance_steps
        for s in sessions:
            status: dict[str, dict[str, Any]] = {}
            lights_by_filter: dict[str, list[dict]] = defaultdict(list)
            for g in s["lightGroups"]:
                if g["frames"]:
                    lights_by_filter[g["filter"]].append(g)
            for filt, lgs in lights_by_filter.items():
                own = flats_by_session[s["folder"]].get(filt, [])
                binnings = {g["binning"] for g in lgs}
                matching = [fg for fg in own if fg["binning"] in binnings]
                light_focus = _median(g["focpos"] for g in lgs)
                if matching:
                    flat_focus = _median(fg["focpos"] for fg in matching)
                    delta = abs(flat_focus - light_focus) if flat_focus is not None and light_focus is not None else None
                    want = self.cfg.flat_readout
                    wrong = sorted({fg["readout"] for fg in matching if want and fg["readout"] and fg["readout"] != want})
                    entry = {
                        "status": "ok",
                        "frames": sum(fg["frames"] for fg in matching),
                        "exposures": sorted({fg["exposure"] for fg in matching if fg["exposure"] is not None}),
                        "readouts": sorted({fg["readout"] for fg in matching if fg["readout"]}),
                        "readoutWrong": wrong,
                        "focusDelta": _round(delta, 0),
                        "focusWarning": delta is not None and delta > tol,
                        "calibration": [self._flat_calibration(s, fg, lgs[0]["instrume"]) for fg in matching],
                    }
                elif own:
                    entry = {"status": "binning-mismatch", "frames": sum(fg["frames"] for fg in own)}
                elif s["date"] and (filt, s["date"]) in master_flats:
                    entry = {"status": "master", "file": master_flats[(filt, s["date"])].replace("\\", "/").rsplit("/", 1)[-1]}
                else:
                    donors = [
                        o["date"] for o in sessions
                        if o is not s and flats_by_session[o["folder"]].get(filt)
                    ]
                    if donors and s["date"]:
                        nearest = min(donors, key=lambda d: abs(date.fromisoformat(d) - date.fromisoformat(s["date"])))
                        entry = {"status": "borrowed", "from": nearest}
                    else:
                        entry = {"status": "missing"}
                status[filt or "?"] = entry
            s["flatStatus"] = status

    def _flat_calibration(self, session: dict[str, Any], fg: dict[str, Any], instrume: str | None) -> dict[str, Any]:
        """How a flat set can be calibrated: bias/dark-flats from the session, else from the library."""
        settings = _settings_label(fg["readout"], fg["gain"], fg["offset"])
        for cg in session["calGroups"]:
            if (cg["readout"], cg["gain"], cg["offset"]) != (fg["readout"], fg["gain"], fg["offset"]):
                continue
            if cg["kind"] == "BIAS" or (
                cg["exposure"] is not None and fg["exposure"] is not None
                and abs(cg["exposure"] - fg["exposure"]) <= self.cfg.dark_exposure_tolerance_s
            ):
                kind = "bias" if cg["kind"] == "BIAS" else "dark-flats"
                return {"status": "ok", "settings": settings, "source": f"session {kind} ({cg['frames']} frames)"}
        candidates = [self.darks.match_flat_calibration(fg["readout"], fg["gain"], fg["offset"], instrume)]
        # Intended workflow: flats in the required readout (Full Resolution) are calibrated with
        # the bias/darks shot in a gain-ignoring readout (Linearity HDR), whatever their gain/offset.
        if self.cfg.flat_readout and fg["readout"] == self.cfg.flat_readout:
            candidates += [self.darks.match_flat_calibration(r, None, None, instrume) for r in self.cfg.gain_ignored_readouts]
        candidates = [c for c in candidates if c]
        best = next((c for c in candidates if not c["suspect"]), candidates[0] if candidates else None)
        if best is None:
            return {"status": "missing", "settings": settings, "source": None}
        return {
            "status": "suspect" if best["suspect"] else "ok",
            "settings": settings,
            "source": f"{best['source']} {best['name']}",
            "reason": best["suspect"],
        }

    def _conditions(self, lights: list[Frame], times: list[datetime]) -> dict[str, Any] | None:
        if not lights:
            return None
        def num(key: str) -> list[float]:
            out = []
            for f in lights:
                v = f.header.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(float(v))
            return out

        amb = num("AMBTEMP")
        dew = num("DEWPOINT")
        margins = [
            a - d for f in lights
            if isinstance(a := f.header.get("AMBTEMP"), (int, float)) and isinstance(d := f.header.get("DEWPOINT"), (int, float))
        ]
        airmass = num("AIRMASS")
        cond: dict[str, Any] = {
            "ambientC": _round(_median(amb)),
            "dewPointC": _round(_median(dew)),
            "minDewMarginC": _round(min(margins)) if margins else None,
            "humidity": _round(_median(num("HUMIDITY")), 0),
            "windKph": _round(_median(num("WINDSPD"))),
            "airmassMin": _round(min(airmass), 2) if airmass else None,
            "airmassMax": _round(max(airmass), 2) if airmass else None,
            "moon": None,
        }
        if times:
            mid = times[len(times) // 2]
            jd = sky.julian_day(mid.astimezone(timezone.utc))
            ra = _median(num("RA"))
            dec = _median(num("DEC"))
            lat = _median(num("SITELAT"))
            lon = _median(num("SITELONG"))
            moon_ra, moon_dec = sky.moon_radec(jd)
            cond["moon"] = {
                "illumination": round(sky.moon_illumination(jd), 2),
                "separation": round(sky.separation(ra, dec, moon_ra, moon_dec)) if ra is not None and dec is not None else None,
                "altitude": round(sky.altitude(moon_ra, moon_dec, jd, lat, lon)) if lat is not None and lon is not None else None,
            }
        return cond

    # ------------------------------------------------------------------ totals & checks

    @staticmethod
    def _filter_totals(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = {}
        for s in sessions:
            for g in s["lightGroups"]:
                a = acc.setdefault(g["filter"] or "?", {
                    "filter": g["filter"] or "?", "frames": 0, "seconds": 0.0, "rejected": 0,
                    "quarantined": 0, "sessions": set(), "withFlats": set(), "exposures": set(),
                })
                a["frames"] += g["frames"]
                a["seconds"] += g["seconds"]
                a["rejected"] += g["rejected"]
                a["quarantined"] += g["quarantined"]
                if g["frames"]:
                    a["sessions"].add(s["folder"])
                    a["exposures"].add(g["exposure"])
                    if s["flatStatus"].get(g["filter"] or "?", {}).get("status") in ("ok", "master"):
                        a["withFlats"].add(s["folder"])
        out = []
        for a in acc.values():
            out.append({
                "filter": a["filter"],
                "frames": a["frames"],
                "seconds": a["seconds"],
                "rejected": a["rejected"],
                "quarantined": a["quarantined"],
                "sessions": len(a["sessions"]),
                "sessionsWithFlats": len(a["withFlats"]),
                "exposures": sorted(e for e in a["exposures"] if e is not None),
            })
        out.sort(key=lambda a: filter_sort_key(a["filter"]))
        return out

    def _checks(self, rev: dict[str, Any], frames: list[Frame]) -> list[Check]:
        checks: list[Check] = []
        missing, borrowed, binmix, focus, readout_wrong = [], [], [], [], []
        flat_cal_missing: dict[str, set] = {}
        flat_cal_suspect: dict[str, set] = {}
        for s in rev["sessions"]:
            for filt, st in s["flatStatus"].items():
                label = f"{s['folder']} · {filt}"
                if st["status"] == "missing":
                    missing.append(label)
                elif st["status"] == "borrowed":
                    borrowed.append(f"{label} (nearest flats: {st['from']})")
                elif st["status"] == "binning-mismatch":
                    binmix.append(label)
                else:
                    if st.get("focusWarning"):
                        focus.append(f"{label} (Δ {st['focusDelta']:.0f} steps)")
                    if st.get("readoutWrong"):
                        readout_wrong.append(f"{label} ({', '.join(st['readoutWrong'])})")
                    for cal in st.get("calibration", []):
                        if cal["status"] == "missing":
                            flat_cal_missing.setdefault(cal["settings"], set()).add(s["folder"])
                        elif cal["status"] == "suspect":
                            flat_cal_suspect.setdefault(f"{cal['source']} — {cal['reason']}", set()).add(s["folder"])
        if missing:
            checks.append(Check("bad", "flats-missing", f"{len(missing)} night/filter combinations have no flats", missing))
        if binmix:
            checks.append(Check("bad", "flats-binning", "Flats with a different binning than the lights", binmix))
        if borrowed:
            checks.append(Check("warn", "flats-borrowed", f"{len(borrowed)} night/filter combinations rely on flats from another night", borrowed))
        if readout_wrong:
            checks.append(Check(
                "bad", "flats-readout",
                f"Flats not taken in {self.cfg.flat_readout} (this sensor's flats misbehave in other readout modes)",
                readout_wrong,
            ))
        if focus:
            checks.append(Check("warn", "flats-focus", f"Flats taken at a different focuser position (> {self.cfg.focus_tolerance_steps} steps)", focus))
        if flat_cal_missing:
            checks.append(Check(
                "warn", "flats-calibration",
                "No bias or dark-flats at the flats' own settings (session or library)",
                [f"{k} — {len(v)} night{'s' if len(v) > 1 else ''}" for k, v in sorted(flat_cal_missing.items())],
            ))
        if flat_cal_suspect:
            checks.append(Check(
                "warn", "flats-calibration-suspect",
                f"Flats can only be calibrated with a suspect bias (shorter than {self.cfg.min_bias_exposure_s:g} s)",
                [f"{k} — {len(v)} night{'s' if len(v) > 1 else ''}" for k, v in sorted(flat_cal_suspect.items())],
            ))
        if not (missing or binmix or borrowed) and rev["sessions"]:
            checks.append(Check("ok", "flats", "Every night/filter has its own flats"))

        dark_missing, dark_raw, dark_unverified, dark_ok, dark_dismissed = {}, {}, {}, {}, {}
        for s in rev["sessions"]:
            for g in s["lightGroups"]:
                if not g["frames"] or not g["dark"]:
                    continue
                key = f"{_fmt(g['exposure'])} s · {_settings_label(g['readout'], g['gain'], g['offset'])} · {_fmt(g['setTemp'])} °C"
                if g["dark"]["status"] == "dismissed":
                    note = g["dark"].get("note")
                    key = f"{key} (dismissed{': ' + note if note else ''})"
                target = {
                    "ok": dark_ok, "unverified": dark_unverified, "raw-only": dark_raw,
                    "missing": dark_missing, "dismissed": dark_dismissed,
                }[g["dark"]["status"]]
                target.setdefault(key, set()).add(s["folder"])
        def fmt(d: dict[str, set]) -> list[str]:
            return [f"{k} — {len(v)} night{'s' if len(v) > 1 else ''}" for k, v in sorted(d.items())]
        if dark_missing:
            checks.append(Check("bad", "darks-missing", "No master dark for these light settings", fmt(dark_missing)))
        if dark_raw:
            checks.append(Check("warn", "darks-raw", "Raw darks exist but no master has been integrated", fmt(dark_raw)))
        if dark_unverified:
            checks.append(Check("info", "darks-unverified", "Master dark found, but its gain/offset could not be verified", fmt(dark_unverified)))
        if dark_dismissed:
            checks.append(Check("info", "darks-dismissed", "Dark issues you dismissed (not counted as problems)", fmt(dark_dismissed)))
        if dark_ok and not (dark_missing or dark_raw):
            checks.append(Check("ok", "darks", "Matching master darks exist for every light setting", fmt(dark_ok)))

        if len(rev["equipment"]) > 1:
            checks.append(Check(
                "warn", "mixed-equipment",
                "Lights come from more than one camera/telescope — consider a separate revision",
                [f"{e['instrume']} · {e['telescop']} · {e['focalLength']:g} mm" for e in rev["equipment"]],
            ))

        mixed = []
        for flt in rev["filters"]:
            if len(flt["exposures"]) > 1:
                mixed.append(f"{flt['filter']}: " + ", ".join(f"{e:g} s" for e in flt["exposures"]))
        if mixed:
            checks.append(Check("info", "mixed-exposures", "Several exposure lengths per filter (each needs its own master dark)", mixed))

        if rev["excludedSessions"]:
            checks.append(Check(
                "info", "excluded-sessions", "Session folders excluded from the totals",
                [f"{e['folder']} — {e['reason']} ({e['lights']} lights)" for e in rev["excludedSessions"]],
            ))
        mismatch = [f"{s['folder']} → header nights {', '.join(s['headerNights'])}" for s in rev["sessions"] if s["nightMismatch"]]
        if mismatch:
            checks.append(Check("info", "night-mismatch", "Folder date differs from the night in the frame headers", mismatch))
        legacy = []
        if rev["legacyYears"]:
            legacy.append("year level: " + ", ".join(rev["legacyYears"]))
        legacy_names = [s["folder"] for s in rev["sessions"] if s["naming"] == "legacy"]
        if legacy_names:
            legacy.append("session names: " + ", ".join(legacy_names))
        if legacy:
            checks.append(Check("info", "legacy-layout", "Legacy folder layout (migration comes with the organizer)", legacy))
        if rev["unreadable"]:
            unreadable = [f.path for f in frames if f.status == "unreadable"][:20]
            checks.append(Check("warn", "unreadable", f"{rev['unreadable']} files could not be read", unreadable))

        order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
        checks.sort(key=lambda c: order[c.level])
        return checks

    # ------------------------------------------------------------------ API views

    def overview(self) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for rev in self.revisions.values():
            g = groups.setdefault(rev["groupId"], {
                "id": rev["groupId"], "catalogId": rev["catalogId"], "name": rev["name"], "revisions": [],
            })
            if not g["name"] and rev["name"]:
                g["name"] = rev["name"]
            g["revisions"].append(self._revision_summary(rev))
        for g in groups.values():
            g["revisions"].sort(key=lambda r: r["revision"])
        return {
            "scannedAt": datetime.fromtimestamp(self.scanned_at, tz=timezone.utc).isoformat(),
            "scanDurationS": round(self.scan_duration, 1),
            "frames": self.frame_count,
            "unreadable": self.unreadable,
            "targets": sorted(groups.values(), key=lambda g: g["catalogId"].casefold()),
        }

    @staticmethod
    def _revision_summary(rev: dict[str, Any]) -> dict[str, Any]:
        levels = [c["level"] for c in rev["checks"]]
        return {
            "id": rev["id"],
            "folder": rev["folder"],
            "revision": rev["revision"],
            "stage": rev["stage"],
            "hasPreview": rev["hasPreview"],
            "panels": rev["panels"],
            "totals": rev["totals"],
            "filters": [{"filter": f["filter"], "seconds": f["seconds"], "frames": f["frames"]} for f in rev["filters"]],
            "issues": {"bad": levels.count("bad"), "warn": levels.count("warn")},
            "needsFlats": any(c["code"] in ("flats-missing", "flats-binning") for c in rev["checks"]),
        }

    def revision(self, rev_id: str) -> dict[str, Any] | None:
        rev = self.revisions.get(rev_id)
        if rev is None:
            return None
        siblings = [
            {"id": r["id"], "revision": r["revision"], "folder": r["folder"]}
            for r in self.revisions.values() if r["groupId"] == rev["groupId"]
        ]
        out = {k: v for k, v in rev.items() if k != "previewPath"}
        out["siblings"] = sorted(siblings, key=lambda r: r["revision"])
        return out

    def preview_path(self, rev_id: str) -> str | None:
        rev = self.revisions.get(rev_id)
        return rev["previewPath"] if rev else None

    def sessions_index(self) -> list[dict[str, Any]]:
        """Every session with lights, flattened (Diagnostics overview)."""
        out = []
        for rev in self.revisions.values():
            for s in rev["sessions"]:
                if not s["totals"]["lights"]:
                    continue
                filters = sorted({g["filter"] for g in s["lightGroups"] if g["frames"]}, key=filter_sort_key)
                out.append({
                    "revisionId": rev["id"], "catalogId": rev["catalogId"], "name": rev["name"],
                    "revision": rev["revision"], "stage": rev["stage"]["value"], "folder": s["folder"],
                    "date": s["date"], "lights": s["totals"]["lights"], "seconds": s["totals"]["seconds"],
                    "filters": filters,
                })
        return sorted(out, key=lambda x: (x["date"] or "", x["catalogId"]), reverse=True)

    def session(self, rev_id: str, folder: str) -> dict[str, Any] | None:
        rev = self.revisions.get(rev_id)
        if rev is None:
            return None
        return next((s for s in rev["sessions"] if s["folder"] == folder), None)

    def calibration(self) -> dict[str, Any]:
        lib = self.darks.as_dict(self.today)
        unmatched: dict[tuple, dict[str, Any]] = {}
        for rev in self.revisions.values():
            for s in rev["sessions"]:
                for g in s["lightGroups"]:
                    if not g["frames"] or not g["dark"] or g["dark"]["status"] in ("ok", "unverified"):
                        continue
                    u = unmatched.setdefault(g["darkKey"], {
                        "key": g["darkKey"],
                        "exposure": g["exposure"], "gain": g["gain"], "offset": g["offset"], "readout": g["readout"],
                        "setTemp": g["setTemp"], "instrume": g["instrume"], "status": g["dark"]["status"],
                        "was": g["dark"].get("was"), "note": g["dark"].get("note"), "uses": [],
                    })
                    u["uses"].append({"revisionId": rev["id"], "target": rev["folder"], "session": s["folder"], "filter": g["filter"], "frames": g["frames"]})
        lib["unmatched"] = sorted(
            unmatched.values(),
            key=lambda u: (u["status"] == "dismissed", u["setTemp"] or 0, u["exposure"] or 0),
        )
        return lib


def _session_path(items: list[Frame], folder: str) -> str | None:
    """Absolute path of the session folder, from any frame in it (not the quarantine copy)."""
    for f in items:
        if f.area != "session":
            continue
        parts = f.path.replace("\\", "/").split("/")
        if folder in parts:
            return "/".join(parts[: parts.index(folder) + 1])
    return None


def _site(lights: list[Frame]) -> list[float] | None:
    lat = _median(f.header.get("SITELAT") for f in lights if isinstance(f.header.get("SITELAT"), (int, float)))
    lon = _median(f.header.get("SITELONG") for f in lights if isinstance(f.header.get("SITELONG"), (int, float)))
    return [lat, lon] if lat is not None and lon is not None else None


def _pointing(lights: list[Frame]) -> dict[str, float] | None:
    """Where the revision's lights point: circular mean of RA, mean of DEC (degrees, J2000).

    Mosaics get the centre of all their panels, which is what visibility needs.
    """
    ras, decs = [], []
    for f in lights:
        ra, dec = f.header.get("RA"), f.header.get("DEC")
        if isinstance(ra, (int, float)) and isinstance(dec, (int, float)) and not isinstance(ra, bool):
            ras.append(float(ra))
            decs.append(float(dec))
    if not ras:
        return None
    x = sum(math.cos(math.radians(r)) for r in ras)
    y = sum(math.sin(math.radians(r)) for r in ras)
    return {"ra": round(math.degrees(math.atan2(y, x)) % 360, 4), "dec": round(sum(decs) / len(decs), 4)}


def dark_key(exposure, readout, gain, offset, set_temp, instrume) -> str:
    """Stable identity of a light setting for dismissals (gain/offset are None in HDR)."""
    return "|".join(["dark", _fmt(exposure), readout or "", _fmt(gain), _fmt(offset), _fmt(set_temp), instrume or ""])


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def _settings_label(readout: str | None, gain: float | None, offset: float | None) -> str:
    """'Linearity HDR' when gain/offset are ignored, else 'Full Resolution · gain 69 · offset 1'."""
    parts = [readout] if readout else []
    if gain is not None or offset is not None:
        parts.append(f"gain {_fmt(gain)} · offset {_fmt(offset)}")
    return " · ".join(parts) or "settings unknown"


def _dark_dict(match: dict[str, Any], today: date) -> dict[str, Any]:
    m = match.get("master")
    return {
        "status": match["status"],
        "master": m.as_dict(today) if m else None,
        "was": match.get("was"),
        "note": match.get("note"),
    }
