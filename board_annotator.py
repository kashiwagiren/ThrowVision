"""ThrowVision – Board Annotator (4-point perspective-correct).

Uses the SAME 4 calibration points as BoardCalibrator to produce a
perspective-correct wireframe overlay on each saved camera frame.

The wireframe is computed by projecting board-space geometry through
the inverse homography (board → camera), so rings appear as correct
ellipses and wires follow the actual camera angle.

Labels are saved as JSON with:
  src_points   — 4 camera-space calibration points [[x,y], ...]
  cam_id
  image / image_width / image_height / timestamp
  dart_tip     — optional [x, y] camera-space dart tip (if provided)
"""

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Standard dartboard geometry (mm)
# ---------------------------------------------------------------------------
BULL_INNER_R  = 6.35
BULL_OUTER_R  = 15.9
TRIPLE_INNER  = 99.0
TRIPLE_OUTER  = 107.0
DOUBLE_INNER  = 162.0
DOUBLE_OUTER  = 170.0
CANVAS_MM     = 451.0   # full diameter including number ring

SECTOR_ORDER: List[int] = [
    20, 5, 12, 9, 14, 11, 8, 16, 7, 19,
    3, 17, 2, 15, 10, 6, 13, 4, 18, 1,
]
SECTOR_ANGLE = 18.0  # degrees per sector

# The 4 destination angles used by BoardCalibrator (same as _DST_ANGLES):
#   D20/D1 = 81°, D6/D10 = 351°, D3/D19 = 261°, D11/D14 = 171°
DST_WIRE_ANGLES = [81.0, 351.0, 261.0, 171.0]


def _sector_boundary_angles() -> List[float]:
    """Return the 20 wire-boundary angles (counter-clockwise, 20 at top=90°)."""
    start = 90.0 - SECTOR_ANGLE / 2
    return [(start + i * SECTOR_ANGLE) % 360.0 for i in range(20)]


def _board_point(cx: float, cy: float, scale: float,
                 angle_deg: float, r_mm: float) -> Tuple[float, float]:
    """Map a (angle, radius) board coordinate to board-space pixels."""
    rad = math.radians(angle_deg)
    return (cx + r_mm * scale * math.cos(rad),
            cy - r_mm * scale * math.sin(rad))


# ---------------------------------------------------------------------------
# 4-point Board Annotation
# ---------------------------------------------------------------------------

