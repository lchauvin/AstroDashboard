"""Framing: is a night pointed and rotated like the night before it? (Library, Diagnostics, Live)

A frame is reduced to the positions of its brightest stars (found on 2×2-binned pixels, given
in sensor pixels). Two frames are matched star to star, at any rotation, which says where one
frame's field sits in the other's pixels: its *pose*, the centre plus the rotation folded into
±90° (a meridian flip turns the image half a turn, and stacking does not care).

A night's framing is the median pose of its lights (every filter) relative to one of them, the
*anchor*. Its *spread* is the 90th percentile of the lights' distances from that median: dither
and drift together. Dithering only shifts the frame, so the *dither* is the same percentile of
their shifts alone, and a change of rotation through the night is drift (field rotation) or the
framing being redone (a meridian flip, a re-centring). The distance between two poses is the largest
corner shift: how far a corner of one field lies from the same corner of the other, so a shift
and a rotation are judged in the same pixels, the ones the stack loses at its edges.

A night is compared with the previous night of the same revision and mosaic panel, median to
median. Within the tolerance (the larger of the two nights' dither spreads, never less than
MIN_TOLERANCE_PX) is aligned (ok), within twice that a little off (warn), beyond it off (bad).
A frame is judged against the previous night's median the same way, with its own night's spread
added to the tolerance since it dithers around that night's median; Live, with one frame of
tonight, takes tonight's spread to be the previous night's.

Frames are only read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BIN = 2                    # sensor px per detection px
MAX_STARS = 150            # brightest stars kept per frame
DETECT_SIGMA = 5.0
EDGE_PX = 20               # sensor px; stars this close to an edge come and go with the dither
MAX_STAR_PIXELS = 1500     # detection px; larger blobs are nebula knots or a galaxy core
MAX_ELONGATION = 3.0       # a/b; longer ones are trails
VOTE_STARS = 60            # brightest stars voting for the translation
PAIR_STARS = 50            # brightest stars whose pairs vote for the rotation
MIN_PAIR_PX = 100.0        # shorter pairs give imprecise angles
VOTE_BIN_PX = 8.0
MIN_MATCHES = 8
GOOD_MATCHES = 25          # a quick (no-rotation) fit this good is not worth a rotation search
MAX_SCALE_ERROR = 0.01     # same optics: a fit scaling by more than 1 % is a false match
MIN_TOLERANCE_PX = 10.0    # below this nothing is visible in a stack, dithered or not
SPREAD_PERCENTILE = 90
# Framed differently from the rest of its night (as Diagnostics' mispointing check): further
# than this from the night's framing, and this many times the night's typical distance.
OUTLIER_PX = 150.0
OUTLIER_FACTOR = 4.0


# ---------------------------------------------------------------------------- stars

@dataclass
class Stars:
    """Brightest-first star positions in sensor pixels, and the frame's size in sensor pixels."""

    xy: np.ndarray
    w: int
    h: int


