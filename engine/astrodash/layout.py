"""Path conventions of the data root, old and new.

The header is the source of truth for what a frame *is* (type, filter,
exposure...). The path decides *where it belongs*: target, revision, session,
panel, and whether it is excluded, rejected or quarantined.

Canonical layout (PLAN.md §3.2):

    <ID> (<Name>)[ R<n>]\\.SessionData\\NIGHT_<YYYY-MM-DD>\\[<ID>_Panel_<n>\\]LIGHT\\<F>\\
    <ID> (<Name>)[ R<n>]\\.SessionData\\NIGHT_<YYYY-MM-DD>\\FLAT\\<F>\\

Legacy shapes still parsed: a <YEAR> level under the target, session folders
named `YYYY-MM-DD` / `SESSION_YYYY-MM-DD`, NINA object folders and
`<Object> Panel <n>` and bare `PANEL_<n>` folders above LIGHT, `FlatWizard\\FLAT`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

TARGET_RE = re.compile(r"^(?P<id>.+?)\s*\((?P<name>[^()]*)\)(?:\s+R(?P<rev>\d+))?\s*$")
REVISION_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\s+R(?P<rev>\d+)$")
YEAR_RE = re.compile(r"^\d{4}$")
PANEL_RE = re.compile(r"^(?:.+_)?PANEL_(\d+)$", re.IGNORECASE)   # M31_Panel_2, legacy PANEL_2
LEGACY_PANEL_RE = re.compile(r"\bPanel\s*(\d+)$", re.IGNORECASE)

SESSION_CANONICAL_RE = re.compile(r"^NIGHT_(\d{4}-\d{2}-\d{2})$")
SESSION_LEGACY_RE = re.compile(r"^(?:SESSION_)?(\d{4}-\d{2}-\d{2})$")
SESSION_EXCLUDED_RE = re.compile(r"^(?:\[GARBAGE\]|\.OLDCAMERA)", re.IGNORECASE)

TYPE_FOLDERS = {"LIGHT", "FLAT", "DARK", "BIAS", "DARKFLAT", "FLATDARK"}
SESSION_DATA = ".SessionData"
QUARANTINE_PREFIX = ".MispointedFrames_Quarantine"
REJECTED_DIRS = {"_rejected", "rejected"}
COPY_DIRS = {"accepted"}               # astro-eval writes copies of kept frames here
IGNORED_SESSION_DIRS = {"snapshot"}


def panel_folder(catalog_id: str, panel: str | int) -> str:
    """Mosaic panel folder: "M 31", 2 → "M31_Panel_2"."""
    return re.sub(r"[^\w-]+", "", catalog_id) + f"_Panel_{panel}"


@dataclass(frozen=True)
class TargetName:
    folder: str
    catalog_id: str
    name: str
    revision: int


def parse_target_folder(folder: str) -> TargetName:
    m = TARGET_RE.match(folder)
    if m:
        return TargetName(folder, m["id"].strip(), m["name"].strip(), int(m["rev"] or 1))
    m = REVISION_SUFFIX_RE.match(folder)
    if m:
        return TargetName(folder, m["base"].strip(), "", int(m["rev"]))
    return TargetName(folder, folder.strip(), "", 1)


@dataclass(frozen=True)
class SessionName:
    folder: str
    date: date | None
    status: str          # "canonical" | "legacy" | "excluded" | "nonconforming"


def parse_session_folder(folder: str) -> SessionName:
    if SESSION_EXCLUDED_RE.match(folder):
        return SessionName(folder, _date_in(folder), "excluded")
    m = SESSION_CANONICAL_RE.match(folder)
    if m:
        return SessionName(folder, _to_date(m[1]), "canonical")
    m = SESSION_LEGACY_RE.match(folder)
    if m:
        return SessionName(folder, _to_date(m[1]), "legacy")
    return SessionName(folder, _date_in(folder), "nonconforming")


def _to_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _date_in(text: str) -> date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return _to_date(m[1]) if m else None


@dataclass(frozen=True)
class FrameLocation:
    """Where a frame sits, relative to a revision root's .SessionData (or quarantine)."""

    session: str
    panel: str | None          # "1", "2"... for mosaics
    object_folder: str | None  # NINA object folder, if any (legacy)
    type_hint: str | None      # LIGHT / FLAT / DARK / BIAS from the folder
    filter_hint: str | None
    rejected: bool
    copy: bool                 # a duplicate copy (astro-eval "Accepted")
    ignored: bool              # Snapshot etc.
    reject_parent: str | None  # for a _REJECTED copy: the folder it was rejected from


def locate_in_session_tree(parts: list[str]) -> FrameLocation | None:
    """`parts` = path components below `.SessionData` (or the quarantine dir), file name last."""
    if len(parts) < 2:
        return None
    session = parts[0]
    dirs = parts[1:-1]
    lowered = [d.casefold() for d in dirs]

    ignored = any(d in IGNORED_SESSION_DIRS for d in lowered)
    copy = any(d in COPY_DIRS for d in lowered)
    rejected = any(d in REJECTED_DIRS for d in lowered)

    type_idx = next((i for i, d in enumerate(dirs) if d.upper() in TYPE_FOLDERS), None)
    type_hint = dirs[type_idx].upper() if type_idx is not None else None
    above = dirs[:type_idx] if type_idx is not None else dirs
    below = dirs[type_idx + 1:] if type_idx is not None else []

    panel = None
    object_folder = None
    for d in above:
        if d.casefold() == "flatwizard":
            continue
        m = PANEL_RE.match(d) or LEGACY_PANEL_RE.search(d)
        if m:
            panel = m.group(1)
        object_folder = object_folder or d

    filter_hint = None
    reject_parent = None
    for i, d in enumerate(below):
        if d.casefold() in REJECTED_DIRS | COPY_DIRS:
            if d.casefold() in REJECTED_DIRS:
                reject_parent = "/".join(parts[: 1 + (type_idx or 0) + 1 + i])
            break
        filter_hint = filter_hint or d

    return FrameLocation(
        session=session,
        panel=panel,
        object_folder=object_folder,
        type_hint=type_hint,
        filter_hint=filter_hint,
        rejected=rejected,
        copy=copy,
        ignored=ignored,
        reject_parent=reject_parent,
    )


MASTER_DARK_RE = re.compile(r"masterDark_(?P<temp>-?\d+(?:\.\d+)?)C?_(?P<exp>\d+(?:\.\d+)?)s", re.IGNORECASE)
MASTER_BIAS_RE = re.compile(r"masterBias_(?P<temp>-?\d+(?:\.\d+)?)C?", re.IGNORECASE)
TEMP_DIR_RE = re.compile(r"^(-?\d+(?:\.\d+)?)C$", re.IGNORECASE)
MASTER_FLAT_RE = re.compile(r"MasterFlat_(?P<filter>[A-Za-z0-9]+)_(?P<date>\d{8})", re.IGNORECASE)
