"""ThrowVision - Automatic Dartboard Calibration Refiner (v3 - Camera-Space Ellipse Detection)

Strategy:
  1. Apply HSV red+green mask directly in the camera frame (no warping).
  2. Find contours and filter for elliptical shapes using cv2.fitEllipse.
  3. Use the rough homography ONLY to estimate each ellipse's board-space radius
     so we can identify which ring it corresponds to.
  4. Sample 36 Cartesian points around each matched ellipse in camera space.
  5. Pair each camera-space point with its known board-mm coordinate.
  6. Feed all correspondences into cv2.findHomography(RANSAC) to produce
     a sub-pixel refined homography.

This approach is robust to poor rough homographies because ring detection
happens in the natural camera frame where board rings appear as proper ellipses
regardless of camera angle.
"""

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np


BULL_INNER_R = 6.35
BULL_OUTER_R = 15.9
TRIPLE_INNER = 99.0
TRIPLE_OUTER = 107.0
DOUBLE_INNER = 162.0
DOUBLE_OUTER = 170.0

_RINGS = [
    (TRIPLE_INNER, "triple_inner"),
    (TRIPLE_OUTER, "triple_outer"),
    (DOUBLE_INNER, "double_inner"),
    (DOUBLE_OUTER, "double_outer"),
]

SAMPLE_N   = 36
RADIUS_TOL = 0.12
MIN_REFINE_RINGS = 3
MIN_AUTO_RINGS = 3


def _ring_family(ring_name: str) -> str:
    if ring_name.startswith("triple"):
        return "triple"
    if ring_name.startswith("double"):
        return "double"
    return ring_name


