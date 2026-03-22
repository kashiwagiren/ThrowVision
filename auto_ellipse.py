"""ThrowVision — Automatic Dartboard Calibration Refiner  (v2 — Polar Transform)

Strategy:
  1. Build a rough homography from the user's 4 or 8 manual clicks.
  2. Warp the (already-undistorted) frame to an approximate top-down view.
  3. Convert that warped image to Polar coordinates using cv2.warpPolar().
     In the polar image, concentric circles become perfectly straight
     horizontal bands — spider-wire gaps become tiny interruptions in a
     large solid signal, so peak detection is fully immune to them.
  4. Apply the HSV red/green mask in polar space.
  5. Project the polar mask onto the radial axis (sum all columns),
     producing a 1-D signal whose peaks mark exact ring radii.
  6. Match each peak to a known board radius (triple inner/outer, double
     inner/outer).
  7. Sample 36 Cartesian points at the matched radius in warp space,
     map them back to camera space via the inverse rough homography, and
     pair each with its known board-mm coordinate.
  8. Feed all (camera-px → board-mm) correspondences into
     cv2.findHomography(RANSAC) to produce a sub-pixel refined H.

Usage (from server.py):
    from auto_ellipse import refine_calibration
    result = refine_calibration(frame, rough_src_pts, calibrator)
    if result:
        refined_src, refined_dst_mm, vis = result
"""

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Known dartboard ring radii in mm (matches calibrator.py)
# ---------------------------------------------------------------------------
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

SAMPLE_N    = 36      # points sampled around each confirmed ring
RADIUS_TOL  = 0.10   # ±10% tolerance when matching peak to known radius
_MIN_PEAK   = 20      # minimum projection value (out of 255*360) to count as a ring


# ---------------------------------------------------------------------------
# HSV colour mask — red + green dartboard bands
# ---------------------------------------------------------------------------
def _ring_mask_bgr(bgr: np.ndarray) -> np.ndarray:
    """Return a binary mask of red + green pixels in *bgr*."""
    hsv  = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (0,   70, 70),  (10,  255, 255))
    red2 = cv2.inRange(hsv, (165, 70, 70),  (180, 255, 255))
    grn  = cv2.inRange(hsv, (35,  50, 50),  (90,  255, 255))
    mask = cv2.bitwise_or(cv2.bitwise_or(red1, red2), grn)
    # Light morphological close to bridge spider-wire gaps
    k    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    return mask


# ---------------------------------------------------------------------------
# Peak finder (no scipy dependency)
# ---------------------------------------------------------------------------
def _find_peaks(signal: np.ndarray,
                min_val:   float = _MIN_PEAK,
                min_dist:  int   = 5,
                smooth_r:  int   = 3) -> List[int]:
    """Return indices of local maxima in *signal* above *min_val*.

    Applies a box-filter smoothing to reduce noise, then finds local peaks
    that are separated by at least *min_dist* samples.
    """
    sig = signal.astype(np.float32)
    # Gaussian-like smoothing via repeated box blur
    if smooth_r > 1:
        kernel = np.ones(smooth_r, dtype=np.float32) / smooth_r
        sig    = np.convolve(sig, kernel, mode='same')

    peaks = []
    for i in range(1, len(sig) - 1):
        if sig[i] >= min_val and sig[i] > sig[i - 1] and sig[i] > sig[i + 1]:
            peaks.append(i)

    # Enforce minimum distance — keep only the strongest in each cluster
    if not peaks:
        return []
    filtered = [peaks[0]]
    for p in peaks[1:]:
        if p - filtered[-1] >= min_dist:
            filtered.append(p)
        elif sig[p] > sig[filtered[-1]]:
            filtered[-1] = p
    return filtered


