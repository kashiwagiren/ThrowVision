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
    _wire_angle(11, 14),  # left
    _wire_angle(3, 19),   # bottom-right
    _wire_angle(6, 10),   # right
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
        self._radial_poly = None  # per-camera radial bias correction

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

    def _calibration_payload(self, include_matrix_mm: bool = False) -> dict:
        payload = {
            "matrix": self._M,
            "src_points": self._src_pts,
            "resolution": np.array([self.w, self.h]),
        }
        if include_matrix_mm and self._M_mm is not None:
            payload["matrix_mm"] = self._M_mm
        return payload

    def _anchor_sets(self, n_points: int) -> Tuple[np.ndarray, np.ndarray]:
        if n_points == 8:
            return self._dst_pts_8, self._dst_mm_pts_8
        if n_points == 4:
            return self._dst_pts_4, self._dst_mm_pts_4
        raise ValueError(f"Unsupported anchor count: {n_points}")

    def _anchor_src_points_from_homography(self,
                                           H_mm: np.ndarray,
                                           n_points: int = 8) -> np.ndarray:
        """Project canonical board anchors back into camera space."""
        _, dst_mm = self._anchor_sets(n_points)
        H_inv = np.linalg.inv(H_mm)
        pts = cv2.perspectiveTransform(
            dst_mm.reshape(-1, 1, 2).astype(np.float32),
            H_inv.astype(np.float64),
        )
        return pts.reshape(-1, 2).astype(np.float32)

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
        elif n == 8:
            # Least-squares (method=0): distributes error evenly across all
            # 8 manual clicks — RANSAC would discard valid points as "outliers"
            M, _   = cv2.findHomography(src, dst_px, 0)
            if M is None:
                raise ValueError("Least-squares homography failed for 8-pt")
            self._M = M
            Mmm, _ = cv2.findHomography(src, dst_mm, 0)
            self._M_mm = Mmm if Mmm is not None else M
        else:
            # Many auto-refine correspondences → RANSAC is fine to filter noise
            M, mask = cv2.findHomography(src, dst_px, cv2.RANSAC, 3.0)
            if M is None:
                raise ValueError("RANSAC failed to find a valid homography")
            self._M = M
            Mmm, _ = cv2.findHomography(src, dst_mm, cv2.RANSAC, 3.0)
            self._M_mm = Mmm if Mmm is not None else M
        self._M_inv = np.linalg.inv(self._M)
        self._M_wobble = None   # reset wobble correction on recalibrate
        self._wireframe_prims = None   # invalidate cached primitives
        self._build_radial_bias(src, dst_mm)

    # ------------------------------------------------------------------
    # Per-camera radial bias correction
    # ------------------------------------------------------------------
    def _build_radial_bias(self, src: np.ndarray,
                           dst_mm: np.ndarray) -> None:
        """Measure and fit per-camera radial bias from calibration points.

        Uses the reprojection residuals of calibration correspondences
        to build a polynomial mapping measured_r → true_r.  With 8-point
        calibration (4 at 170mm + 4 at 107mm), the overdetermined
        least-squares homography leaves measurable per-point radial errors
        that reveal the camera's foreshortening bias.

        When 3+ distinct radii are available (e.g. from ML calibration
        providing points at 99, 107, 162, 170mm), a degree-2 quadratic
        is used to capture non-linear lens distortion.  Otherwise falls
        back to degree-1 (linear).

        Also adds an anchor at r=0 (centre maps to centre) to constrain
        the polynomial near the bull.
        """
        self._radial_poly = None
        if self._M_mm is None:
            return

        # Collect (measured_r, true_r) pairs from calibration anchors
        samples = [(0.0, 0.0)]  # anchor: centre maps to centre

        pts_cam = np.array([[p] for p in src], dtype=np.float32)
        pts_mm_out = cv2.perspectiveTransform(pts_cam, self._M_mm)
        for i in range(len(src)):
            true_x, true_y = float(dst_mm[i][0]), float(dst_mm[i][1])
            meas_x, meas_y = float(pts_mm_out[i, 0, 0]), float(pts_mm_out[i, 0, 1])
            true_r = math.hypot(true_x, true_y)
            meas_r = math.hypot(meas_x, meas_y)
            if true_r > 5.0:
                samples.append((meas_r, true_r))

        self._fit_radial_poly(samples)

    def _fit_radial_poly(self, samples: list) -> None:
        """Fit a radial bias polynomial from (measured_r, true_r) pairs.

        Chooses degree-2 when 3+ distinct radii are present (enough
        curvature information), otherwise degree-1.
        """
        if len(samples) < 4:
            print(f"[CAL] Cam {self.cam_id}: too few calibration points "
                  f"for radial bias ({len(samples)})")
            return

        meas = np.array([s[0] for s in samples])
        true = np.array([s[1] for s in samples])

        # Check if there's actually measurable bias
        # (4-point getPerspectiveTransform is exact → zero residuals)
        residuals_raw = np.abs(meas[1:] - true[1:])  # skip centre anchor
        if np.max(residuals_raw) < 0.05:
            print(f"[CAL] Cam {self.cam_id}: radial bias negligible "
                  f"(max_residual={np.max(residuals_raw):.3f}mm)")
            return

        # Count distinct radii (within 5mm tolerance) to decide poly degree
        true_nonzero = true[1:]  # skip centre
        distinct_radii = []
        for r in true_nonzero:
            if not any(abs(r - dr) < 5.0 for dr in distinct_radii):
                distinct_radii.append(r)

        # Degree-2 is stable with 3+ distinct radii and enough total points;
        # with only 2 radii, quadratic would overfit
        if len(distinct_radii) >= 3 and len(samples) >= 6:
            degree = 2
        else:
            degree = 1

        coeffs = np.polyfit(meas, true, degree)
        self._radial_poly = np.poly1d(coeffs)

        # Log the correction magnitude at key ring boundaries
        corrections = {}
        for name, r in [('triple', 103.0), ('double', 166.0)]:
            corrections[name] = float(self._radial_poly(r)) - r
        deg_str = f"deg={degree}" if degree > 1 else f"slope={coeffs[0]:.4f}"
        print(f"[CAL] Cam {self.cam_id}: radial bias correction: "
              f"{deg_str}, distinct_radii={len(distinct_radii)} "
              f"(d@triple={corrections['triple']:+.1f}mm, "
              f"d@double={corrections['double']:+.1f}mm)")

    def build_radial_bias_from_correspondences(
        self,
        src_cam: np.ndarray,
        dst_mm: np.ndarray,
    ) -> None:
        """Build radial bias from external correspondences (e.g. ML pipeline).

        Accepts arbitrary-length arrays of camera-space and board-mm points,
        typically from an ML model that provides detections at 4 ring radii
        (99, 107, 162, 170mm).  This gives a much richer radial profile
        than the standard 8-point calibration (2 radii).
        """
        if self._M_mm is None:
            return

        samples = [(0.0, 0.0)]  # centre anchor
        pts = cv2.perspectiveTransform(
            src_cam.reshape(-1, 1, 2).astype(np.float32), self._M_mm
        ).reshape(-1, 2)

        for i in range(len(src_cam)):
            true_r = math.hypot(float(dst_mm[i][0]), float(dst_mm[i][1]))
            meas_r = math.hypot(float(pts[i][0]), float(pts[i][1]))
            if true_r > 5.0:
                samples.append((meas_r, true_r))

        self._fit_radial_poly(samples)

    def correct_radius(self, measured_r: float) -> float:
        """Apply radial bias correction to a measured radius.

        Returns corrected radius, or the original if no correction
        is available.
        """
        if self._radial_poly is None:
            return measured_r
        return float(self._radial_poly(measured_r))

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------
    def load_cached(self) -> bool:
        """Load saved calibration.  Supports 4-pt and 8-pt npz formats."""
        if os.path.isfile(self.matrix_path):
            data = np.load(self.matrix_path, allow_pickle=True)
            saved_w = int(data["resolution"][0])
            saved_h = int(data["resolution"][1])
            src_pts = data["src_points"] if "src_points" in data.files else None
            stored_H_mm = data["matrix_mm"] if "matrix_mm" in data.files else None

            src_valid = (
                src_pts is not None
                and len(src_pts) in (4, 8)
                and np.ptp(src_pts[:, 0]) > 1e-3
                and np.ptp(src_pts[:, 1]) > 1e-3
            )

            if stored_H_mm is None and "matrix" in data.files:
                saved_bs = min(saved_w, saved_h)
                saved_scale = saved_bs / self.CANVAS_MM
                saved_c = saved_bs / 2.0
                px_to_mm = np.array([
                    [1.0 / saved_scale, 0.0, -saved_c / saved_scale],
                    [0.0, -1.0 / saved_scale, saved_c / saved_scale],
                    [0.0, 0.0, 1.0],
                ], dtype=np.float64)
                stored_H_mm = px_to_mm @ np.asarray(data["matrix"], dtype=np.float64)

            if stored_H_mm is not None:
                n = len(src_pts) if src_valid else 8
                src_pts = self._anchor_src_points_from_homography(
                    np.asarray(stored_H_mm, dtype=np.float64),
                    n_points=n,
                )
                if saved_w != self.w or saved_h != self.h:
                    sx = self.w / saved_w
                    sy = self.h / saved_h
                    src_pts = src_pts.copy()
                    src_pts[:, 0] *= sx
                    src_pts[:, 1] *= sy
            elif src_valid:
                if saved_w != self.w or saved_h != self.h:
                    sx = self.w / saved_w
                    sy = self.h / saved_h
                    src_pts = src_pts.copy()
                    src_pts[:, 0] *= sx
                    src_pts[:, 1] *= sy
            else:
                print(f"[CAL] Camera {self.cam_id}: calibration cache missing usable anchors")
                return False

            self._src_pts = src_pts
            n = len(src_pts)
            dst_px, dst_mm = self._anchor_sets(n)
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
        dst_px, dst_mm = self._anchor_sets(n)
        self._build_homography(src, dst_px, dst_mm)

        np.savez(self.matrix_path, **self._calibration_payload(include_matrix_mm=True))
        self._build_mask()

    def commit_homography(self, H_mm: np.ndarray,
                          n_points: int = 8) -> np.ndarray:
        """Commit a solved camera→board homography as canonical anchor points."""
        src = self._anchor_src_points_from_homography(H_mm, n_points=n_points)
        self._src_pts = src
        dst_px, dst_mm = self._anchor_sets(n_points)
        self._build_homography(src, dst_px, dst_mm)
        np.savez(self.matrix_path, **self._calibration_payload(include_matrix_mm=True))
        self._build_mask()
        return src

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
    # Calibration quality score
    # ------------------------------------------------------------------
    def calibration_quality(self) -> float:
        """Compute a 0-1 calibration quality score.

        Based on reprojection error of the calibration anchors in mm-space
        (resolution-independent).  Uses a tolerance of 25% of the double-
        outer radius (~42.5 mm), matching MDH's approach but in board-mm
        rather than pixels.

        Returns 0.0 if not calibrated.
        """
        if self._M_mm is None or self._src_pts is None:
            return 0.0

        src = self._src_pts
        n = len(src)
        _, dst_mm = self._anchor_sets(n)

        # Project source points through the mm homography
        pts = cv2.perspectiveTransform(
            src.reshape(-1, 1, 2).astype(np.float32), self._M_mm
        ).reshape(-1, 2)

        # Reprojection error in mm
        errors = np.linalg.norm(pts - dst_mm, axis=1)
        mean_err = float(np.mean(errors))

        # Tolerance: max(2mm, 25% of double-outer radius)
        # At DOUBLE_OUTER=170mm → tol=42.5mm, so mean_err of ~4mm → quality≈0.9
        tol_mm = max(2.0, DOUBLE_OUTER * 0.25)
        quality = max(0.0, min(1.0, 1.0 - mean_err / tol_mm))

        # Bonus for 8-point over 4-point (more constraints = more reliable)
        if n == 8:
            quality = min(1.0, quality * 1.05)

        return round(quality, 3)

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
    # Camera azimuth (derived from homography)
    # ------------------------------------------------------------------
    def camera_azimuth(self) -> Optional[float]:
        """Derive the camera's physical azimuth angle around the board.

        The homography inverse (board_mm → camera_px) compresses the
        board direction that aligns with the camera's depth axis
        (foreshortening).  We sample the Jacobian at the board centre
        and find the board-space direction with minimum pixel stretch.
        That direction points FROM centre TOWARD the camera → azimuth.

        Returns degrees [0, 360) or None if not calibrated.
        """
        if self._M_mm is None:
            return None
        H_inv = np.linalg.inv(self._M_mm)

        def _to_px(bx, by):
            p = H_inv @ np.array([bx, by, 1.0])
            return p[:2] / p[2]

        centre = _to_px(0.0, 0.0)
        delta = 10.0  # mm step
        min_stretch = float('inf')
        best_angle = 0.0

        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            px = _to_px(delta * math.cos(rad), delta * math.sin(rad))
            stretch = math.hypot(px[0] - centre[0], px[1] - centre[1])
            if stretch < min_stretch:
                min_stretch = stretch
                best_angle = float(deg)

        return best_angle

    # ------------------------------------------------------------------
    # Auto-anchor detection
    # ------------------------------------------------------------------
    def auto_detect_anchors(self, frame: np.ndarray,
                            n_points: int = 8) -> Optional[np.ndarray]:
        """Fully automatic 1-click anchor detection using HSV colour segmentation.

        Fits an ellipse to the outer boundary of the red+green ring region
        (after morphological close).  That boundary sits approximately at the
        double-outer ring, giving good starting anchor points.
        """
        h_img, w_img = frame.shape[:2]

        # 1. Isolate the dartboard (Red + Green scoring beds)
        hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # Wider red range — covers LED-lit and standard lighting
        red1 = cv2.inRange(hsv, (0,   40, 40), (12,  255, 255))
        red2 = cv2.inRange(hsv, (158, 40, 40), (180, 255, 255))
        # Wider green range — covers dark and bright green segments
        grn  = cv2.inRange(hsv, (30,  30, 30), (95,  255, 255))
        mask = red1 | red2 | grn

        # Two-stage morphological close:
        # Stage 1 — small kernel fills spider-wire gaps without merging
        #           separate board regions into the black surround
        # Stage 2 — larger kernel bridges remaining gaps
        k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        k_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_small, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_large, iterations=2)
        # Remove small isolated blobs that are not part of the board
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k_small, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        main_contour = max(contours, key=cv2.contourArea)

        # Reject if detected area is too small to be a dartboard
        if len(main_contour) < 20 or cv2.contourArea(main_contour) < 5000:
            return None

        # 3. Fit an ellipse — the RETR_EXTERNAL contour outer boundary sits
        #    approximately at the double-outer ring, so no scale correction needed.
        (cx, cy), (width, height), angle_deg = cv2.fitEllipse(main_contour)

        a = width  / 2.0
        b = height / 2.0
        ell_ang_rad = math.radians(angle_deg)

        angles = _ANCHOR_ANGLES if n_points >= 4 else _ANCHOR_ANGLES[:n_points]
        if n_points == 8:
            angles_all  = angles + angles
            radii_scale = [1.0] * 4 + [TRIPLE_OUTER / DOUBLE_OUTER] * 4
        else:
            angles_all  = angles
            radii_scale = [1.0] * 4

        # 4. Generate anchor points parametrically
        raw_pts = []
        for board_ang, rs in zip(angles_all, radii_scale):
            # Image Y goes down, physical Y goes up
            t     = math.radians(360 - board_ang)
            x_ell = rs * a * math.cos(t)
            y_ell = rs * b * math.sin(t)
            # Apply ellipse tilt and translate to centre
            px = cx + x_ell * math.cos(ell_ang_rad) - y_ell * math.sin(ell_ang_rad)
            py = cy + x_ell * math.sin(ell_ang_rad) + y_ell * math.cos(ell_ang_rad)
            # Clamp to frame bounds
            px = float(np.clip(px, 1, w_img - 1))
            py = float(np.clip(py, 1, h_img - 1))
            raw_pts.append([px, py])

        return np.array(raw_pts, dtype=np.float32)


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
