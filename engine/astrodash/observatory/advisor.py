"""Guiding advisor: the AI looks at tonight's guiding and *suggests* parameter changes. You apply them.

Order of reasoning, and why the model is not first:

1. `guiding.stats` measures the last few clean minutes (disturbances masked out).
2. `guiding.diagnose` classifies each axis from the shape of its error (code, not the model:
   it is statistics with a known answer).
3. The model gets numbers, verdicts, setup, the parameters with their safe ranges and what was
   already tried tonight, and answers with at most two suggestions — or none.
4. Every suggestion is validated: a whitelisted parameter the current algorithm actually exposes,
   inside its range, a step no larger than `max_step`. Out-of-range proposals are dropped.

The model may also suggest switching an axis's algorithm (`param` "algorithm", one per answer). PHD2's
server has no command for that (`set_algo_param` only takes numbers), so you switch it in PHD2's Brain;
the switch writes PHD2's profile, PHD2 sends ConfigurationChange, and the runtime logs and measures it
like any change, after a longer settle (Predictive PEC has to learn the period first).

The periodic run skips the model call when the diagnosis says nothing is actionable (healthy or
seeing-limited, or a caveat such as low SNR): most guiding error is seeing and wind, which no
parameter fixes. "Analyze now" always asks.

Every applied change (yours or the AI's) is measured: RMS over the 5 clean minutes before vs
the 5 clean minutes after. A difference smaller than 1.5 combined standard errors is "no clear
change"; a guide-star HFD that moved > 20 % means the sky changed and the result is void.
"""

from __future__ import annotations

import itertools
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from . import guiding

# Safe ranges for the AI (manual edits are only limited by PHD2 itself). minMove is in guide-camera
# pixels; `lo_arcsec`/`hi_arcsec` re-derive its range from the pixel scale so it stays meaningful
# on any guide scope.
BOUNDS: dict[str, dict[str, float]] = {
    "aggression": {"lo": 0.40, "hi": 1.00, "max_step": 0.10, "quantum": 0.05},
    "aggressiveness": {"lo": 0.40, "hi": 1.00, "max_step": 0.10, "quantum": 0.05},
    "hysteresis": {"lo": 0.00, "hi": 0.30, "max_step": 0.05, "quantum": 0.05},
    "minMove": {"lo": 0.05, "hi": 0.50, "max_step": 0.05, "quantum": 0.01, "lo_arcsec": 0.10, "hi_arcsec": 0.70},
}

# Plain-language names and help for the parameter panel (PHD2's own names stay visible in Advanced mode).
PARAM_INFO = {
    "aggression": ("Aggressiveness", "Share of the measured error corrected each frame."),
    "aggressiveness": ("Aggressiveness", "Share of the measured error corrected each frame."),
    "hysteresis": ("Hysteresis", "Weight of the previous correction: smooths noise, slows the response."),
    "minMove": ("Min-move", "Errors smaller than this (pixels) get no correction."),
    "fastSwitch": ("Fast switch", "Resist-switch: reverse immediately on a large error."),
    "slopeWeight": ("Slope weight", "Low-pass: weight of the trend line."),
    "predictWeight": ("Predictive weight", "PPEC: weight of the predicted periodic error."),
    "periodLengthsInference": ("Period lengths (inference)", "PPEC: periods used for the prediction."),
    "periodLengthsPeriodEstimation": ("Period lengths (estimation)", "PPEC: periods used to estimate the period."),
    "numPointsApproximation": ("Points (approximation)", "PPEC: points in the approximation."),
    "SE0KLengthScale": ("SE0 length scale", "PPEC kernel parameter."),
    "SE0KSignalVariance": ("SE0 signal variance", "PPEC kernel parameter."),
    "PKLengthScale": ("Periodic length scale", "PPEC kernel parameter."),
    "PKSignalVariance": ("Periodic signal variance", "PPEC kernel parameter."),
    "SE1KLengthScale": ("SE1 length scale", "PPEC kernel parameter."),
    "SE1KSignalVariance": ("SE1 signal variance", "PPEC kernel parameter."),
    "PKPeriodLength": ("Period length", "PPEC: the periodic error's period (s)."),
    "predictiveWeight": ("Predictive weight", "PPEC: weight of the predicted periodic correction."),
    "reactiveWeight": ("Reactive weight", "PPEC: weight of the ordinary correction of the measured error."),
    "periodLength": ("Period length", "PPEC: the periodic error's period (s); PPEC refines it as it learns."),
    "expFactor": ("Exponential factor", "ZFilter: smoothing of the corrections."),
}
BASIC_PARAMS = {"aggression", "aggressiveness", "hysteresis", "minMove"}

