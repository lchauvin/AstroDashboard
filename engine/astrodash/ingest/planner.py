"""Auto-ingest planning: what in `.Download\\` would move where, and what is held back and why (PLAN §5.1).

Pure and read-only: this module lists `.Download`, stats files and reads FITS
headers. It returns a plan; `fileops.py` is the only code that ever moves a
file, and only the ingest service calls it, and only in Auto mode.

Rules, in order, for each frame under `.Download\\<YYYY-MM-DD | NIGHT_YYYY-MM-DD>\\…`:

- Snapshot / guide images / DARK / BIAS / anything not LIGHT or FLAT: left in place.
- Not ready: modified less than `stable_s` ago, or a FITS file shorter than its
  header promises (NINA may still be writing it over SMB).
- The newest LIGHT of a night stays until the night is closed, so
  AstroController's preview of the latest frame keeps working.
- Night from DATE-LOC (local noon to noon, named after the evening); held if it
  disagrees with the folder date by more than a day.
- Target from the OBJECT header (" Panel n" → PANEL_n) through the mapping
  table, else by matching a target's catalog id or name; it must have a
  revision whose stage is "acquiring" — otherwise the frame is held with a prompt.
- FILTER header vs. the filter folder (both through the aliases): disagreement is held.
- The destination must not exist: never overwrite; a clash is held.
- Flats go to the night's first target (move) and are copied to the others.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..frames import canonical_filter, normalize_kind, parse_time
from ..headers import IMAGE_SUFFIXES, read_header

DOWNLOAD = ".Download"
NIGHT_DIR_RE = re.compile(r"^(?:NIGHT_)?(\d{4}-\d{2}-\d{2})$")
PANEL_OBJECT_RE = re.compile(r"^(?P<base>.*?)\s+Panel\s*(?P<n>\d+)\s*$", re.IGNORECASE)
IGNORED_DIRS = {"snapshot", "snapshots"}
FITS_BLOCK = 2880


@dataclass
class Action:
    op: str                    # "move" | "copy"
    src: str
    dst: str
    night: str                 # NIGHT_ date
    download_night: str        # the .Download folder
    kind: str
    filter: str | None
    revision_id: str
    target: str
    size: int


@dataclass
class Hold:
    src: str
    download_night: str
    reason: str                # machine code
    message: str
    kind: str | None = None
    object: str | None = None
    filter: str | None = None
    prompt: bool = False       # needs a decision from you (map the object, new revision…)


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)
    skipped: list[Hold] = field(default_factory=list)      # left in place by design (darks, snapshots…)
    nights: dict[str, dict[str, Any]] = field(default_factory=dict)
    scanned_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions": [asdict(a) for a in self.actions],
            "holds": [asdict(h) for h in self.holds],
            "skipped": [asdict(s) for s in self.skipped],
            "nights": self.nights,
            "scannedAt": self.scanned_at,
        }


def fits_complete(path: str, size: int) -> bool | None:
    """True when the file is as long as its header says; None when it is not FITS (XISF: trust stability)."""
    if not path.lower().endswith((".fits", ".fit", ".fts")):
        return None
    cards: dict[str, int] = {}
    try:
        with open(path, "rb") as fh:
            for block_no in range(1, 200):
                block = fh.read(FITS_BLOCK)
                if len(block) < FITS_BLOCK:
                    return False
                text = block.decode("ascii", errors="replace")
                for i in range(0, FITS_BLOCK, 80):
                    card = text[i:i + 80]
                    key = card[:8].strip()
                    if key == "END":
                        header_len = block_no * FITS_BLOCK
                        naxis = cards.get("NAXIS", 0)
                        data = abs(cards.get("BITPIX", 8)) // 8 if naxis else 0
                        for n in range(1, naxis + 1):
                            data *= cards.get(f"NAXIS{n}", 0)
                        padded = -(-data // FITS_BLOCK) * FITS_BLOCK
                        return size >= header_len + padded
                    if key in ("BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3") and card[8:10] == "= ":
                        try:
                            cards[key] = int(card[10:].split("/")[0].strip())
                        except ValueError:
                            pass
    except OSError:
        return False
    return False


def evening_of(value: Any) -> date | None:
    t = parse_time(value)
    return (t - timedelta(hours=12)).date() if t else None


def split_panel(obj: str | None) -> tuple[str | None, str | None]:
    if not obj:
        return None, None
    m = PANEL_OBJECT_RE.match(obj.strip())
    return (m["base"].strip(), m["n"]) if m else (obj.strip(), None)


def _walk(root: Path) -> Iterable[tuple[str, list[str], os.stat_result]]:
    stack: list[tuple[str, list[str]]] = [(str(root), [])]
    while stack:
        cur, rel = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append((e.path, rel + [e.name]))
                    elif e.is_file(follow_symlinks=False) and os.path.splitext(e.name)[1].lower() in IMAGE_SUFFIXES:
                        try:
                            yield e.path, rel + [e.name], e.stat()
                        except OSError:
                            continue
        except OSError:
            continue


@dataclass
class TargetRef:
    revision_id: str
    target: str                # folder name
    catalog_id: str
    name: str
    stage: str
    session_root: str          # absolute path of the revision's .SessionData
    revision: int = 1


def plan_ingest(
    data_root: Path,
    targets: list[TargetRef],
    object_map: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    *,
    now: float | None = None,
    stable_s: float = 30.0,
    closed_nights: set[str] | None = None,
    moved_lights: dict[str, list[str]] | None = None,
) -> Plan:
    """`moved_lights`: per .Download night, revision ids that already received lights (from the journal)."""
    now = now if now is not None else time.time()
    closed_nights = closed_nights or set()
    plan = Plan(scanned_at=now)
    base = data_root / DOWNLOAD
    if not base.is_dir():
        return plan

    by_revision = {t.revision_id: t for t in targets}

    def resolve(obj: str) -> tuple[TargetRef | None, str, str]:
        """→ (target, how, problem message)."""
        key = obj.casefold()
        mapped = object_map.get(key)
        candidates: list[TargetRef] = []
        how = "mapping"
        if mapped and mapped.get("revisionId") in by_revision:
            return by_revision[mapped["revisionId"]], "mapping", ""
        if mapped and mapped.get("catalogId"):
            candidates = [t for t in targets if t.catalog_id.casefold() == mapped["catalogId"].casefold()]
        if not candidates:
            how = "name"
            candidates = [t for t in targets if key in (t.catalog_id.casefold(), t.name.casefold())]
        if not candidates:
            return None, how, f'No target is mapped to the NINA object "{obj}".'
        acquiring = [t for t in candidates if t.stage == "acquiring"]
        if not acquiring:
            latest = max(candidates, key=lambda t: t.revision)
            return None, how, f'{latest.target} is "{latest.stage}": start a new revision or reopen it to receive new nights.'
        return max(acquiring, key=lambda t: t.revision), how, ""

    for night_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        m = NIGHT_DIR_RE.match(night_dir.name)
        if not m:
            continue
        folder_date = date.fromisoformat(m[1])
        dl = night_dir.name
        info = plan.nights.setdefault(dl, {"lights": 0, "flats": 0, "closed": dl in closed_nights, "newestLight": None})
        frames = []
        for path, rel, st in _walk(night_dir):
            if any(part.casefold() in IGNORED_DIRS for part in rel[:-1]) or rel[-1].lower().startswith("phdguider"):
                plan.skipped.append(Hold(path, dl, "ignored", "Snapshot or guide image: left in place."))
                continue
            try:
                header = read_header(Path(path))
            except Exception as exc:  # noqa: BLE001 — a half-written file has no END yet
                if now - st.st_mtime < stable_s * 4:
                    plan.holds.append(Hold(path, dl, "writing", "Still being written (header incomplete)."))
                else:
                    plan.holds.append(Hold(path, dl, "unreadable", f"Header unreadable: {exc}"))
                continue
            hint = next((p.upper() for p in rel[:-1] if p.upper() in ("LIGHT", "FLAT", "DARK", "BIAS", "DARKFLAT")), None)
            kind = normalize_kind(header.get("IMAGETYP"), hint, rel[-1])
            frames.append((path, rel, st, header, kind))

        lights = [f for f in frames if f[4] == "LIGHT"]
        newest = max(lights, key=lambda f: f[2].st_mtime)[0] if lights else None
        info["newestLight"] = newest
        night_targets: list[str] = list((moved_lights or {}).get(dl, []))

        def common_checks(path, rel, st, header, kind) -> tuple[str | None, date | None, Hold | None]:
            filt = canonical_filter(header.get("FILTER"), aliases)
            folder_filt = rel[-2] if len(rel) >= 2 and rel[-2].upper() not in ("LIGHT", "FLAT") else None
            if folder_filt and filt and canonical_filter(folder_filt, aliases) != filt:
                return filt, None, Hold(path, dl, "filter-mismatch", f'FILTER header "{header.get("FILTER")}" disagrees with the folder "{folder_filt}".', kind, header.get("OBJECT"), filt)
            if now - st.st_mtime < stable_s:
                return filt, None, Hold(path, dl, "settling", f"Modified {now - st.st_mtime:.0f} s ago; waiting for {stable_s:.0f} s of stability.", kind, header.get("OBJECT"), filt)
            if fits_complete(path, st.st_size) is False:
                return filt, None, Hold(path, dl, "incomplete", "Shorter than its header promises: still being written.", kind, header.get("OBJECT"), filt)
            night = evening_of(header.get("DATE-LOC")) or evening_of(header.get("DATE-OBS"))
            if night is None:
                return filt, None, Hold(path, dl, "no-date", "No DATE-LOC/DATE-OBS: cannot tell which night it belongs to.", kind, header.get("OBJECT"), filt)
            if abs((night - folder_date).days) > 1:
                return filt, None, Hold(path, dl, "date-mismatch", f"Header night {night} is more than a day from the folder {dl}.", kind, header.get("OBJECT"), filt)
            return filt, night, None

        # LIGHT frames
        for path, rel, st, header, kind in lights:
            info["lights"] += 1
            filt, night, problem = common_checks(path, rel, st, header, kind)
            if problem:
                plan.holds.append(problem)
                continue
            base_obj, panel = split_panel(header.get("OBJECT") or (rel[0] if len(rel) > 2 else None))
            if not base_obj:
                plan.holds.append(Hold(path, dl, "no-object", "No OBJECT header and no object folder.", kind, None, filt, prompt=True))
                continue
            target, _how, why = resolve(base_obj)
            if target is None:
                plan.holds.append(Hold(path, dl, "unmapped", why, kind, base_obj, filt, prompt=True))
                continue
            if path == newest and dl not in closed_nights:
                plan.holds.append(Hold(path, dl, "newest", "Newest light of the night: kept for the live preview until a newer one arrives or the night is closed.", kind, base_obj, filt))
                if target.revision_id not in night_targets:
                    night_targets.append(target.revision_id)
                continue
            parts = [f"NIGHT_{night.isoformat()}"] + ([f"PANEL_{panel}"] if panel else []) + ["LIGHT", filt or "unknown", rel[-1]]
            dst = os.path.normpath(os.path.join(target.session_root, *parts))
            if os.path.exists(dst):
                plan.holds.append(Hold(path, dl, "exists", f"Destination already exists: {dst}", kind, base_obj, filt))
                continue
            plan.actions.append(Action("move", path, dst, f"NIGHT_{night.isoformat()}", dl, kind, filt, target.revision_id, target.target, st.st_size))
            if target.revision_id not in night_targets:
                night_targets.append(target.revision_id)

        # FLAT frames: to the night's targets (first: move, others: copy).
        for path, rel, st, header, kind in (f for f in frames if f[4] == "FLAT"):
            info["flats"] += 1
            filt, night, problem = common_checks(path, rel, st, header, kind)
            if problem:
                plan.holds.append(problem)
                continue
            if not night_targets:
                plan.holds.append(Hold(path, dl, "no-target", "Flats with no lights from this night: no target to send them to.", kind, None, filt, prompt=True))
                continue
            dsts = []
            for i, rid in enumerate(night_targets):
                t = by_revision.get(rid)
                if t is None:
                    continue
                dst = os.path.normpath(os.path.join(t.session_root, f"NIGHT_{night.isoformat()}", "FLAT", filt or "unknown", rel[-1]))
                dsts.append((t, dst, "move" if not dsts else "copy"))
            clash = [d for _, d, _ in dsts if os.path.exists(d)]
            if clash:
                plan.holds.append(Hold(path, dl, "exists", f"Destination already exists: {clash[0]}", kind, None, filt))
                continue
            # Copies first, then the move, so the source is still there to copy from.
            for t, dst, op in sorted(dsts, key=lambda x: x[2] == "move"):
                plan.actions.append(Action(op, path, dst, f"NIGHT_{night.isoformat()}", dl, kind, filt, t.revision_id, t.target, st.st_size))

        for path, rel, st, header, kind in frames:
            if kind not in ("LIGHT", "FLAT"):
                plan.skipped.append(Hold(path, dl, "calibration", f"{kind or 'Unknown type'}: left in place (calibration frames go to the library by an approved plan).", kind))
    return plan