# ---------------------------------------------------------------------------
# Polar-transform ring detection
# ---------------------------------------------------------------------------
def _detect_rings_polar(
    warped: np.ndarray,
    board_cx: float,
    board_cy: float,
    max_r: int,
    scale: float,           # px / mm in warp canvas
) -> List[Tuple[float, float]]:
    """Detect concentric ring radii (in warp-space pixels) via polar transform.

    Returns list of (r_px, r_mm) for each confirmed ring.
    """
    # warpPolar output shape: rows = max_r radii, cols = 360 angles
    polar = cv2.warpPolar(
        warped,
        (360, max_r),
        (board_cx, board_cy),
        max_r,
        cv2.WARP_POLAR_LINEAR | cv2.INTER_LINEAR,
    )
    # polar.shape == (max_r, 360, channels)
    # Row r = radius r, Col θ = angle θ°
    # A concentric ring at radius R maps to a HORIZONTAL band at row R

    mask_polar = _ring_mask_bgr(polar)  # (max_r, 360) uint8

    # Project: sum each ROW across all 360 columns → 1-D radial signal
    # Shape: (max_r, 1) → flatten to (max_r,)
    projection = cv2.reduce(
        mask_polar.astype(np.float32),
        1,  # reduce along axis=1 (columns)
        cv2.REDUCE_SUM,
    ).flatten()

    # Normalise to 0-255 range for consistent threshold
    max_val = projection.max()
    if max_val > 0:
        projection_norm = projection * (255.0 / max_val)
    else:
        return []

    # Find peaks → candidate ring radii in warp pixels
    peak_indices = _find_peaks(
        projection_norm,
        min_val  = 30,     # at least 30/255 of max signal
        min_dist = 8,
        smooth_r = 5,
    )

    # Match each peak to a known board ring
    matched = []
    for r_px in peak_indices:
        r_mm = r_px / scale
        for (known_mm, _name) in _RINGS:
            tol = known_mm * RADIUS_TOL
            if abs(r_mm - known_mm) < tol:
                matched.append((float(r_px), known_mm))
                break

    return matched