# Switching the algorithm: the names PHD2 reports (`algorithmName`), the ones the advisor may pick per axis
# (Predictive PEC is RA only in PHD2; Lowpass and ZFilter are left to you), and the Brain's own spelling.
ALGORITHM = "algorithm"  # the `param` of a suggestion or change that switches the algorithm
ALGORITHMS = {"ra": ("Hysteresis", "Lowpass2", "ResistSwitch", "Predictive PEC"),
              "dec": ("ResistSwitch", "Lowpass2", "Hysteresis")}
ALGORITHM_LABEL = {"ResistSwitch": "Resist Switch"}
_ALGORITHM_ALIASES = {"hysteresis": "Hysteresis", "lowpass2": "Lowpass2", "resistswitch": "ResistSwitch",
                      "predictivepec": "Predictive PEC", "ppec": "Predictive PEC", "gaussianprocess": "Predictive PEC"}

WINDOW_S = 300.0  # before/after measurement windows and the analysis window
SETTLE_S = 20.0  # after a change, before the after-window starts
ALGORITHM_SETTLE_S = {"Predictive PEC": 600.0}  # PPEC learns the period over its first minutes
ALGORITHM_SETTLE_DEFAULT_S = 120.0  # the other algorithms start from fresh state too
MIN_EFFECT_SIGMA = 1.5
HFD_CONFOUND = 0.20


def resolved_bounds(param: str, scale: float | None) -> dict[str, float] | None:
    b = BOUNDS.get(param)
    if b is None:
        return None
    b = dict(b)
    if scale and "lo_arcsec" in b:
        b["lo"], b["hi"] = round(b["lo_arcsec"] / scale, 2), round(b["hi_arcsec"] / scale, 2)
    return b


def algorithm_name(value: Any) -> str | None:
    """"ppec", "Resist Switch", "Predictive PEC" … → PHD2's name, or None."""
    key = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    return _ALGORITHM_ALIASES.get(key)


def settle_s(param: str, new: Any) -> float:
    if param == ALGORITHM:
        return ALGORITHM_SETTLE_S.get(str(new), ALGORITHM_SETTLE_DEFAULT_S)
    return SETTLE_S