def detect(path: Path) -> Stars:
    import sep

    from .headers import read_header
    from .preview import _load

    img = _load(path)
    try:
        xbin = int(read_header(path).get("XBINNING") or 1)
    except Exception:  # noqa: BLE001 — the pixels were readable; assume unbinned
        xbin = 1
    h, w = (img.shape[0] // BIN) * BIN, (img.shape[1] // BIN) * BIN
    small = np.ascontiguousarray(img[:h, :w].reshape(h // BIN, BIN, w // BIN, BIN).mean(axis=(1, 3)), dtype=np.float32)
    small = np.nan_to_num(small, nan=float(np.nanmedian(small)))
    bkg = sep.Background(small, bw=64, bh=64)
    data = small - bkg.back()
    sep.set_extract_pixstack(2_000_000)
    sep.set_sub_object_limit(4096)
    objs = sep.extract(data, DETECT_SIGMA, err=bkg.globalrms, minarea=4)
    keep = (objs["npix"] <= MAX_STAR_PIXELS) & (objs["a"] <= MAX_ELONGATION * np.maximum(objs["b"], 1e-3))
    objs = objs[keep]
    objs = objs[np.argsort(-objs["flux"])]
    # Detection px → sensor px: a binned pixel's centre is (BIN − 1) / 2 into its block.
    x = (objs["x"] * BIN + (BIN - 1) / 2) * xbin + (xbin - 1) / 2
    y = (objs["y"] * BIN + (BIN - 1) / 2) * xbin + (xbin - 1) / 2
    width, height = img.shape[1] * xbin, img.shape[0] * xbin
    inside = (x >= EDGE_PX) & (x <= width - 1 - EDGE_PX) & (y >= EDGE_PX) & (y <= height - 1 - EDGE_PX)
    xy = np.column_stack([x[inside], y[inside]])[:MAX_STARS]
    return Stars(xy.astype(np.float32), int(width), int(height))


def measure(path: str) -> tuple[str, dict[str, Any] | None, str | None]:
    """Process-pool entry point → (path, stars as a dict, error)."""
    try:
        return path, stars_to_dict(detect(Path(path))), None
    except Exception as exc:  # noqa: BLE001 — an unreadable frame is reported, never fatal
        return path, None, f"{type(exc).__name__}: {exc}"


def stars_to_dict(s: Stars) -> dict[str, Any]:
    return {"w": s.w, "h": s.h, "xy": np.round(s.xy.astype(np.float64), 2).tolist()}


def stars_from_dict(d: dict[str, Any]) -> Stars:
    return Stars(np.asarray(d["xy"], dtype=np.float32).reshape(-1, 2), int(d["w"]), int(d["h"]))


# ---------------------------------------------------------------------------- matching

@dataclass
class Fit:
    """Similarity transform taking reference pixels to frame pixels: p_frame = A @ p_ref + t."""

    A: np.ndarray
    t: np.ndarray
    matches: int
    rms: float

    @property
    def angle(self) -> float:
        return math.degrees(math.atan2(self.A[1, 0], self.A[0, 0]))

    @property
    def scale(self) -> float:
        return float(math.sqrt(abs(np.linalg.det(self.A))))


def _similarity(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares rotation + uniform scale + translation taking src onto dst (Umeyama)."""
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s, d = src - mu_s, dst - mu_d
    var_s = float((s ** 2).sum() / len(s))
    U, sig, Vt = np.linalg.svd(d.T @ s / len(s))
    D = np.diag([1.0, np.sign(np.linalg.det(U @ Vt)) or 1.0])
    R = U @ D @ Vt
    scale = float(np.trace(np.diag(sig) @ D) / var_s) if var_s > 0 else 1.0
    A = scale * R
    return A, mu_d - A @ mu_s


def _match(pred: np.ndarray, tree, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """One-to-one nearest-neighbour pairs within `radius` (the closest pair wins)."""
    dist, j = tree.query(pred, distance_upper_bound=radius)
    ok = np.isfinite(dist)
    ri, fj, dd = np.nonzero(ok)[0], j[ok], dist[ok]
    order = np.argsort(dd)
    _, first = np.unique(fj[order], return_index=True)
    keep = order[first]
    return ri[keep], fj[keep]


def _rotation(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s], [s, c]])


def _fit_at(ref: np.ndarray, frm: np.ndarray, tree, angle: float, centre: np.ndarray) -> Fit | None:
    """Rotate the reference by `angle` about the frame centre, vote for the translation, refine."""
    R = _rotation(angle)
    rotated = (ref[:VOTE_STARS] - centre) @ R.T + centre
    off = (frm[:VOTE_STARS][None, :, :] - rotated[:, None, :]).reshape(-1, 2)
    cells, counts = np.unique(np.floor(off / VOTE_BIN_PX).astype(np.int64), axis=0, return_counts=True)
    if not len(cells) or counts.max() < 3:
        return None
    peak = (cells[np.argmax(counts)] + 0.5) * VOTE_BIN_PX
    near = np.all(np.abs(off - peak) <= VOTE_BIN_PX, axis=1)
    if near.sum() < 3:
        return None
    A, t = R, centre - R @ centre + np.median(off[near], axis=0)
    ri = fj = np.empty(0, dtype=int)
    for radius in (24.0, 8.0, 3.0):
        ri, fj = _match(ref @ A.T + t, tree, radius)
        if len(ri) < MIN_MATCHES:
            return None
        A, t = _similarity(ref[ri], frm[fj])
    fit = Fit(A, t, int(len(ri)), float(np.sqrt(np.mean(np.sum((ref[ri] @ A.T + t - frm[fj]) ** 2, axis=1)))))
    return fit if abs(fit.scale - 1) <= MAX_SCALE_ERROR else None


def _angle_votes(ref: np.ndarray, frm: np.ndarray) -> list[float]:
    """Rotation candidates: the angle between star pairs of the same length, modulo 180°.

    A pair's direction is ambiguous (which star comes first), so the votes are modulo 180°
    and each peak gives two candidates, half a turn apart.
    """

    def pairs(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        i, j = np.triu_indices(len(p), 1)
        d = p[j] - p[i]
        length = np.hypot(d[:, 0], d[:, 1])
        keep = length >= MIN_PAIR_PX
        return length[keep], np.degrees(np.arctan2(d[keep, 1], d[keep, 0]))

    la, ta = pairs(ref[:PAIR_STARS].astype(np.float64))
    lb, tb = pairs(frm[:PAIR_STARS].astype(np.float64))
    if not len(la) or not len(lb):
        return []
    order = np.argsort(lb)
    lb, tb = lb[order], tb[order]
    tol = 1.5 + 0.002 * la
    lo, hi = np.searchsorted(lb, la - tol), np.searchsorted(lb, la + tol)
    counts = hi - lo
    if not counts.sum():
        return []
    ia = np.repeat(np.arange(len(la)), counts)
    ib = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts) + np.repeat(lo, counts)
    votes = (tb[ib] - ta[ia]) % 180.0
    hist, edges = np.histogram(votes, bins=360, range=(0.0, 180.0))
    smooth = hist + np.roll(hist, 1) + np.roll(hist, -1)
    out: list[float] = []
    for _ in range(2):                                   # the two strongest peaks
        k = int(np.argmax(smooth))
        if smooth[k] < 3:
            break
        centre = (edges[k] + edges[k + 1]) / 2
        diff = (votes - centre + 90.0) % 180.0 - 90.0
        near = np.abs(diff) <= 1.0
        peak = centre + float(np.mean(diff[near]))
        out += [peak % 360.0, (peak + 180.0) % 360.0]
        idx = (np.arange(-6, 7) + k) % len(smooth)       # ±3° around it: the same peak
        smooth[idx] = 0
    return out


def register(ref: Stars, frm: Stars) -> Fit | None:
    """Where `ref`'s stars land in `frm`, at any rotation; None when fewer than MIN_MATCHES match.

    First the cheap case (the same orientation or a half turn, as within a night), then, unless
    that already matched plenty, every rotation the star pairs vote for. The most matches win.
    """
    from scipy.spatial import cKDTree

    if len(ref.xy) < MIN_MATCHES or len(frm.xy) < MIN_MATCHES:
        return None
    a, b = ref.xy.astype(np.float64), frm.xy.astype(np.float64)
    tree = cKDTree(b)
    centre = np.array([(frm.w - 1) / 2, (frm.h - 1) / 2])
    best: Fit | None = None

    def consider(fit: Fit | None) -> None:
        nonlocal best
        if fit is not None and (best is None or fit.matches > best.matches):
            best = fit

    for angle in (0.0, 180.0):
        consider(_fit_at(a, b, tree, angle, centre))
    if best is None or best.matches < GOOD_MATCHES:
        for angle in _angle_votes(a, b):
            consider(_fit_at(a, b, tree, angle, centre))
    return best


# ---------------------------------------------------------------------------- poses

def fold(deg: float) -> float:
    """An angle into (−90°, 90°]: a half turn changes nothing for the framing."""
    v = (deg + 90.0) % 180.0 - 90.0
    return 90.0 if v == -90.0 else v


@dataclass
class Pose:
    """Where a frame's field sits in an anchor's pixels: its centre, and its rotation (folded)."""

    x: float
    y: float
    rot: float


def pose_in_ref(fit: Fit, frm: Stars) -> Pose:
    """The pose of the frame `fit` was registered onto, in the reference's pixels."""
    inv = np.linalg.inv(fit.A)
    c = inv @ (np.array([(frm.w - 1) / 2, (frm.h - 1) / 2]) - fit.t)
    return Pose(float(c[0]), float(c[1]), fold(-fit.angle))


def to_ref(pose: Pose, fit: Fit) -> Pose:
    """A pose given in the pixels of the frame `fit` was registered onto → in the reference's pixels."""
    inv = np.linalg.inv(fit.A)
    c = inv @ (np.array([pose.x, pose.y]) - fit.t)
    return Pose(float(c[0]), float(c[1]), fold(pose.rot - fit.angle))


def _corner_shifts(a: list[Pose], b: list[Pose], w: float, h: float) -> np.ndarray:
    """Largest corner shift between every pose of `a` and every pose of `b` (len(a) × len(b))."""
    ax, ay, ar = (np.array([getattr(p, k) for p in a])[:, None] for k in ("x", "y", "rot"))
    bx, by, br = (np.array([getattr(p, k) for p in b])[None, :] for k in ("x", "y", "rot"))
    d = np.radians((br - ar + 90.0) % 180.0 - 90.0)
    c, s = np.cos(np.radians(-ar)), np.sin(np.radians(-ar))
    # In a's own axes: the centre offset, plus how the rotation between them moves each corner.
    dx, dy = c * (bx - ax) - s * (by - ay), s * (bx - ax) + c * (by - ay)
    out = np.zeros(np.broadcast(dx, dy).shape)
    for vx in (-w / 2, w / 2):
        for vy in (-h / 2, h / 2):
            ex = dx + (np.cos(d) - 1) * vx - np.sin(d) * vy
            ey = dy + np.sin(d) * vx + (np.cos(d) - 1) * vy
            out = np.maximum(out, np.hypot(ex, ey))
    return out


def corner_shift(a: Pose, b: Pose, w: float, h: float) -> float:
    """The largest distance between the same corner of the two fields (sensor px)."""
    return float(_corner_shifts([a], [b], w, h)[0, 0])


def centre_shift(a: Pose, b: Pose) -> float:
    return float(math.hypot(b.x - a.x, b.y - a.y))


def _median_pose(ps: list[Pose]) -> Pose:
    """Median centre and rotation; rotations are taken relative to the first pose's, so a framing near ±90°
    does not straddle the fold."""
    r0 = ps[0].rot
    return Pose(float(np.median([p.x for p in ps])), float(np.median([p.y for p in ps])),
                fold(r0 + float(np.median([fold(p.rot - r0) for p in ps]))))


def _consensus(poses: dict[str, Pose], w: int, h: int) -> tuple[Pose, list[str]]:
    """A night's framing from its frames' poses → (median pose, frames framed differently).

    The frame closest to all the others (the medoid) stands for the night; a frame further from it
    than OUTLIER_PX and OUTLIER_FACTOR × the typical distance was framed differently (a failed
    centering, the camera turned) and counts neither in the median nor in the spreads.
    """
    paths = list(poses)
    if len(paths) == 1:
        return poses[paths[0]], []
    ps = [poses[p] for p in paths]
    dist = _corner_shifts(ps, ps, w, h)
    m = int(np.argmin(np.median(dist, axis=1)))
    typical = float(np.median(np.delete(dist[m], m)))
    inside = dist[m] <= max(OUTLIER_PX, OUTLIER_FACTOR * typical)
    kept = [ps[m]] + [p for i, (p, ok) in enumerate(zip(ps, inside)) if ok and i != m]
    return _median_pose(kept), [p for p, ok in zip(paths, inside) if not ok]


# ---------------------------------------------------------------------------- nights

@dataclass
class Night:
    """One night's (one panel's) framing, in its anchor's pixels."""

    anchor: str
    w: int
    h: int
    poses: dict[str, Pose]       # every registered frame, the anchor included
    median: Pose                 # the night's framing (frames framed differently left out)
    spread: float                # how far the frames sit from it (px, largest corner shift): dither and drift
    dither: float                # the same for their shifts alone (px): dithering only moves the frame
    failed: list[str]            # frames whose stars did not match the anchor's
    outliers: list[str]          # frames framed differently from the rest of the night

    @property
    def half_diagonal(self) -> float:
        return math.hypot(self.w, self.h) / 2

    def offsets(self, path: str) -> tuple[float, float, float]:
        """A frame against the night's framing → (largest corner shift px, shift of its centre px, rotation °)."""
        q = self.poses[path]
        return corner_shift(self.median, q, self.w, self.h), centre_shift(self.median, q), fold(q.rot - self.median.rot)

    @property
    def rotation_span(self) -> float:
        """How much the rotation changed over the night (°; frames framed differently left out): never dither,
        so drift (a steady turn: field rotation from the polar alignment) or the framing redone (a step: a meridian
        flip, a re-centring)."""
        rots = [fold(q.rot - self.median.rot) for p, q in self.poses.items() if p not in self.outliers]
        return max(rots) - min(rots) if rots else 0.0


def night(stars: dict[str, Stars], order: list[str] | None = None) -> Night | None:
    """The framing of one night from its frames' stars (`order`: the frames in time order).

    The anchor is the frame with the most stars, ties broken towards the middle of the night;
    when it matches fewer than half the frames (a mispointed frame, a cloud), the next best
    candidates are tried and the one matching the most frames is kept.
    """
    paths = [p for p in (order or sorted(stars)) if p in stars and len(stars[p].xy) >= MIN_MATCHES]
    if not paths:
        return None
    mid = (len(paths) - 1) / 2
    ranked = sorted(range(len(paths)), key=lambda i: (-len(stars[paths[i]].xy), abs(i - mid)))
    best: Night | None = None
    for i in ranked[:3]:
        anchor = paths[i]
        a = stars[anchor]
        poses: dict[str, Pose] = {anchor: Pose((a.w - 1) / 2, (a.h - 1) / 2, 0.0)}
        failed = []
        for p in paths:
            if p == anchor:
                continue
            fit = register(a, stars[p])
            if fit is None:
                failed.append(p)
            else:
                poses[p] = pose_in_ref(fit, stars[p])
        med, outliers = _consensus(poses, a.w, a.h)
        kept = [q for p, q in poses.items() if p not in outliers]
        spread = float(np.percentile(_corner_shifts([med], kept, a.w, a.h)[0], SPREAD_PERCENTILE)) if len(kept) > 1 else 0.0
        dither = float(np.percentile([centre_shift(med, q) for q in kept], SPREAD_PERCENTILE)) if len(kept) > 1 else 0.0
        candidate = Night(anchor, a.w, a.h, poses, med, spread, dither, failed, outliers)
        if best is None or len(candidate.poses) > len(best.poses):
            best = candidate
        if len(best.poses) * 2 > len(paths):
            break
    return best


def level(distance: float, tolerance: float) -> str:
    if distance <= tolerance:
        return "ok"
    return "warn" if distance <= 2 * tolerance else "bad"


def compare(prev: Night, cur: Night, prev_anchor: Stars, cur_anchor: Stars) -> dict[str, Any] | None:
    """This night against the previous one, median to median; None when their anchors do not match.

    Also gives every frame of this night against the previous night's median ("byFrame", by path).
    """
    fit = register(prev_anchor, cur_anchor)
    if fit is None:
        return None
    here = to_ref(cur.median, fit)
    tol = max(prev.spread, cur.spread, MIN_TOLERANCE_PX)
    # A frame dithers around its own night's median, which may itself sit up to `tol` away.
    frame_tol = tol + max(cur.spread, MIN_TOLERANCE_PX)
    dist = corner_shift(prev.median, here, prev.w, prev.h)
    frames = {}
    for path, pose in cur.poses.items():
        d = corner_shift(prev.median, to_ref(pose, fit), prev.w, prev.h)
        frames[path] = {"cornerPx": round(d, 1), "level": level(d, frame_tol), "outlier": path in cur.outliers}
    return {
        "level": level(dist, tol),
        "cornerPx": round(dist, 1),
        "shiftPx": round(centre_shift(prev.median, here), 1),
        "rotation": round(fold(here.rot - prev.median.rot), 3),
        "tolerancePx": round(tol, 1),
        "frameTolerancePx": round(frame_tol, 1),
        "spreadPx": round(cur.spread, 1),
        "previousSpreadPx": round(prev.spread, 1),
        "matches": fit.matches,
        "byFrame": frames,
    }


def compare_frame(prev: Night, prev_anchor: Stars, frame: Stars) -> dict[str, Any] | None:
    """One frame against the previous night's median (Live); None when its stars do not match.

    Tonight's own dither is not known from one frame: it is taken to be the previous night's.
    """
    fit = register(prev_anchor, frame)
    if fit is None:
        return None
    here = pose_in_ref(fit, frame)
    tol = 2 * max(prev.spread, MIN_TOLERANCE_PX)
    dist = corner_shift(prev.median, here, prev.w, prev.h)
    return {
        "level": level(dist, tol),
        "cornerPx": round(dist, 1),
        "shiftPx": round(centre_shift(prev.median, here), 1),
        "rotation": round(fold(here.rot - prev.median.rot), 3),
        "tolerancePx": round(tol, 1),
        "previousSpreadPx": round(prev.spread, 1),
        "matches": fit.matches,
    }