def _ring_mask_bgr(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of red + green pixels. Wide ranges for LED-lit boards."""
    h, w = bgr.shape[:2]
    hsv  = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Widen saturation/value floor at lower resolutions (colour info is coarser)
    sat_lo = max(25, int(45 * (w * h) / (1920 * 1080)))
    red1 = cv2.inRange(hsv, (0,   sat_lo, sat_lo), (12,  255, 255))
    red2 = cv2.inRange(hsv, (162, sat_lo, sat_lo), (180, 255, 255))
    grn  = cv2.inRange(hsv, (28,  max(20, sat_lo - 10), max(20, sat_lo - 10)), (97, 255, 255))
    mask = cv2.bitwise_or(cv2.bitwise_or(red1, red2), grn)
    # Scale kernel sizes with image resolution so morph ops are proportional
    k_small = max(3, int(3 * w / 1920) * 2 + 1)   # always odd, ≥3
    k_big   = max(5, int(7 * w / 1920) * 2 + 1)   # always odd, ≥5
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_small, k_small))
    k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_big,   k_big))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k3, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k7, iterations=1)
    return mask


def _estimate_ring_radius_mm(
    ellipse_pts: np.ndarray,
    rough_H: np.ndarray,
) -> float:
    """Estimate the board-space radius of a camera-space ellipse.

    Projects a sample of points on the ellipse through the rough homography
    and returns the median distance from the board centre. This is only used
    to identify WHICH ring we matched — accuracy of a few mm is sufficient.
    """
    pts = ellipse_pts.reshape(-1, 1, 2).astype(np.float32)
    board_pts = cv2.perspectiveTransform(pts, rough_H)
    board_pts = board_pts.reshape(-1, 2)
    radii = np.hypot(board_pts[:, 0], board_pts[:, 1])
    return float(np.median(radii))


def _sample_ellipse_points(
    cx: float, cy: float, a: float, b: float, angle_deg: float,
    n: int = SAMPLE_N,
) -> np.ndarray:
    """Sample *n* evenly-spaced points around a rotated ellipse."""
    t = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    cos_a = math.cos(math.radians(angle_deg))
    sin_a = math.sin(math.radians(angle_deg))
    ex = a * np.cos(t)
    ey = b * np.sin(t)
    px = cx + ex * cos_a - ey * sin_a
    py = cy + ex * sin_a + ey * cos_a
    return np.column_stack([px, py]).astype(np.float32)


def refine_calibration(
    frame: np.ndarray,
    rough_src_pts: np.ndarray,
    calibrator,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Refine board calibration using camera-space ellipse detection.

    Parameters
    ----------
    frame          : undistorted BGR camera frame
    rough_src_pts  : user-dragged calibration points in camera space (4 or 8)
    calibrator     : BoardCalibrator instance

    Returns
    -------
    (refined_src_pts, refined_dst_mm_pts, vis) or None if detection fails.
    """
    n = len(rough_src_pts)
    if n < 4:
        return None

    dst_px = calibrator._dst_pts_8 if n == 8 else calibrator._dst_pts_4
    dst_mm = calibrator._dst_mm_pts_8 if n == 8 else calibrator._dst_mm_pts_4

    src = rough_src_pts.astype(np.float32)
    if n == 4:
        rough_H = cv2.getPerspectiveTransform(src, dst_px)
    else:
        rough_H, _ = cv2.findHomography(src, dst_px, 0)
    if rough_H is None:
        return None

    h_img, w_img = frame.shape[:2]
    vis = frame.copy()

    # ── Scale-aware thresholds (calibrated against 1920×1080 baseline) ──────
    img_area  = h_img * w_img
    ref_area  = 1920 * 1080          # baseline resolution
    # Relative pixel density vs full-HD; ≤1 for lower resolutions
    res_scale = img_area / ref_area

    # At the reference resolution: min_area ≈ 0.002*img_area (~4147 px).
    # Scale down proportionally so lower-res images are treated fairly.
    min_contour_area = max(80, img_area * 0.002)
    max_contour_area = img_area * 0.85

    # Relax circularity at low-res — spider wires break arcs into chunkier blobs
    min_circularity  = max(0.15, 0.25 * res_scale)

    # ── Detect ring ellipses in the original camera frame ──────────────────

    mask = _ring_mask_bgr(frame)

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

    candidate_ellipses = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_contour_area or area > max_contour_area:
            continue
        if len(cnt) < 5:
            continue
        perim = cv2.arcLength(cnt, True)
        if perim < 1:
            continue
        circularity = 4 * math.pi * area / (perim * perim)
        if circularity < min_circularity:
            continue

        try:
            ellipse = cv2.fitEllipse(cnt)
        except cv2.error:
            continue

        (ex, ey), (ew, eh), eangle = ellipse
        if ew < 1 or eh < 1:
            continue
        # Relax aspect tolerance at lower resolutions
        min_aspect = max(0.15, 0.25 * res_scale)
        aspect = min(ew, eh) / max(ew, eh)
        if aspect < min_aspect:
            continue

        # Reject ellipses whose centre is far from the image centre
        cx_img, cy_img = w_img / 2.0, h_img / 2.0
        dist_from_center = math.hypot(ex - cx_img, ey - cy_img)
        if dist_from_center > max(w_img, h_img) * 0.45:
            continue

        a = max(ew, eh) / 2.0
        b = min(ew, eh) / 2.0
        sample_pts = _sample_ellipse_points(ex, ey, a, b, eangle, SAMPLE_N)

        # Use rough homography ONLY to identify which ring this ellipse is
        r_mm = _estimate_ring_radius_mm(sample_pts, rough_H)

        candidate_ellipses.append({
            'ellipse': ellipse,
            'r_mm': r_mm,
            'sample_pts': sample_pts,
            'area': area,
        })

    if not candidate_ellipses:
        return None

    # ── Match each candidate to the closest known ring ─────────────────────
    matched = []
    used_rings = set()
    for (known_mm, ring_name) in _RINGS:
        best = None
        best_err = known_mm * RADIUS_TOL
        for cand in candidate_ellipses:
            err = abs(cand['r_mm'] - known_mm)
            if err < best_err and ring_name not in used_rings:
                best_err = err
                best = cand
        if best is not None:
            matched.append((best, known_mm, ring_name))
            used_rings.add(ring_name)

    families = {_ring_family(ring_name) for (_cand, _r_mm, ring_name) in matched}
    if len(matched) < MIN_REFINE_RINGS or len(families) < 2:
        return None

    # ── Build correspondences: camera-space ellipse pts → board-mm pts ─────
    all_src = []
    all_dst = []

    for (cand, r_mm, ring_name) in matched:
        ellipse = cand['ellipse']
        (ex, ey), (ew, eh), eangle = ellipse

        # Draw matched ellipse on visualisation
        cv2.ellipse(vis, ellipse, (0, 200, 255), 2)
        cv2.putText(
            vis,
            f"{r_mm:.0f}mm ({ring_name})",
            (int(ex) + 10, int(ey)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1,
        )

        a = max(ew, eh) / 2.0
        b = min(ew, eh) / 2.0
        cam_pts = _sample_ellipse_points(ex, ey, a, b, eangle, SAMPLE_N)

        # Board-mm points at the known ring radius
        thetas = np.linspace(0.0, 2 * math.pi, SAMPLE_N, endpoint=False)
        brd_pts = np.column_stack([
            r_mm * np.cos(thetas),
            r_mm * np.sin(thetas),
        ]).astype(np.float32)

        all_src.append(cam_pts)
        all_dst.append(brd_pts)

    if not all_src:
        return None

    refined_src = np.vstack(all_src)
    refined_dst = np.vstack(all_dst)

    return refined_src, refined_dst, vis


# ===========================================================================
#  AUTO-CALIBRATION PIPELINE  (used by /api/cal/auto route)
# ===========================================================================

import base64

# All rings in ascending order (mm, label) — used by auto_calibrate
ALL_RINGS = [
    (BULL_INNER_R,  "bull"),
    (BULL_OUTER_R,  "bullseye"),
    (TRIPLE_INNER,  "triple_inner"),
    (TRIPLE_OUTER,  "triple_outer"),
    (DOUBLE_INNER,  "double_inner"),
    (DOUBLE_OUTER,  "double_outer"),
]

SAMPLE_N_AUTO = 24   # points sampled around each confirmed ring (auto route)


# ---------------------------------------------------------------------------
# Shared pre-processing helper
# ---------------------------------------------------------------------------

def _adaptive_canny(gray: np.ndarray) -> np.ndarray:
    """Compute Canny edges with thresholds derived from the image median."""
    med = float(np.median(gray))
    sigma = 0.33
    lo = max(0,   int((1.0 - sigma) * med))
    hi = min(255, int((1.0 + sigma) * med))
    if hi < lo * 2:
        hi = min(255, lo * 2)
    return cv2.Canny(gray, lo, hi)


# ---------------------------------------------------------------------------
# LAYER 1 — Color-seeded ellipse (most robust for steep-angle cameras)
# ---------------------------------------------------------------------------

def _detect_ellipse_from_color(
    frame: np.ndarray,
) -> Optional[Tuple[Tuple, float]]:
    """
    Use the board's red + green ring bands as the primary detection seed.
    Returns ((cx, cy, a, b, angle_deg), master_mm) or None.
    """
    h, w = frame.shape[:2]
    img_cx, img_cy = w / 2.0, h / 2.0
    min_dim = min(w, h)

    filt = cv2.bilateralFilter(frame, 9, 75, 75)
    hsv  = cv2.cvtColor(filt, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (  0,  70,  60), ( 12, 255, 255))
    red2 = cv2.inRange(hsv, (158,  70,  60), (180, 255, 255))
    grn  = cv2.inRange(hsv, ( 35,  50,  50), ( 90, 255, 255))
    mask = cv2.bitwise_or(cv2.bitwise_or(red1, red2), grn)

    k13  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k13, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None

    def _valid_ell(ex, ey, a, b, min_a_frac=0.15):
        if a < min_a_frac * min_dim or b / a < 0.10:
            return False
        if abs(ex - img_cx) > 0.55 * w or abs(ey - img_cy) > 0.55 * h:
            return False
        return True

    qualifying = [cnt for cnt in contours if cv2.contourArea(cnt) > 200]
    if len(qualifying) >= 2:
        merged = np.vstack(qualifying)
        hull   = cv2.convexHull(merged)
        if len(hull) >= 5:
            try:
                (ex, ey), (ew, eh), angle = cv2.fitEllipse(hull)
                a = max(ew, eh) / 2.0
                b = min(ew, eh) / 2.0
                if _valid_ell(ex, ey, a, b, min_a_frac=0.20):
                    print(f"[CAL] color: merged hull OK  c=({ex:.0f},{ey:.0f}) a={a:.0f}")
                    return ((float(ex), float(ey), float(a), float(b), float(angle)),
                            DOUBLE_OUTER)
            except cv2.error:
                pass

    qualifying.sort(key=cv2.contourArea, reverse=True)
    for cnt in qualifying[:5]:
        hull = cv2.convexHull(cnt)
        if len(hull) < 5:
            continue
        try:
            (ex, ey), (ew, eh), angle = cv2.fitEllipse(hull)
        except cv2.error:
            continue
        a = max(ew, eh) / 2.0
        b = min(ew, eh) / 2.0
        if _valid_ell(ex, ey, a, b, min_a_frac=0.15):
            print(f"[CAL] color: single-contour fallback c=({ex:.0f},{ey:.0f}) a={a:.0f}")
            return ((float(ex), float(ey), float(a), float(b), float(angle)),
                    DOUBLE_OUTER)

    return None


# ---------------------------------------------------------------------------
# Shared contour → ellipse extractor for layers 2 & 3
# ---------------------------------------------------------------------------

def _best_ellipse_from_contours(
    contours, w: int, h: int,
    top_n: int = 5, min_aspect: float = 0.10,
) -> Optional[Tuple]:
    """Return the first valid (cx, cy, a, b, angle) from *contours*."""
    img_cx, img_cy = w / 2.0, h / 2.0
    min_dim = min(w, h)
    min_area = 0.005 * w * h

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:top_n]:
        if len(cnt) < 5 or cv2.contourArea(cnt) < min_area:
            continue
        try:
            (ex, ey), (ew, eh), angle = cv2.fitEllipse(cnt)
        except cv2.error:
            continue
        a = max(ew, eh) / 2.0
        b = min(ew, eh) / 2.0
        if a < 0.12 * min_dim or b / a < min_aspect:
            continue
        if abs(ex - img_cx) > 0.65 * w or abs(ey - img_cy) > 0.65 * h:
            continue
        return (float(ex), float(ey), float(a), float(b), float(angle))
    return None


# ---------------------------------------------------------------------------
# LAYER 2 — Relaxed edge-based outer-boundary detection
# ---------------------------------------------------------------------------

def _detect_ellipse_from_edges(frame: np.ndarray) -> Optional[Tuple]:
    h, w = frame.shape[:2]
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray  = clahe.apply(gray)

    k25    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k25)
    edges  = _adaptive_canny(closed)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    return _best_ellipse_from_contours(contours, w, h, top_n=5, min_aspect=0.10)


