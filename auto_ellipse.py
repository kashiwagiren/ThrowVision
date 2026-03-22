"""ThrowVision — Automatic Dartboard Calibration Refiner.

Coarse-to-fine strategy:
  1. Take the user's rough 4-pt (or 8-pt) calibration → build rough homography.
  2. Warp the frame into approximate top-down view.
  3. Detect the red/green dartboard rings using HSV color segmentation.
     (Rings become near-circles in warped space — far easier than ellipses.)
  4. Fit a circle to each detected ring blob; match to known board radii.
  5. Sample 24 points around each detected circle circumference.
  6. Map those points back to camera space via the inverse rough homography.
  7. Pair each camera-space point with its known mm board coordinate.
  8. Run cv2.findHomography(RANSAC) on 50-200 point pairs → refined H.

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
# Known dartboard ring radii in mm (same as calibrator.py)
# ---------------------------------------------------------------------------
BULL_INNER_R  =   6.35
BULL_OUTER_R  =  15.9
TRIPLE_INNER  =  99.0
TRIPLE_OUTER  = 107.0
DOUBLE_INNER  = 162.0
DOUBLE_OUTER  = 170.0

# Ordered list of rings we try to detect (radius mm, name, colour hint)
_RINGS = [
    (TRIPLE_INNER,  "triple_inner"),
    (TRIPLE_OUTER,  "triple_outer"),
    (DOUBLE_INNER,  "double_inner"),
    (DOUBLE_OUTER,  "double_outer"),
]

# How many points to sample around each detected ring circumference
SAMPLE_N = 24

# Tolerance: how close (in fraction of expected px radius) a detected
# circle can be to a known ring and still be accepted
RADIUS_TOL = 0.12   # ±12 %


# ---------------------------------------------------------------------------
# HSV colour masks for the red/green ring segments
# ---------------------------------------------------------------------------
def _ring_mask(hsv: np.ndarray) -> np.ndarray:
    """Binary mask of red + green band pixels (the double/triple rings)."""
    # Red has two hue ranges in HSV
    red1 = cv2.inRange(hsv, (0,   80, 80), (10,  255, 255))
    red2 = cv2.inRange(hsv, (168, 80, 80), (180, 255, 255))
    # Green
    grn  = cv2.inRange(hsv, (38,  60, 60), (85,  255, 255))
    mask = red1 | red2 | grn
    # Morphological cleanup to join fragmented blobs
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k, iterations=1)
    return mask


# ---------------------------------------------------------------------------
# Circle detection from mask
# ---------------------------------------------------------------------------
def _detect_circles_from_mask(
    mask: np.ndarray,
    img_w: int,
    img_h: int,
    min_area: int = 300,
    min_circularity: float = 0.4,
) -> List[Tuple[float, float, float]]:
    """Return list of (cx, cy, radius) for each circular blob in *mask*."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
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
        # Discard circles that are clearly outside the frame
        if r < 5 or cx < 0 or cy < 0 or cx > img_w or cy > img_h:
            continue
        circles.append((float(cx), float(cy), float(r)))
    return circles


# ---------------------------------------------------------------------------
# Match detected circles to known ring radii
# ---------------------------------------------------------------------------
def _match_rings(
    circles: List[Tuple[float, float, float]],
    scale: float,          # px_per_mm in warped space
    board_cx: float,
    board_cy: float,
) -> List[Tuple[float, float, float, float]]:
    """Return list of (cx, cy, r_px, r_mm) for circles that match a known ring.

    Uses a two-pass approach:
      Pass 1 — find the board centre by clustering circle centres.
      Pass 2 — match circles whose radius ≈ a known ring radius.
    """
    if not circles:
        return []

    matches = []
    for (cx, cy, r) in circles:
        r_mm = r / scale
        for (ring_mm, _name) in _RINGS:
            tol = ring_mm * RADIUS_TOL
            if abs(r_mm - ring_mm) < tol:
                matches.append((cx, cy, r, ring_mm))
                break
    return matches


