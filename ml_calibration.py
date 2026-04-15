"""ThrowVision – ML-Based Dartboard Calibration.

Uses a YOLO11 detection model (trained on dartboard ring landmarks) to
automatically calibrate the camera by detecting:
  - '20' and '3' number positions → rotation alignment
  - 'bull' → board centre
  - 'cal' / 'cal1' / 'cal2' / 'cal3' → ring boundary correspondences

Class → geometry:
    'bull' → centre (0, 0)
    'cal'  → outer double ring (170.0 mm)
    'cal1' → inner double ring (162.0 mm)
    'cal2' → outer triple ring (107.0 mm)
    'cal3' → inner triple ring ( 99.0 mm)
    '20'   → sector 20 marker (~185 mm, centred at board angle 90°)
    '3'    → sector 3 marker  (~185 mm, centred at board angle 270°)

Pipeline:
    1. Run YOLO inference.
    2. Estimate board centre (bull, refined by radial-line intersection).
    3. Convert all detection angles to Y-up math convention (matches the
       board-mm coordinate system used downstream).
    4. Determine rotation offset from the '20' and/or '3' markers.
    5. Snap each ring detection to its nearest sector and build
       (camera-px → board-mm) correspondences.
    6. Solve an initial homography with RANSAC.
    7. Iteratively refine: project expected sector×radius positions back
       into camera space using H_inv and greedily re-match detections to
       the nearest expected point.  Re-solve.
    8. Reject outliers, gate on reprojection error, synthesise the 8
       anchor points the BoardCalibrator commit pipeline expects.

Important coordinate-system notes:
    * Camera pixels are Y-down (cy grows downward).
    * Board mm is Y-up math coords (board 90° = top of canvas).
    * We compute angles with dy = -(cy - center_y) so angles are directly
      in Y-up math convention throughout this module.  The homography
      handles the mm→pixel flip downstream in _compute_homography_and_error.
"""

import base64
import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# ── Known ring radii (mm) ───────────────────────────────────────────────────
BULL_INNER_R = 6.35
BULL_OUTER_R = 15.9
TRIPLE_INNER = 99.0
TRIPLE_OUTER = 107.0
DOUBLE_INNER = 162.0
DOUBLE_OUTER = 170.0

# Approximate radial position of the number digits (between double ring
# and the outer edge of the playing surface).  Used for '20'/'3' markers.
NUMBER_R = 185.0

# Class → ring radius used for correspondence building
_CLASS_RADIUS: Dict[str, float] = {
    'cal':  DOUBLE_OUTER,
    'cal1': DOUBLE_INNER,
    'cal2': TRIPLE_OUTER,
    'cal3': TRIPLE_INNER,
}

# Per-class tolerance for re-assigning a detection's ring after homography
# refinement (as a fraction of ring radius).
_RING_SNAP_FRAC = 0.12

# Counter-clockwise order starting from 20 at top (math 90°).
# SECTOR_ORDER[k] is the sector value at math angle 90° + k*18°.
SECTOR_ORDER = [20, 5, 12, 9, 14, 11, 8, 16, 7, 19,
                3, 17, 2, 15, 10, 6, 13, 4, 18, 1]
SECTOR_ANGLE = 18.0  # degrees per sector
N_SECTORS = 20

# Maximum allowed reprojection error, as a fraction of board_size.
# ~2.5% ≈ 18 px at 720p.  Beyond this the homography is unusable.
_MAX_ERROR_FRAC = 0.028

# Rotation search hardening.  Marker detections are useful hints, but they
# should never be trusted as the only search path.
_MARKER_ROTATION_WINDOW_DEG = 27.0
_MARKER_ROTATION_STEP_DEG = 3.0
_EXHAUSTIVE_ROTATION_STEP_DEG = 3.0
_MARKER_MATCH_TOL_DEG = 24.0
_MAX_MARKER_VOTES_PER_CLASS = 3

# Refinement accepts slightly larger projected offsets so a near-miss initial
# homography can still recover.
_REFINE_MATCH_DIST_FRAC = 0.10
_ROTATION_LOG_LIMIT = 10


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _sector_angle_deg(sector_val: int) -> float:
    """Return the centre angle of a sector in board math coords.

    Board math coords: 0° = +X (right), CCW positive, 90° = top.
    """
    try:
        idx = SECTOR_ORDER.index(sector_val)
    except ValueError:
        return 0.0
    return (90.0 + idx * SECTOR_ANGLE) % 360.0


def _boundary_angle_deg(boundary_idx: int) -> float:
    return ((90.0 - SECTOR_ANGLE / 2.0) + boundary_idx * SECTOR_ANGLE) % 360.0


def _boundary_label(boundary_idx: int) -> str:
    return f"D{SECTOR_ORDER[boundary_idx]}/D{SECTOR_ORDER[(boundary_idx - 1) % N_SECTORS]}"


@lru_cache(maxsize=1)
def _ordered_boundary_rows() -> Tuple[Tuple[float, int, str], ...]:
    """Return ring-boundary rows sorted by absolute board angle."""
    rows = [
        (_boundary_angle_deg(boundary_idx), boundary_idx, _boundary_label(boundary_idx))
        for boundary_idx in range(N_SECTORS)
    ]
    rows.sort(key=lambda row: row[0])
    return tuple(rows)