# ---------------------------------------------------------------------------
# LAYER 3 — Sobel gradient magnitude fallback
# ---------------------------------------------------------------------------

def _detect_ellipse_from_gradient(frame: np.ndarray) -> Optional[Tuple]:
    h, w = frame.shape[:2]
    gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    gx  = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gy  = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    mag_norm = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8U)
    _, thresh = cv2.threshold(mag_norm, 20, 255, cv2.THRESH_BINARY)

    k15    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, k15)

    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    return _best_ellipse_from_contours(contours, w, h, top_n=10, min_aspect=0.08)


# ---------------------------------------------------------------------------
# Phase-2 helpers: bootstrap rough H, then detect individual rings
# ---------------------------------------------------------------------------

def _build_rough_H_from_ellipse(
    ell: Tuple, r_mm: float,
) -> Optional[np.ndarray]:
    """Build a rough camera -> board-mm homography from ONE detected ellipse.

    Samples 4 points on the camera-space ellipse and pairs them with
    the corresponding board-space points at the known ring radius.
    """
    cx, cy, a, b, ang = ell
    src = _sample_ellipse_points(cx, cy, a, b, ang, 4)
    dst = _build_board_points(r_mm, 4)
    try:
        return cv2.getPerspectiveTransform(src, dst)
    except cv2.error:
        return None