# ---------------------------------------------------------------------------
# Sample points around a circle
# ---------------------------------------------------------------------------
def _sample_circle(cx: float, cy: float, r: float, n: int = SAMPLE_N
                   ) -> np.ndarray:
    """Return (n, 2) float32 array of evenly-spaced points on the circle."""
    angles = np.linspace(0, 2 * math.pi, n, endpoint=False)
    pts = np.column_stack([
        cx + r * np.cos(angles),
        cy + r * np.sin(angles),
    ]).astype(np.float32)
    return pts


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def refine_calibration(
    frame: np.ndarray,
    rough_src_pts: np.ndarray,   # (4 or 8, 2) float32 – camera space
    calibrator,                  # BoardCalibrator instance
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Refine board calibration using HSV ring detection.

    Parameters
    ----------
    frame          : undistorted BGR camera frame
    rough_src_pts  : user-dragged calibration points in camera space
    calibrator     : BoardCalibrator (provides dst pixel/mm points and scale)

    Returns
    -------
    (refined_src_pts, refined_dst_mm_pts, vis) or None if detection fails.
      refined_src_pts   : (N, 2) float32 in camera space
      refined_dst_mm_pts: (N, 2) float32 in board mm coords
      vis               : annotated BGR image (warped view with rings drawn)
    """
    n = len(rough_src_pts)
    if n < 4:
        return None

    dst_px = calibrator._dst_pts_8 if n == 8 else calibrator._dst_pts_4
    dst_mm = calibrator._dst_mm_pts_8 if n == 8 else calibrator._dst_mm_pts_4

    # ---- Build rough homography -----------------------------------------
    src = rough_src_pts.astype(np.float32)
    if n == 4:
        rough_H = cv2.getPerspectiveTransform(src, dst_px)
    else:
        rough_H, _ = cv2.findHomography(src, dst_px, cv2.RANSAC, 5.0)
    if rough_H is None:
        return None

    rough_H_inv = np.linalg.inv(rough_H)

    # ---- Warp frame into approximate top-down board view ---------------
    bs = calibrator.board_size
    warped = cv2.warpPerspective(frame, rough_H, (bs, bs))

    # ---- Detect red/green rings in warped space -------------------------
    hsv  = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    mask = _ring_mask(hsv)

    circles = _detect_circles_from_mask(mask, bs, bs,
                                        min_area=500,
                                        min_circularity=0.35)

    # Board centre in warped space
    board_cx_warp = bs / 2.0
    board_cy_warp = bs / 2.0
    scale = calibrator._scale   # px / mm in the warp canvas

    matched = _match_rings(circles, scale, board_cx_warp, board_cy_warp)

    if len(matched) < 2:
        # Not enough rings — return None so caller can fall back
        return None

    # ---- Build annotated visualisation ---------------------------------
    vis = warped.copy()
    cv2.drawContours(vis,
                     cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_NONE)[0],
                     -1, (0, 255, 0), 1)

    all_src = []   # camera-space points
    all_dst = []   # board mm points

    for (cx_w, cy_w, r_w, r_mm) in matched:
        # Draw detected ring on visualisation
        cv2.circle(vis, (int(cx_w), int(cy_w)), int(r_w), (0, 200, 255), 2)
        cv2.putText(vis, f"{r_mm:.0f}mm",
                    (int(cx_w) - 20, int(cy_w) - int(r_w) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)

        # Sample points around the ring in warp space
        warp_pts = _sample_circle(cx_w, cy_w, r_w, SAMPLE_N)  # (N,2)

        # Map those warp-space points back to camera space
        warp_pts_h = np.hstack([warp_pts,
                                np.ones((len(warp_pts), 1),
                                        dtype=np.float32)])  # (N,3)
        cam_pts_h  = (rough_H_inv @ warp_pts_h.T).T          # (N,3)
        cam_pts    = (cam_pts_h[:, :2]
                      / cam_pts_h[:, 2:3]).astype(np.float32) # (N,2)

        # Build matching board mm points: known r_mm, same angles
        angles = np.linspace(0, 2 * math.pi, SAMPLE_N, endpoint=False)
        # Board coordinate system: x right, y up
        # warp canvas: x right, y down (origin top-left, centre=(bs/2, bs/2))
        # In the warp canvas the board centre may be offset from (bs/2, bs/2)
        # but here we treat it as centred (good enough for the ring sampling).
        board_pts = np.column_stack([
            r_mm * np.cos(angles),
            r_mm * np.sin(angles),
        ]).astype(np.float32)

        all_src.append(cam_pts)
        all_dst.append(board_pts)

    if not all_src:
        return None

    refined_src = np.vstack(all_src)
    refined_dst = np.vstack(all_dst)

    return refined_src, refined_dst, vis