SYSTEM = """\
You tune PHD2 autoguiding parameters for an amateur astrophotography mount. You get the last few \
clean minutes of guiding (dithers, settling, star loss, flips excluded), the conditions, the setup, \
the parameters with their safe ranges, and what was already changed tonight with the measured outcome. \
You suggest at most TWO small parameter changes, ranked, or none.

Most guiding error is seeing and wind, which NO parameter fixes. A change only helps when the error \
has the wrong SHAPE for its size. Per-axis columns:
- lag1 (frame-to-frame autocorrelation): below -0.3 = overshooting (each correction flips the sign): \
LOWER aggressiveness or RAISE hysteresis (RA). Above +0.5 = slow wander not caught: RAISE aggressiveness; \
if idle is high, LOWER minMove instead. Near 0 = white noise = seeing.
- mean: a persistent signed offset = drift under-corrected (raise aggressiveness / lower minMove). A large \
Dec offset can also be backlash or a one-direction Dec guide mode, which no parameter fixes.
- idle: share of frames with no pulse (inside minMove). High idle + wander = deadband too wide. Near-zero \
idle + white noise + many reversals = chasing seeing: RAISE minMove.
- reversals: share of consecutive pulses that reversed direction.
- pulse: mean pulse per frame (ms). limited: share of frames hitting the max-duration limit.
- osc: RA peak/RMS; ~1.7 random, above ~2.2 ringing.
The AUTOMATIC DIAGNOSIS applies these rules; trust it unless tonight's measured changes contradict it.

Rules:
- "none" (empty list) is the right answer when both axes are healthy or seeing-limited, a CAVEAT is \
listed, or the evidence is weak. Say so plainly; do not invent changes.
- Only parameters listed as TUNABLE, exact names, inside [min,max], step at most max_step (about half \
when evidence is moderate). If the needed parameter is at its limit, use the alternative fix or none.
- Never repeat a change that tonight's log shows made things WORSE; do not undo one that made them BETTER.
- The guide exposure, Dec guide mode and calibration are not yours to change, but you may mention them \
in the assessment (e.g. low SNR → longer exposure; stale calibration → recalibrate).
- POLAR ALIGNMENT is measured from the Dec corrections. A steady Dec drift is normal and guiding cancels \
it; it calls for a parameter only when Dec is under-correcting. Mention re-aligning in the assessment only \
when the error exceeds about 10′ or a SHIFT was detected (the mount moved).

ALGORITHM: you may also switch an axis's guide algorithm: param "algorithm", value one of the names listed \
under ALGORITHMS for that axis. It is a bigger step than a parameter: suggest it only when the current \
algorithm cannot fix what the diagnosis shows (its parameter is at its limit, or tonight's changes on that \
axis measured no better), at most ONE switch per answer and no parameter change on the same axis with it \
(the new algorithm brings its own parameters). The usual cases:
- RA slow periodic wander (lag1 above +0.5, osc low) that tonight's aggressiveness/min-move changes did not \
fix: the periodic error of a gear mount. Predictive PEC learns and anticipates it (guide exposures of a few \
seconds at most; it needs ~10 min to learn). Predictive PEC that measured worse: back to Hysteresis.
- Dec chasing seeing (many reversals, white noise) on Hysteresis or Lowpass2: ResistSwitch, which resists \
reversals. Dec slow drift corrected in jerks on ResistSwitch: Lowpass2.
- Never switch back an algorithm that tonight's log shows measured BETTER.

Reply with ONE JSON object and nothing else:
{"assessment": "<what limits guiding now, <=200 chars>",
 "suggestions": [{"axis": "ra"|"dec", "param": "<name>", "value": <number, or an algorithm name>, "why": "<=160 chars>"}]}"""


def _fmt(v: Any, d: int = 2) -> str:
    return "?" if v is None else f"{v:.{d}f}" if isinstance(v, (int, float)) else str(v)