def _detect_individual_rings(
    frame: np.ndarray,
    rough_H: np.ndarray,
    master_cx: float,
    master_cy: float,
    master_a: float,
) -> List[Tuple]:
    """Detect individual ring ellipses from the colour mask.

    Uses a rough homography (from a single master ellipse) to estimate
    the board-space radius of every contour-fitted ellipse, then matches
    each to the closest known ring within tolerance.

    Returns list of ((cx,cy,a,b,angle), known_mm, ring_name).
    """
    h, w = frame.shape[:2]
    img_area = h * w
    res_scale = img_area / (1920 * 1080)
    img_cx, img_cy = w / 2.0, h / 2.0

    # Wider morphological closing to merge adjacent red/green segments
    mask = _ring_mask_bgr(frame)
    k_extra = max(9, int(13 * w / 1920) * 2 + 1)
    ke = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_extra, k_extra))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ke, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

    # More permissive filters than refine_calibration (rough H is less accurate)
    min_area = max(60, img_area * 0.0005)
    max_area = img_area * 0.85
    min_circ = max(0.08, 0.15 * res_scale)
    min_aspect = max(0.10, 0.18 * res_scale)

    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue
        if len(cnt) < 5:
            continue
        perim = cv2.arcLength(cnt, True)
        if perim < 1:
            continue
        circ = 4 * math.pi * area / (perim * perim)
        if circ < min_circ:
            continue

        try:
            ellipse = cv2.fitEllipse(cnt)
        except cv2.error:
            continue

        (ex, ey), (ew, eh), eangle = ellipse
        if ew < 1 or eh < 1:
            continue
        aspect = min(ew, eh) / max(ew, eh)
        if aspect < min_aspect:
            continue

        # Centre must be near the master ellipse centre
        dist_from_master = math.hypot(ex - master_cx, ey - master_cy)
        if dist_from_master > master_a * 0.5:
            continue

        # Must be within the image bounds
        if abs(ex - img_cx) > 0.55 * w or abs(ey - img_cy) > 0.55 * h:
            continue

        a = max(ew, eh) / 2.0
        b = min(ew, eh) / 2.0
        pts = _sample_ellipse_points(ex, ey, a, b, eangle, SAMPLE_N)
        r_mm = _estimate_ring_radius_mm(pts, rough_H)

        candidates.append({
            'ellipse': (float(ex), float(ey), float(a), float(b), float(eangle)),
            'r_mm': r_mm,
            'area': area,
        })

    if not candidates:
        print(f"[CAL] individual ring det: 0 candidates from {len(contours)} contours")
        return []

    print(f"[CAL] individual ring det: {len(candidates)} candidates "
          f"(r_mm: {min(c['r_mm'] for c in candidates):.0f}"
          f"-{max(c['r_mm'] for c in candidates):.0f})")

    # Match each candidate to the closest known ring (within tolerance)
    matched: List[Tuple] = []
    used: set = set()
    for known_mm, ring_name in _RINGS:
        best_idx = None
        best_err = known_mm * RADIUS_TOL
        for i, cand in enumerate(candidates):
            if i in used:
                continue
            err = abs(cand['r_mm'] - known_mm)
            if err < best_err:
                best_err = err
                best_idx = i
        if best_idx is not None:
            c = candidates[best_idx]
            matched.append((c['ellipse'], known_mm, ring_name))
            used.add(best_idx)
            print(f"[CAL]   {ring_name:14s} ({known_mm:6.1f}mm): "
                  f"detected r={c['r_mm']:.1f}mm  err={best_err:.1f}mm")

    print(f"[CAL] matched {len(matched)}/{len(_RINGS)} rings individually")
    return matched


