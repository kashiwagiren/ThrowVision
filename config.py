"""ThrowVision – Configuration Manager.

Centralises every tuneable parameter so the rest of the pipeline never
hard-codes magic numbers.

Default resolution is 848×480 at 30 fps.  Each camera should be on
its own USB controller to avoid bandwidth conflicts.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


class DetectionSpeed(Enum):
    """Scales absdiff threshold & stability correlation requirement."""
    VERY_LOW = 0
    LOW = 1
    DEFAULT = 2
    HIGH = 3
    VERY_HIGH = 4


# Pre-computed look-up: (absdiff_threshold, stability_correlation)
# Autodarts uses stability threshold ~0.99 with ~10-18 iterations.
_SPEED_PARAMS: dict[DetectionSpeed, Tuple[int, float]] = {
    DetectionSpeed.VERY_LOW:  (40, 0.995),
    DetectionSpeed.LOW:       (30, 0.992),
    DetectionSpeed.DEFAULT:   (20, 0.990),
    DetectionSpeed.HIGH:      (15, 0.985),
    DetectionSpeed.VERY_HIGH: (10, 0.970),
}

_STANDBY_MAP: dict[str, int] = {
    "5m": 300, "10m": 600, "15m": 900, "30m": 1800, "1h": 3600,
}

# Resolution fallback ladder — tried top-to-bottom until all cameras open.
RESOLUTION_LADDER: List[Tuple[int, int]] = [
    (848, 480),
    (640, 480),
    (640, 360),
    (424, 240),
    (320, 240),
]


@dataclass
class ConfigManager:
    """Single source of truth for every runtime parameter."""

    # --- Camera / capture ---------------------------------------------------
    resolution: Tuple[int, int] = (1920, 1080)
    fps: int = 30          # 30 fps — each camera on its own USB controller
    num_cameras: int = 3

    # --- Derived processing resolutions (computed in __post_init__) ---------
    board_size: int = field(init=False)
    detection_resolution: Tuple[int, int] = field(init=False)
    motion_resolution: Tuple[int, int] = field(init=False)

    # --- Behaviour ----------------------------------------------------------
    standby_time: str = "15m"
    approximate_distortion: bool = True
    calibrate_on_startup: bool = False
    calibrate_on_camera_change: bool = False
    detection_speed: DetectionSpeed = DetectionSpeed.DEFAULT

    # --- Segmentation -------------------------------------------------------
    dart_size_min: int = 800       # min contour area (pixels) — scaled for 1080p
    dart_size_max: int = 25000     # max contour area (pixels)
    hand_size_max: int = 45000     # anything above -> HAND state

    # --- Tip offset (pixels along dart vector) ------------------------------
    tip_offset_px: float = 8.0

    # --- Triangle tip adjustment factor ------------------------------------
    # Fraction of the base->tip vector to shift the detected tip vertex.
    # Negative values move the tip TOWARD the base (flights), compensating
    # for the enclosing triangle overshooting the physical tip contact point.
    triangle_k_factor: float = -0.20

    # --- Calibration cache (template - {cam_id} substituted) ---------------
    matrix_path_template: str = "calibration/calibration_{}.npz"

    # --- Hough parameters ---------------------------------------------------
    hough_rho: float = 1.0
    hough_theta_div: int = 180
    hough_threshold: int = 15
    hough_min_line_len: int = 15
    hough_max_line_gap: int = 10

    # --- Gaussian blur for segmentation ------------------------------------
    blur_kernel: Tuple[int, int] = (5, 5)
    binary_thresh: int = 30

    def __post_init__(self) -> None:
        w, h = self.resolution
        bs = min(w, h)            # square board output
        self.board_size = bs
        self.detection_resolution = (bs, bs)
        # 1/4 board size for motion detection
        self.motion_resolution = (bs // 4, bs // 4)

    # --- Derived helpers ----------------------------------------------------
    @property
    def standby_seconds(self) -> int:
        return _STANDBY_MAP.get(self.standby_time, 900)

    @property
    def absdiff_threshold(self) -> int:
        return _SPEED_PARAMS[self.detection_speed][0]

    @property
    def stability_correlation(self) -> float:
        return _SPEED_PARAMS[self.detection_speed][1]

    def matrix_path_for(self, cam_id: int) -> str:
        return self.matrix_path_template.format(cam_id)

    def summary(self) -> str:
        lines = [
            "=== ThrowVision Config ===",
            f"  Resolution       : {self.resolution}",
            f"  Board size       : {self.board_size}x{self.board_size}",
            f"  FPS              : {self.fps}",
            f"  Motion res       : {self.motion_resolution}",
            f"  Detection speed  : {self.detection_speed.name}",
            f"  absdiff thresh   : {self.absdiff_threshold}",
            f"  Stability corr   : {self.stability_correlation}",
            f"  Standby          : {self.standby_time} ({self.standby_seconds}s)",
            f"  Tip offset       : {self.tip_offset_px} px",
            f"  Triangle k       : {self.triangle_k_factor}",
        ]
        return "\n".join(lines)

