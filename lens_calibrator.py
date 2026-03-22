"""ThrowVision – Per-Camera Lens Distortion Calibrator.

Uses a printed checkerboard (9×6 inner corners, 25 mm squares) to compute
the camera intrinsic matrix K and distortion coefficients dist via
cv2.calibrateCamera().  Results are saved to calibration/lens_{cam_id}.npz
and applied to every captured frame before any perspective math.

Typical workflow
----------------
1. Print checkerboard (or display on a monitor).
2. Hold it in front of the camera at ~20 different angles/distances.
3. Call add_frame() each time — it returns how many valid frames are stored.
4. Call compute() once ≥ 10 frames are collected (20 recommended).
5. Reload the app — calibrator.py + server.py will auto-load and undistort.
"""

import os
from typing import Optional, Tuple

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Default checkerboard geometry
# ---------------------------------------------------------------------------
DEFAULT_PATTERN   = (9, 6)   # interior corner count (cols, rows)
DEFAULT_SQUARE_MM = 25.0     # physical square size in millimetres
MIN_FRAMES        = 10       # minimum needed to compute
SUBPIX_CRITERIA   = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
                     30, 0.001)


def _lens_path(cam_id: int) -> str:
    return os.path.join("calibration", f"lens_{cam_id}.npz")


class LensCalibrator:
    """Per-camera lens intrinsic calibrator."""

    def __init__(self, cam_id: int,
                 pattern: Tuple[int, int] = DEFAULT_PATTERN,
                 square_mm: float = DEFAULT_SQUARE_MM) -> None:
        self.cam_id    = cam_id
        self.pattern   = pattern
        self.square_mm = square_mm
        self._img_pts  = []          # list of detected corner arrays
        self._obj_pt   = self._make_obj_pt()
        self._K: Optional[np.ndarray]    = None
        self._dist: Optional[np.ndarray] = None
        self._rms: float = 0.0
        self._img_size: Optional[Tuple[int, int]] = None
        self._cells_covered: set = set()   # (gx, gy) grid cells for coverage %

    # ------------------------------------------------------------------
    def _make_obj_pt(self) -> np.ndarray:
        """3-D world points for one checkerboard view (z = 0 plane)."""
        cols, rows = self.pattern
        pts = np.zeros((cols * rows, 3), np.float32)
        pts[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
        pts *= self.square_mm
        return pts

    # ------------------------------------------------------------------
    # Frame intake
    # ------------------------------------------------------------------
    def detect(self, frame: np.ndarray) -> Tuple[bool, np.ndarray]:
        """Detect corners in *frame* and return (found, annotated_frame).

        Does NOT add the frame to the collection — call add_frame() for that.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCorners(
            gray, self.pattern,
            cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        vis = frame.copy()
        if found:
            sub = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                                   SUBPIX_CRITERIA)
            cv2.drawChessboardCorners(vis, self.pattern, sub, found)
        return found, vis

    # ------------------------------------------------------------------
    # Coverage helpers
    # ------------------------------------------------------------------
    _GRID_COLS = 8
    _GRID_ROWS = 6

    def _update_coverage(self, corners: np.ndarray, w: int, h: int) -> None:
        gc, gr = self._GRID_COLS, self._GRID_ROWS
        for pt in corners.reshape(-1, 2):
            gx = min(int(pt[0] / w * gc), gc - 1)
            gy = min(int(pt[1] / h * gr), gr - 1)
            self._cells_covered.add((gx, gy))

    def coverage_pct(self) -> int:
        total = self._GRID_COLS * self._GRID_ROWS  # 48 cells
        return min(100, int(len(self._cells_covered) / total * 100))

    def draw_coverage_overlay(self, frame: np.ndarray) -> np.ndarray:
        """Return a copy of *frame* with covered grid cells tinted red + blue guide ring."""
        h, w = frame.shape[:2]

        if self._cells_covered:
            gc, gr = self._GRID_COLS, self._GRID_ROWS
            overlay = np.zeros_like(frame)
            for (gx, gy) in self._cells_covered:
                x1 = int(gx / gc * w)
                y1 = int(gy / gr * h)
                x2 = int((gx + 1) / gc * w)
                y2 = int((gy + 1) / gr * h)
                cv2.rectangle(overlay, (x1, y1), (x2, y2), (40, 40, 210), -1)  # red (BGR)
            result = cv2.addWeighted(frame, 1.0, overlay, 0.45, 0)
        else:
            result = frame.copy()

        # Blue guide ellipse — shows the area to sweep the checkerboard across
        cx, cy = w // 2, h // 2
        rx = int(w * 0.44)
        ry = int(h * 0.44)
        cv2.ellipse(result, (cx, cy), (rx, ry), 0, 0, 360, (210, 100, 30), 2)

        return result

    def add_frame(self, frame: np.ndarray) -> Tuple[bool, int]:
        """Detect and, if successful, add this frame to the collection.

        Returns (added, total_count).
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCorners(
            gray, self.pattern,
            cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        if not found:
            return False, len(self._img_pts)
        sub = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                               SUBPIX_CRITERIA)
        self._img_pts.append(sub)
        self._img_size = (gray.shape[1], gray.shape[0])
        self._update_coverage(sub, gray.shape[1], gray.shape[0])
        return True, len(self._img_pts)

    # ------------------------------------------------------------------
    # Compute + persist
    # ------------------------------------------------------------------
    def compute(self) -> Tuple[bool, float, str]:
        """Run calibrateCamera.  Returns (ok, rms, message)."""
        n = len(self._img_pts)
        if n < MIN_FRAMES:
            return False, 0.0, f"Need at least {MIN_FRAMES} frames (have {n})"
        if self._img_size is None:
            return False, 0.0, "No frames captured"

        obj_pts = [self._obj_pt] * n
        try:
            rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
                obj_pts, self._img_pts, self._img_size, None, None
            )
        except cv2.error as e:
            return False, 0.0, f"calibrateCamera failed: {e}"

        self._K, self._dist, self._rms = K, dist, rms
        self._save()
        return True, rms, f"RMS reprojection error: {rms:.3f} px"

    def _save(self) -> None:
        os.makedirs("calibration", exist_ok=True)
        np.savez(_lens_path(self.cam_id),
                 K=self._K,
                 dist=self._dist,
                 rms=np.array([self._rms]),
                 img_size=np.array(self._img_size))

    # ------------------------------------------------------------------
    # Load + apply
    # ------------------------------------------------------------------
    @staticmethod
    def load(cam_id: int) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Load saved K + dist.  Returns (K, dist) or (None, None)."""
        path = _lens_path(cam_id)
        if not os.path.exists(path):
            return None, None
        try:
            data = np.load(path)
            return data["K"], data["dist"]
        except Exception:
            return None, None

    @staticmethod
    def rms_saved(cam_id: int) -> Optional[float]:
        path = _lens_path(cam_id)
        if not os.path.exists(path):
            return None
        try:
            return float(np.load(path)["rms"][0])
        except Exception:
            return None

    @staticmethod
    def undistort(frame: np.ndarray,
                  K: np.ndarray,
                  dist: np.ndarray) -> np.ndarray:
        """Apply lens undistortion (keeps original frame size)."""
        h, w = frame.shape[:2]
        new_K, roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), 0, (w, h))
        return cv2.undistort(frame, K, dist, None, new_K)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def reset(self) -> None:
        self._img_pts.clear()
        self._img_size = None
        self._cells_covered.clear()

    @property
    def count(self) -> int:
        return len(self._img_pts)

    @property
    def is_calibrated(self) -> bool:
        return os.path.exists(_lens_path(self.cam_id))