# ---------------------------------------------------------------------------
# Homography computation
# ---------------------------------------------------------------------------

def _build_board_points(r_mm: float, n: int = SAMPLE_N_AUTO) -> np.ndarray:
    t = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([
        r_mm * np.cos(t),
        r_mm * np.sin(t),
    ]).astype(np.float32)


def _compute_homography(
    matched_rings: List[Tuple],
) -> Tuple[Optional[np.ndarray], float, np.ndarray, np.ndarray]:
    all_src: List[np.ndarray] = []
    all_dst: List[np.ndarray] = []

    for (ell, known_mm, _label) in matched_rings:
        cx, cy, a, b, angle = ell
        src_pts = _sample_ellipse_points(cx, cy, a, b, angle, SAMPLE_N_AUTO)
        dst_pts = _build_board_points(known_mm, SAMPLE_N_AUTO)
        all_src.append(src_pts)
        all_dst.append(dst_pts)

    if not all_src:
        return None, 9999.0, np.zeros((0, 2)), np.zeros((0, 2))

    src = np.vstack(all_src)
    dst = np.vstack(all_dst)

    if len(src) < 30:
        return None, 9999.0, src, dst

    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransacReprojThreshold=8.0)
    if H is None or mask is None:
        return None, 9999.0, src, dst

    inlier_mask = mask.ravel().astype(bool)
    n_inliers = int(inlier_mask.sum())

    if n_inliers < 6:
        return None, 9999.0, src, dst
    if n_inliers / len(src) < 0.10:
        return None, 9999.0, src, dst

    src_in = src[inlier_mask]
    dst_in = dst[inlier_mask]

    src_h = np.hstack([src_in, np.ones((n_inliers, 1), dtype=np.float32)])
    dst_proj_h = (H @ src_h.T).T
    w = dst_proj_h[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)
    dst_proj = dst_proj_h[:, :2] / w

    err = np.linalg.norm(dst_in - dst_proj, axis=1)
    mean_err = float(np.mean(err))

    return H, mean_err, src, dst


