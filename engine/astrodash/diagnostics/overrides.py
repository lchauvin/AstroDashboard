"""The user's own keep / reject decisions, layered over astro-eval's verdicts.

They live in the stored result under "overrides" ({frame path: "keep" | "reject"}), so an
organizer move remaps them with the rest of the result (cache.remap_diagnostics) and a
re-run carries them over. astro-eval's own verdict stays in the frame row as
autoRejected / autoReasons, so the UI can show what was overruled.
"""

from __future__ import annotations

import copy
from typing import Any

from .runner import _group_summary

DECISIONS = ("keep", "reject")
MANUAL_REASON = "manual"


def apply(result: dict[str, Any]) -> dict[str, Any]:
    """The result as the user decided it: a copy with overridden frames and their group summaries updated."""
    overrides = result.get("overrides") or {}
    if not overrides:
        return result
    out = copy.copy(result)
    out["lights"] = []
    for group in result.get("lights", []):
        if not any(f["path"] in overrides for f in group["frames"]):
            out["lights"].append(group)
            continue
        group = copy.deepcopy(group)
        for f in group["frames"]:
            decision = overrides.get(f["path"])
            if decision not in DECISIONS:
                continue
            f["override"] = decision
            f["autoRejected"], f["autoReasons"] = f["rejected"], f["reasons"]
            f["rejected"] = decision == "reject"
            f["reasons"] = [MANUAL_REASON] if f["rejected"] else []
        group["summary"] = _group_summary(group["frames"])
        out["lights"].append(group)
    return out


def light_paths(result: dict[str, Any]) -> set[str]:
    return {f["path"] for g in result.get("lights", []) for f in g["frames"]}


def carry_over(previous: dict[str, Any] | None, result: dict[str, Any]) -> None:
    """Keep the decisions of the last run for the frames this run measured again."""
    old = (previous or {}).get("overrides") or {}
    paths = light_paths(result)
    kept = {p: d for p, d in old.items() if p in paths}
    if kept:
        result["overrides"] = kept