def _val(v: Any) -> str:
    """A change's old/new: a number, or an algorithm name."""
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def build_prompt(ctx: dict) -> str:
    st, diag, phd = ctx["stats"], ctx["diagnosis"], ctx["phd2"]
    scale = phd.get("pixelScale")
    lines = []
    cond = [f"seeing {_fmt(st['hfdPx'] * scale if scale else None)}″ (guide-star HFD)", f"guide SNR {_fmt(st['snr'], 1)}",
            f"star mass {st['mass']}"]
    mount = ctx.get("mount") or {}
    if mount:
        cond += [f"altitude {_fmt(mount.get('altitude'), 0)}°", f"pier {mount.get('pier') or '?'}"]
        if mount.get("flipInH") is not None:
            cond.append(f"meridian flip in {_fmt(mount['flipInH'], 1)} h")
    for k, v in (ctx.get("weather") or {}).items():
        cond.append(f"{k} {v}")
    lines.append("CONDITIONS: " + ", ".join(cond))
    setup = [f"RA algorithm {phd['algorithms'].get('ra', '?')}", f"Dec algorithm {phd['algorithms'].get('dec', '?')}",
             f"guide exposure {(phd.get('exposureMs') or 0) / 1000:g} s", f"scale {_fmt(scale)}″/px (minMove is in px)",
             f"Dec guide mode {phd.get('decGuideMode') or '?'}"]
    cal = phd.get("calibration") or {}
    if cal:
        setup.append(f"calibration: RA rate {_fmt(cal.get('xRate'), 3)} px/s, Dec rate {_fmt(cal.get('yRate'), 3)} px/s, "
                     f"axes {_fmt(cal.get('xAngle'), 0)}°/{_fmt(cal.get('yAngle'), 0)}°, at Dec {_fmt(cal.get('declination'), 0)}°")
    lines.append("SETUP: " + ", ".join(setup))
    lines.append(f"TARGET: total RMS {ctx['targetRms']:.2f}″")
    if pol := polar_line(ctx.get("polar")):
        lines.append(pol)
    lines.append("")
    lines.append(f"GUIDING NOW: total {st['total']:.2f}″ ±{st['se']:.2f}″ (n={st['n']} frames, {st['seconds']} s clean, osc {st['oscillation']:.2f})")
    lines.append("  axis   rms    peak   mean    lag1  idle reversals pulse limited")
    for axis in ("ra", "dec"):
        a = st[axis]
        lines.append(f"  {axis.upper():<4} {a['rms']:5.2f}″ {a['peak']:5.2f}″ {a['mean']:+5.2f}″ {a['lag1']:+5.2f} {a['idle']:4.0%} {a['flips']:6.0%}"
                     f"   {a['pulseMs']:4.0f}ms {a['limited']:4.0%}")
    lines.append("")
    lines.append("AUTOMATIC DIAGNOSIS:")
    for a in diag["axes"]:
        lines.append(f"  {a['axis'].upper():<3} {a['verdict']}: {a['evidence']} -> {a['hint']}")
    for c in diag["caveats"]:
        lines.append(f"  CAVEAT: {c}")
    lines.append("")
    lines.append("PARAMETERS (axis name value [min,max] max_step; TUNABLE ones have a range):")
    for axis, params in phd["params"].items():
        for name, value in params.items():
            b = resolved_bounds(name, scale)
            rng = f" [{b['lo']:g},{b['hi']:g}] max_step {b['max_step']:g}  TUNABLE" if b else "  (not tunable)"
            lines.append(f"  {axis} {name} {value:g}{rng}")
    lines.append("")
    lines.append("ALGORITHMS (param \"algorithm\"; current in brackets):")
    for axis, names in ALGORITHMS.items():
        current = phd["algorithms"].get(axis)
        lines.append(f"  {axis} " + " | ".join(f"[{n}]" if n == current else n for n in names)
                     + ("" if current in names or current is None else f"  (now {current})"))
    lines.append("")
    log = ctx.get("changes") or []
    lines.append("TONIGHT'S CHANGES (oldest first):" if log else "TONIGHT'S CHANGES: none yet.")
    for c in log[-8:]:
        lines.append(f"  {c['axis']}.{c['param']} {_val(c['old'])} -> {_val(c['new'])} ({c['source']}): {c['outcome']}")
    lines.append("")
    lines.append("Reply with the JSON object only.")
    return "\n".join(lines)


def polar_line(p: dict | None) -> str | None:
    if not p or not p.get("now"):
        return None
    now, f = p["now"], p["fit"]
    out = f"POLAR ALIGNMENT: Dec drift {now['rate']:+.2f}″/min, error at least {now['minArcmin']:.0f}′"
    if f.get("ok"):
        out += f"; fitted over {f['spanH']} h: altitude {f['alt']:+.0f}′ ±{f['altSe']:.0f}, azimuth {f['az']:+.0f}′ ±{f['azSe']:.0f}"
    for s in p.get("shifts") or []:
        if time.time() - s["t"] < 3 * 3600:
            out += f"; SHIFT {s['deltaArcmin']:g}′ at {time.strftime('%H:%M', time.localtime(s['t']))}"
    return out


