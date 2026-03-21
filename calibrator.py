"""ThrowVision – Board Calibrator.

Handles N-point perspective transform, wireframe verification overlay,
circular mask generation, board-wobble micro-correction, and
auto-anchor detection.

Calibration data is saved as .npz with the source points and the
resolution they were captured at.  When loaded at a different resolution
the source points are automatically rescaled and the transform recomputed.

Point counts supported:
  4  pts → cv2.getPerspectiveTransform       (legacy / minimum)
  5+ pts → cv2.findHomography(RANSAC)        (recommended: 8 pts)
"""

import math
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

from config import ConfigManager


# ---------------------------------------------------------------------------
# Standard dartboard geometry (all radii in mm from centre)
# ---------------------------------------------------------------------------
BULL_INNER_R  = 6.35
BULL_OUTER_R  = 15.9
TRIPLE_INNER  = 99.0
TRIPLE_OUTER  = 107.0
DOUBLE_INNER  = 162.0
DOUBLE_OUTER  = 170.0

# Counter-clockwise order starting from 20 (top of the board = 90°)
SECTOR_ORDER: List[int] = [
    20, 5, 12, 9, 14, 11, 8, 16, 7, 19,
    3, 17, 2, 15, 10, 6, 13, 4, 18, 1,
]
SECTOR_ANGLE = 18.0  # degrees per sector


def _sector_boundary_angles() -> List[float]:
    start = 90.0 - SECTOR_ANGLE / 2       # 20 at top (90°)
    return [(start + i * SECTOR_ANGLE) % 360.0 for i in range(20)]


def _wire_angle(sector_a: int, sector_b: int) -> float:
    boundaries = _sector_boundary_angles()
    for i in range(20):
        sa = SECTOR_ORDER[i - 1]
        sb = SECTOR_ORDER[i]
        if {sa, sb} == {sector_a, sector_b}:
            return boundaries[i]
    raise ValueError(f"Sectors {sector_a}/{sector_b} are not adjacent")


def _board_point_mm(angle_deg: float, radius_mm: float) -> Tuple[float, float]:
    rad = math.radians(angle_deg)
    return (radius_mm * math.cos(rad), radius_mm * math.sin(rad))


# ---------------------------------------------------------------------------
# 8-point anchor definitions (4 outer double + 4 outer triple)
# ---------------------------------------------------------------------------
_ANCHOR_ANGLES = [
    _wire_angle(20, 1),   # top-right
    _wire_angle(6, 10),   # right
    _wire_angle(3, 19),   # bottom-right
    _wire_angle(11, 14),  # left
]

# Outer set: on the double ring outer edge
# Inner set: on the triple ring outer edge (same angles, smaller radius)
ANCHOR_DST_MM_8 = [
    _board_point_mm(a, DOUBLE_OUTER) for a in _ANCHOR_ANGLES
] + [
    _board_point_mm(a, TRIPLE_OUTER) for a in _ANCHOR_ANGLES
]

ANCHOR_DST_MM_4 = [_board_point_mm(a, DOUBLE_OUTER) for a in _ANCHOR_ANGLES]