# ---------------------------------------------------------------------------
# Sample points around a circle in warp space
# ---------------------------------------------------------------------------
def _sample_circle_warp(
    cx: float, cy: float, r_px: float, n: int = SAMPLE_N
) -> np.ndarray:
    angles = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([
        cx + r_px * np.cos(angles),
        cy + r_px * np.sin(angles),
    ]).astype(np.float32)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def refine_calibration(
    frame: np.ndarray,
    rough_src_pts: np.ndarray,   # (4|8, 2) float32 — camera space
    calibrator,                  # BoardCalibrator instance
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Refine board calibration using polar-transform ring detection.

    Parameters
    ----------
    frame          : undistorted BGR camera frame
    rough_src_pts  : user-dragged calibration points in camera space
    calibrator     : BoardCalibrator (provides dst pixel/mm points and scale)

    Returns
    -------
    (refined_src_pts, refined_dst_mm_pts, vis) or None if detection fails.
    """
    n = len(rough_src_pts)
    if n < 4:
        return None

    dst_px = calibrator._dst_pts_8 if n == 8 else calibrator._dst_pts_4
    dst_mm = calibrator._dst_mm_pts_8 if n == 8 else calibrator._dst_mm_pts_4

    # ── Build rough homography ──────────────────────────────────────────────
    src = rough_src_pts.astype(np.float32)
    if n == 4:
        rough_H = cv2.getPerspectiveTransform(src, dst_px)
    else:
        # Use LSQR for 8 manual points so none are rejected as outliers
        rough_H, _ = cv2.findHomography(src, dst_px, 0)
    if rough_H is None:
        return None
    rough_H_inv = np.linalg.inv(rough_H)

    # ── Warp to approximate top-down view ───────────────────────────────────
    bs     = calibrator.board_size
    warped = cv2.warpPerspective(frame, rough_H, (bs, bs))

    board_cx = bs / 2.0
    board_cy = bs / 2.0
    scale    = calibrator._scale      # px / mm in warp canvas
    # Search up to slightly beyond the double ring outer edge
    max_r    = int(DOUBLE_OUTER * scale * 1.12) + 1
    max_r    = min(max_r, bs // 2)

    # ── Detect rings via polar projection ───────────────────────────────────
    matched = _detect_rings_polar(warped, board_cx, board_cy, max_r, scale)

    if len(matched) < 2:
        return None

    # ── Build correspondence arrays ─────────────────────────────────────────
    # Also build visualisation image (annotated warp view + polar projection)
    vis = warped.copy()

    # Draw the polar image inset (top-left quarter of vis)
    polar_vis = cv2.warpPolar(
        warped, (360, max_r), (board_cx, board_cy),
        max_r, cv2.WARP_POLAR_LINEAR | cv2.INTER_LINEAR,
    )  # shape (max_r, 360)
    # Rotate 90° so rows→cols for a horizontal strip that fits in the corner
    polar_strip = cv2.rotate(polar_vis, cv2.ROTATE_90_CLOCKWISE)  # (360, max_r)
    inset_h    = min(bs // 4, polar_strip.shape[0])
    inset_w    = min(bs // 3, polar_strip.shape[1])
    polar_small = cv2.resize(polar_strip, (inset_w, inset_h))
    vis[0:inset_h, 0:inset_w] = polar_small
    cv2.rectangle(vis, (0, 0), (inset_w, inset_h), (80, 80, 80), 1)

    all_src = []
    all_dst = []

    for (r_px, r_mm) in matched:
        # Highlight ring on warp view
        cv2.circle(vis, (int(board_cx), int(board_cy)), int(r_px),
                   (0, 200, 255), 2)
        cv2.putText(vis, f"{r_mm:.0f}mm",
                    (int(board_cx) + int(r_px) + 4, int(board_cy)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)

        # Mark the detected radius on the polar inset
        r_inset = int(r_px * inset_h / max_r)
        cv2.line(vis, (0, r_inset), (inset_w, r_inset), (0, 200, 255), 1)

        # Sample points in warp space on this ring's circle
        warp_pts = _sample_circle_warp(board_cx, board_cy, r_px, SAMPLE_N)

        # Map warp-space points back to camera space
        warp_h  = np.hstack([warp_pts,
                              np.ones((len(warp_pts), 1), dtype=np.float32)])
        cam_h   = (rough_H_inv @ warp_h.T).T
        cam_pts = (cam_h[:, :2] / cam_h[:, 2:3]).astype(np.float32)

        # Corresponding board-mm points
        angles  = np.linspace(0.0, 2 * math.pi, SAMPLE_N, endpoint=False)
        brd_pts = np.column_stack([
            r_mm * np.cos(angles),
            r_mm * np.sin(angles),
        ]).astype(np.float32)

        all_src.append(cam_pts)
        all_dst.append(brd_pts)

    if not all_src:
        return None

    refined_src = np.vstack(all_src)
    refined_dst = np.vstack(all_dst)

    return refined_src, refined_dst, vis


# ---------------------------------------------------------------------------
# Legacy helpers kept for backward compatibility (no longer called internally)
# ---------------------------------------------------------------------------
def _ring_mask(hsv: np.ndarray) -> np.ndarray:
    """Legacy HSV mask (Cartesian space). Kept for external callers."""
    red1 = cv2.inRange(hsv, (0,   80, 80), (10,  255, 255))
    red2 = cv2.inRange(hsv, (168, 80, 80), (180, 255, 255))
    grn  = cv2.inRange(hsv, (38,  60, 60), (85,  255, 255))
    return cv2.bitwise_or(cv2.bitwise_or(red1, red2), grn)


def _detect_circles_from_mask(mask, img_w, img_h, min_area=300,
                                min_circularity=0.4):
    """Legacy blob-based circle detector. Kept for backward compat."""
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST,   # Fix 3: was RETR_EXTERNAL
                                   cv2.CHAIN_APPROX_NONE)
    circles = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        perim = cv2.arcLength(cnt, True)
        if perim < 1:
            continue
        circularity = 4 * math.pi * area / (perim * perim)
        if circularity < min_circularity:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(cnt)
        if r < 5 or cx < 0 or cy < 0 or cx > img_w or cy > img_h:
            continue
        circles.append((float(cx), float(cy), float(r)))
    return circles
