"""ThrowVision – Board Calibrator.

Handles 4-point perspective transform, wireframe verification overlay,
and circular mask generation for background removal.

Calibration data is saved as .npz with the source points and the
resolution they were captured at.  When loaded at a different
resolution the source points are automatically rescaled and the
transform recomputed so the user never has to recalibrate after a
resolution change.
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


class BoardCalibrator:
    """4-point perspective calibration, wireframe drawing, and mask."""

    # Full dartboard diameter including number ring (225.5mm radius)
    CANVAS_MM = 451.0

    _DST_ANGLES = [
        _wire_angle(20, 1),
        _wire_angle(6, 10),
        _wire_angle(3, 19),
        _wire_angle(11, 14),
    ]

    def __init__(self, cfg: ConfigManager, cam_id: int = 0) -> None:
        self.cfg = cfg
        self.cam_id = cam_id
        self.matrix_path = cfg.matrix_path_for(cam_id)
        self.w, self.h = cfg.resolution
        # Square output — just the dartboard circle, no wasted margins
        self.board_size = min(self.w, self.h)
        self._M: Optional[np.ndarray] = None
        self._M_inv: Optional[np.ndarray] = None
        self._M_mm: Optional[np.ndarray] = None  # raw px → mm (center=0,0)
        self._mask: Optional[np.ndarray] = None
        self._raw_mask: Optional[np.ndarray] = None
        # Scale so the full board (with number ring) fits inside the
        # square output.  No extra shrink factor needed — the larger
        # canvas already provides margin around the playing area.
        self._scale = self.board_size / self.CANVAS_MM
        self._src_pts: Optional[np.ndarray] = None   # saved for rescaling

        cx = cy = self.board_size / 2
        self._dst_pts = np.array([
            self._mm_to_px(*_board_point_mm(a, DOUBLE_OUTER), cx, cy)
            for a in self._DST_ANGLES
        ], dtype=np.float32)

        # Destination points in mm coordinates (center=0,0)
        # Used for direct raw→mm transform
        self._dst_mm_pts = np.array([
            _board_point_mm(a, DOUBLE_OUTER)
            for a in self._DST_ANGLES
        ], dtype=np.float32)

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------
    def _mm_to_px(self, xmm: float, ymm: float,
                  cx: float, cy: float) -> Tuple[float, float]:
        return (cx + xmm * self._scale, cy - ymm * self._scale)

    def _radius_mm_to_px(self, r_mm: float) -> float:
        return r_mm * self._scale

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------
    def load_cached(self) -> bool:
        """Load saved calibration.  Returns True on success.

        Handles both new .npz format (with src_points + resolution)
        and legacy .npy files (matrix only, at original resolution).
        """
        # --- New format (.npz) ---
        if os.path.isfile(self.matrix_path):
            data = np.load(self.matrix_path, allow_pickle=True)
            src_pts = data["src_points"]
            saved_w = int(data["resolution"][0])
            saved_h = int(data["resolution"][1])

            if saved_w != self.w or saved_h != self.h:
                # Rescale src_points to current resolution
                sx = self.w / saved_w
                sy = self.h / saved_h
                src_pts = src_pts.copy()
                src_pts[:, 0] *= sx
                src_pts[:, 1] *= sy


            self._src_pts = src_pts
            self._M = cv2.getPerspectiveTransform(src_pts, self._dst_pts)
            self._M_inv = np.linalg.inv(self._M)
            self._M_mm = cv2.getPerspectiveTransform(src_pts, self._dst_mm_pts)
            self._build_mask()
            self._loaded_path = self.matrix_path
            return True

        # --- Legacy format (.npy) ---
        legacy = self.matrix_path.replace(".npz", ".npy")
        old_template = f"transformation_matrix_{self.cam_id}.npy"
        for path in [legacy, old_template]:
            if os.path.isfile(path):
                print(f"[CAL] Camera {self.cam_id}: found legacy file {path}")
                print(f"[CAL]   -> Cannot rescale legacy format. "
                      f"Please recalibrate with --calibrate")
                # Don't load — force recalibration
                return False

        return False

    def calibrate(self, src_points: np.ndarray) -> None:
        src = np.asarray(src_points, dtype=np.float32)
        assert src.shape == (4, 2), "Need exactly 4 source points"

        self._src_pts = src
        self._M = cv2.getPerspectiveTransform(src, self._dst_pts)
        self._M_inv = np.linalg.inv(self._M)
        self._M_mm = cv2.getPerspectiveTransform(src, self._dst_mm_pts)

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
    # Un-warp / point transforms
    # ------------------------------------------------------------------
    def unwarp(self, frame: np.ndarray) -> np.ndarray:
        assert self._M is not None, "Not calibrated"
        return cv2.warpPerspective(frame, self._M,
                                   (self.board_size, self.board_size))

    def cam_to_board(self, x: float, y: float) -> Tuple[float, float]:
        pt = np.array([[[x, y]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self._M)
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    def board_px_to_mm(self, bx: float, by: float) -> Tuple[float, float]:
        c = self.board_size / 2
        return ((bx - c) / self._scale, (c - by) / self._scale)

    def transform_to_mm(self, raw_x: float, raw_y: float) -> Tuple[float, float]:
        """Transform raw camera pixel to dartboard mm coordinates (center=0,0).

        Uses a direct perspective transform from raw camera space to
        standard dartboard mm coordinates, avoiding the double-conversion
        through warped pixel space.
        """
        assert self._M_mm is not None, "Not calibrated"
        pt = np.array([[[raw_x, raw_y]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self._M_mm)
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    # ------------------------------------------------------------------
    # Masks
    # ------------------------------------------------------------------
    def _build_mask(self) -> None:
        """Build board-space and raw-camera-space masks."""
        s = self.board_size
        mask = np.zeros((s, s), dtype=np.uint8)
        c = s // 2
        # Use full visible radius (number ring + small margin)
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
        """Return cached wireframe drawing primitives (circle radii + line endpoints).
        Computed once and reused — avoids redoing math per frame."""
        if hasattr(self, '_wireframe_prims') and self._wireframe_prims is not None:
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