def validate(parsed: dict | None, phd: dict) -> tuple[str, list[dict], list[str]]:
    """Model reply → (assessment, valid suggestions, notes on what was dropped or adjusted)."""
    if not isinstance(parsed, dict):
        return "", [], ["The model did not answer with a JSON object."]
    assessment = str(parsed.get("assessment") or parsed.get("diagnosis") or "")[:300]
    raw = parsed.get("suggestions")
    if raw is None and parsed.get("action") == "set":  # tolerate AstroController's single-action shape
        raw = [parsed]
    notes: list[str] = []
    out: list[dict] = []
    scale = phd.get("pixelScale")
    for s in (raw if isinstance(raw, list) else [])[:4]:
        if not isinstance(s, dict):
            continue
        axis = str(s.get("axis", "")).strip().lower()
        param = str(s.get("param", "")).strip()
        for sep in (" ", "."):
            if param.lower().startswith(axis + sep):
                param = param[len(axis) + 1:].strip()
        if param.lower() in (ALGORITHM, "algorithmname", "guide algorithm"):
            if sg := _validate_algorithm(axis, s, phd, out, notes):
                out.append(sg)
            continue
        current = (phd["params"].get(axis) or {}).get(param)
        b = resolved_bounds(param, scale)
        try:
            value = float(s.get("value"))
        except (TypeError, ValueError):
            notes.append(f"Dropped {axis}.{param}: no numeric value.")
            continue
        if axis not in ("ra", "dec") or current is None:
            notes.append(f"Dropped {axis}.{param}: the current {axis.upper() or '?'} algorithm has no such parameter.")
            continue
        if b is None:
            notes.append(f"Dropped {axis}.{param}: not a parameter the advisor may change.")
            continue
        adjusted = None
        if value < b["lo"] - b["max_step"] or value > b["hi"] + b["max_step"]:
            notes.append(f"Dropped {axis}.{param} = {value:g}: far outside the safe range [{b['lo']:g}, {b['hi']:g}].")
            continue
        clamped = min(max(value, b["lo"]), b["hi"])
        delta = clamped - current
        if abs(delta) > b["max_step"] + 1e-9:
            clamped = current + math.copysign(b["max_step"], delta)
        q = b["quantum"]
        clamped = round(round(clamped / q) * q, 4)
        if abs(clamped - value) > 1e-6:
            adjusted = f"adjusted from {value:g} to stay within range and step"
        if abs(clamped - current) < 1e-9:
            notes.append(f"Dropped {axis}.{param}: no change from the current {current:g}.")
            continue
        out.append({
            "id": uuid.uuid4().hex[:10], "axis": axis, "param": param, "current": current, "value": clamped,
            "why": str(s.get("why") or s.get("rationale") or "")[:240], "note": adjusted, "status": "open",
        })
    switched = {s["axis"] for s in out if s["param"] == ALGORITHM}
    for s in [s for s in out if s["axis"] in switched and s["param"] != ALGORITHM]:
        notes.append(f"Dropped {s['axis']}.{s['param']}: it belongs to the algorithm the switch replaces.")
        out.remove(s)
    return assessment, out[:2], notes


def _validate_algorithm(axis: str, s: dict, phd: dict, out: list[dict], notes: list[str]) -> dict | None:
    current = (phd.get("algorithms") or {}).get(axis)
    wanted = algorithm_name(s.get("value"))
    if axis not in ALGORITHMS or current is None:
        notes.append(f"Dropped {axis}.algorithm: PHD2 did not report the current {axis.upper() or '?'} algorithm.")
    elif wanted not in ALGORITHMS[axis]:
        notes.append(f"Dropped {axis}.algorithm = {s.get('value')!r}: not one of {', '.join(ALGORITHMS[axis])}.")
    elif wanted == current:
        notes.append(f"Dropped {axis}.algorithm: {current} is already in use.")
    elif any(x["param"] == ALGORITHM for x in out):
        notes.append(f"Dropped {axis}.algorithm = {wanted}: one algorithm switch at a time.")
    else:
        return {"id": uuid.uuid4().hex[:10], "axis": axis, "param": ALGORITHM, "current": current, "value": wanted,
                "why": str(s.get("why") or s.get("rationale") or "")[:240], "note": None, "status": "open"}
    return None


# ---------------------------------------------------------------------- change log


def _brief(st: dict | None) -> dict | None:
    """Full stats → the before/after summary; a summary already (an analysis's `stats`) passes as it is."""
    if not st:
        return None
    if not isinstance(st.get("ra"), dict):
        return st
    return {"total": st["total"], "ra": st["ra"]["rms"], "dec": st["dec"]["rms"], "n": st["n"], "se": st["se"], "hfdPx": st["hfdPx"]}