class BoardAnnotation:
    """Perspective-correct dartboard wireframe from 4 calibration points.

    Parameters
    ----------
    src_points : array-like, shape (4, 2)
        Camera-space pixel coordinates of the 4 wire-intersection points,
        in the SAME order as BoardCalibrator:
          [D20/D1, D6/D10, D3/D19, D11/D14]
    frame_w, frame_h : int
        Actual image dimensions (used for board_size computation).
    """

    def __init__(self,
                 src_points,
                 frame_w: int = 1920,
                 frame_h: int = 1080) -> None:
        self.src_pts = np.asarray(src_points, dtype=np.float32)
        assert self.src_pts.shape == (4, 2), \
            f"Need exactly 4 source points, got {self.src_pts.shape}"

        # Board-space canvas — same logic as BoardCalibrator
        board_size = min(frame_w, frame_h)
        self._scale = board_size / CANVAS_MM
        cx = cy = board_size / 2.0

        # Build destination points (board-space pixel positions of the 4 wires)
        self._dst_pts = np.array([
            _board_point(cx, cy, self._scale, a, DOUBLE_OUTER)
            for a in DST_WIRE_ANGLES
        ], dtype=np.float32)

        # Perspective matrix: board → camera (inverse of calibration M)
        self._M = cv2.getPerspectiveTransform(self.src_pts, self._dst_pts)
        self._M_inv = np.linalg.inv(self._M)  # camera → board (not needed for drawing, kept for reference)
        self._board_cx = cx
        self._board_cy = cy
        self._board_size = board_size

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def _board_to_cam(self, bx: float, by: float) -> Tuple[float, float]:
        """Project a board-space point to camera pixel coords."""
        pt = np.array([[[bx, by]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self._M_inv)
        return float(out[0, 0, 0]), float(out[0, 0, 1])

    def _angle_radius_to_cam(self, angle_deg: float,
                              r_mm: float) -> Tuple[int, int]:
        """Board polar → camera pixel (integer)."""
        bx, by = _board_point(
            self._board_cx, self._board_cy, self._scale, angle_deg, r_mm)
        cx, cy = self._board_to_cam(bx, by)
        return int(round(cx)), int(round(cy))

    def bull_center_cam(self) -> Tuple[float, float]:
        """Bullseye centre in camera pixels."""
        return self._board_to_cam(self._board_cx, self._board_cy)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw_wireframe(self,
                       img: np.ndarray,
                       color: Tuple[int, int, int] = (0, 255, 255),
                       thickness: int = 1,
                       dart_tip: Optional[Tuple[float, float]] = None
                       ) -> np.ndarray:
        """Draw perspective-correct dartboard wireframe on *img*.

        Parameters
        ----------
        dart_tip : (x, y) camera-space pixel coords of dart tip, or None.
        """
        out = img.copy()
        h, w = out.shape[:2]

        # ── Rings (sampled polylines give perspective-correct ellipses) ──
        steps = 90  # enough for smooth curves
        for r_mm in (BULL_INNER_R, BULL_OUTER_R, TRIPLE_INNER,
                     TRIPLE_OUTER, DOUBLE_INNER, DOUBLE_OUTER):
            pts = []
            for k in range(steps + 1):
                ang = k * 360.0 / steps
                cx_c, cy_c = self._angle_radius_to_cam(ang, r_mm)
                pts.append([cx_c, cy_c])
            poly = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(out, [poly], isClosed=True, color=color,
                          thickness=thickness, lineType=cv2.LINE_AA)

        # ── Sector wires ──
        for ang in _sector_boundary_angles():
            p1 = self._angle_radius_to_cam(ang, BULL_OUTER_R)
            p2 = self._angle_radius_to_cam(ang, DOUBLE_OUTER)
            cv2.line(out, p1, p2, color, thickness, cv2.LINE_AA)

        # ── Sector numbers ──
        num_r = DOUBLE_OUTER + 12
        boundaries = _sector_boundary_angles()
        for i, num in enumerate(SECTOR_ORDER):
            mid_ang = boundaries[i] + SECTOR_ANGLE / 2
            pt = self._angle_radius_to_cam(mid_ang, num_r)
            cv2.putText(out, str(num), (pt[0] - 8, pt[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

        # ── Bullseye marker ──
        bull_cx, bull_cy = self.bull_center_cam()
        cv2.circle(out, (int(bull_cx), int(bull_cy)), 4, (0, 0, 255), -1, cv2.LINE_AA)

        # ── Calibration anchor points ──
        anchor_colors = [(255, 80, 80), (80, 255, 80), (80, 120, 255), (255, 180, 0)]
        anchor_labels = ['D20/D1', 'D6/D10', 'D3/D19', 'D11/D14']
        for k, (px, py) in enumerate(self.src_pts):
            pt = (int(round(px)), int(round(py)))
            cv2.circle(out, pt, 8, anchor_colors[k], -1, cv2.LINE_AA)
            cv2.circle(out, pt, 9, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(out, anchor_labels[k], (pt[0] + 10, pt[1] - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

        # ── Dart tip marker (if provided) ──
        if dart_tip is not None:
            tx, ty = int(round(dart_tip[0])), int(round(dart_tip[1]))
            cv2.circle(out, (tx, ty), 10, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.drawMarker(out, (tx, ty), (0, 255, 0),
                           cv2.MARKER_CROSS, 20, 2, cv2.LINE_AA)
            cv2.putText(out, 'TIP', (tx + 12, ty - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

        return out


# ---------------------------------------------------------------------------
# Saving annotations
# ---------------------------------------------------------------------------

def save_annotation(frame: np.ndarray,
                    annotation: BoardAnnotation,
                    cam_id: int,
                    dart_tip: Optional[Tuple[float, float]] = None,
                    out_dir: str = "board_annotations") -> str:
    """Save annotated frame + label as training data.

    Parameters
    ----------
    dart_tip : (x, y) camera-space dart tip, or None.

    Returns
    -------
    str : path to the saved label file.
    """
    img_dir = Path(out_dir) / "images"
    lbl_dir = Path(out_dir) / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"board_cam{cam_id}_{ts}"

    # Raw image
    img_path = img_dir / f"{base}.jpg"
    cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

    # Overlay image (for visual verification)
    overlay_path = img_dir / f"{base}_overlay.jpg"
    overlay = annotation.draw_wireframe(frame, dart_tip=dart_tip)
    cv2.imwrite(str(overlay_path), overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])

    # Label JSON
    h, w = frame.shape[:2]
    bull_cam = annotation.bull_center_cam()
    label: dict = {
        "image": f"{base}.jpg",
        "cam_id": cam_id,
        "image_width": w,
        "image_height": h,
        "timestamp": ts,
        "src_points": annotation.src_pts.tolist(),
        "bull_center_cam": [round(bull_cam[0], 2), round(bull_cam[1], 2)],
    }
    if dart_tip is not None:
        label["dart_tip"] = [round(dart_tip[0], 2), round(dart_tip[1], 2)]

    lbl_path = lbl_dir / f"{base}.json"
    with open(lbl_path, "w", encoding="utf-8") as f:
        json.dump(label, f, indent=2)

    print(f"[ANN-BOARD] Saved {img_path.name} + {lbl_path.name}")
    return str(lbl_path)