# ---------------------------------------------------------------------------
# Rotation alignment
# ---------------------------------------------------------------------------

def _auto_rotate_homography(
    frame: np.ndarray,
    H_cam_to_mm: np.ndarray,
    board_size: int,
    scale: float,
) -> np.ndarray:
    bs = board_size
    cx_px = cy_px = bs / 2.0

    mm_to_px = np.array([
        [scale, 0,      cx_px],
        [0,     -scale, cy_px],
        [0,     0,      1    ],
    ], dtype=np.float64)
    H_cam_to_px = mm_to_px @ H_cam_to_mm

    warped = cv2.warpPerspective(frame, H_cam_to_px, (bs, bs))
    gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

    r_px = (DOUBLE_INNER + DOUBLE_OUTER) / 2.0 * scale
    angles_deg = np.arange(360)
    angles_rad = np.radians(angles_deg)

    sample_x = (cx_px + r_px * np.cos(angles_rad)).astype(np.int32)
    sample_y = (cy_px - r_px * np.sin(angles_rad)).astype(np.int32)

    sample_x = np.clip(sample_x, 0, bs - 1)
    sample_y = np.clip(sample_y, 0, bs - 1)

    brightness = gray_w[sample_y, sample_x].astype(np.float32)

    grad = np.abs(np.gradient(brightness))

    best_offset = 0
    best_score  = -1.0
    sector_deg  = 18
    for offset in range(sector_deg):
        boundary_idx = [(offset + i * sector_deg) % 360 for i in range(20)]
        score = float(np.mean(grad[boundary_idx]))
        if score > best_score:
            best_score = score
            best_offset = offset

    detected_20_angle = (best_offset + sector_deg / 2) % 360
    delta_deg = 90.0 - detected_20_angle

    if abs(delta_deg % 360) < 1.0 or abs(delta_deg % 360) > 18.0:
        return H_cam_to_mm

    delta_rad = math.radians(delta_deg)
    cos_d, sin_d = math.cos(delta_rad), math.sin(delta_rad)
    R = np.array([
        [cos_d, -sin_d, 0],
        [sin_d,  cos_d, 0],
        [0,      0,     1],
    ], dtype=np.float64)

    return R @ H_cam_to_mm


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------

def _overlay_text(img: np.ndarray, text: str) -> None:
    cv2.putText(img, text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0),   4, cv2.LINE_AA)
    cv2.putText(img, text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 255), 2, cv2.LINE_AA)