class BoardCalibrator:
    """N-point perspective calibration, wireframe drawing, and mask.

    Supports 4-point (legacy) and 8-point (RANSAC) calibration.
    Also provides board-wobble micro-correction via homography delta.
    """

    # Full dartboard diameter including number ring (225.5mm radius)
    CANVAS_MM = 451.0

    def __init__(self, cfg: ConfigManager, cam_id: int = 0) -> None:
        self.cfg = cfg
        self.cam_id = cam_id
        self.matrix_path = cfg.matrix_path_for(cam_id)
        self.w, self.h = cfg.resolution
        self.board_size = min(self.w, self.h)
        self._M: Optional[np.ndarray] = None
        self._M_inv: Optional[np.ndarray] = None
        self._M_mm: Optional[np.ndarray] = None
        self._M_wobble: Optional[np.ndarray] = None   # micro-correction delta
        self._mask: Optional[np.ndarray] = None
        self._raw_mask: Optional[np.ndarray] = None
        self._scale = self.board_size / self.CANVAS_MM
        self._src_pts: Optional[np.ndarray] = None
        self._wireframe_prims = None

        cx = cy = self.board_size / 2

        # Destination pixel points for 4 anchors (double ring)
        self._dst_pts_4 = np.array([
            self._mm_to_px(*_board_point_mm(a, DOUBLE_OUTER), cx, cy)
            for a in _ANCHOR_ANGLES
        ], dtype=np.float32)

        # Destination pixel points for 8 anchors (double + triple rings)
        self._dst_pts_8 = np.array([
            self._mm_to_px(*pt, cx, cy) for pt in ANCHOR_DST_MM_8
        ], dtype=np.float32)

        # Keep backward compat alias
        self._dst_pts = self._dst_pts_4

        # MM destination points (for direct raw→mm transform)
        self._dst_mm_pts_4 = np.array(ANCHOR_DST_MM_4, dtype=np.float32)
        self._dst_mm_pts_8 = np.array(ANCHOR_DST_MM_8, dtype=np.float32)

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------
    def _mm_to_px(self, xmm: float, ymm: float,
                  cx: float, cy: float) -> Tuple[float, float]:
        return (cx + xmm * self._scale, cy - ymm * self._scale)

    def _radius_mm_to_px(self, r_mm: float) -> float:
        return r_mm * self._scale

    # ------------------------------------------------------------------
    # Internal homography builder
    # ------------------------------------------------------------------
    def _build_homography(self, src: np.ndarray,
                          dst_px: np.ndarray,
                          dst_mm: np.ndarray) -> None:
        """Compute M, M_inv, and M_mm from src→dst mappings."""
        n = len(src)
        if n == 4:
            self._M    = cv2.getPerspectiveTransform(src, dst_px)
            self._M_mm = cv2.getPerspectiveTransform(src, dst_mm)
        else:
            M, mask = cv2.findHomography(src, dst_px, cv2.RANSAC, 3.0)
            if M is None:
                raise ValueError("RANSAC failed to find a valid homography")
            self._M = M
            Mmm, _ = cv2.findHomography(src, dst_mm, cv2.RANSAC, 3.0)
            self._M_mm = Mmm if Mmm is not None else self._M
        self._M_inv = np.linalg.inv(self._M)
        self._M_wobble = None   # reset wobble correction on recalibrate
        self._wireframe_prims = None   # invalidate cached primitives

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------
    def load_cached(self) -> bool:
        """Load saved calibration.  Supports 4-pt and 8-pt npz formats."""
        if os.path.isfile(self.matrix_path):
            data = np.load(self.matrix_path, allow_pickle=True)
            src_pts = data["src_points"]
            saved_w = int(data["resolution"][0])
            saved_h = int(data["resolution"][1])

            if saved_w != self.w or saved_h != self.h:
                sx = self.w / saved_w
                sy = self.h / saved_h
                src_pts = src_pts.copy()
                src_pts[:, 0] *= sx
                src_pts[:, 1] *= sy

            self._src_pts = src_pts
            n = len(src_pts)
            dst_px = self._dst_pts_8 if n == 8 else self._dst_pts_4
            dst_mm = self._dst_mm_pts_8 if n == 8 else self._dst_mm_pts_4
            try:
                self._build_homography(src_pts, dst_px, dst_mm)
            except Exception as e:
                print(f"[CAL] Camera {self.cam_id}: homography error: {e}")
                return False
            self._build_mask()
            self._loaded_path = self.matrix_path
            return True

        # Legacy .npy fallback
        legacy = self.matrix_path.replace(".npz", ".npy")
        old_template = f"transformation_matrix_{self.cam_id}.npy"
        for path in [legacy, old_template]:
            if os.path.isfile(path):
                print(f"[CAL] Camera {self.cam_id}: legacy file {path} — please recalibrate")
                return False

        return False

    def calibrate(self, src_points: np.ndarray) -> None:
        """Calibrate from N source points (4 or 8)."""
        src = np.asarray(src_points, dtype=np.float32)
        n = len(src)
        assert n in (4, 8), f"Need 4 or 8 source points, got {n}"

        self._src_pts = src
        dst_px = self._dst_pts_8 if n == 8 else self._dst_pts_4
        dst_mm = self._dst_mm_pts_8 if n == 8 else self._dst_mm_pts_4
        self._build_homography(src, dst_px, dst_mm)

        np.savez(self.matrix_path,
                 matrix=self._M,
                 src_points=src,
                 resolution=np.array([self.w, self.h]))
        self._build_mask()

    @property
    def is_calibrated(self) -> bool:
        return self._M is not None

    @property
    def matrix(self) -> Optional[np.ndarray]:
        return self._M

    @property
    def matrix_inv(self) -> Optional[np.ndarray]:
        return self._M_inv

    # ------------------------------------------------------------------
    # Board-wobble micro-correction
    # ------------------------------------------------------------------
    def apply_wobble_correction(self, frame_before: np.ndarray,
                                frame_after: np.ndarray,
                                search_band: int = 40) -> bool:
        """Estimate board shift between two frames and apply as a delta.

        Compares a thin ring-shaped ROI around the board edge in warped
        space using phase correlation.  Returns True if correction applied.
        Only updates self._M_wobble (not the persistent self._M).
        """
        if self._M is None:
            return False
        try:
            s = self.board_size
            cx = cy = s // 2
            r_outer = int(self._radius_mm_to_px(DOUBLE_OUTER))
            r_inner = max(r_outer - search_band, 1)

            # Warp both frames
            def _warp(f):
                w = cv2.warpPerspective(f, self._M, (s, s))
                gray = cv2.cvtColor(w, cv2.COLOR_BGR2GRAY) if w.ndim == 3 else w
                # Mask to ring ROI
                mask = np.zeros((s, s), dtype=np.uint8)
                cv2.circle(mask, (cx, cy), r_outer, 255, -1)
                cv2.circle(mask, (cx, cy), r_inner, 0, -1)
                return (gray.astype(np.float32) / 255.0) * (mask / 255.0)

            f_before = _warp(frame_before)
            f_after  = _warp(frame_after)

            # Phase correlation → (dx, dy) shift
            shift, _ = cv2.phaseCorrelate(f_before, f_after)
            dx, dy = shift

            # Ignore large shifts (>8px) — likely a real disturbance, not wobble
            if abs(dx) > 8 or abs(dy) > 8:
                return False
            if abs(dx) < 0.3 and abs(dy) < 0.3:
                return False   # negligible

            # Build correction: translate warped space by (-dx, -dy)
            # This must be applied after the main homography
            T = np.array([[1, 0, -dx],
                          [0, 1, -dy],
                          [0, 0,  1]], dtype=np.float64)
            self._M_wobble = T
            return True
        except Exception as e:
            print(f"[CAL] Wobble correction failed: {e}")
            return False

    def clear_wobble_correction(self) -> None:
        self._M_wobble = None

    def _effective_M(self) -> np.ndarray:
        """Return homography with wobble correction applied if present."""
        if self._M_wobble is not None:
            return self._M_wobble @ self._M
        return self._M

    # ------------------------------------------------------------------
    # Un-warp / point transforms
    # ------------------------------------------------------------------
    def unwarp(self, frame: np.ndarray) -> np.ndarray:
        assert self._M is not None, "Not calibrated"
        return cv2.warpPerspective(frame, self._effective_M(),
                                   (self.board_size, self.board_size))

    def cam_to_board(self, x: float, y: float) -> Tuple[float, float]:
        pt = np.array([[[x, y]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self._effective_M())
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    def board_px_to_mm(self, bx: float, by: float) -> Tuple[float, float]:
        c = self.board_size / 2
        return ((bx - c) / self._scale, (c - by) / self._scale)

    def transform_to_mm(self, raw_x: float, raw_y: float) -> Tuple[float, float]:
        """Transform raw camera pixel → dartboard mm (center=0,0)."""
        assert self._M_mm is not None, "Not calibrated"
        M_eff = self._M_wobble @ self._M_mm if self._M_wobble is not None else self._M_mm
        pt = np.array([[[raw_x, raw_y]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, M_eff)
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    # ------------------------------------------------------------------
    # Auto-anchor detection
    # ------------------------------------------------------------------
    def auto_detect_anchors(self, frame: np.ndarray,
                            n_points: int = 8) -> Optional[np.ndarray]:
        """Detect board anchor points automatically from a camera frame.

        Strategy:
        1. HoughCircles to find the outer double ring
        2. Canny + ellipse fit as fallback
        3. Project expected anchor angles onto detected circle/ellipse
        4. Edge-snap each projected point to the nearest wire edge

        Returns float32 array of shape (n_points, 2) or None on failure.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        h, w = gray.shape

        blurred = cv2.GaussianBlur(gray, (7, 7), 2)

        # ── Step 1: HoughCircles ────────────────────────────────────
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=1.2,
            minDist=min(w, h) // 4,
            param1=80, param2=50,
            minRadius=int(min(w, h) * 0.15),
            maxRadius=int(min(w, h) * 0.48),
        )

        best_cx, best_cy, best_rx, best_ry, best_angle = None, None, None, None, 0.0

        if circles is not None:
            circles = np.round(circles[0]).astype(int)
            img_cx, img_cy = w / 2, h / 2
            best_dist = float('inf')
            for cx, cy, r in circles:
                d = math.hypot(cx - img_cx, cy - img_cy)
                if d < best_dist:
                    best_dist = d
                    best_cx, best_cy = float(cx), float(cy)
                    best_rx = best_ry = float(r)

        # ── Step 2: Ellipse fit fallback ────────────────────────────
        if best_cx is None:
            edges = cv2.Canny(blurred, 30, 80)
            # Dilate to connect broken edges
            edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
            contours, _ = cv2.findContours(
                edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            best_area = 0
            for cnt in contours:
                if len(cnt) < 50:
                    continue
                area = cv2.contourArea(cnt)
                if area < (min(w, h) * 0.08) ** 2:
                    continue
                try:
                    (ecx, ecy), (ma, Mi), angle = cv2.fitEllipse(cnt)
                    ratio = min(ma, Mi) / max(ma, Mi) if max(ma, Mi) > 0 else 0
                    if ratio > 0.55 and area > best_area:
                        best_area = area
                        best_cx, best_cy = float(ecx), float(ecy)
                        best_rx = float(max(ma, Mi) / 2)
                        best_ry = float(min(ma, Mi) / 2)
                        best_angle = float(angle)
                except Exception:
                    continue

        if best_cx is None:
            return None

        # ── Step 3: Project anchors onto detected circle/ellipse ────
        angles = _ANCHOR_ANGLES if n_points >= 4 else _ANCHOR_ANGLES[:n_points]
        if n_points == 8:
            angles_all = angles + angles
            radii_scale = [1.0] * 4 + [TRIPLE_OUTER / DOUBLE_OUTER] * 4
        else:
            angles_all = angles
            radii_scale = [1.0] * 4

        raw_pts = []
        for ang, rs in zip(angles_all, radii_scale):
            rad = math.radians(ang)
            px = best_cx + best_rx * rs * math.cos(rad)
            py = best_cy - best_ry * rs * math.sin(rad)
            raw_pts.append([px, py])

        # ── Step 4: Edge-snap ────────────────────────────────────────
        edges = cv2.Canny(blurred, 25, 70)
        snapped = []
        snap_radius = max(12, int(best_rx * 0.06))
        for px, py in raw_pts:
            snapped.append(self._snap_to_edge(edges, px, py, snap_radius))

        result = np.array(snapped, dtype=np.float32)
        return result

    @staticmethod
    def _snap_to_edge(edge_map: np.ndarray,
                      px: float, py: float,
                      radius: int) -> Tuple[float, float]:
        """Move (px,py) to nearest edge pixel within radius px."""
        h, w = edge_map.shape
        x0 = max(0, int(px) - radius)
        y0 = max(0, int(py) - radius)
        x1 = min(w, int(px) + radius + 1)
        y1 = min(h, int(py) + radius + 1)
        roi = edge_map[y0:y1, x0:x1]
        ys, xs = np.where(roi > 0)
        if len(xs) == 0:
            return (px, py)   # no edge found — keep original
        dists = (xs + x0 - px) ** 2 + (ys + y0 - py) ** 2
        idx = int(np.argmin(dists))
        return (float(xs[idx] + x0), float(ys[idx] + y0))

    # ------------------------------------------------------------------
    # Masks
    # ------------------------------------------------------------------
    def _build_mask(self) -> None:
        s = self.board_size
        mask = np.zeros((s, s), dtype=np.uint8)
        c = s // 2
        r = int(self._radius_mm_to_px(self.CANVAS_MM / 2)) + 5
        cv2.circle(mask, (c, c), r, 255, -1)
        self._mask = mask

        if self._M_inv is not None:
            raw = cv2.warpPerspective(mask, self._M_inv, (self.w, self.h))
            _, self._raw_mask = cv2.threshold(raw, 127, 255, cv2.THRESH_BINARY)
        else:
            self._raw_mask = mask

    @property
    def board_mask(self) -> np.ndarray:
        assert self._mask is not None, "Not calibrated"
        return self._mask

    @property
    def raw_mask(self) -> np.ndarray:
        assert self._raw_mask is not None, "Not calibrated"
        return self._raw_mask

    @property
    def board_centre_cam(self) -> Tuple[float, float]:
        """Bullseye centre in raw camera pixel coordinates."""
        assert self._M_inv is not None, "Not calibrated"
        c = self.board_size / 2
        pt = np.array([[[c, c]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self._M_inv)
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    def apply_mask(self, frame: np.ndarray,
                   raw: bool = False) -> np.ndarray:
        m = self._raw_mask if raw else self._mask
        if frame.ndim == 3:
            return cv2.bitwise_and(frame, frame, mask=m)
        return cv2.bitwise_and(frame, m)

    # ------------------------------------------------------------------
    # Wireframe drawing
    # ------------------------------------------------------------------
    def get_wireframe_primitives(self) -> dict:
        if self._wireframe_prims is not None:
            return self._wireframe_prims
        cx = cy = self.board_size // 2
        circles = []
        for r_mm in (BULL_INNER_R, BULL_OUTER_R, TRIPLE_INNER,
                     TRIPLE_OUTER, DOUBLE_INNER, DOUBLE_OUTER):
            circles.append(int(self._radius_mm_to_px(r_mm)))
        inner_r = int(self._radius_mm_to_px(BULL_OUTER_R))
        outer_r = int(self._radius_mm_to_px(DOUBLE_OUTER))
        lines = []
        for angle in _sector_boundary_angles():
            rad = math.radians(angle)
            x1 = int(cx + inner_r * math.cos(rad))
            y1 = int(cy - inner_r * math.sin(rad))
            x2 = int(cx + outer_r * math.cos(rad))
            y2 = int(cy - outer_r * math.sin(rad))
            lines.append(((x1, y1), (x2, y2)))
        self._wireframe_prims = {'center': (cx, cy), 'circles': circles, 'lines': lines}
        return self._wireframe_prims

    def draw_wireframe(self, img: np.ndarray) -> np.ndarray:
        out = img.copy()
        cx = cy = self.board_size // 2
        colour = (255, 255, 0)
        for r_mm in (BULL_INNER_R, BULL_OUTER_R, TRIPLE_INNER,
                     TRIPLE_OUTER, DOUBLE_INNER, DOUBLE_OUTER):
            r_px = int(self._radius_mm_to_px(r_mm))
            cv2.circle(out, (cx, cy), r_px, colour, 1, cv2.LINE_AA)
        inner_r = int(self._radius_mm_to_px(BULL_OUTER_R))
        outer_r = int(self._radius_mm_to_px(DOUBLE_OUTER))
        for angle in _sector_boundary_angles():
            rad = math.radians(angle)
            x1 = int(cx + inner_r * math.cos(rad))
            y1 = int(cy - inner_r * math.sin(rad))
            x2 = int(cx + outer_r * math.cos(rad))
            y2 = int(cy - outer_r * math.sin(rad))
            cv2.line(out, (x1, y1), (x2, y2), colour, 1, cv2.LINE_AA)
        return out

    def draw_anchor_points(self, img: np.ndarray,
                           src_points: np.ndarray) -> np.ndarray:
        out = img.copy()
        for pt in src_points:
            cv2.circle(out, (int(pt[0]), int(pt[1])),
                       6, (0, 255, 255), -1, cv2.LINE_AA)
        return out