@dataclass
class Advisor:
    """Holds tonight's analyses and the change log; the runtime calls `tick` and `record_change`."""

    store: Any  # HeaderCache-like: get_json / set_json

    def __post_init__(self) -> None:
        saved = self.store.get_json("guiding_log") or {}
        self.changes: list[dict] = saved.get("changes", [])[-200:]
        self.analyses: list[dict] = saved.get("analyses", [])[-30:]
        self.busy = False
        self.last_call = 0.0
        self.last_error: str | None = None
        self._ids = itertools.count(1)

    def save(self) -> None:
        self.store.set_json("guiding_log", {"changes": self.changes[-200:], "analyses": self.analyses[-30:]})

    def tonight(self, hours: float = 14) -> tuple[list[dict], list[dict]]:
        cutoff = time.time() - hours * 3600
        return [c for c in self.changes if c["t"] >= cutoff], [a for a in self.analyses if a["t"] >= cutoff]

    def record_change(self, axis: str, param: str, old: float | str, new: float | str, source: str, before: dict | None,
                      suggestion: str | None = None) -> dict:
        """`param` ALGORITHM: `old`/`new` are algorithm names, and every open suggestion for that axis goes stale."""
        now = time.time()
        settle = settle_s(param, new)
        for c in self.changes:  # a newer change spoils the measurement of a pending one
            if c["verdict"] == "pending":
                c["verdict"], c["outcome"] = "void", "superseded by a newer change before it could be measured"
        entry = {
            "id": uuid.uuid4().hex[:10], "t": now, "axis": axis, "param": param, "old": old, "new": new, "source": source,
            "suggestion": suggestion, "before": _brief(before), "after": None, "verdict": "pending" if before else "void",
            "outcome": "measuring…" if before else "not measured (no clean guiding before the change)",
            "settleS": settle, "measureAt": now + settle + WINDOW_S,
        }
        self.changes.append(entry)
        for a in self.analyses:
            for s in a.get("suggestions", []):
                if s["status"] == "open" and s["axis"] == axis and (s["param"] == param or param == ALGORITHM):
                    s["status"] = "applied" if s["id"] == suggestion else "stale"
        self.save()
        return entry

    def measure(self, buffer: guiding.GuideBuffer, dist: guiding.Disturbances) -> None:
        now = time.time()
        changed = False
        for c in self.changes:
            if c["verdict"] != "pending" or now < c["measureAt"]:
                continue
            start = c["t"] + c.get("settleS", SETTLE_S)
            samples = buffer.window(start, start + WINDOW_S, dist)
            after = guiding.stats(samples, dist.clean_seconds(start, start + WINDOW_S))
            changed = True
            c["after"] = _brief(after)
            before = c["before"]
            if not after or after["n"] < guiding.MIN_SAMPLES:
                c["verdict"], c["outcome"] = "void", "not measured (too little clean guiding afterwards)"
                continue
            if before["hfdPx"] and abs(after["hfdPx"] - before["hfdPx"]) / before["hfdPx"] > HFD_CONFOUND:
                c["verdict"], c["outcome"] = "void", "seeing changed during the test (guide-star HFD moved > 20 %); inconclusive"
                continue
            diff = after["total"] - before["total"]
            sig = MIN_EFFECT_SIGMA * math.hypot(before["se"], after["se"])
            pct = 100 * diff / before["total"] if before["total"] else 0
            if abs(diff) < sig:
                c["verdict"], c["outcome"] = "neutral", f"no clear change ({before['total']:.2f}″ → {after['total']:.2f}″)"
            elif diff < 0:
                c["verdict"], c["outcome"] = "better", f"BETTER: {before['total']:.2f}″ → {after['total']:.2f}″ ({pct:+.0f} %)"
            else:
                c["verdict"], c["outcome"] = "worse", f"WORSE: {before['total']:.2f}″ → {after['total']:.2f}″ ({pct:+.0f} %)"
        if changed:
            self.save()

    def pending(self) -> dict | None:
        return next((c for c in reversed(self.changes) if c["verdict"] == "pending"), None)

    def add_analysis(self, entry: dict) -> None:
        # Older open suggestions are superseded by the new analysis.
        for a in self.analyses:
            for s in a.get("suggestions", []):
                if s["status"] == "open":
                    s["status"] = "stale"
        self.analyses.append(entry)
        self.save()

    def suggestion(self, sid: str) -> dict | None:
        for a in reversed(self.analyses):
            for s in a.get("suggestions", []):
                if s["id"] == sid:
                    return s
        return None

    def dismiss(self, sid: str) -> bool:
        s = self.suggestion(sid)
        if s and s["status"] == "open":
            s["status"] = "dismissed"
            self.save()
            return True
        return False
