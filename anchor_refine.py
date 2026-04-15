"""Anchor-based dartboard calibration refinement.

This module refines the exact manual calibration anchors (wire/ring
intersections) instead of estimating homography from ring ellipses.
That keeps auto/refine aligned with the same 4/8-point model used by
manual calibration.
"""

from __future__ import annotations

import base64
import math
from typing import Optional, Tuple

import cv2
import numpy as np


def _encode_preview(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    if max(h, w) > 960:
        s = 960 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _dst_points(calibrator, n_points: int) -> np.ndarray:
    return calibrator._dst_pts_8 if n_points == 8 else calibrator._dst_pts_4


def _homography_from_points(src_pts: np.ndarray,
                            dst_pts: np.ndarray) -> Optional[np.ndarray]:
    src = np.asarray(src_pts, dtype=np.float32)
    dst = np.asarray(dst_pts, dtype=np.float32)
    if len(src) == 4:
        try:
            return cv2.getPerspectiveTransform(src, dst)
        except cv2.error:
            return None
    H, _ = cv2.findHomography(src, dst, 0)
    return H


def _prepare_corner_maps(warped: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    edge = cv2.Canny(gray, 60, 160, apertureSize=3, L2gradient=True)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)
    grad = cv2.normalize(grad, None, 0.0, 1.0, cv2.NORM_MINMAX)
    return gray, 0.65 * grad + 0.35 * (edge.astype(np.float32) / 255.0)


def _sample_map(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    map_x = np.asarray(xs, dtype=np.float32).reshape(1, -1)
    map_y = np.asarray(ys, dtype=np.float32).reshape(1, -1)
    sampled = cv2.remap(
        img.astype(np.float32),
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT101,
    )
    return sampled.reshape(-1)


def _polar_point(cx: float, cy: float, radius: float, theta: float) -> np.ndarray:
    return np.array([cx + radius * math.cos(theta),
                     cy - radius * math.sin(theta)], dtype=np.float32)


def _pick_anchor(score_map: np.ndarray,
                 expected_xy: np.ndarray,
                 center_xy: Tuple[float, float],
                 radius_px: int,
                 line_r_start: float,
                 line_r_end: float) -> Tuple[np.ndarray, float]:
    cx0, cy0 = center_xy
    px, py = float(expected_xy[0]), float(expected_xy[1])
    dx = px - cx0
    dy = cy0 - py
    theta0 = math.atan2(dy, dx)
    r0 = math.hypot(dx, dy)

    theta_candidates = np.linspace(theta0 - math.radians(4.5),
                                   theta0 + math.radians(4.5), 49)
    radial_line = np.linspace(line_r_start, line_r_end, 96, dtype=np.float32)
    best_theta = theta0
    best_theta_score = -1.0
    for theta in theta_candidates:
        xs = cx0 + radial_line * math.cos(theta)
        ys = cy0 - radial_line * math.sin(theta)
        resp = _sample_map(score_map, xs, ys)
        score = float(np.mean(resp) + 0.35 * np.max(resp))
        if score > best_theta_score:
            best_theta_score = score
            best_theta = theta

    radius_candidates = np.linspace(max(8.0, r0 - radius_px),
                                    r0 + radius_px, 65)
    best_radius = r0
    best_radius_score = -1.0
    arc_thetas = np.linspace(best_theta - math.radians(3.2),
                             best_theta + math.radians(3.2), 31)
    for radius in radius_candidates:
        xs = cx0 + radius * np.cos(arc_thetas)
        ys = cy0 - radius * np.sin(arc_thetas)
        resp = _sample_map(score_map, xs, ys)
        score = float(np.mean(resp) + 0.45 * np.max(resp))
        if score > best_radius_score:
            best_radius_score = score
            best_radius = float(radius)

    theta_candidates_2 = np.linspace(best_theta - math.radians(2.0),
                                     best_theta + math.radians(2.0), 33)
    radial_line_2 = np.linspace(max(line_r_start, best_radius - radius_px * 0.9),
                                min(line_r_end, best_radius + radius_px * 0.9),
                                80, dtype=np.float32)
    for theta in theta_candidates_2:
        xs = cx0 + radial_line_2 * math.cos(theta)
        ys = cy0 - radial_line_2 * math.sin(theta)
        resp = _sample_map(score_map, xs, ys)
        score = float(np.mean(resp) + 0.4 * np.max(resp))
        if score > best_theta_score:
            best_theta_score = score
            best_theta = theta

    best_xy = _polar_point(cx0, cy0, best_radius, best_theta)
    shift = float(np.linalg.norm(best_xy - np.array([px, py], dtype=np.float32)))
    return best_xy, shift


def refine_anchor_points(frame: np.ndarray,
                         rough_src_pts: np.ndarray,
                         calibrator,
                         search_radius_px: Optional[int] = None,
                         iters: int = 2) -> Optional[Tuple[np.ndarray, np.ndarray, dict]]:
    """Refine 4/8 rough camera-space anchor points.

    Returns (refined_src_pts, vis, metrics).
    """
    src = np.asarray(rough_src_pts, dtype=np.float32)
    n_points = len(src)
    if n_points not in (4, 8):
        return None

    dst_px = _dst_points(calibrator, n_points).astype(np.float32)
    H = _homography_from_points(src, dst_px)
    if H is None:
        return None

    bs = calibrator.board_size
    radius = search_radius_px or max(16, int(bs * (0.06 if n_points == 8 else 0.07)))
    latest_metrics = {"mean_shift_px": 0.0, "max_shift_px": 0.0}
    latest_warped = None
    latest_refined_warped = None

    for step in range(max(1, iters)):
        warped = cv2.warpPerspective(frame, H, (bs, bs))
        _, score_map = _prepare_corner_maps(warped)
        center_xy = (bs / 2.0, bs / 2.0)
        r_bull_outer = calibrator._radius_mm_to_px(15.9) + 4.0
        r_double_outer = calibrator._radius_mm_to_px(170.0) + 8.0

        refined_warped = []
        shifts = []
        for dst in dst_px:
            dst_r = float(np.hypot(dst[0] - center_xy[0], dst[1] - center_xy[1]))
            best_xy, shift = _pick_anchor(
                score_map,
                dst,
                center_xy,
                radius,
                line_r_start=max(r_bull_outer, dst_r - radius * 1.4),
                line_r_end=min(r_double_outer, dst_r + radius * 1.4),
            )
            refined_warped.append(best_xy)
            shifts.append(shift)

        refined_warped = np.asarray(refined_warped, dtype=np.float32)
        H_inv = np.linalg.inv(H)
        refined_src = cv2.perspectiveTransform(
            refined_warped.reshape(-1, 1, 2),
            H_inv.astype(np.float64),
        ).reshape(-1, 2).astype(np.float32)

        H_next = _homography_from_points(refined_src, dst_px)
        if H_next is None:
            return None

        H = H_next
        src = refined_src
        latest_metrics = {
            "mean_shift_px": float(np.mean(shifts)),
            "max_shift_px": float(np.max(shifts)),
        }
        latest_warped = warped
        latest_refined_warped = refined_warped
        radius = max(10, int(radius * 0.65))

    vis = frame.copy()
    for x, y in rough_src_pts:
        cv2.circle(vis, (int(x), int(y)), 5, (80, 80, 255), 2, cv2.LINE_AA)
    for x, y in src:
        cv2.circle(vis, (int(x), int(y)), 5, (0, 255, 255), -1, cv2.LINE_AA)
    if latest_warped is not None and latest_refined_warped is not None:
        warped_vis = latest_warped.copy()
        warped_vis = calibrator.draw_wireframe(warped_vis)
        for dst in dst_px:
            cv2.circle(warped_vis, (int(dst[0]), int(dst[1])), 4, (255, 120, 0), 1, cv2.LINE_AA)
        for q in latest_refined_warped:
            cv2.circle(warped_vis, (int(q[0]), int(q[1])), 4, (0, 255, 255), -1, cv2.LINE_AA)
        inset_w = min(360, warped_vis.shape[1])
        inset_h = int(warped_vis.shape[0] * (inset_w / warped_vis.shape[1]))
        inset = cv2.resize(warped_vis, (inset_w, inset_h))
        vis[10:10 + inset_h, 10:10 + inset_w] = inset

    cv2.putText(
        vis,
        f"anchor refine  mean_shift={latest_metrics['mean_shift_px']:.1f}px  max={latest_metrics['max_shift_px']:.1f}px",
        (10, vis.shape[0] - 14),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 220, 255),
        2,
        cv2.LINE_AA,
    )
    return src, vis, latest_metrics


def auto_calibrate_from_anchors(frame: np.ndarray,
                                calibrator) -> dict:
    """Auto-calibrate by detecting rough 8 anchors, then refining them locally."""
    rough_src = calibrator.auto_detect_anchors(frame, n_points=8)
    if rough_src is None or len(rough_src) != 8:
        preview = frame.copy()
        cv2.putText(preview, "FAILED: rough anchor detection failed",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 220, 255), 2, cv2.LINE_AA)
        return {
            "success": False,
            "reason": "rough_anchor_detection_failed",
            "rings_found": 0,
            "preview_b64": _encode_preview(preview),
        }

    result = refine_anchor_points(frame, rough_src, calibrator,
                                  search_radius_px=max(22, int(calibrator.board_size * 0.075)),
                                  iters=2)
    if result is None:
        preview = calibrator.draw_anchor_points(frame, rough_src)
        cv2.putText(preview, "FAILED: anchor refinement failed",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 220, 255), 2, cv2.LINE_AA)
        return {
            "success": False,
            "reason": "anchor_refinement_failed",
            "rings_found": 0,
            "preview_b64": _encode_preview(preview),
        }

    refined_src, vis, metrics = result
    return {
        "success": True,
        "rings_found": 8,
        "reprojection_error": round(metrics["mean_shift_px"], 3),
        "points": refined_src.tolist(),
        "n_points": 8,
        "preview_b64": _encode_preview(vis),
    }