def _encode_preview(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    if max(h, w) > 960:
        s = 960 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode("ascii")


# ---------------------------------------------------------------------------
# Main auto_calibrate entry point
# ---------------------------------------------------------------------------

def auto_calibrate(
    frame: np.ndarray,
    calibrator,
) -> dict:
    """Fully automatic dartboard calibration from a raw camera frame.

    Two-phase pipeline:
      Phase 1 — Find a single "master" ellipse (outer board boundary)
                using the 3-layer detector (colour → edge → gradient).
      Phase 2 — Build a rough homography from the master, then detect
                individual ring contours in the colour mask and match
                each to its known board radius.  This avoids the old
                approach of deriving all rings from one ellipse (which
                shares centre & angle — wrong under perspective).
      Phase 3 — RANSAC homography from individually matched rings.

    Returns dict with keys: success, rings_found, reprojection_error,
    H, H_mm, preview_b64, reason.
    """
    bs    = calibrator.board_size
    scale = calibrator._scale

    # ── Phase 1: find master (outer boundary) ellipse ─────────────────
    master = None
    master_mm = DOUBLE_OUTER

    color_result = _detect_ellipse_from_color(frame)
    if color_result is not None:
        master, master_mm = color_result
        _src = "Layer 1 (color)"
    else:
        edge_result = _detect_ellipse_from_edges(frame)
        if edge_result is not None:
            master = edge_result
            _src = "Layer 2 (edge)"
        else:
            grad_result = _detect_ellipse_from_gradient(frame)
            if grad_result is not None:
                master = grad_result
                _src = "Layer 3 (gradient)"
            else:
                _src = "all failed"

    preview = frame.copy()

    if master is None:
        print("[CAL] auto_calibrate: no master ellipse found")
        _overlay_text(preview, "FAILED: no board boundary detected")
        return {
            "success":     False,
            "reason":      "Ring detection failed",
            "rings_found": 0,
            "preview_b64": _encode_preview(preview),
        }

    mcx, mcy, ma, mb, mang = master
    print(f"[CAL] auto_calibrate: master via {_src}  "
          f"c=({mcx:.0f},{mcy:.0f}) a={ma:.0f} r={master_mm:.0f}mm")

    # Draw master ellipse (thin cyan)
    cv2.ellipse(preview, (int(mcx), int(mcy)),
                (max(1, int(ma)), max(1, int(mb))),
                mang, 0, 360, (255, 200, 0), 1)

    # ── Phase 2: individual ring detection via rough H ────────────────
    rough_H = _build_rough_H_from_ellipse(master, master_mm)

    matched: List[Tuple] = []
    if rough_H is not None:
        matched = _detect_individual_rings(frame, rough_H, mcx, mcy, ma)

    n_rings = len(matched)
    families = {_ring_family(label) for (_ell, _known_mm, label) in matched}

    # Draw matched ring ellipses (green)
    for (ell, known_mm, label) in matched:
        ex, ey, a, b, angle = ell
        axes = (max(1, int(a)), max(1, int(b)))
        cv2.ellipse(preview, (int(ex), int(ey)), axes,
                    angle, 0, 360, (0, 255, 80), 2)
        cv2.putText(preview, f"{label} ({known_mm:.0f}mm)",
                    (int(ex + a + 4), int(ey)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 80), 1)

    if n_rings < MIN_AUTO_RINGS or len(families) < 2:
        _overlay_text(preview, f"FAILED: only {n_rings} strong rings (need {MIN_AUTO_RINGS}+ incl. triple+double)")
        return {
            "success":     False,
            "reason":      "insufficient_rings",
            "rings_found": n_rings,
            "preview_b64": _encode_preview(preview),
        }

    # ── Phase 3: RANSAC homography from individually matched rings ────
    H_mm, mean_err, src_pts, dst_pts = _compute_homography(matched)

    if H_mm is None:
        _overlay_text(preview, "FAILED: homography computation failed")
        return {
            "success":     False,
            "reason":      "homography_failed",
            "rings_found": n_rings,
            "preview_b64": _encode_preview(preview),
        }

    if mean_err >= 9.0:
        _overlay_text(preview,
                      f"FAILED: reproj error {mean_err:.2f}mm >= 9.0mm")
        return {
            "success":            False,
            "reason":             "high_reprojection_error",
            "rings_found":        n_rings,
            "reprojection_error": mean_err,
            "preview_b64":        _encode_preview(preview),
        }

    try:
        H_mm = _auto_rotate_homography(frame, H_mm, bs, scale)
    except Exception:
        pass

    _overlay_text(preview,
                  f"OK  rings={n_rings}  reproj={mean_err:.2f}mm  corr={len(src_pts)}")

    return {
        "success":            True,
        "rings_found":        n_rings,
        "reprojection_error": round(mean_err, 3),
        "H":                  H_mm.tolist(),
        "H_mm":               H_mm.tolist(),
        "preview_b64":        _encode_preview(preview),
    }