def _encode_preview(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    if max(h, w) > 960:
        s = 960.0 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _angle_diff(a: float, b: float) -> float:
    """Signed angular difference a - b, wrapped to [-180, 180)."""
    d = (a - b) % 360.0
    if d >= 180.0:
        d -= 360.0
    return d


def _angle_dist(a: float, b: float) -> float:
    return abs(_angle_diff(a, b))


def _math_angle(det, center: Tuple[float, float]) -> float:
    """Return the Y-up math angle of *det* from *center*, in degrees.

    Y-up convention: top of the image → 90°, right → 0°, CCW positive.
    """
    dx = det.cx - center[0]
    dy = -(det.cy - center[1])  # flip to Y-up
    return math.degrees(math.atan2(dy, dx)) % 360.0


def _math_angle_xy(x: float, y: float, cx: float, cy: float) -> float:
    return math.degrees(math.atan2(-(y - cy), x - cx)) % 360.0


def _fmt_xy(x: float, y: float) -> str:
    return f"({x:.0f},{y:.0f})"


def _sort_detections_by_angle(det_list: List,
                              center: Tuple[float, float],
                              limit: Optional[int] = None) -> List[Dict[str, Any]]:
    ranked = sorted(det_list, key=lambda d: d.confidence, reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    rows: List[Dict[str, Any]] = []
    for det in ranked:
        rows.append({
            "det": det,
            "img_angle": _math_angle(det, center),
            "confidence": float(det.confidence),
        })
    rows.sort(key=lambda row: row["img_angle"])
    return rows


def _log_detection_inventory(by_class: Dict[str, list],
                             center: Tuple[float, float]) -> None:
    """Print the detections the solver is actually working with."""
    for cls_name in sorted(by_class):
        ranked = sorted(by_class[cls_name], key=lambda d: d.confidence, reverse=True)
        for idx, det in enumerate(ranked, start=1):
            img_ang = _math_angle(det, center)
            print(
                f"[ML-CAL] det {cls_name}#{idx}: conf={det.confidence:.2f} "
                f"src={_fmt_xy(det.cx, det.cy)} size=({det.width:.0f}x{det.height:.0f}) "
                f"img={img_ang:.1f}°"
            )


def _marker_fit_from_homography(
    H_mm: Optional[np.ndarray],
    num20_dets: List,
    num3_dets: List,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    if H_mm is None:
        return {
            "rows": rows,
            "accepted_markers": 0,
            "mean_marker_error_deg": None,
            "max_marker_error_deg": None,
            "mean_marker_radius_error_mm": None,
            "max_marker_radius_error_mm": None,
        }

    for sect_int, det_list in ((20, num20_dets), (3, num3_dets)):
        if not det_list:
            continue
        det = max(det_list, key=lambda d: d.confidence)
        pt = np.array([[[det.cx, det.cy]]], dtype=np.float32)
        pt_mm = cv2.perspectiveTransform(pt, H_mm).reshape(-1, 2)[0]
        board_ang = math.degrees(math.atan2(pt_mm[1], pt_mm[0])) % 360.0
        radius_mm = math.hypot(float(pt_mm[0]), float(pt_mm[1]))
        rows.append({
            "marker": sect_int,
            "det": det,
            "board_angle": board_ang,
            "board_radius_mm": radius_mm,
            "angle_error_deg": _angle_dist(board_ang, _sector_angle_deg(sect_int)),
            "radius_error_mm": abs(radius_mm - NUMBER_R),
        })

    return {
        "rows": rows,
        "accepted_markers": len(rows),
        "mean_marker_error_deg": (
            float(np.mean([row["angle_error_deg"] for row in rows])) if rows else None
        ),
        "max_marker_error_deg": (
            float(np.max([row["angle_error_deg"] for row in rows])) if rows else None
        ),
        "mean_marker_radius_error_mm": (
            float(np.mean([row["radius_error_mm"] for row in rows])) if rows else None
        ),
        "max_marker_radius_error_mm": (
            float(np.max([row["radius_error_mm"] for row in rows])) if rows else None
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Centre estimation via radial-line intersection (SVD)
# ═══════════════════════════════════════════════════════════════════════════

def _fit_line_direction(points: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    if len(points) < 2:
        return None
    pts = np.asarray(points, dtype=np.float64)
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    direction = vt[0]
    nrm = np.linalg.norm(direction)
    if nrm < 1e-10:
        return None
    return centroid, direction / nrm


def _intersect_two_lines(
    p1: np.ndarray, d1: np.ndarray,
    p2: np.ndarray, d2: np.ndarray,
) -> Optional[np.ndarray]:
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-10:
        return None
    delta = p2 - p1
    t = (delta[0] * d2[1] - delta[1] * d2[0]) / denom
    inter = p1 + t * d1
    if not np.all(np.isfinite(inter)):
        return None
    return inter


def _estimate_center_radial_lines(
    ring_dets: List,
    rough_center: Tuple[float, float],
    angle_tolerance_deg: float = 12.0,
) -> Tuple[float, float]:
    """Refine centre by intersecting radial lines through sector clusters.

    Groups detections by their (camera-pixel) angle from a rough centre,
    fits a line through each radial group, and returns the median pair-wise
    intersection.  Works in Y-up angles for consistency with the rest of
    the module.
    """
    if len(ring_dets) < 6:
        return rough_center

    angle_pts: List[Tuple[float, float, float]] = []
    for det in ring_dets:
        ang = _math_angle(det, rough_center)
        angle_pts.append((ang, det.cx, det.cy))
    angle_pts.sort(key=lambda t: t[0])

    used = set()
    groups: List[List[Tuple[float, float]]] = []
    for i, (ref_ang, ref_x, ref_y) in enumerate(angle_pts):
        if i in used:
            continue
        group = [(ref_x, ref_y)]
        used.add(i)
        for j, (ang, x, y) in enumerate(angle_pts):
            if j in used:
                continue
            if _angle_dist(ang, ref_ang) < angle_tolerance_deg:
                group.append((x, y))
                used.add(j)
        if len(group) >= 2:
            groups.append(group)

    if len(groups) < 2:
        return rough_center

    lines = []
    for g in groups:
        line = _fit_line_direction(np.array(g))
        if line is not None:
            lines.append(line)

    if len(lines) < 2:
        return rough_center

    inters = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            pt = _intersect_two_lines(lines[i][0], lines[i][1],
                                      lines[j][0], lines[j][1])
            if pt is not None:
                inters.append(pt)

    if not inters:
        return rough_center

    arr = np.asarray(inters, dtype=np.float64)
    cx = float(np.median(arr[:, 0]))
    cy = float(np.median(arr[:, 1]))

    # Sanity: refined centre must stay close to rough.
    dist = math.hypot(cx - rough_center[0], cy - rough_center[1])
    all_cx = np.array([d.cx for d in ring_dets])
    all_cy = np.array([d.cy for d in ring_dets])
    spread = max(float(np.ptp(all_cx)), float(np.ptp(all_cy)), 80.0)
    if dist > spread * 0.25:
        print(f"[ML-CAL] Radial centre drift too large ({dist:.1f}px); "
              f"keeping rough centre")
        return rough_center

    print(f"[ML-CAL] Refined centre ({cx:.0f}, {cy:.0f}) "
          f"from {len(lines)} lines, {len(inters)} intersections "
          f"(drift={dist:.1f}px)")
    return (cx, cy)


# ═══════════════════════════════════════════════════════════════════════════
# Rotation estimation from '20' / '3' markers
# ═══════════════════════════════════════════════════════════════════════════

def _marker_rotation_votes(
    num20_dets: List,
    num3_dets: List,
    center: Tuple[float, float],
) -> List[Dict[str, Any]]:
    """Return per-marker rotation votes from every plausible number marker."""
    votes: List[Dict[str, Any]] = []
    for marker_dets, expected_val in [(num20_dets, 20), (num3_dets, 3)]:
        ranked = sorted(marker_dets, key=lambda d: d.confidence, reverse=True)
        if len(ranked) > _MAX_MARKER_VOTES_PER_CLASS:
            print(
                f"[ML-CAL] marker '{expected_val}': "
                f"using top {_MAX_MARKER_VOTES_PER_CLASS}/{len(ranked)} detections"
            )
        for rank, det in enumerate(ranked[:_MAX_MARKER_VOTES_PER_CLASS], start=1):
            img_ang = _math_angle(det, center)
            expected_ang = _sector_angle_deg(expected_val)  # 90° for 20, 270° for 3
            rotation = (img_ang - expected_ang) % 360.0
            vote = {
                "marker": expected_val,
                "rank": rank,
                "confidence": float(det.confidence),
                "img_angle": img_ang,
                "expected_angle": expected_ang,
                "rotation": rotation,
                "det": det,
            }
            votes.append(vote)
            print(
                f"[ML-CAL] marker vote {expected_val}#{rank}: "
                f"conf={det.confidence:.2f} src={_fmt_xy(det.cx, det.cy)} "
                f"img={img_ang:.1f}° expected={expected_ang:.1f}° → r={rotation:.1f}°"
            )
    return votes


def _determine_rotation(marker_votes: List[Dict[str, Any]]) -> Optional[float]:
    """Return the weighted circular mean of the available marker votes.

        board_math_angle = (image_math_angle - r) mod 360.

    For a perfectly aligned camera (sector 20 at top), r = 0 because
    sector 20 is at math angle 90° and the camera '20' marker is also at
    math angle 90°.  A tilted/rotated board produces a non-zero r.
    """
    if not marker_votes:
        return None

    sin_sum = 0.0
    cos_sum = 0.0
    weight_sum = 0.0
    for vote in marker_votes:
        weight = max(0.05, float(vote["confidence"]))
        ang = math.radians(float(vote["rotation"]))
        sin_sum += math.sin(ang) * weight
        cos_sum += math.cos(ang) * weight
        weight_sum += weight

    if weight_sum <= 1e-9:
        return None

    r = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    consensus = math.hypot(sin_sum, cos_sum) / weight_sum
    classes = sorted({int(v["marker"]) for v in marker_votes})
    print(
        f"[ML-CAL] marker consensus: rot={r:.1f}° "
        f"votes={len(marker_votes)} classes={classes} agreement={consensus:.2f}"
    )
    if len(marker_votes) == 1 or len(classes) == 1:
        print("[ML-CAL] marker consensus is single-sided; exhaustive sweep stays enabled")
    return r


def _rotation_offsets(window_deg: float, step_deg: float) -> List[float]:
    offsets = [0.0]
    n_steps = int(math.ceil(window_deg / step_deg))
    for i in range(1, n_steps + 1):
        off = min(i * step_deg, window_deg)
        offsets.extend([+off, -off])
    return offsets


def _build_rotation_candidates(
    marker_votes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return marker-guided candidates plus an always-on exhaustive sweep."""
    candidates: Dict[float, Dict[str, Any]] = {}

    def _add(rot: float, source: str) -> None:
        rot = rot % 360.0
        key = round(rot, 4)
        entry = candidates.get(key)
        if entry is None:
            candidates[key] = {"rotation": rot, "sources": [source]}
            return
        if source not in entry["sources"]:
            entry["sources"].append(source)

    if marker_votes:
        estimate = _determine_rotation(marker_votes)
        if estimate is not None:
            for off in _rotation_offsets(
                _MARKER_ROTATION_WINDOW_DEG, _MARKER_ROTATION_STEP_DEG
            ):
                label = "consensus" if abs(off) < 1e-9 else f"consensus{off:+.1f}"
                _add(estimate + off, label)

        for vote in sorted(marker_votes, key=lambda row: row["confidence"], reverse=True):
            prefix = f"marker{int(vote['marker'])}#{int(vote['rank'])}"
            for off in _rotation_offsets(
                _MARKER_ROTATION_WINDOW_DEG, _MARKER_ROTATION_STEP_DEG
            ):
                label = prefix if abs(off) < 1e-9 else f"{prefix}{off:+.1f}"
                _add(float(vote["rotation"]) + off, label)

    marker_candidate_count = len(candidates)
    exhaustive_count = int(round(360.0 / _EXHAUSTIVE_ROTATION_STEP_DEG))
    for i in range(exhaustive_count):
        _add(i * _EXHAUSTIVE_ROTATION_STEP_DEG, "exhaustive")

    ordered = list(candidates.values())
    print(
        f"[ML-CAL] rotation search: {len(ordered)} candidates "
        f"(marker-guided={marker_candidate_count}, exhaustive={exhaustive_count})"
    )
    return ordered


def _select_marker_detection(
    det_list: List,
    expected_val: int,
    center: Tuple[float, float],
    rotation: float,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pick the marker detection that best fits the current rotation guess."""
    expected_ang = _sector_angle_deg(expected_val)
    evaluations: List[Dict[str, Any]] = []
    for rank, det in enumerate(
        sorted(det_list, key=lambda d: d.confidence, reverse=True),
        start=1,
    ):
        img_ang = _math_angle(det, center)
        board_ang = _board_angle(img_ang, rotation)
        board_err = _angle_dist(board_ang, expected_ang)
        evaluations.append({
            "det": det,
            "rank": rank,
            "marker": expected_val,
            "confidence": float(det.confidence),
            "img_angle": img_ang,
            "board_angle": board_ang,
            "board_error_deg": board_err,
        })

    if not evaluations:
        return None, evaluations

    selected = min(
        evaluations,
        key=lambda row: (row["board_error_deg"], -row["confidence"]),
    )
    if selected["board_error_deg"] > _MARKER_MATCH_TOL_DEG:
        return None, evaluations
    return selected, evaluations


# ═══════════════════════════════════════════════════════════════════════════
# Correspondence building (snap-based, using Y-up math throughout)
# ═══════════════════════════════════════════════════════════════════════════

def _board_angle(img_math_angle: float, rotation: float) -> float:
    return (img_math_angle - rotation) % 360.0


def _nearest_sector_boundary(board_angle: float) -> Tuple[float, int, float]:
    """Snap *board_angle* (Y-up degrees) to the nearest sector boundary."""
    start = 90.0 - SECTOR_ANGLE / 2.0
    shifted = (board_angle - start) % 360.0
    k = int(round(shifted / SECTOR_ANGLE)) % N_SECTORS
    snapped = (start + k * SECTOR_ANGLE) % 360.0
    return snapped, k, _angle_dist(board_angle, snapped)


def _build_correspondences_snap(
    by_class: Dict[str, list],
    center: Tuple[float, float],
    rotation: float,
    num20_dets: List,
    num3_dets: List,
    angle_tol_deg: float = 11.0,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Match each ring detection to its nearest sector boundary.

    Correspondences are returned as (src_camera_px, dst_board_mm) where
    board-mm uses Y-up math convention.
    """
    src_pts: List[List[float]] = []
    dst_pts: List[List[float]] = []
    debug: Dict[str, Any] = {
        "rotation": float(rotation),
        "accepted_rings": [],
        "rejected_rings": [],
        "marker_evals": [],
    }
    snap_errors: List[float] = []

    # Bull centre anchor
    src_pts.append([center[0], center[1]])
    dst_pts.append([0.0, 0.0])

    for cls_name, radius_mm in _CLASS_RADIUS.items():
        for det in sorted(by_class.get(cls_name, []), key=lambda d: d.confidence, reverse=True):
            img_ang = _math_angle(det, center)
            b_ang = _board_angle(img_ang, rotation)
            snapped, k_idx, err = _nearest_sector_boundary(b_ang)
            row = {
                "class_name": cls_name,
                "confidence": float(det.confidence),
                "src": (float(det.cx), float(det.cy)),
                "img_angle": img_ang,
                "board_angle": b_ang,
                "snapped_angle": snapped,
                "snap_error_deg": err,
                "radius_mm": float(radius_mm),
                "boundary_label": _boundary_label(k_idx),
            }
            if err > angle_tol_deg:
                row["reason"] = f"snap>{angle_tol_deg:.1f}°"
                debug["rejected_rings"].append(row)
                continue
            rad = math.radians(snapped)
            src_pts.append([det.cx, det.cy])
            dst_pts.append([radius_mm * math.cos(rad),
                            radius_mm * math.sin(rad)])
            snap_errors.append(err)
            debug["accepted_rings"].append(row)

    # '20' / '3' markers as additional correspondences near the number ring.
    for sect_int, det_list in [(20, num20_dets), (3, num3_dets)]:
        selected, evaluations = _select_marker_detection(
            det_list, sect_int, center, rotation)
        for row in evaluations:
            row["selected"] = selected is not None and row["det"] is selected["det"]
            debug["marker_evals"].append(row)
        if selected is None:
            continue
        best = selected["det"]
        rad = math.radians(_sector_angle_deg(sect_int))
        src_pts.append([best.cx, best.cy])
        dst_pts.append([NUMBER_R * math.cos(rad),
                        NUMBER_R * math.sin(rad)])

    debug["summary"] = {
        "ring_total": sum(len(by_class.get(cls_name, [])) for cls_name in _CLASS_RADIUS),
        "accepted_rings": len(debug["accepted_rings"]),
        "rejected_rings": len(debug["rejected_rings"]),
        "accepted_markers": sum(1 for row in debug["marker_evals"] if row["selected"]),
        "marker_candidates": len(debug["marker_evals"]),
        "mean_marker_error_deg": float(np.mean([
            row["board_error_deg"] for row in debug["marker_evals"]
            if row["selected"]
        ])) if any(row["selected"] for row in debug["marker_evals"]) else None,
        "max_marker_error_deg": float(np.max([
            row["board_error_deg"] for row in debug["marker_evals"]
            if row["selected"]
        ])) if any(row["selected"] for row in debug["marker_evals"]) else None,
        "mean_snap_error_deg": float(np.mean(snap_errors)) if snap_errors else None,
        "max_snap_error_deg": float(max(snap_errors)) if snap_errors else None,
        "corr": len(src_pts),
    }

    return (
        np.asarray(src_pts, dtype=np.float32),
        np.asarray(dst_pts, dtype=np.float32),
        debug,
    )


def _sequence_direction_name(direction: int) -> str:
    return "angle+" if direction >= 0 else "angle-"


def _build_correspondences_ordered(
    by_class: Dict[str, list],
    center: Tuple[float, float],
    num20_dets: List,
    num3_dets: List,
    shift: int,
    direction: int,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Assign ring detections by cyclic order instead of uniform image angles.

    A planar homography preserves the cyclic order of rays through the board
    centre, but not the angular spacing between those rays.  For near-complete
    20-point ring detections, ordering is a much more stable cue than trying
    to subtract a single rotation from image angles.
    """
    src_pts: List[List[float]] = [[center[0], center[1]]]
    dst_pts: List[List[float]] = [[0.0, 0.0]]
    debug: Dict[str, Any] = {
        "mode": "ordered",
        "shift": int(shift),
        "direction": int(direction),
        "direction_name": _sequence_direction_name(direction),
        "accepted_rings": [],
        "rejected_rings": [],
        "marker_evals": [],
    }
    boundary_rows = _ordered_boundary_rows()

    for cls_name, radius_mm in _CLASS_RADIUS.items():
        det_rows = _sort_detections_by_angle(
            by_class.get(cls_name, []), center, limit=N_SECTORS)
        for seq_idx, det_row in enumerate(det_rows):
            det = det_row["det"]
            boundary_idx = (shift + (seq_idx if direction >= 0 else -seq_idx)) % N_SECTORS
            assigned_angle, _boundary_slot, boundary_label = boundary_rows[boundary_idx]
            rad = math.radians(assigned_angle)
            src_pts.append([det.cx, det.cy])
            dst_pts.append([radius_mm * math.cos(rad),
                            radius_mm * math.sin(rad)])
            debug["accepted_rings"].append({
                "class_name": cls_name,
                "confidence": det_row["confidence"],
                "src": (float(det.cx), float(det.cy)),
                "img_angle": float(det_row["img_angle"]),
                "assigned_angle": float(assigned_angle),
                "boundary_label": boundary_label,
                "radius_mm": float(radius_mm),
                "seq_idx": int(seq_idx),
            })

    for sect_int, det_list in ((20, num20_dets), (3, num3_dets)):
        if not det_list:
            continue
        best = max(det_list, key=lambda d: d.confidence)
        rad = math.radians(_sector_angle_deg(sect_int))
        src_pts.append([best.cx, best.cy])
        dst_pts.append([NUMBER_R * math.cos(rad),
                        NUMBER_R * math.sin(rad)])
        debug["marker_evals"].append({
            "marker": sect_int,
            "det": best,
            "selected": True,
            "confidence": float(best.confidence),
            "img_angle": _math_angle(best, center),
        })

    debug["summary"] = {
        "ring_total": sum(min(len(by_class.get(cls_name, [])), N_SECTORS)
                          for cls_name in _CLASS_RADIUS),
        "accepted_rings": len(debug["accepted_rings"]),
        "rejected_rings": 0,
        "accepted_markers": len(debug["marker_evals"]),
        "marker_candidates": len(debug["marker_evals"]),
        "mean_marker_error_deg": None,
        "max_marker_error_deg": None,
        "mean_marker_radius_error_mm": None,
        "max_marker_radius_error_mm": None,
        "mean_snap_error_deg": None,
        "max_snap_error_deg": None,
        "corr": len(src_pts),
    }

    return (
        np.asarray(src_pts, dtype=np.float32),
        np.asarray(dst_pts, dtype=np.float32),
        debug,
    )


def _log_order_debug(debug: Dict[str, Any]) -> None:
    summary = debug.get("summary", {})
    marker_mean = summary.get("mean_marker_error_deg")
    marker_max = summary.get("max_marker_error_deg")
    marker_r_mean = summary.get("mean_marker_radius_error_mm")
    marker_mean_txt = f"{marker_mean:.1f}°" if marker_mean is not None else "n/a"
    marker_max_txt = f"{marker_max:.1f}°" if marker_max is not None else "n/a"
    marker_r_txt = f"{marker_r_mean:.1f}mm" if marker_r_mean is not None else "n/a"
    print(
        f"[ML-CAL] ordered matches shift={debug.get('shift')} "
        f"dir={debug.get('direction_name')}: "
        f"rings={summary.get('accepted_rings', 0)}/{summary.get('ring_total', 0)} "
        f"markers={summary.get('accepted_markers', 0)} "
        f"corr={summary.get('corr', 0)} marker_mean={marker_mean_txt} "
        f"marker_max={marker_max_txt} marker_r_mean={marker_r_txt}"
    )
    for row in debug.get("accepted_rings", []):
        print(
            f"[ML-CAL] ordered ring {row['class_name']} conf={row['confidence']:.2f} "
            f"src={_fmt_xy(*row['src'])} img={row['img_angle']:.1f}° "
            f"seq={row['seq_idx']} -> {row['boundary_label']} "
            f"ang={row['assigned_angle']:.1f}° r={row['radius_mm']:.1f}"
        )
    for row in debug.get("marker_fit", {}).get("rows", []):
        det = row["det"]
        print(
            f"[ML-CAL] ordered marker {row['marker']} conf={det.confidence:.2f} "
            f"src={_fmt_xy(det.cx, det.cy)} board_ang={row['board_angle']:.1f}° "
            f"ang_err={row['angle_error_deg']:.1f}° "
            f"board_r={row['board_radius_mm']:.1f}mm r_err={row['radius_error_mm']:.1f}mm"
        )


def _log_rotation_trials(trials: List[Dict[str, Any]]) -> None:
    if not trials:
        return
    ranked = sorted(
        trials,
        key=lambda row: (
            float('inf') if row["err"] is None else row["err"],
            -row["corr"],
        ),
    )
    print(f"[ML-CAL] rotation trials checked: {len(trials)}")
    for row in ranked[:_ROTATION_LOG_LIMIT]:
        err_txt = f"{row['err']:.1f}px" if row["err"] is not None else "no-H"
        mean_snap = row["mean_snap_error_deg"]
        snap_txt = f"{mean_snap:.1f}°" if mean_snap is not None else "n/a"
        mean_marker = row["mean_marker_error_deg"]
        marker_txt = f"{mean_marker:.1f}°" if mean_marker is not None else "n/a"
        src_txt = ",".join(row["sources"][:3])
        if len(row["sources"]) > 3:
            src_txt += ",..."
        print(
            f"[ML-CAL] trial rot={row['rotation']:.1f}° err={err_txt} "
            f"corr={row['corr']} rings={row['accepted_rings']}/{row['ring_total']} "
            f"markers={row['accepted_markers']} marker_mean={marker_txt} "
            f"snap_mean={snap_txt} src={src_txt}"
        )


def _log_sequence_trials(trials: List[Dict[str, Any]]) -> None:
    if not trials:
        return
    ranked = sorted(
        trials,
        key=lambda row: (
            float('inf') if row["err"] is None else row["err"],
            row["mean_marker_error_deg"] if row["mean_marker_error_deg"] is not None else float('inf'),
        ),
    )
    print(f"[ML-CAL] sequence trials checked: {len(trials)}")
    for row in ranked[:_ROTATION_LOG_LIMIT]:
        err_txt = f"{row['err']:.1f}px" if row["err"] is not None else "no-H"
        marker_mean = row["mean_marker_error_deg"]
        marker_r = row["mean_marker_radius_error_mm"]
        marker_txt = f"{marker_mean:.1f}°" if marker_mean is not None else "n/a"
        marker_r_txt = f"{marker_r:.1f}mm" if marker_r is not None else "n/a"
        print(
            f"[ML-CAL] trial shift={row['shift']:02d} dir={row['direction_name']} "
            f"err={err_txt} corr={row['corr']} "
            f"rings={row['accepted_rings']}/{row['ring_total']} "
            f"markers={row['accepted_markers']} marker_mean={marker_txt} "
            f"marker_r_mean={marker_r_txt}"
        )


def _log_snap_debug(debug: Dict[str, Any]) -> None:
    summary = debug.get("summary", {})
    mean_snap = summary.get("mean_snap_error_deg")
    max_snap = summary.get("max_snap_error_deg")
    mean_marker = summary.get("mean_marker_error_deg")
    max_marker = summary.get("max_marker_error_deg")
    mean_txt = f"{mean_snap:.1f}°" if mean_snap is not None else "n/a"
    max_txt = f"{max_snap:.1f}°" if max_snap is not None else "n/a"
    marker_mean_txt = f"{mean_marker:.1f}°" if mean_marker is not None else "n/a"
    marker_max_txt = f"{max_marker:.1f}°" if max_marker is not None else "n/a"
    print(
        f"[ML-CAL] snap matches @ rot={debug.get('rotation', 0.0):.1f}°: "
        f"rings={summary.get('accepted_rings', 0)}/{summary.get('ring_total', 0)} "
        f"markers={summary.get('accepted_markers', 0)} "
        f"corr={summary.get('corr', 0)} marker_mean={marker_mean_txt} "
        f"marker_max={marker_max_txt} snap_mean={mean_txt} snap_max={max_txt}"
    )

    for row in debug.get("accepted_rings", []):
        print(
            f"[ML-CAL] snap ring {row['class_name']} conf={row['confidence']:.2f} "
            f"src={_fmt_xy(*row['src'])} img={row['img_angle']:.1f}° "
            f"board={row['board_angle']:.1f}° -> {row['boundary_label']} "
            f"snap={row['snapped_angle']:.1f}° err={row['snap_error_deg']:.1f}° "
            f"r={row['radius_mm']:.1f}"
        )

    rejected = debug.get("rejected_rings", [])
    if rejected:
        print(f"[ML-CAL] snap rejected {len(rejected)} ring detections")
        for row in rejected[:6]:
            print(
                f"[ML-CAL] snap reject {row['class_name']} conf={row['confidence']:.2f} "
                f"src={_fmt_xy(*row['src'])} board={row['board_angle']:.1f}° "
                f"err={row['snap_error_deg']:.1f}° ({row['reason']})"
            )
        if len(rejected) > 6:
            print(f"[ML-CAL] snap reject: ... {len(rejected) - 6} more")

    for row in debug.get("marker_evals", []):
        state = "use" if row.get("selected") else "skip"
        det = row["det"]
        print(
            f"[ML-CAL] snap marker {state} {row['marker']}#{row['rank']} "
            f"conf={row['confidence']:.2f} src={_fmt_xy(det.cx, det.cy)} "
            f"img={row['img_angle']:.1f}° board={row['board_angle']:.1f}° "
            f"err={row['board_error_deg']:.1f}°"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Homography + error
# ═══════════════════════════════════════════════════════════════════════════

def _canvas_scale_px_per_mm(calibrator) -> float:
    return float(getattr(calibrator, "_scale", calibrator.board_size / calibrator.CANVAS_MM))


def _mm_to_canvas_px(dst_mm: np.ndarray, calibrator) -> np.ndarray:
    """Convert board-mm (Y-up) to the calibrator's canonical canvas pixels."""
    bs = calibrator.board_size
    half = bs / 2.0
    px_per_mm = _canvas_scale_px_per_mm(calibrator)
    out = np.empty_like(dst_mm)
    out[:, 0] = half + dst_mm[:, 0] * px_per_mm
    out[:, 1] = half - dst_mm[:, 1] * px_per_mm
    return out


def _solve_homographies(
    src_np: np.ndarray,
    dst_mm: np.ndarray,
    calibrator,
    robust: bool = True,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], np.ndarray]:
    """Fit camera→canvas-px and camera→board-mm homographies."""
    dst_px = _mm_to_canvas_px(dst_mm, calibrator)

    n = len(src_np)
    method = cv2.RANSAC if robust and n >= 8 else 0
    reproj = 5.0 if robust and n >= 8 else 0.0
    if n >= 8:
        H_px, _mask = cv2.findHomography(src_np, dst_px, method, reproj)
        H_mm, _mask_mm = cv2.findHomography(src_np, dst_mm, method, reproj)
    elif n >= 4:
        H_px, _mask = cv2.findHomography(src_np, dst_px, 0)
        H_mm, _mask_mm = cv2.findHomography(src_np, dst_mm, 0)
    else:
        return None, None, dst_px

    return H_px, H_mm, dst_px


def _compute_homography_and_error(
    src_np: np.ndarray,
    dst_mm: np.ndarray,
    calibrator,
    robust: bool = True,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float, np.ndarray]:
    """Fit homographies and return reprojection error on the canonical canvas."""
    H_px, H_mm, dst_px = _solve_homographies(src_np, dst_mm, calibrator, robust=robust)
    if H_px is None or H_mm is None:
        return None, None, 9999.0, dst_px

    projected = cv2.perspectiveTransform(
        src_np.reshape(-1, 1, 2), H_px
    ).reshape(-1, 2)
    errors = np.linalg.norm(projected - dst_px, axis=1)
    return H_px, H_mm, float(np.mean(errors)), dst_px


def _prefer_snap_candidate(
    err_px: float,
    debug: Dict[str, Any],
    best_err_px: float,
    best_debug: Optional[Dict[str, Any]],
) -> bool:
    """Tie-break rotation candidates with marker agreement before snap noise."""
    if best_debug is None:
        return True

    tie_px = 1.0
    if err_px < best_err_px - tie_px:
        return True
    if err_px > best_err_px + tie_px:
        return False

    new_summary = debug.get("summary", {})
    best_summary = best_debug.get("summary", {})

    new_markers = int(new_summary.get("accepted_markers", 0))
    best_markers = int(best_summary.get("accepted_markers", 0))
    if new_markers != best_markers:
        return new_markers > best_markers

    new_marker_err = new_summary.get("mean_marker_error_deg")
    best_marker_err = best_summary.get("mean_marker_error_deg")
    if new_markers >= 2 and new_marker_err is not None and best_marker_err is not None:
        if abs(new_marker_err - best_marker_err) > 1.0:
            return new_marker_err < best_marker_err

    new_corr = int(new_summary.get("corr", 0))
    best_corr = int(best_summary.get("corr", 0))
    if new_corr != best_corr:
        return new_corr > best_corr

    new_snap = new_summary.get("mean_snap_error_deg")
    best_snap = best_summary.get("mean_snap_error_deg")
    if new_snap is not None and best_snap is not None and abs(new_snap - best_snap) > 0.25:
        return new_snap < best_snap

    return err_px < best_err_px


# ═══════════════════════════════════════════════════════════════════════════
# Iterative refinement: use the homography to re-assign detections
# ═══════════════════════════════════════════════════════════════════════════

def _iterative_refine(
    H_init: np.ndarray,
    all_ring_dets: List,
    center: Tuple[float, float],
    num20_dets: List,
    num3_dets: List,
    calibrator,
    iters: int = 2,
) -> Tuple[Optional[np.ndarray], float, np.ndarray, np.ndarray]:
    """Refine the homography by projecting expected sector points into the
    camera plane and re-matching each detection to the closest expected.

    Returns (H, mean_error_px, src_np, dst_mm).
    """
    bs = calibrator.board_size
    half = bs / 2.0
    px_per_mm = _canvas_scale_px_per_mm(calibrator)

    best_H = H_init
    best_err = float('inf')
    best_src = None
    best_dst = None
    best_debug = None
    match_tol_px = bs * _REFINE_MATCH_DIST_FRAC

    for iter_idx in range(max(1, iters)):
        # Build the expected (canvas-px) points for every ring boundary.
        expected_rows: List[Tuple[str, float, float, str, float, float]] = []
        for cls_name, r_mm in _CLASS_RADIUS.items():
            for boundary_idx in range(N_SECTORS):
                ang_deg = _boundary_angle_deg(boundary_idx)
                ang = math.radians(ang_deg)
                mx = r_mm * math.cos(ang)
                my = r_mm * math.sin(ang)
                cx_px = half + mx * px_per_mm
                cy_px = half - my * px_per_mm
                expected_rows.append((
                    cls_name,
                    r_mm,
                    ang_deg,
                    _boundary_label(boundary_idx),
                    cx_px,
                    cy_px,
                ))

        # Project expected canvas-px points back into camera space.
        try:
            H_inv = np.linalg.inv(best_H)
        except np.linalg.LinAlgError:
            break
        exp_canvas = np.asarray([(r[4], r[5]) for r in expected_rows],
                                dtype=np.float32).reshape(-1, 1, 2)
        exp_cam = cv2.perspectiveTransform(exp_canvas, H_inv).reshape(-1, 2)

        # Greedy 1-to-1 assignment: each detection → nearest expected point
        # whose class matches (or whose radius is within tolerance).
        src_list: List[List[float]] = [[center[0], center[1]]]
        dst_list: List[List[float]] = [[0.0, 0.0]]
        accepted_rows: List[Dict[str, Any]] = []
        rejected_rows: List[Dict[str, Any]] = []
        marker_rows: List[Dict[str, Any]] = []

        used_expected = set()
        for det in all_ring_dets:
            det_cam = np.array([det.cx, det.cy], dtype=np.float32)
            best_j = -1
            best_d = float('inf')
            for j, row in enumerate(expected_rows):
                if j in used_expected:
                    continue
                cls_name = row[0]
                # Class must match
                if cls_name != det.class_name:
                    continue
                d = float(np.hypot(exp_cam[j, 0] - det_cam[0],
                                   exp_cam[j, 1] - det_cam[1]))
                if d < best_d:
                    best_d = d
                    best_j = j

            if best_j < 0:
                rejected_rows.append({
                    "class_name": det.class_name,
                    "confidence": float(det.confidence),
                    "src": (float(det.cx), float(det.cy)),
                    "reason": "no_expected_slot",
                })
                continue

            row = expected_rows[best_j]
            ring_row = {
                "class_name": det.class_name,
                "confidence": float(det.confidence),
                "src": (float(det.cx), float(det.cy)),
                "dist_px": best_d,
                "radius_mm": float(row[1]),
                "angle_deg": float(row[2]),
                "boundary_label": str(row[3]),
                "expected_src": (float(exp_cam[best_j, 0]), float(exp_cam[best_j, 1])),
            }

            # Accept only if reasonably close (fraction of board_size)
            if best_d > match_tol_px:
                ring_row["reason"] = f"dist>{match_tol_px:.1f}px"
                rejected_rows.append(ring_row)
                continue

            used_expected.add(best_j)
            r_mm = row[1]
            ang_deg = row[2]
            rad = math.radians(ang_deg)
            src_list.append([det.cx, det.cy])
            dst_list.append([r_mm * math.cos(rad),
                             r_mm * math.sin(rad)])
            accepted_rows.append(ring_row)

        # Add '20' / '3' number markers using the projected expected position,
        # not just the highest-confidence marker.
        for sect_int, det_list in [(20, num20_dets), (3, num3_dets)]:
            if not det_list:
                continue
            rad = math.radians(_sector_angle_deg(sect_int))
            marker_canvas = np.array([[
                [
                    half + NUMBER_R * math.cos(rad) * px_per_mm,
                    half - NUMBER_R * math.sin(rad) * px_per_mm,
                ]
            ]], dtype=np.float32)
            exp_marker_cam = cv2.perspectiveTransform(marker_canvas, H_inv).reshape(-1, 2)[0]
            best = None
            best_d = float('inf')
            for rank, det in enumerate(
                sorted(det_list, key=lambda d: d.confidence, reverse=True),
                start=1,
            ):
                dist_px = float(np.hypot(det.cx - exp_marker_cam[0], det.cy - exp_marker_cam[1]))
                marker_row = {
                    "marker": sect_int,
                    "rank": rank,
                    "confidence": float(det.confidence),
                    "src": (float(det.cx), float(det.cy)),
                    "expected_src": (float(exp_marker_cam[0]), float(exp_marker_cam[1])),
                    "dist_px": dist_px,
                    "selected": False,
                }
                marker_rows.append(marker_row)
                if dist_px < best_d:
                    best_d = dist_px
                    best = det
            if best is None or best_d > match_tol_px * 1.2:
                continue
            for marker_row in marker_rows:
                if (marker_row["marker"] == sect_int
                        and abs(marker_row["src"][0] - best.cx) < 1e-6
                        and abs(marker_row["src"][1] - best.cy) < 1e-6):
                    marker_row["selected"] = True
                    break
            rad = math.radians(_sector_angle_deg(sect_int))
            src_list.append([best.cx, best.cy])
            dst_list.append([NUMBER_R * math.cos(rad),
                             NUMBER_R * math.sin(rad)])

        src_np = np.asarray(src_list, dtype=np.float32)
        dst_np = np.asarray(dst_list, dtype=np.float32)
        if len(src_np) < 4:
            break

        H_new, _H_mm_new, err, _dst_px = _compute_homography_and_error(
            src_np, dst_np, calibrator)
        if H_new is None:
            print(
                f"[ML-CAL] refine iter {iter_idx + 1}: "
                f"homography failed (corr={len(src_np)})"
            )
            break

        mean_dist = (
            float(np.mean([row["dist_px"] for row in accepted_rows]))
            if accepted_rows else None
        )
        mean_dist_txt = f"{mean_dist:.1f}px" if mean_dist is not None else "n/a"
        print(
            f"[ML-CAL] refine iter {iter_idx + 1}: "
            f"rings={len(accepted_rows)}/{len(all_ring_dets)} "
            f"markers={sum(1 for row in marker_rows if row['selected'])} "
            f"tol={match_tol_px:.1f}px mean_dist={mean_dist_txt} "
            f"corr={len(src_np)} err={err:.1f}px"
        )

        best_H = H_new
        best_err = err
        best_src = src_np
        best_dst = dst_np
        best_debug = {
            "iter_idx": iter_idx + 1,
            "accepted_rows": accepted_rows,
            "rejected_rows": rejected_rows,
            "marker_rows": marker_rows,
            "match_tol_px": match_tol_px,
        }

    if best_debug is not None:
        print(
            f"[ML-CAL] refine best iter {best_debug['iter_idx']}: "
            f"accepted={len(best_debug['accepted_rows'])} "
            f"rejected={len(best_debug['rejected_rows'])} "
            f"markers={sum(1 for row in best_debug['marker_rows'] if row['selected'])}"
        )
        for row in best_debug["accepted_rows"]:
            print(
                f"[ML-CAL] refine ring {row['class_name']} conf={row['confidence']:.2f} "
                f"src={_fmt_xy(*row['src'])} -> {row['boundary_label']} "
                f"r={row['radius_mm']:.1f} dist={row['dist_px']:.1f}px "
                f"exp={_fmt_xy(*row['expected_src'])}"
            )
        for row in best_debug["marker_rows"]:
            state = "use" if row["selected"] else "skip"
            print(
                f"[ML-CAL] refine marker {state} {row['marker']}#{row['rank']} "
                f"conf={row['confidence']:.2f} src={_fmt_xy(*row['src'])} "
                f"dist={row['dist_px']:.1f}px exp={_fmt_xy(*row['expected_src'])}"
            )

    return best_H, best_err, (best_src if best_src is not None
                               else np.zeros((0, 2), dtype=np.float32)), \
        (best_dst if best_dst is not None
         else np.zeros((0, 2), dtype=np.float32))


# ═══════════════════════════════════════════════════════════════════════════
# Outlier rejection
# ═══════════════════════════════════════════════════════════════════════════

def _reject_outliers(
    src_np: np.ndarray,
    dst_mm: np.ndarray,
    H: np.ndarray,
    calibrator,
    max_error_factor: float = 2.5,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """Remove correspondences whose reprojection error exceeds
    *max_error_factor* × median.  Keeps the bull centre and a minimum
    of 4 points.
    """
    dst_px = _mm_to_canvas_px(dst_mm, calibrator)
    projected = cv2.perspectiveTransform(
        src_np.reshape(-1, 1, 2), H
    ).reshape(-1, 2)
    errors = np.linalg.norm(projected - dst_px, axis=1)

    median_err = float(np.median(errors))
    threshold = max(median_err * max_error_factor, 6.0)

    keep = errors <= threshold
    keep[0] = True  # always keep bull centre
    if int(np.sum(keep)) < 4:
        order = np.argsort(errors)
        keep[:] = False
        keep[order[:4]] = True

    n_removed = int(np.sum(~keep))
    return src_np[keep], dst_mm[keep], n_removed


# ═══════════════════════════════════════════════════════════════════════════
# Main entry
# ═══════════════════════════════════════════════════════════════════════════

def ml_calibrate(
    frame: np.ndarray,
    model,          # OpenVINOModel
    calibrator,     # BoardCalibrator
) -> dict:
    """Run ML-based auto-calibration.

    Pipeline:
        1. YOLO inference.
        2. Bull centre + radial-line refinement.
        3. Rotation from '20'/'3' markers (or exhaustive search).
        4. Snap-based initial homography.
        5. Iterative refinement using H_inv to re-match detections.
        6. Outlier rejection + final solve.
        7. Anchor synthesis for the commit pipeline.
    """
    if model is None or not model.available:
        return {"success": False, "reason": "ml_model_not_available"}

    detections = model.predict(frame)
    if not detections:
        return {"success": False, "reason": "no_detections",
                "preview_b64": _encode_preview(frame)}

    by_class: Dict[str, list] = {}
    for det in detections:
        by_class.setdefault(det.class_name, []).append(det)

    det_summary = ", ".join(f"{k}:{len(v)}" for k, v in sorted(by_class.items()))
    print(f"[ML-CAL] classes → {det_summary}")

    # ── Ring detections (cal / cal1 / cal2 / cal3) ─────────────────────────
    all_ring_dets: List = []
    for cls in ('cal', 'cal1', 'cal2', 'cal3'):
        all_ring_dets.extend(by_class.get(cls, []))
    n_ring_dets = len(all_ring_dets)

    # ── Board centre ───────────────────────────────────────────────────────
    bull_dets = by_class.get('bull', [])
    if bull_dets:
        best_bull = max(bull_dets, key=lambda d: d.confidence)
        bull_cx, bull_cy = best_bull.cx, best_bull.cy
        print(f"[ML-CAL] bull @ ({bull_cx:.0f},{bull_cy:.0f}) "
              f"conf={best_bull.confidence:.2f}")
        if n_ring_dets >= 6:
            bull_cx, bull_cy = _estimate_center_radial_lines(
                all_ring_dets, (bull_cx, bull_cy))
    else:
        if n_ring_dets < 3:
            return {"success": False, "reason": "no_bull_and_few_rings",
                    "rings_found": n_ring_dets,
                    "preview_b64": _encode_preview(frame)}
        rough_cx = sum(d.cx for d in all_ring_dets) / n_ring_dets
        rough_cy = sum(d.cy for d in all_ring_dets) / n_ring_dets
        bull_cx, bull_cy = _estimate_center_radial_lines(
            all_ring_dets, (rough_cx, rough_cy))

    center = (bull_cx, bull_cy)
    _log_detection_inventory(by_class, center)

    if n_ring_dets < 3:
        return {"success": False, "reason": "insufficient_ring_detections",
                "rings_found": n_ring_dets,
                "preview_b64": _encode_preview(frame)}

    num20_dets = by_class.get('20', [])
    num3_dets = by_class.get('3', [])

    max_error_px = _MAX_ERROR_FRAC * calibrator.board_size

    # ── Initial candidate search ───────────────────────────────────────────
    marker_votes = _marker_rotation_votes(num20_dets, num3_dets, center)
    marker_rot = _determine_rotation(marker_votes)
    full_ring_coverage = all(len(by_class.get(cls, [])) >= N_SECTORS
                             for cls in _CLASS_RADIUS)

    best_H_px: Optional[np.ndarray] = None
    best_H_mm: Optional[np.ndarray] = None
    best_err = float('inf')
    best_rot = float(marker_rot) if marker_rot is not None else 0.0
    best_src: Optional[np.ndarray] = None
    best_dst: Optional[np.ndarray] = None
    best_snap_debug: Optional[Dict[str, Any]] = None

    if full_ring_coverage:
        sequence_trials: List[Dict[str, Any]] = []
        for shift in range(N_SECTORS):
            for direction in (+1, -1):
                src_np, dst_np, ordered_debug = _build_correspondences_ordered(
                    by_class, center, num20_dets, num3_dets, shift, direction)
                trial = {
                    "shift": int(shift),
                    "direction_name": ordered_debug["direction_name"],
                    "corr": int(len(src_np)),
                    "accepted_rings": int(ordered_debug["summary"]["accepted_rings"]),
                    "ring_total": int(ordered_debug["summary"]["ring_total"]),
                    "accepted_markers": int(ordered_debug["summary"]["accepted_markers"]),
                    "mean_marker_error_deg": None,
                    "mean_marker_radius_error_mm": None,
                    "err": None,
                }
                if len(src_np) < 4:
                    sequence_trials.append(trial)
                    continue
                H_px, H_mm, err, _ = _compute_homography_and_error(
                    src_np, dst_np, calibrator, robust=False)
                trial["err"] = None if H_px is None else float(err)
                if H_mm is not None:
                    marker_fit = _marker_fit_from_homography(H_mm, num20_dets, num3_dets)
                    ordered_debug["marker_fit"] = marker_fit
                    ordered_debug["summary"].update({
                        "accepted_markers": marker_fit["accepted_markers"],
                        "mean_marker_error_deg": marker_fit["mean_marker_error_deg"],
                        "max_marker_error_deg": marker_fit["max_marker_error_deg"],
                        "mean_marker_radius_error_mm": marker_fit["mean_marker_radius_error_mm"],
                        "max_marker_radius_error_mm": marker_fit["max_marker_radius_error_mm"],
                    })
                    trial["accepted_markers"] = marker_fit["accepted_markers"]
                    trial["mean_marker_error_deg"] = marker_fit["mean_marker_error_deg"]
                    trial["mean_marker_radius_error_mm"] = marker_fit["mean_marker_radius_error_mm"]
                sequence_trials.append(trial)
                if H_px is None or H_mm is None:
                    continue
                if _prefer_snap_candidate(err, ordered_debug, best_err, best_snap_debug):
                    best_H_px = H_px
                    best_H_mm = H_mm
                    best_err = err
                    best_src = src_np
                    best_dst = dst_np
                    best_snap_debug = ordered_debug

        _log_sequence_trials(sequence_trials)
    else:
        rot_candidates = _build_rotation_candidates(marker_votes)
        rotation_trials: List[Dict[str, Any]] = []

        for candidate in rot_candidates:
            rot_try = float(candidate["rotation"])
            src_np, dst_np, snap_debug = _build_correspondences_snap(
                by_class, center, rot_try, num20_dets, num3_dets)
            trial = {
                "rotation": rot_try,
                "sources": list(candidate["sources"]),
                "corr": int(len(src_np)),
                "accepted_rings": int(snap_debug["summary"]["accepted_rings"]),
                "ring_total": int(snap_debug["summary"]["ring_total"]),
                "accepted_markers": int(snap_debug["summary"]["accepted_markers"]),
                "mean_marker_error_deg": None,
                "mean_snap_error_deg": snap_debug["summary"]["mean_snap_error_deg"],
                "err": None,
            }
            if len(src_np) < 4:
                rotation_trials.append(trial)
                continue
            H_px, H_mm, err, _ = _compute_homography_and_error(
                src_np, dst_np, calibrator, robust=False)
            trial["err"] = None if H_px is None else float(err)
            if H_mm is not None:
                marker_fit = _marker_fit_from_homography(H_mm, num20_dets, num3_dets)
                snap_debug["marker_fit"] = marker_fit
                snap_debug["summary"].update({
                    "accepted_markers": marker_fit["accepted_markers"],
                    "mean_marker_error_deg": marker_fit["mean_marker_error_deg"],
                    "max_marker_error_deg": marker_fit["max_marker_error_deg"],
                    "mean_marker_radius_error_mm": marker_fit["mean_marker_radius_error_mm"],
                    "max_marker_radius_error_mm": marker_fit["max_marker_radius_error_mm"],
                })
                trial["accepted_markers"] = marker_fit["accepted_markers"]
                trial["mean_marker_error_deg"] = marker_fit["mean_marker_error_deg"]
            rotation_trials.append(trial)
            if H_px is None or H_mm is None:
                continue
            if _prefer_snap_candidate(err, snap_debug, best_err, best_snap_debug):
                best_H_px = H_px
                best_H_mm = H_mm
                best_err = err
                best_rot = rot_try
                best_src = src_np
                best_dst = dst_np
                best_snap_debug = snap_debug

        _log_rotation_trials(rotation_trials)

    if best_H_px is None or best_H_mm is None:
        print("[ML-CAL] snap-stage: no homography found")
        return {"success": False, "reason": "homography_failed",
                "rings_found": 0,
                "preview_b64": _encode_preview(frame)}

    if full_ring_coverage:
        print(f"[ML-CAL] ordered stage: rot_hint={best_rot:.1f}° err={best_err:.1f}px "
              f"corr={len(best_src)}")
    else:
        print(f"[ML-CAL] snap stage: rot={best_rot:.1f}° err={best_err:.1f}px "
              f"corr={len(best_src)}")
    if best_snap_debug is not None:
        if best_snap_debug.get("mode") == "ordered":
            _log_order_debug(best_snap_debug)
        else:
            _log_snap_debug(best_snap_debug)

    # ── Iterative refinement (H_inv re-matching) ───────────────────────────
    H_ref, err_ref, src_ref, dst_ref = _iterative_refine(
        best_H_px, all_ring_dets, center, num20_dets, num3_dets, calibrator,
        iters=3,
    )
    if H_ref is not None and len(src_ref) >= 4 and err_ref < best_err * 1.5:
        print(f"[ML-CAL] refine: err {best_err:.1f}→{err_ref:.1f}px "
              f"corr {len(best_src)}→{len(src_ref)}")
        best_H_px = H_ref
        best_err = err_ref
        best_src = src_ref
        best_dst = dst_ref
        best_H_px, best_H_mm, _, _ = _compute_homography_and_error(
            best_src, best_dst, calibrator)

    # ── Outlier rejection + re-solve ───────────────────────────────────────
    if best_src is not None and len(best_src) >= 6:
        f_src, f_dst, n_removed = _reject_outliers(
            best_src, best_dst, best_H_px, calibrator)
        if n_removed > 0 and len(f_src) >= 4:
            H2_px, H2_mm, err2, _ = _compute_homography_and_error(
                f_src, f_dst, calibrator)
            if H2_px is not None and H2_mm is not None and err2 < best_err:
                print(f"[ML-CAL] outliers: removed {n_removed}, "
                      f"err {best_err:.1f}→{err2:.1f}px")
                best_H_px = H2_px
                best_H_mm = H2_mm
                best_err = err2
                best_src = f_src
                best_dst = f_dst

    n_correspondences = len(best_src) if best_src is not None else 0
    rotation_deg = float(best_rot)
    mean_error = best_err

    print(f"[ML-CAL] final: rot={rotation_deg:.1f}° err={mean_error:.2f}px "
          f"corr={n_correspondences}")

    if best_H_px is None or best_H_mm is None or n_correspondences < 4:
        return {"success": False, "reason": "homography_failed",
                "rings_found": n_correspondences,
                "preview_b64": _encode_preview(frame)}

    if mean_error > max_error_px:
        print(f"[ML-CAL] reproj {mean_error:.1f}px > threshold "
              f"{max_error_px:.1f}px — rejecting")
        return {"success": False, "reason": "high_reprojection_error",
                "rings_found": n_correspondences,
                "reprojection_error": round(mean_error, 3),
                "preview_b64": _encode_preview(frame)}

    # ── Preview image with detections + warped inset ───────────────────────
    bs = calibrator.board_size
    preview = frame.copy()
    for det in detections:
        if det.class_name == 'bull':
            color = (0, 255, 0)
        elif det.class_name in ('20', '3'):
            color = (0, 255, 255)
        else:
            color = (255, 100, 0)
        cv2.rectangle(preview,
                      (int(det.x1), int(det.y1)),
                      (int(det.x2), int(det.y2)),
                      color, 2)
        cv2.putText(preview, f"{det.class_name} {det.confidence:.2f}",
                    (int(det.x1), int(det.y1) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    warped = cv2.warpPerspective(frame, best_H_px, (bs, bs))
    try:
        warped_vis = calibrator.draw_wireframe(warped)
    except Exception:
        warped_vis = warped
    inset_w = min(360, warped_vis.shape[1])
    inset_h = int(warped_vis.shape[0] * (inset_w / warped_vis.shape[1]))
    inset = cv2.resize(warped_vis, (inset_w, inset_h))
    preview[10:10 + inset_h, 10:10 + inset_w] = inset

    cv2.putText(
        preview,
        f"ML-CAL: {n_correspondences} pts, err={mean_error:.1f}px, "
        f"rot={rotation_deg:.1f} deg",
        (10, preview.shape[0] - 14),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 2, cv2.LINE_AA,
    )

    # ── Synthesise 8 anchor points for the commit pipeline ────────────────
    anchor_pts = _synthesise_anchor_points(
        best_H_px, bull_cx, bull_cy, calibrator, rotation_deg,
    )
    if anchor_pts is None or len(anchor_pts) not in (4, 8):
        return {"success": False, "reason": "anchor_synthesis_failed",
                "rings_found": n_correspondences,
                "preview_b64": _encode_preview(preview)}

    return {
        "success": True,
        "reason": "ml_calibration",
        "rings_found": n_correspondences,
        "reprojection_error": round(mean_error, 3),
        "rotation_deg": round(rotation_deg, 2),
        "points": anchor_pts.tolist(),
        "n_points": len(anchor_pts),
        "H": best_H_mm.tolist(),
        "preview_b64": _encode_preview(preview),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Anchor synthesis (unchanged — inverts H to project calibrator dst_pts_8)
# ═══════════════════════════════════════════════════════════════════════════

def _synthesise_anchor_points(
    H: np.ndarray,
    bull_cx: float,
    bull_cy: float,
    calibrator,
    rotation_deg: float,
) -> Optional[np.ndarray]:
    """Project the calibrator's 8 known board-pixel anchors back into
    camera space using H_inv.
    """
    try:
        H_inv = np.linalg.inv(H)
        if hasattr(calibrator, '_dst_pts_8'):
            dst_pts = calibrator._dst_pts_8.astype(np.float32)
        else:
            return None
        src_pts = cv2.perspectiveTransform(
            dst_pts.reshape(-1, 1, 2), H_inv
        ).reshape(-1, 2).astype(np.float32)
        return src_pts
    except Exception as e:
        print(f"[ML-CAL] anchor synthesis failed: {e}")
        return None
