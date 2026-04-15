"""ThrowVision – YOLO Dart Tip Verifier.

Uses a YOLOv8 object-detection model (darttipbox1.1.pt) to verify
CV-detected dart tip positions.  The model detects bounding boxes
around dart tips in raw camera frames.

Design principle — YOLO is a VALIDATOR, not a position replacer:
    The CV line-fit is geometrically more accurate for the tip's exact
    board position (it traces the actual dart shaft pixels and projects
    through the calibrated homography).  YOLO's bounding box centre
    reliably tells us "there is a dart tip in this region of the frame"
    but for a dart viewed at an angle from a side camera, no single
    bbox point maps precisely to the tip contact point.

    Therefore YOLO is only used to CONFIRM or REJECT a CV reading:
      - If YOLO sees a dart within CONFIRM_RADIUS_MM of the CV tip →
        the CV tip is confirmed (method boosted to +YOLO).
      - If YOLO sees no dart near the CV tip → no change to CV reading
        (YOLO may have missed it; we don't penalise CV).
      - YOLO never supplies an alternative tip position.

Integration flow:
    1. CV pipeline detects dart tips as usual (absdiff + line-fit).
    2. For each camera that found a tip, YOLO runs on the raw frame.
    3. YOLO detections are converted to board-mm via the calibrator.
    4. Old (previously scored) darts are filtered out.
    5. YOLO detections outside the board scoring area are discarded
       (they are detecting the barrel/shaft above the board surface).
    6. If any remaining YOLO detection is within CONFIRM_RADIUS_MM of
       the CV tip, the CV tip is confirmed (YoloResult.confirms=True).
"""

import math
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class YoloResult:
    """Result of a single-camera YOLO verification."""
    confirms: bool                    # True if YOLO confirms CV tip
    confidence: float                 # best matching YOLO detection conf
    distance_mm: float                # distance from CV tip to nearest YOLO detection
    nearest_mm: Tuple[float, float]   # nearest YOLO detection in board-mm


class YoloVerifier:
    """Loads a YOLOv8 dart-tip model and verifies CV detections."""

    # A YOLO detection within this radius of the CV tip confirms it.
    CONFIRM_RADIUS_MM = 35.0
    # Only reject detections that are effectively the same old hole.
    # Close groupings often land well inside the old 20mm filter radius.
    OLD_TIP_DUPLICATE_MM = 8.0
    # YOLO detections beyond this radius from the board centre are
    # barrel/shaft detections above the board surface — discard them.
    BOARD_VALID_R_MM  = 175.0   # slightly beyond DOUBLE_OUTER (170mm)

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.4,
        match_radius_mm: float = 20.0,
        imgsz: int = 800,
        device: Optional[str] = None,
    ) -> None:
        self.conf_threshold = conf_threshold
        self.match_radius_mm = match_radius_mm
        self.imgsz = imgsz
        self._model = None
        self._available = False

        if not os.path.isfile(model_path):
            print(f"[YOLO] Model not found: {model_path} — YOLO disabled")
            return

        try:
            from ultralytics import YOLO
            self._model = YOLO(model_path)

            # Pick device
            if device is None:
                import torch
                device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
            self._device = device

            # Warmup inference (forces GPU allocation)
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            t0 = time.perf_counter()
            self._model(dummy, imgsz=self.imgsz, verbose=False, device=self._device)
            dt = (time.perf_counter() - t0) * 1000
            print(f"[YOLO] Model loaded on {device} — warmup {dt:.0f}ms")
            self._available = True
        except Exception as e:
            print(f"[YOLO] Failed to load model: {e} — YOLO disabled")

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Core verification
    # ------------------------------------------------------------------
    def verify_tip(
        self,
        frame: np.ndarray,
        calibrator,                                     # BoardCalibrator
        cv_tip_warped: Tuple[float, float],             # CV tip in warped px
        scored_tips_mm: List[Tuple[float, float]],      # previously scored
        cv_tip_method: str = 'NONE',
    ) -> Optional[YoloResult]:
        """Run YOLO on *frame* and check whether it confirms the CV tip.

        YOLO is used as a VALIDATOR only — it never overrides the CV
        position.  Returns a YoloResult with confirms=True if a YOLO
        detection is found within CONFIRM_RADIUS_MM of the CV tip,
        or None if YOLO found nothing worth reporting.
        """
        if not self._available or frame is None:
            return None
        if not calibrator.is_calibrated:
            return None

        # --- Run YOLO inference ----------------------------------------
        t0 = time.perf_counter()
        results = self._model(
            frame, imgsz=self.imgsz, verbose=False,
            device=self._device, conf=self.conf_threshold,
        )
        infer_ms = (time.perf_counter() - t0) * 1000
        boxes_found = 0 if not results else len(results[0].boxes)
        print(f"[YOLO] Verify on {self._device}: "
              f"{infer_ms:.0f}ms boxes={boxes_found}")
        if not results or len(results[0].boxes) == 0:
            return None

        boxes = results[0].boxes

        # --- Extract detections and convert to board mm ----------------
        # Use the TRUE bounding-box centre ((x1+x2)/2, (y1+y2)/2).
        # This matches how the model was trained (yolodarts uses center).
        # We do NOT use bottom-edge (y2): for a dart angled into the board
        # as seen from a side camera, any single bbox edge can point to
        # the barrel/shaft rather than the tip depending on dart angle.
        yolo_tips: List[Tuple[Tuple[float, float], float]] = []
        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            cx = float((x1 + x2) / 2.0)
            cy = float((y1 + y2) / 2.0)
            conf = float(box.conf[0])

            try:
                mm = calibrator.transform_to_mm(cx, cy)
            except Exception:
                continue

            # Discard detections outside the board scoring area — these
            # are the barrel/shaft sticking out above the board surface.
            r = math.hypot(mm[0], mm[1])
            if r > self.BOARD_VALID_R_MM:
                continue

            yolo_tips.append((mm, conf))

        if not yolo_tips:
            return None

        # --- Filter out exact duplicates of old (previously scored) darts ---
        new_tips: List[Tuple[Tuple[float, float], float]] = []
        for mm, conf in yolo_tips:
            is_old = any(
                math.hypot(mm[0] - sx, mm[1] - sy) < self.OLD_TIP_DUPLICATE_MM
                for sx, sy in scored_tips_mm
            )
            if not is_old:
                new_tips.append((mm, conf))

        if not new_tips:
            return None

        # --- Convert CV tip to mm for comparison -----------------------
        cv_mm = calibrator.board_px_to_mm(cv_tip_warped[0], cv_tip_warped[1])

        # --- Find the YOLO detection closest to the CV tip -------------
        best_mm, best_conf = min(
            new_tips,
            key=lambda t: math.hypot(t[0][0] - cv_mm[0], t[0][1] - cv_mm[1]),
        )
        dist = math.hypot(best_mm[0] - cv_mm[0], best_mm[1] - cv_mm[1])

        # Confirm if the closest YOLO detection is within the confirm radius.
        confirms = dist <= self.CONFIRM_RADIUS_MM

        return YoloResult(
            confirms=confirms,
            confidence=best_conf,
            distance_mm=dist,
            nearest_mm=best_mm,
        )
