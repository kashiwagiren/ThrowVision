"""ThrowVision -- Dart Detector.

Implements the per-camera detection pipeline with a state machine:
    WAIT -> STABLE -> DART / HAND / TAKEOUT -> WAIT

Key features
-------------
* **Threaded frame reading** -- each camera runs its own grab thread so
  USB I/O never blocks the main loop.
* **Resolution fallback** -- if a camera can't open at the requested
  resolution, lower resolutions are tried automatically.
* **MJPEG preferred** -- we ask for MJPEG every time (saves USB 2.0
  bandwidth), but fall back gracefully if the camera ignores it.
* **Robust opener** -- sequential per-backend, per-resolution attempts
  with retries and settle delays.
"""

from enum import Enum, auto
from threading import Thread, Lock
from typing import List, Optional, Tuple
import time

import cv2
import numpy as np

from calibrator import BoardCalibrator
from config import ConfigManager, RESOLUTION_LADDER

# Morphological kernel shared across instances
_MORPH_KERNEL = np.ones((5, 5), np.uint8)

# MJPEG fourcc
_MJPG = cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')

# Backends to try (DSHOW fastest on Windows, MSMF fallback, ANY last)
_BACKENDS = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
_BACKEND_NAMES = {cv2.CAP_DSHOW: 'DSHOW', cv2.CAP_MSMF: 'MSMF',
                  cv2.CAP_ANY: 'ANY'}


def _fourcc_str(code: int) -> str:
    """Convert an integer fourcc to a 4-char string."""
    chars = []
    for i in range(4):
        c = (code >> 8 * i) & 0xFF
        chars.append(chr(c) if 32 <= c < 127 else '?')
    return "".join(chars)


# ======================================================================
# Threaded camera reader
# ======================================================================

class _CameraThread:
    """Continuously grabs frames in a background thread.

    The main loop calls ``read()`` which always returns the latest
    frame *instantly* without blocking on USB I/O.
    """

    def __init__(self, cap: cv2.VideoCapture) -> None:
        self.cap = cap
        self._frame: Optional[np.ndarray] = None
        self._ok = False
        self._lock = Lock()
        self._stopped = False
        self._thread = Thread(target=self._update, daemon=True)

    def start(self) -> "_CameraThread":
        self._thread.start()
        return self

    def _update(self) -> None:
        while not self._stopped:
            ok, frame = self.cap.read()
            with self._lock:
                self._ok = ok
                self._frame = frame

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        with self._lock:
            return self._ok, self._frame

    def stop(self) -> None:
        self._stopped = True
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def release(self) -> None:
        self.stop()
        self.cap.release()


# ======================================================================
# State enum
# ======================================================================

class State(Enum):
    WAIT = auto()
    STABLE = auto()
    DART = auto()
    HAND = auto()
    TAKEOUT = auto()


# ======================================================================
# DartDetector -- one per camera
# ======================================================================

class DartDetector:
    """Per-camera detection pipeline.

    Parameters
    ----------
    cam_id : int      -- OpenCV VideoCapture index
    cfg    : ConfigManager
    cal    : BoardCalibrator (must already be calibrated)
    """

    def __init__(self, cam_id: int, cfg: ConfigManager,
                 cal: BoardCalibrator, verbose: bool = False) -> None:
        self.cam_id = cam_id
        self.cfg = cfg
        self.cal = cal
        self.state = State.WAIT
        self.active = False
        self.verbose = verbose

        # Camera + threaded reader (set by open_camera)
        self.cap: Optional[cv2.VideoCapture] = None
        self._reader: Optional[_CameraThread] = None
        self.actual_resolution: Optional[Tuple[int, int]] = None

        # Reference frames (computed from warped images)
        self._ref_motion: Optional[np.ndarray] = None
        self._ref_detect: Optional[np.ndarray] = None
        self._ref_raw_gray: Optional[np.ndarray] = None   # raw cam gray

        # Per-frame outputs
        self.last_frame: Optional[np.ndarray] = None
        self.warped_frame: Optional[np.ndarray] = None   # current warped
        self.warped_ref: Optional[np.ndarray] = None     # reference warped
        self.warped_prev: Optional[np.ndarray] = None    # previous warped
        self.diff_frame: Optional[np.ndarray] = None
        self.motion_frame: Optional[np.ndarray] = None
        self.motion_change: int = 0

        # Dart outputs
        self.dart_roi: Optional[np.ndarray] = None
        self.dart_tip: Optional[Tuple[float, float]] = None
        self.dart_vector: Optional[Tuple[float, float]] = None

        # Stability tracking (Autodarts needs ~10-18 iterations to
        # reach 0.99 correlation -- we require 4 consecutive above
        # threshold for reliability)
        self._prev_motion: Optional[np.ndarray] = None
        self._stable_count: int = 0
        self._STABLE_FRAMES_NEEDED: int = 10  # raised: dart must be fully still (no blur)

        # Cooldown
        self._cooldown: int = 0
        self._COOLDOWN_FRAMES: int = 30   # ~1 sec at 30fps -- time for hand to withdraw

        # Hand-detection hysteresis counter.
        # A dart throw creates a brief motion spike (1-3 frames) that should
        # NOT be classified as a hand.  Require this many consecutive frames
        # of sustained large motion before committing to State.HAND.
        # At 30 fps, 4 frames = 133 ms -- longer than any throw transient.
        self._hand_count: int = 0
        self._HAND_FRAMES_NEEDED: int = 4

        # Board mask area (pixels) -- used to detect stale reference.
        # Computed lazily after calibration is available.
        self._board_mask_area: int = 0
        self._motion_mask_area: int = 0

        # Previously scored tip positions (warped px) -- used to
        # identify which contour is the *new* dart when multiple
        # dart-sized blobs appear in the diff.
        self._scored_tips: List[Tuple[float, float]] = []

        # Number of grab-and-discard cycles after a reference update
        # so the reference truly reflects the settled board state.
        self._settle_frames: int = 0
        self._SETTLE_FRAME_COUNT: int = 12    # ~400ms at 30fps -- dart wobble + auto-exposure

        # Actual camera FPS (from OpenCV, set on open)
        self.camera_fps: float = 0.0
        # Contour area of the detected dart (for reliability filtering)
        self.dart_area: int = 0
        # How the tip was found: 'PROFILE' | 'PROXIMITY' | 'WARPED' | 'NONE'
        self.dart_tip_method: str = 'NONE'
        # Direct mm coordinates from raw->mm transform (center=0,0)
        self.dart_tip_mm: Optional[Tuple[float, float]] = None
        # Gaussian-blurred diff image (stored for gftt secondary estimate)
        self._diff_blur: Optional[np.ndarray] = None

        self._last_step_frame_id: int = 0
        self._last_raw_frame_id: int = 0

        # Per-camera health metrics -- track detection reliability
        self._health_frames: int = 0      # total frames processed
        self._health_darts: int = 0       # times reached DART state
        self._health_grab_fails: int = 0  # grab() returned None
        self._health_motion_hits: int = 0 # motion refined tip succeeded
        self._consecutive_grab_fails: int = 0
        self._GRAB_FAIL_THRESHOLD: int = 150  # ~5s at 30fps -> mark inactive

        # -- MOG2 adaptive background subtractor --
        # Maintains a per-pixel Gaussian mixture model that adapts to
        # gradual lighting changes and sensor noise.  Much more robust
        # than simple absdiff for isolating the dart foreground.
        #   history   = 300 frames (~10s at 30fps) -- how fast it learns
        #   varThreshold = 25 -- sensitivity (lower = more sensitive)
        #   detectShadows = False -- shadows not relevant on a dartboard
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=25, detectShadows=False)
        self._bg_sub_ready: bool = False  # set True after first reference
        self._fg_mask: Optional[np.ndarray] = None  # latest MOG2 foreground mask (raw space)

        # Cross-camera mask intersection: raw-space binary diff mask of the
        # detected dart.  Stored when a dart is found so server.py can warp
        # each camera's mask to board space and AND them -- shaft pixels
        # cancel out (different parallax per camera), tip pixels survive.
        self._dart_mask_raw: Optional[np.ndarray] = None

    # ==================================================================
    # Camera opening
    # ==================================================================

    def _try_open_atomic(self, backend: int, res_w: int, res_h: int,
                         fps: int) -> Optional[cv2.VideoCapture]:
        """Open camera with atomic params -- best chance of getting MJPEG.

        OpenCV 4.5+ supports passing properties to ``cap.open()`` as a
        flat list: [prop_id, value, prop_id, value, ...].  The backend
        builds its filter graph / media-type negotiation with these
        hints *before* streaming starts, giving it the best chance to
        select the MJPEG pin instead of falling back to YUY2.
        """
        cap = cv2.VideoCapture()
        try:
            ok = cap.open(self.cam_id, backend, [
                cv2.CAP_PROP_FOURCC, float(_MJPG),
                cv2.CAP_PROP_FRAME_WIDTH, float(res_w),
                cv2.CAP_PROP_FRAME_HEIGHT, float(res_h),
                cv2.CAP_PROP_FPS, float(fps),
            ])
        except (TypeError, cv2.error):
            # Older OpenCV without params support
            cap.release()
            return None

        if not ok or not cap.isOpened():
            cap.release()
            return None

        # Quick test -- one good frame is enough
        time.sleep(0.15)
        for _ in range(5):
            ret, frame = cap.read()
            if ret and frame is not None:
                return cap
            time.sleep(0.1)

        cap.release()
        return None

    def _try_open_sequential(self, backend: int, res_w: int, res_h: int,
                             fps: int, timeout: float = 6.0
                             ) -> Optional[cv2.VideoCapture]:
        """Fallback: open + set(). Wrapped in a thread with a hard
        timeout so a saturated USB bus can never hang the process."""
        result: list = [None]

        def _attempt() -> None:
            try:
                cap = cv2.VideoCapture(self.cam_id, backend)
                if not cap.isOpened():
                    cap.release()
                    return
                cap.set(cv2.CAP_PROP_FOURCC, _MJPG)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, res_w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res_h)
                cap.set(cv2.CAP_PROP_FPS, fps)
                time.sleep(0.15)
                for _ in range(5):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        result[0] = cap
                        return
                    time.sleep(0.1)
                cap.release()
            except Exception:
                pass

        t = Thread(target=_attempt, daemon=True)
        t.start()
        t.join(timeout=timeout)
        return result[0]

    def open_camera(self) -> bool:
        """Open the camera (verify with test frames) but do NOT start
        the reader thread.

        Tries MJPEG first (lower CPU), accepts YUY2 at full fps too
        -- each camera should be on its own USB controller.

        Call ``start_reader()`` after ALL cameras have been opened.
        Returns True on success.
        """
        # Reset any stale cap from a previous close before trying to open
        self.cap = None
        w, h = self.cfg.resolution

        resolutions = [(w, h)]
        for rw, rh in RESOLUTION_LADDER:
            if (rw, rh) != (w, h):
                resolutions.append((rw, rh))

        fps_options = sorted({self.cfg.fps, 30, 10, 5}, reverse=True)

        for res_w, res_h in resolutions:
            for try_fps in fps_options:
                for backend in [cv2.CAP_DSHOW, cv2.CAP_MSMF]:
                    bname = _BACKEND_NAMES.get(backend, str(backend))

                    # Strategy 1: atomic params (MJPEG most likely)
                    cap = self._try_open_atomic(
                        backend, res_w, res_h, try_fps)

                    # Strategy 2: sequential set() with timeout
                    if cap is None:
                        cap = self._try_open_sequential(
                            backend, res_w, res_h, try_fps,
                            timeout=6.0)

                    if cap is None:
                        continue

                    # Success -- record settings (don't start reader yet)
                    act_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    act_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    act_fps = cap.get(cv2.CAP_PROP_FPS)
                    act_fourcc = _fourcc_str(
                        int(cap.get(cv2.CAP_PROP_FOURCC)))

                    self.cap = cap
                    self.actual_resolution = (act_w, act_h)
                    self.active = True
                    self.camera_fps = act_fps

                    print(f"[CAM] Camera {self.cam_id}: opened via "
                          f"{bname} ({act_w}x{act_h} @{act_fps:.0f}"
                          f"fps, fourcc={act_fourcc})")
                    return True

        print(f"[CAM] Camera {self.cam_id}: FAILED to open")
        return False

    def start_reader(self) -> None:
        """Start the background reader thread.

        Called separately from ``open_camera()`` so that all cameras
        can be opened first (claiming USB bandwidth) before any of
        them begin continuous streaming.
        """
        if self.cap is not None and self._reader is None:
            self._reader = _CameraThread(self.cap).start()

    # ==================================================================
    # Frame grab (threaded)
    # ==================================================================

    def _grab(self) -> Optional[np.ndarray]:
        """Grab the latest frame (non-blocking via reader thread).

        Resizes to cfg.resolution and warps to the top-down board
        view.  All detection stages operate on the warped image.
        """
        if self._reader is None:
            return None

        ok, frame = self._reader.read()
        if not ok or frame is None:
            return None

        frame_id = id(frame)
        if self._last_raw_frame_id == frame_id:
            return self.last_frame
        self._last_raw_frame_id = frame_id

        self.active = True
        w, h = self.cfg.resolution
        if frame.shape[1] != w or frame.shape[0] != h:
            frame = cv2.resize(frame, (w, h))
        self.last_frame = frame

        # Warp to top-down board view (used by every detection stage)
        if self.cal.is_calibrated:
            self.warped_frame = self.cal.unwarp(frame)
        return frame

    # ==================================================================
    # Grayscale + mask helpers
    # ==================================================================

    def _to_motion(self, warped: np.ndarray) -> np.ndarray:
        """Convert a *warped* BGR frame to motion-resolution grayscale."""
        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        mw, mh = self.cfg.motion_resolution
        small = cv2.resize(gray, (mw, mh))
        mask_small = cv2.resize(self.cal.board_mask, (mw, mh))
        return cv2.bitwise_and(small, mask_small)

    def _to_detect(self, warped: np.ndarray) -> np.ndarray:
        """Convert a *warped* BGR frame to full-res detection grayscale."""
        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        mask = self.cal.board_mask
        if gray.shape != mask.shape:
            mask = cv2.resize(mask, (gray.shape[1], gray.shape[0]))
        return cv2.bitwise_and(gray, mask)

    # ==================================================================
    # Scored-region exclusion mask
    # ==================================================================

    _EXCLUSION_RADIUS_WARPED: int = 35  # ~15mm at 1080 board_size

    def _build_scored_exclusion_mask(
        self, shape: tuple, space: str = 'warped',
    ) -> Optional[np.ndarray]:
        """Create a keep-mask (255=keep, 0=exclude) with black circles
        at previously scored dart positions."""
        if not self._scored_tips:
            return None
        h, w = shape[:2]
        mask = np.full((h, w), 255, dtype=np.uint8)
        bs = self.cal.board_size

        for wx, wy in self._scored_tips:
            if space == 'warped':
                cx, cy = int(round(wx)), int(round(wy))
                radius = self._EXCLUSION_RADIUS_WARPED
            elif space == 'motion':
                mw, mh = self.cfg.motion_resolution
                cx = int(round(wx * mw / bs))
                cy = int(round(wy * mh / bs))
                radius = max(14, int(self._EXCLUSION_RADIUS_WARPED * mw / bs))
            elif space == 'raw':
                if self.cal._M_inv is None:
                    continue
                pt = np.array([[[wx, wy]]], dtype=np.float32)
                pt_raw = cv2.perspectiveTransform(pt, self.cal._M_inv)
                cx = int(round(pt_raw[0, 0, 0]))
                cy = int(round(pt_raw[0, 0, 1]))
                raw_long = max(self.cfg.resolution)
                scale = raw_long / bs
                radius = int(self._EXCLUSION_RADIUS_WARPED * scale)
            else:
                continue
            if 0 <= cx < w and 0 <= cy < h:
                cv2.circle(mask, (cx, cy), radius, 0, thickness=-1)
        return mask

    # ==================================================================
    # Reference frame
    # ==================================================================

    _REF_AVG_FRAMES: int = 4   # average N frames to reduce sensor noise

    def capture_reference(self) -> None:
        frame = self._grab()
        if frame is not None and self.warped_frame is not None:
            self._ref_motion = self._to_motion(self.warped_frame)
            self._ref_detect = self._to_detect(self.warped_frame)
            self.warped_ref = self.warped_frame.copy()

            # Multi-frame averaging for raw reference -- reduces sensor noise
            # so the frame-diff is cleaner and motion contour more precise.
            if self.last_frame is not None:
                acc = cv2.cvtColor(
                    self.last_frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
                for _ in range(self._REF_AVG_FRAMES - 1):
                    f = self._grab()
                    if f is not None and self.last_frame is not None:
                        acc += cv2.cvtColor(
                            self.last_frame, cv2.COLOR_BGR2GRAY).astype(
                                np.float32)
                    else:
                        break
                self._ref_raw_gray = (acc / self._REF_AVG_FRAMES).astype(
                    np.uint8)

            # Prime MOG2 background model with the clean reference frame.
            # Feed the same frame multiple times so MOG2 treats it as stable
            # background immediately (learningRate=1.0 forces full adoption).
            if self.last_frame is not None:
                raw_gray = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)
                for _ in range(5):
                    self._bg_sub.apply(raw_gray, learningRate=1.0)
                self._bg_sub_ready = True

            # Cache mask areas (only once)
            if self._board_mask_area == 0:
                self._board_mask_area = cv2.countNonZero(
                    self.cal.board_mask)
                mw, mh = self.cfg.motion_resolution
                mask_small = cv2.resize(self.cal.board_mask, (mw, mh))
                self._motion_mask_area = cv2.countNonZero(mask_small)

    def update_reference(self) -> None:
        """Update reference from the latest warped frame.

        Triggers a short settle period so the next few frames are
        discarded before detection resumes -- this lets the camera
        auto-exposure and dart wobble settle.
        """
        if self.warped_frame is None:
            return
        self._ref_motion = self._to_motion(self.warped_frame)
        self._ref_detect = self._to_detect(self.warped_frame)
        self.warped_ref = self.warped_frame.copy()
        # Multi-frame averaged raw reference
        if self.last_frame is not None:
            acc = cv2.cvtColor(
                self.last_frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            grabbed = 1
            for _ in range(self._REF_AVG_FRAMES - 1):
                f = self._grab()
                if f is not None and self.last_frame is not None:
                    acc += cv2.cvtColor(
                        self.last_frame, cv2.COLOR_BGR2GRAY).astype(
                            np.float32)
                    grabbed += 1
            self._ref_raw_gray = (acc / grabbed).astype(np.uint8)
        self._settle_frames = self._SETTLE_FRAME_COUNT
        self._dart_mask_raw = None  # clear stale mask for next dart

    # ==================================================================
    # State machine -- single step (non-blocking)
    # ==================================================================

    def step(self) -> State:
        if self._reader is not None:
            _, raw_frame = self._reader.read()
            if raw_frame is not None:
                fid = id(raw_frame)
                if self._last_step_frame_id == fid:
                    return self.state
                self._last_step_frame_id = fid

        frame = self._grab()
        if frame is None:
            self._health_grab_fails += 1
            self._consecutive_grab_fails += 1
            if (self._consecutive_grab_fails >= self._GRAB_FAIL_THRESHOLD
                    and self.active):
                self.active = False
                print(f"[HEALTH] Cam {self.cam_id}: marked INACTIVE after "
                      f"{self._consecutive_grab_fails} consecutive grab "
                      f"failures")
            return self.state

        # Successful grab -- reset consecutive failure counter
        if self._consecutive_grab_fails > 0:
            if not self.active:
                print(f"[HEALTH] Cam {self.cam_id}: recovered after "
                      f"{self._consecutive_grab_fails} grab failures, "
                      f"marking ACTIVE")
            self._consecutive_grab_fails = 0
        self._health_frames += 1

        # -- Feed MOG2 background model continuously --
        # Low learning rate during WAIT (slow adaptation to lighting).
        # High learning rate during settle (absorb dart into background).
        if self._bg_sub_ready and self.last_frame is not None:
            raw_gray = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)
            lr = 0.005 if self._settle_frames == 0 else 0.5
            self._fg_mask = self._bg_sub.apply(raw_gray, learningRate=lr)

        if self._ref_motion is None:
            self.capture_reference()
            return self.state

        # Settle period -- grab fresh frames but don't detect
        if self._settle_frames > 0:
            self._settle_frames -= 1
            if self._settle_frames == 0:
                # Re-snapshot the reference now that the board has
                # settled (dart wobble / auto-exposure done)
                if self.warped_frame is not None:
                    self._ref_motion = self._to_motion(self.warped_frame)
                    self._ref_detect = self._to_detect(self.warped_frame)
                    self.warped_ref = self.warped_frame.copy()
                    if self.last_frame is not None:
                        # Multi-frame averaging for cleaner raw reference
                        acc = cv2.cvtColor(
                            self.last_frame, cv2.COLOR_BGR2GRAY
                        ).astype(np.float32)
                        grabbed = 1
                        for _ in range(self._REF_AVG_FRAMES - 1):
                            f = self._grab()
                            if f is not None and self.last_frame is not None:
                                acc += cv2.cvtColor(
                                    self.last_frame, cv2.COLOR_BGR2GRAY
                                ).astype(np.float32)
                                grabbed += 1
                        self._ref_raw_gray = (acc / grabbed).astype(
                            np.uint8)
                        # Absorb averaged frame into MOG2
                        self._bg_sub.apply(self._ref_raw_gray,
                                           learningRate=1.0)
            return self.state

        if self._cooldown > 0:
            self._cooldown -= 1
            return self.state

        if self.state in (State.WAIT, State.HAND, State.TAKEOUT):
            self._step_wait(frame)
        elif self.state == State.STABLE:
            self._step_stable(frame)
        return self.state

    # ---- WAIT ----------------------------------------------------------

    def _step_wait(self, frame: np.ndarray) -> None:
        if self.warped_frame is None:
            return
        motion = self._to_motion(self.warped_frame)
        diff = cv2.absdiff(motion, self._ref_motion)
        _, thresh = cv2.threshold(
            diff, self.cfg.absdiff_threshold, 255, cv2.THRESH_BINARY)
        # NOTE: MOG2 fusion is NOT applied here -- _step_wait must be
        # maximally sensitive to catch ANY motion, including from cameras
        # with slightly misaligned calibrations.  MOG2 filtering is
        # applied later in _motion_refined_tip() for tip precision.

        # Exclude previously-scored dart regions from motion diff
        excl = self._build_scored_exclusion_mask(thresh.shape, space='motion')
        if excl is not None:
            thresh = cv2.bitwise_and(thresh, excl)

        change = cv2.countNonZero(thresh)

        self.motion_frame = cv2.resize(thresh, self.cfg.detection_resolution)
        self.motion_change = change

        # ---- Stale-reference guard ------------------------------------
        # If > 30 % of the board mask is "changed", the reference is
        # completely outdated (auto-exposure shift, lighting change,
        # etc.).  Re-capture immediately instead of cycling forever.
        if self._motion_mask_area > 0:
            frac = change / self._motion_mask_area
            if frac > 0.30:
                if self.verbose:
                    print(f"[DET] Cam {self.cam_id}: stale ref "
                          f"({frac:.0%} of board changed) -- recapturing")
                self.update_reference()
                self._stable_count = 0
                self._prev_motion = None
                return

        # At 1/4 resolution a dart body is only ~10-40 changed
        # pixels.  Autodarts uses a very small motion resolution
        # (212x120) with a low threshold.
        #
        # A hand sweeping across the board at motion resolution covers
        # 10%+ of the board area -- much larger than any dart impact (1-5%).
        #
        # KEY FIX: use temporal hysteresis.  A dart throw arm sweeps past
        # the camera for only 1-3 frames (< 100 ms at 30 fps) then motion
        # drops.  A real hand resting/retrieving darts creates SUSTAINED
        # large motion over many consecutive frames.  Requiring
        # _HAND_FRAMES_NEEDED consecutive frames above the threshold before
        # committing to State.HAND eliminates false positives from the
        # throwing motion itself.
        if self._motion_mask_area > 0:
            hand_frac = change / self._motion_mask_area
            if hand_frac > 0.10:  # >10 % of board = candidate hand frame
                self._hand_count += 1
                if self.verbose:
                    print(f"[DET] Cam {self.cam_id}: large motion "
                          f"({hand_frac:.1%}, count={self._hand_count}/"
                          f"{self._HAND_FRAMES_NEEDED})")
                if self._hand_count >= self._HAND_FRAMES_NEEDED:
                    # Sustained large motion confirmed -- real hand present
                    print(f"[DET] Cam {self.cam_id}: HAND confirmed "
                          f"({hand_frac:.1%} for {self._hand_count} frames)")
                    self.state = State.HAND
                    self._stable_count = 0
                    self._prev_motion = None
                    self._hand_count = 0
                # Either way, don't start dart stability tracking while
                # large motion is ongoing
                return
            else:
                # Motion dropped below threshold -- reset hand counter.
                # If this was a brief throw transient it will naturally
                # fall through to the dart stabilisation logic below.
                self._hand_count = 0

        if change > 25:   # raised from 15 -- ignore camera vibration / lighting flicker
            if self._prev_motion is not None:
                result = cv2.matchTemplate(
                    motion, self._prev_motion, cv2.TM_CCOEFF_NORMED)
                corr = result[0, 0]
                if corr > self.cfg.stability_correlation:
                    self._stable_count += 1
                    if self._stable_count == 1 and self.verbose:
                        print(f"[DET] Cam {self.cam_id}: motion={change}px, "
                              f"stabilising (corr={corr:.3f})...")
                else:
                    self._stable_count = 0
            else:
                self._stable_count = 0

            self._prev_motion = motion
            self.warped_prev = self.warped_frame.copy()

            if self._stable_count >= self._STABLE_FRAMES_NEEDED:
                self.state = State.STABLE
                self._stable_count = 0
                self._prev_motion = None
                if self.verbose:
                    print(f"[DET] Cam {self.cam_id}: STABLE -> evaluating")
                self._step_stable(frame)
                return
        else:
            self._stable_count = 0
            self._prev_motion = None

    # ---- STABLE --------------------------------------------------------

    def _step_stable(self, frame: Optional[np.ndarray]) -> None:
        if frame is None or self.warped_frame is None:
            return
        detect = self._to_detect(self.warped_frame)
        if self._ref_detect is None:
            return

        diff = cv2.absdiff(detect, self._ref_detect)
        blur = cv2.GaussianBlur(diff, self.cfg.blur_kernel, 0)
        self._diff_blur = blur          # store for gftt in _extract_dart
        _, binary = cv2.threshold(
            blur, self.cfg.binary_thresh, 255, cv2.THRESH_BINARY)
        # NOTE: MOG2 fusion is NOT applied to _step_stable -- we need ALL
        # contour pixels for accurate dart/hand/takeout classification.
        # MOG2 filtering is used later in _motion_refined_tip() only.

        dilated = cv2.dilate(binary, _MORPH_KERNEL, iterations=1)
        cleaned = cv2.erode(dilated, _MORPH_KERNEL, iterations=1)

        # Exclude previously-scored dart regions
        excl = self._build_scored_exclusion_mask(cleaned.shape, space='warped')
        if excl is not None:
            cleaned = cv2.bitwise_and(cleaned, excl)

        self.diff_frame = cleaned

        contours, _ = cv2.findContours(
            cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Check if any dart-sized contour survived exclusion
        has_dart_sized = any(
            self.cfg.dart_size_min < cv2.contourArea(c) < self.cfg.dart_size_max
            for c in contours
        ) if contours else False

        if not contours or (excl is not None and not has_dart_sized):
            # Fallback: exclusion ate all dart-sized blobs.
            # This happens when a new dart lands in (or very close to) the
            # exclusion zone of a previously-scored dart — e.g. three darts
            # in the same small segment.  Retry on the full binary and let
            # the novelty filter in _classify_blobs pick the newest contour.
            if excl is not None:
                cleaned_full = cv2.erode(
                    cv2.dilate(binary, _MORPH_KERNEL, iterations=1),
                    _MORPH_KERNEL, iterations=1)
                contours, _ = cv2.findContours(
                    cleaned_full, cv2.RETR_EXTERNAL,
                    cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                self.state = State.WAIT
                return

        self._classify_blobs(contours, self.warped_frame)

    # ---- Blob classification ----------------------------------------------

    def _classify_blobs(self, contours: list,
                        frame: np.ndarray) -> None:
        areas = [cv2.contourArea(c) for c in contours]

        if self.verbose:
            print(f"[DET] Cam {self.cam_id}: {len(contours)} contours, "
                  f"areas={[int(a) for a in sorted(areas, reverse=True)[:5]]}")

        large = [a for a in areas if a > self.cfg.hand_size_max]
        if large:
            self.state = State.HAND
            return

        # Also classify as HAND if combined area of all blobs is too large
        # (hand creates many medium contours that individually look dart-sized)
        total_area = sum(areas)
        if total_area > self.cfg.hand_size_max:
            self.state = State.HAND
            return

        darts = [(c, a) for c, a in zip(contours, areas)
                 if self.cfg.dart_size_min < a < self.cfg.dart_size_max]

        if not darts:
            total = sum(areas)
            if total > self.cfg.hand_size_max:
                self.state = State.TAKEOUT
                self.capture_reference()
            else:
                self.state = State.WAIT
            return

        darts.sort(key=lambda ca: ca[1], reverse=True)

        # --- Prefer the NEWEST dart contour ----------------------------
        # When previous darts are on the board, residual diffs can
        # produce multiple dart-sized blobs.  If we have scored tips
        # from earlier throws, pick the contour whose centroid is
        # furthest from all previously scored positions (= new dart).
        if len(darts) > 1 and self._scored_tips:
            bs_half = self.cal.board_size / 2.0
            board_radius_w = bs_half * 0.90  # 90% of warped radius = on-board

            def _novelty(contour_area):
                c = contour_area[0]
                M = cv2.moments(c)
                if M["m00"] > 0:
                    cx = M["m10"] / M["m00"]
                    cy = M["m01"] / M["m00"]
                else:
                    pts = c.reshape(-1, 2)
                    cx, cy = pts.mean(axis=0)
                # Tier 1: blobs outside the board circle (flights, edge noise)
                # are deprioritized with a large negative offset so they always
                # rank below on-board candidates regardless of their min_d.
                dist_from_centre = np.hypot(cx - bs_half, cy - bs_half)
                off_board_penalty = -1e6 if dist_from_centre > board_radius_w else 0.0
                # Tier 2: among on-board blobs, prefer the one furthest from
                # all previously scored tips (= the newest dart).
                min_d = min(np.hypot(cx - t[0], cy - t[1])
                            for t in self._scored_tips)
                return off_board_penalty + min_d
            darts.sort(key=_novelty, reverse=True)


        contour = darts[0][0]
        self.dart_area = int(darts[0][1])

        self.state = State.DART
        self._health_darts += 1
        print(f"[DART] Cam {self.cam_id}: DART detected  "
              f"area={self.dart_area}")
        self._extract_dart(contour, frame)

    # ---- Motion-refined tip detection (PCA-based) --------------------------

    def _motion_refined_tip(
        self, bx1: int, by1: int, bx2: int, by2: int,
    ) -> Optional[Tuple[float, float]]:
        """Refine the dart tip by fusing a bounding box with motion (frame diff).

        The bounding box tells us WHERE the dart is, but its edge is
        only a rough approximation of the tip.  The frame-difference
        (motion) contour shows the *actual shape* of the dart -- narrow at
        the tip, wide at the flights.

        Pipeline:
          1.  Compute raw-space frame diff (current vs reference).
          2.  Mask the diff to the bbox region (with padding).
          3.  Find the motion contour within that region.
          4.  Fit a line (cv2.fitLine) through the motion blob to find the
              dart barrel axis.
          5.  The tip = the extremal blob pixels along the barrel axis on
              the side closer to the board centre.

        This produces a tip estimate that is MUCH more accurate than the
        bbox perimeter, because it uses the actual pixel-change shape
        rather than a rectangle.

        Returns (raw_tip_x, raw_tip_y) in raw camera pixels, or None if
        motion refinement is not possible (falls back to bbox method).
        """
        if self._ref_raw_gray is None or self.last_frame is None:
            return None

        # -- 1. Compute raw-space diff --
        raw_gray = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)
        raw_diff = cv2.absdiff(raw_gray, self._ref_raw_gray)
        raw_diff = cv2.GaussianBlur(raw_diff, (5, 5), 0)

        # Board mask (expanded to include dart sticking out)
        raw_mask = cv2.dilate(
            self.cal.raw_mask,
            np.ones((41, 41), np.uint8), iterations=1)
        raw_diff = cv2.bitwise_and(raw_diff, raw_mask)

        # -- 2. Constrain to bbox region --
        pad = 25
        h, w = raw_diff.shape[:2]
        bbox_mask = np.zeros((h, w), dtype=np.uint8)
        bbox_mask[max(0, by1 - pad):min(h, by2 + pad),
                  max(0, bx1 - pad):min(w, bx2 + pad)] = 255
        raw_diff = cv2.bitwise_and(raw_diff, bbox_mask)

        # -- 3. Threshold + MOG2 fusion + morphology --
        _, raw_thresh = cv2.threshold(
            raw_diff, self.cfg.absdiff_threshold, 255,
            cv2.THRESH_BINARY)

        # Fuse with MOG2 adaptive background subtractor for cleaner mask.
        # MOG2 adapts to gradual lighting/sensor changes that simple
        # absdiff misses, producing fewer false-positive pixels.
        if self._bg_sub_ready:
            # learningRate=0 -> query only, don't update the model
            fg_mask = self._bg_sub.apply(raw_gray, learningRate=0)
            # Apply same bbox + board masks to fg_mask
            fg_mask = cv2.bitwise_and(fg_mask, bbox_mask)
            fg_mask = cv2.bitwise_and(fg_mask, raw_mask)
            # AND: keep only pixels that BOTH methods agree are foreground
            raw_thresh = cv2.bitwise_and(raw_thresh, fg_mask)

        raw_thresh = cv2.morphologyEx(
            raw_thresh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        raw_thresh = cv2.dilate(
            raw_thresh, np.ones((3, 3), np.uint8), iterations=1)

        # Exclude previously-scored dart regions (raw camera space)
        excl_raw = self._build_scored_exclusion_mask(
            raw_thresh.shape, space='raw')
        if excl_raw is not None:
            raw_thresh = cv2.bitwise_and(raw_thresh, excl_raw)

        # Store for cross-camera mask intersection (server.py)
        self._dart_mask_raw = raw_thresh.copy()

        # -- 4. Find motion contours --
        contours_raw, _ = cv2.findContours(
            raw_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        dart_contours = [
            (c, cv2.contourArea(c)) for c in contours_raw
            if cv2.contourArea(c) > 50
        ]
        if not dart_contours:
            return None

        dart_contours.sort(key=lambda x: x[1], reverse=True)

        # Elongation pre-filter: dart shafts are narrow and elongated
        # (aspect ratio >> 1); flights are wide/fan-shaped (aspect ≈ 1.5).
        # When previous darts are on the board, filter to elongated blobs
        # only so flights are not picked as the "newest dart" contour.
        if self._scored_tips and len(dart_contours) > 1:
            def _aspect(ca):
                rect = cv2.minAreaRect(ca[0])
                _, (rw, rh), _ = rect
                return max(rw, rh, 1.0) / max(min(rw, rh), 1.0)
            elongated = [(c, a) for c, a in dart_contours if _aspect((c, a)) >= 2.5]
            if elongated:
                dart_contours = elongated

        # Novelty filter: prefer contour furthest from scored tips;
        # off-board blobs (flights, edge noise) are always deprioritized.
        if len(dart_contours) > 1 and self._scored_tips:
            _bs_half = self.cal.board_size / 2.0
            _board_r = _bs_half * 0.90
            def _novelty(ca):
                c = ca[0]
                M = cv2.moments(c)
                if M['m00'] > 0:
                    cx = M['m10'] / M['m00']
                    cy = M['m01'] / M['m00']
                else:
                    pts = c.reshape(-1, 2)
                    cx, cy = pts.mean(axis=0)
                pt = np.array([[[cx, cy]]], dtype=np.float32)
                bp = cv2.perspectiveTransform(pt, self.cal.matrix)
                bx, by = float(bp[0, 0, 0]), float(bp[0, 0, 1])
                off_board = (-1e6 if np.hypot(bx - _bs_half, by - _bs_half)
                             > _board_r else 0.0)
                return off_board + min(np.hypot(bx - t[0], by - t[1])
                                       for t in self._scored_tips)
            dart_contours.sort(key=_novelty, reverse=True)

        raw_c = dart_contours[0][0]
        raw_area = dart_contours[0][1]
        raw_pts = raw_c.reshape(-1, 2).astype(np.float64)

        if len(raw_pts) < 5:
            return None

        # -- 5. PCA axis endpoints -> project both to mm -> pick tip --
        # The dart tip is ON the board surface, so the homography maps it
        # correctly.  The barrel/flights are ABOVE the surface, so the
        # homography projects them to wrong locations (parallax).
        #
        # Strategy:
        #   1. PCA on raw contour -> major axis direction.
        #   2. Project the two extreme points along that axis to mm
        #      via transform_to_mm (direct raw->mm homography).
        #   3. The endpoint with smaller r_mm is the tip (correct
        #      surface projection), the other is the barrel (wrong).
        #
        # Fallback: if PCA fails, use the contour pixel closest to the
        # board centre in raw camera space (tip is always the end
        # closest to the bull).
        pts_xy = raw_pts.astype(np.float32)

        # PCA to find major axis
        mean_pt = pts_xy.mean(axis=0)
        centered = pts_xy - mean_pt
        cov = np.cov(centered.T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        major_axis = eigvecs[:, -1]  # largest eigenvalue

        # Project all contour points onto major axis
        projections = centered @ major_axis
        idx_min = int(np.argmin(projections))
        idx_max = int(np.argmax(projections))

        # Two extreme raw-space points (tip candidate A and B)
        endpt_a = pts_xy[idx_min]
        endpt_b = pts_xy[idx_max]

        # Average a small neighbourhood around each endpoint for stability
        n_avg = max(3, int(len(pts_xy) * 0.05))
        idx_near_a = np.argpartition(projections, n_avg)[:n_avg]
        idx_near_b = np.argpartition(-projections, n_avg)[:n_avg]
        endpt_a_avg = pts_xy[idx_near_a].mean(axis=0)
        endpt_b_avg = pts_xy[idx_near_b].mean(axis=0)

        # Project both endpoints to mm coordinates
        try:
            mm_a = self.cal.transform_to_mm(
                float(endpt_a_avg[0]), float(endpt_a_avg[1]))
            mm_b = self.cal.transform_to_mm(
                float(endpt_b_avg[0]), float(endpt_b_avg[1]))
            r_a = np.hypot(mm_a[0], mm_a[1])
            r_b = np.hypot(mm_b[0], mm_b[1])
        except Exception:
            # transform_to_mm failed -- use raw-space fallback
            bull_raw = self.cal.board_centre_cam
            d_a = np.hypot(endpt_a_avg[0] - bull_raw[0],
                           endpt_a_avg[1] - bull_raw[1])
            d_b = np.hypot(endpt_b_avg[0] - bull_raw[0],
                           endpt_b_avg[1] - bull_raw[1])
            tip_raw = endpt_a_avg if d_a < d_b else endpt_b_avg
            self._health_motion_hits += 1
            print(f"[FUSE] Cam {self.cam_id}: PCA raw-fallback "
                  f"area={raw_area:.0f} "
                  f"tip=({tip_raw[0]:.0f},{tip_raw[1]:.0f})")
            return (float(tip_raw[0]), float(tip_raw[1]))

        # Pick the endpoint closer to board centre in mm (that's the tip)
        if r_a <= r_b:
            tip_raw = endpt_a_avg
            r_tip, r_bar = r_a, r_b
        else:
            tip_raw = endpt_b_avg
            r_tip, r_bar = r_b, r_a

        # Sanity check: if both endpoints are far from center or the chosen
        # "tip" is beyond the board, fall back to raw-space closest pixel
        if r_tip > 180.0:
            bull_raw = self.cal.board_centre_cam
            dists_raw = np.hypot(pts_xy[:, 0] - bull_raw[0],
                                 pts_xy[:, 1] - bull_raw[1])
            n_close = max(3, int(len(dists_raw) * 0.05))
            close_idx = np.argpartition(dists_raw, n_close)[:n_close]
            tip_raw = pts_xy[close_idx].mean(axis=0)
            print(f"[FUSE] Cam {self.cam_id}: PCA tip off-board "
                  f"(r={r_tip:.0f}mm) -- raw-closest fallback "
                  f"tip=({tip_raw[0]:.0f},{tip_raw[1]:.0f})")
        else:
            print(f"[FUSE] Cam {self.cam_id}: PCA axis "
                  f"area={raw_area:.0f} n={len(pts_xy)} "
                  f"rTip={r_tip:.1f}mm rBar={r_bar:.1f}mm "
                  f"tip=({tip_raw[0]:.0f},{tip_raw[1]:.0f})")

        self._health_motion_hits += 1
        return (float(tip_raw[0]), float(tip_raw[1]))

    # ---- Cross-camera mask helper -------------------------------------------

    def get_dart_mask_board(self) -> Optional[np.ndarray]:
        """Warp the raw-space dart diff mask to board space.

        Used by the cross-camera intersection in server.py to eliminate
        shaft pixels: each camera's mask is warped to board space and
        ANDed together.  Shaft pixels project to different board-space
        locations per camera (parallax) and cancel out; the tip (on the
        board surface) projects consistently and survives.
        """
        if self._dart_mask_raw is None or self.cal._M is None:
            return None
        return cv2.warpPerspective(
            self._dart_mask_raw, self.cal._M,
            (self.cal.board_size, self.cal.board_size))

    # ---- Bounding-box tip helper -------------------------------------------

    def _bbox_closest_to_bull(
        self, bx1: int, by1: int, bx2: int, by2: int,
    ) -> Tuple[float, float]:
        """Return the point on the bbox perimeter closest to the board centre.

        Instead of only checking four edge midpoints, we clamp the board
        centre onto the rectangle perimeter.  This yields the true closest
        point for *any* relative position of the box and the bull,
        producing a more accurate tip estimate for angled darts.
        """
        bull_x, bull_y = self.cal.board_centre_cam  # raw cam pixels

        # Clamp bull to box interior -> nearest point on perimeter
        cx = float(np.clip(bull_x, bx1, bx2))
        cy = float(np.clip(bull_y, by1, by2))

        # If the bull is *inside* the box, project to the nearest edge
        if bx1 < bull_x < bx2 and by1 < bull_y < by2:
            # distances to each edge from the clamped (=bull) point
            d_left   = bull_x - bx1
            d_right  = bx2 - bull_x
            d_top    = bull_y - by1
            d_bottom = by2 - bull_y
            min_d = min(d_left, d_right, d_top, d_bottom)
            if min_d == d_left:
                cx = float(bx1)
            elif min_d == d_right:
                cx = float(bx2)
            elif min_d == d_top:
                cy = float(by1)
            else:
                cy = float(by2)

        return (cx, cy)

    # ---- Dart tip extraction -----------------------------------------------

    def _extract_dart(self, contour: np.ndarray,
                      warped: np.ndarray) -> None:
        """Extract dart tip using RAW camera space, then project to board.

        The perspective warp distorts the 3D dart shape (shaft/flights
        are above the board surface), making warped-space tip detection
        unreliable.  Instead we:

        1.  Compute a diff in **raw camera space** (before warping).
        2.  Find the dart contour in raw space where the dart looks
            natural -- clearly narrow at the tip, wide at the flights.
        3.  **Width-profile** along the PCA major axis to find the
            narrow (tip) end.
        4.  Project the raw-space tip point through the homography
            to get the board-surface position.

        This works because the tip IS on the board surface, so the
        homography maps it correctly.
        """
        bs = self.cal.board_size

        # --- small ROI for debug display --------------------------------
        x, y, rw, rh = cv2.boundingRect(contour)
        pad = 15
        x0 = max(x - pad, 0)
        y0 = max(y - pad, 0)
        x1 = min(x + rw + pad, bs)
        y1 = min(y + rh + pad, bs)
        gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        self.dart_roi = gray_w[y0:y1, x0:x1]

        # ==============================================================
        # PRIMARY: Detect tip in RAW camera space
        # ==============================================================
        if (self._ref_raw_gray is not None
                and self.last_frame is not None):
            raw_gray = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)
            raw_diff = cv2.absdiff(raw_gray, self._ref_raw_gray)
            raw_diff = cv2.GaussianBlur(raw_diff, (5, 5), 0)

            # Expanded raw mask -- include dart sticking out of board
            raw_mask = cv2.dilate(
                self.cal.raw_mask,
                np.ones((41, 41), np.uint8), iterations=1)
            raw_diff = cv2.bitwise_and(raw_diff, raw_mask)

            _, raw_thresh = cv2.threshold(
                raw_diff, self.cfg.absdiff_threshold, 255,
                cv2.THRESH_BINARY)
            raw_thresh = cv2.morphologyEx(
                raw_thresh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            raw_thresh = cv2.dilate(
                raw_thresh, np.ones((3, 3), np.uint8), iterations=1)

            # Store FULL mask (before exclusion) for cross-camera intersection.
            # The exclusion zeroes the tip region when darts cluster, which is
            # exactly the area the Xcam vote needs — storing pre-exclusion lets
            # the 2-of-3 vote register at the real tip position.
            self._dart_mask_raw = raw_thresh.copy()

            # Exclude previously-scored dart regions (raw camera space) —
            # applied AFTER storing the mask for Xcam so individual tip
            # detection still avoids noisy residuals near scored positions.
            excl_raw = self._build_scored_exclusion_mask(
                raw_thresh.shape, space='raw')
            if excl_raw is not None:
                raw_thresh = cv2.bitwise_and(raw_thresh, excl_raw)


            contours_raw, _ = cv2.findContours(
                raw_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filter to dart-sized contours in raw space
            dart_contours = [
                (c, cv2.contourArea(c)) for c in contours_raw
                if cv2.contourArea(c) > 100   # lowered: allow smaller raw blobs
            ]

            if dart_contours:
                dart_contours.sort(key=lambda x: x[1], reverse=True)

                # Elongation pre-filter: dart shafts are narrow and elongated
                # (aspect ratio ≥ 2.5); flights are wider (aspect ≈ 1.5).
                # Filter to elongated blobs only when other darts already scored.
                if self._scored_tips and len(dart_contours) > 1:
                    def _aspect_r(ca):
                        rect = cv2.minAreaRect(ca[0])
                        _, (rw, rh), _ = rect
                        return max(rw, rh, 1.0) / max(min(rw, rh), 1.0)
                    elongated = [(c, a) for c, a in dart_contours
                                 if _aspect_r((c, a)) >= 2.5]
                    if elongated:
                        dart_contours = elongated

                # -- Novelty filter: prefer the NEWEST dart;
                # off-board blobs (flights, edge noise) always deprioritized.
                # When previous darts are on the board, the diff picks
                # up ALL darts.  Project each raw centroid -> warped
                # board space & pick the one most distant from already-
                # scored tips (= the new dart).
                if len(dart_contours) > 1 and self._scored_tips:
                    _bs_half = self.cal.board_size / 2.0
                    _board_r = _bs_half * 0.90
                    def _raw_novelty(ca):
                        c = ca[0]
                        M = cv2.moments(c)
                        if M['m00'] > 0:
                            cx = M['m10'] / M['m00']
                            cy = M['m01'] / M['m00']
                        else:
                            pts = c.reshape(-1, 2)
                            cx, cy = pts.mean(axis=0)
                        # Project to warped board space for comparison
                        pt = np.array([[[cx, cy]]], dtype=np.float32)
                        bp = cv2.perspectiveTransform(pt, self.cal.matrix)
                        bx, by = float(bp[0, 0, 0]), float(bp[0, 0, 1])
                        off_board = (-1e6 if np.hypot(bx - _bs_half,
                                                      by - _bs_half)
                                     > _board_r else 0.0)
                        return off_board + min(
                            np.hypot(bx - t[0], by - t[1])
                            for t in self._scored_tips)
                    dart_contours.sort(key=_raw_novelty, reverse=True)


                raw_c = dart_contours[0][0]
                raw_area = dart_contours[0][1]
                raw_pts = raw_c.reshape(-1, 2).astype(np.float64)

                if len(raw_pts) >= 8:
                    tip = self._line_fit_tip(
                        raw_pts, raw_area, raw_thresh)
                    if tip is not None:
                        return

        # ==============================================================
        # FALLBACK: warped-space closest point to board centre
        # ==============================================================
        print(f"[DART] Cam {self.cam_id}: raw detection failed, "
              f"using warped fallback")
        cpts = contour.reshape(-1, 2).astype(np.float64)
        self._warped_fallback_tip(cpts)

    # ------------------------------------------------------------------
    def _hough_line_tip(
        self,
        raw_pts: np.ndarray,
        raw_area: float,
        raw_thresh: np.ndarray,
    ) -> bool:
        """Hough-Line tip detection in raw camera space.

        Uses cv2.HoughLinesP on the edge image of the dart blob.
        Hough voting naturally rejects outlier pixels (flights, shaft
        noise) and finds the dominant straight line through the barrel.

        Steps:
          1. Mask + Canny edges on the dart blob region
          2. HoughLinesP to detect line segments
          3. Pick the longest segment as the dart axis
          4. Tip = endpoint closer to board centre
          5. Project through homography to board coordinates

        Returns True if a tip was successfully found.
        """
        h_img, w_img = raw_thresh.shape[:2]

        # -- 1. Create blob mask from contour --
        blob_mask = np.zeros((h_img, w_img), dtype=np.uint8)
        raw_contour = raw_pts.astype(np.int32).reshape(-1, 1, 2)
        cv2.drawContours(blob_mask, [raw_contour], -1, 255,
                         thickness=cv2.FILLED)

        # Dilate slightly to connect nearby edge pixels
        blob_mask = cv2.dilate(blob_mask, np.ones((3, 3), np.uint8),
                               iterations=1)

        # Apply mask to threshold image so we only see this dart
        masked = cv2.bitwise_and(raw_thresh, blob_mask)

        # -- 2. Canny edge detection on the masked dart --
        edges = cv2.Canny(masked, 50, 150)

        # -- 3. HoughLinesP --
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=15,           # min votes
            minLineLength=12,       # min line segment length in px
            maxLineGap=8,           # max gap between segments
        )

        if lines is None or len(lines) == 0:
            return False

        # -- 4. Pick the longest line as the dominant axis --
        best_line = None
        best_len = 0
        for seg in lines:
            x1, y1, x2, y2 = seg[0]
            seg_len = np.hypot(x2 - x1, y2 - y1)
            if seg_len > best_len:
                best_len = seg_len
                best_line = (x1, y1, x2, y2)

        if best_line is None or best_len < 8:
            return False

        x1, y1, x2, y2 = best_line

        # Direction vector of the detected line
        vx = x2 - x1
        vy = y2 - y1
        vlen = np.hypot(vx, vy)
        if vlen < 1:
            return False
        vx /= vlen
        vy /= vlen

        # -- 5. Disambiguate: which endpoint is the tip? --
        # Project both endpoints to warped board space and pick the one
        # closest to the board centre (bs/2, bs/2).  This is correct because
        # the tip is on the board surface and maps accurately through the
        # homography; the barrel/flights are elevated and their raw-space
        # position is skewed by the 45 degree camera elevation -- differently for
        # each camera due to the 120 degree spacing.
        bs_half = self.cal.board_size / 2.0
        pt1 = np.array([[[float(x1), float(y1)]]], dtype=np.float32)
        pt2 = np.array([[[float(x2), float(y2)]]], dtype=np.float32)
        w1 = cv2.perspectiveTransform(pt1, self.cal.matrix)[0, 0]
        w2 = cv2.perspectiveTransform(pt2, self.cal.matrix)[0, 0]
        da = np.hypot(w1[0] - bs_half, w1[1] - bs_half)
        db = np.hypot(w2[0] - bs_half, w2[1] - bs_half)

        if da < db:
            tip_raw = np.array([float(x1), float(y1)])
            far_raw = np.array([float(x2), float(y2)])
        else:
            tip_raw = np.array([float(x2), float(y2)])
            far_raw = np.array([float(x1), float(y1)])

        # -- 5b. Refine: find the most-extreme blob pixels along
        #    the Hough direction near the tip endpoint --
        blob_px = np.column_stack(np.where(blob_mask > 0))  # (row, col)
        if len(blob_px) >= 5:
            pts_xy = blob_px[:, ::-1].astype(np.float32)  # -> (x, y)
            # Project onto Hough direction
            cx, cy = tip_raw[0], tip_raw[1]
            dx = pts_xy[:, 0] - cx
            dy = pts_xy[:, 1] - cy
            projs = dx * vx + dy * vy
            # Tip is at the extreme in the tip direction (negative proj)
            n_tip = max(3, int(len(projs) * 0.05))
            tip_idx = np.argpartition(projs, min(n_tip, len(projs)-1))[:n_tip]
            tip_raw = pts_xy[tip_idx].mean(axis=0)

        # -- 5c. Confidence check --
        d_ratio = min(da, db) / max(da, db) if max(da, db) > 0 else 1.0
        tip_confident = d_ratio < 0.85

        # Log for diagnostics
        conf_tag = "OK" if tip_confident else "~WEAK"
        print(f"[RAW] Cam {self.cam_id}: area={raw_area:.0f} "
              f"pts={len(raw_pts)} houghLen={best_len:.0f}px "
              f"nLines={len(lines)} "
              f"dA={da:.0f} dB={db:.0f} tip={'A' if da < db else 'B'} "
              f"{conf_tag}")

        # -- 6. Project raw tip -> warped board space via homography
        pt = np.array([[[tip_raw[0], tip_raw[1]]]], dtype=np.float32)
        board_pt = cv2.perspectiveTransform(pt, self.cal.matrix)
        tip = board_pt[0, 0].astype(np.float64)

        # -- 7. Direction + tip offset --
        far_pt = np.array([[[far_raw[0], far_raw[1]]]], dtype=np.float32)
        far_warped = cv2.perspectiveTransform(
            far_pt, self.cal.matrix)[0, 0]
        direction = tip - far_warped
        norm = np.linalg.norm(direction)
        if norm > 1.0:
            direction /= norm
            tip += direction * self.cfg.tip_offset_px
            self.dart_vector = (float(direction[0]), float(direction[1]))
        else:
            self.dart_vector = None

        self.dart_tip_method = 'HOUGH_LINE' if tip_confident else 'HOUGH_LINE_WEAK'
        self.dart_tip = (float(tip[0]), float(tip[1]))
        tag = 'HOUGH-LINE' if tip_confident else 'HOUGH-LINE-WEAK'
        print(f"[DART] Cam {self.cam_id}: "
              f"tip=({tip[0]:.0f},{tip[1]:.0f}) [{tag}]")
        return True

    # ------------------------------------------------------------------
    def _line_fit_tip(
        self,
        raw_pts: np.ndarray,
        raw_area: float,
        raw_thresh: np.ndarray,
    ) -> bool:
        """Line-fit tip detection in raw camera space (AutoDarts-style).

        Fits a line through the entire dart blob using cv2.fitLine, then
        finds which end of that line is the tip (using board-centre
        proximity).  The actual tip pixel is the extremal point of the
        blob along the fitted line direction -- much more precise than
        the width-profile average of the narrow end.

        Returns True if a tip was successfully found.
        """
        # -- 1. Collect all foreground pixels (filled dart blob, not whole frame)
        #    Draw the dart contour filled onto a blank mask so fitLine
        #    only sees this dart's pixels, not other noise blobs.
        h_img, w_img = raw_thresh.shape[:2]
        blob_mask = np.zeros((h_img, w_img), dtype=np.uint8)
        # raw_pts is the contour in (x,y) -- convert to integer for drawing
        raw_contour = raw_pts.astype(np.int32).reshape(-1, 1, 2)
        cv2.drawContours(blob_mask, [raw_contour], -1, 255,
                         thickness=cv2.FILLED)
        blob_px = np.column_stack(np.where(blob_mask > 0))  # (row, col)
        if len(blob_px) < 8:
            return False

        pts_xy = blob_px[:, ::-1].astype(np.float32)  # -> (x, y)

        # -- 2. Fit a line: [vx, vy, x0, y0]  direction unit vector + point
        line = cv2.fitLine(pts_xy, cv2.DIST_L2, 0, 0.01, 0.01).flatten()
        vx, vy, x0, y0 = float(line[0]), float(line[1]), \
                          float(line[2]), float(line[3])

        # -- 3. Project all points onto the fitted line axis
        dx = pts_xy[:, 0] - x0
        dy = pts_xy[:, 1] - y0
        projs = dx * vx + dy * vy

        proj_range = projs.max() - projs.min()
        if proj_range < 5.0:
            return False   # degenerate -- blob is too round

        # -- 4. Identify the two "ends" (top 10 % of projections each side)
        n_end = max(3, int(len(projs) * 0.10))
        # End A = minimum projection side
        idx_a = np.argpartition(projs,  min(n_end, len(projs)-1))[:n_end]
        # End B = maximum projection side
        idx_b = np.argpartition(projs, -min(n_end, len(projs)-1))[-n_end:]

        centroid_a = pts_xy[idx_a].mean(axis=0)   # (x, y) raw space
        centroid_b = pts_xy[idx_b].mean(axis=0)

        # -- 5. Disambiguate: tip end is closer to board centre in WARPED space
        # Project both end-centroids through the homography into warped board
        # space and pick the end closer to (board_size/2, board_size/2).
        # This is correct because:
        #   * The tip is on the board surface -> homography maps it correctly.
        #   * The barrel is ~35-45 mm above the surface -> its raw position is
        #     shifted by the 45 degree camera elevation in a camera-specific direction
        #     (the 120 degree-spaced cameras each push the barrel a different way).
        #   * In warped space the barrel is always pushed *outward* from the
        #     board centre, so the tip end is always the one closest to centre.
        bs_half = self.cal.board_size / 2.0
        pt_a = np.array([[[centroid_a[0], centroid_a[1]]]], dtype=np.float32)
        pt_b = np.array([[[centroid_b[0], centroid_b[1]]]], dtype=np.float32)
        wa = cv2.perspectiveTransform(pt_a, self.cal.matrix)[0, 0]
        wb = cv2.perspectiveTransform(pt_b, self.cal.matrix)[0, 0]
        da = np.hypot(wa[0] - bs_half, wa[1] - bs_half)
        db = np.hypot(wb[0] - bs_half, wb[1] - bs_half)

        # -- 5b. Raw-space fallback disambiguation --
        # When dA ≈ dB (< 15% difference) the warped-space judgment is
        # unreliable.  Fall back to raw camera-space distance to the raw
        # board centre.  In raw space the dart tip (on the board surface)
        # is physically closest to the board-centre projection on the sensor;
        # the barrel and flights are further away.  This is stable regardless
        # of dart angle or black-segment visibility.
        warped_ratio = min(da, db) / max(da, db) if max(da, db) > 0 else 1.0
        if warped_ratio >= 0.85:          # warped disambiguation ambiguous
            try:
                bull_rx, bull_ry = self.cal.board_centre_cam
                d_raw_a = np.hypot(centroid_a[0] - bull_rx,
                                   centroid_a[1] - bull_ry)
                d_raw_b = np.hypot(centroid_b[0] - bull_rx,
                                   centroid_b[1] - bull_ry)
                # Override da/db with raw-space judgement so subsequent code
                # picks the correct tip end.
                if d_raw_a < d_raw_b:
                    da, db = 0.0, 1.0   # A is tip
                else:
                    da, db = 1.0, 0.0   # B is tip
                raw_ratio = min(d_raw_a, d_raw_b) / max(d_raw_a, d_raw_b, 1.0)
                tip_confident_override = raw_ratio < 0.85
            except Exception:
                tip_confident_override = False
        else:
            tip_confident_override = None   # use warped ratio below


        # -- 5c. Two-stage tip refinement:
        #   Stage 1 — 25% zone cuts shaft/flights.
        #   Stage 2 — within that zone, take the most extreme 5% of pixels
        #             along the axis to land on the actual dart tip point.
        tip_zone_size = proj_range * 0.25
        if da < db:
            stage1_mask = projs <= (projs.min() + tip_zone_size)
        else:
            stage1_mask = projs >= (projs.max() - tip_zone_size)
        stage1_pts  = pts_xy[stage1_mask]
        stage1_proj = projs[stage1_mask]
        if len(stage1_pts) >= 3:
            n_tip = max(3, int(len(stage1_pts) * 0.05))
            if da < db:
                tip_idx = np.argpartition(stage1_proj,
                                          min(n_tip, len(stage1_proj) - 1))[:n_tip]
            else:
                tip_idx = np.argpartition(-stage1_proj,
                                          min(n_tip, len(stage1_proj) - 1))[:n_tip]
            tip_raw = stage1_pts[tip_idx].mean(axis=0)
        else:
            tip_pts_xy = pts_xy[idx_a] if da < db else pts_xy[idx_b]
            tip_raw = tip_pts_xy.mean(axis=0)

        # -- 5d. Adaptive dark-segment extrapolation --
        # Skip this when the warped disambiguation was ambiguous and we used
        # the raw-space fallback — pushing further in an uncertain direction
        # would make the error much larger.  Only extrapolate when we are
        # confident about which end is the tip.
        used_raw_fallback = tip_confident_override is not None
        if not used_raw_fallback:
            tip_xi = int(round(float(tip_raw[0])))
            tip_yi = int(round(float(tip_raw[1])))
            if (self.last_frame is not None
                    and 0 <= tip_yi < self.last_frame.shape[0]
                    and 0 <= tip_xi < self.last_frame.shape[1]):
                brightness = float(self.last_frame[tip_yi, tip_xi].mean())
                extra_frac = 0.18 if brightness < 80 else 0.05
            else:
                extra_frac = 0.08
            tip_dir = np.array([-vx, -vy] if da < db else [vx, vy],
                                dtype=np.float32)
            tip_raw = tip_raw + tip_dir * (proj_range * extra_frac)

        # -- 5e. Confidence: is the tip direction clear?
        # If raw-space fallback was used, use its confidence.
        # Otherwise use the warped-space ratio.
        if tip_confident_override is not None:
            tip_confident = tip_confident_override
        else:
            d_ratio = min(da, db) / max(da, db) if max(da, db) > 0 else 1.0
            tip_confident = d_ratio < 0.85

        # Log for diagnostics
        ratio_check = proj_range / (max(raw_area, 1) ** 0.5)
        conf_tag = ("OK[raw]" if (used_raw_fallback and tip_confident)
                    else "~WEAK[raw]" if used_raw_fallback
                    else "OK" if tip_confident else "~WEAK")
        print(f"[RAW] Cam {self.cam_id}: area={raw_area:.0f} "
              f"pts={len(raw_pts)} len={proj_range:.0f}px "
              f"lineFit=({vx:.2f},{vy:.2f}) "
              f"dA={da:.0f} dB={db:.0f} tip={'A' if da < db else 'B'} "
              f"{conf_tag}")


        # -- 6. Project raw tip -> warped board space via homography
        pt = np.array([[[tip_raw[0], tip_raw[1]]]], dtype=np.float32)
        board_pt = cv2.perspectiveTransform(pt, self.cal.matrix)
        tip = board_pt[0, 0].astype(np.float64)

        # -- 7. Store barrel direction for debug overlay
        centroid_raw = pts_xy.mean(axis=0)
        cen_pt = np.array([[[centroid_raw[0], centroid_raw[1]]]],
                          dtype=np.float32)
        cen_warped = cv2.perspectiveTransform(
            cen_pt, self.cal.matrix)[0, 0]
        direction = tip - cen_warped
        norm = np.linalg.norm(direction)
        if norm > 1.0:
            direction /= norm
            tip += direction * self.cfg.tip_offset_px
            self.dart_vector = (float(direction[0]), float(direction[1]))
        else:
            self.dart_vector = None

        self.dart_tip_method = 'LINE_FIT' if tip_confident else 'LINE_FIT_WEAK'
        self.dart_tip = (float(tip[0]), float(tip[1]))
        tag = 'LINE-FIT' if tip_confident else 'LINE-FIT-WEAK'
        print(f"[DART] Cam {self.cam_id}: "
              f"tip=({tip[0]:.0f},{tip[1]:.0f}) [{tag}]")
        return True



    # ------------------------------------------------------------------
    def _warped_fallback_tip(self, cpts: np.ndarray) -> None:
        """Simple fallback: closest contour point to board centre."""
        bs = self.cal.board_size
        cx = cy = bs / 2.0
        dists = np.hypot(cpts[:, 0] - cx, cpts[:, 1] - cy)
        tip = cpts[np.argmin(dists)].copy()

        mean = cpts.mean(axis=0)
        direction = tip - mean
        norm = np.linalg.norm(direction)
        if norm > 1.0:
            direction = direction / norm
            tip = tip + direction * self.cfg.tip_offset_px
            self.dart_vector = (float(direction[0]),
                                float(direction[1]))
        else:
            self.dart_vector = None

        self.dart_tip_method = 'WARPED'          # least-reliable fallback
        self.dart_tip = (float(tip[0]), float(tip[1]))
        print(f"[DART] Cam {self.cam_id}: "
              f"tip=({tip[0]:.0f},{tip[1]:.0f}) [WARPED-FALLBACK]")

    # ==================================================================
    # Opportunistic one-shot scan
    # ==================================================================

    def try_yolo_scan(
        self,
        scored_mm: list | None = None,
    ) -> Optional[Tuple[float, float, str]]:
        """One-shot frame-diff scan for a dart on the current frame.

        Called by server.py when this camera did NOT reach DART state in
        time but other cameras did.  Performs a quick diff-based detection
        and, if a dart-sized blob is found, extracts the tip and returns
        its position in mm coordinates.

        Parameters
        ----------
        scored_mm : list of (x_mm, y_mm) | None
            Previously scored dart positions (mm, board-centre origin).
            Used to exclude old darts from the diff.

        Returns
        -------
        (x_mm, y_mm, method_str) or None
        """
        if (self._ref_raw_gray is None
                or self.last_frame is None
                or not self.cal.is_calibrated):
            return None

        raw_gray = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)
        raw_diff = cv2.absdiff(raw_gray, self._ref_raw_gray)
        raw_diff = cv2.GaussianBlur(raw_diff, (5, 5), 0)

        # Board mask (expanded to include dart sticking out)
        raw_mask = cv2.dilate(
            self.cal.raw_mask,
            np.ones((41, 41), np.uint8), iterations=1)
        raw_diff = cv2.bitwise_and(raw_diff, raw_mask)

        _, raw_thresh = cv2.threshold(
            raw_diff, self.cfg.absdiff_threshold, 255,
            cv2.THRESH_BINARY)

        # MOG2 fusion for cleaner mask
        if self._bg_sub_ready:
            fg_mask = self._bg_sub.apply(raw_gray, learningRate=0)
            fg_mask = cv2.bitwise_and(fg_mask, raw_mask)
            raw_thresh = cv2.bitwise_and(raw_thresh, fg_mask)

        raw_thresh = cv2.morphologyEx(
            raw_thresh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        raw_thresh = cv2.dilate(
            raw_thresh, np.ones((3, 3), np.uint8), iterations=1)

        # Exclude previously-scored dart regions
        excl_raw = self._build_scored_exclusion_mask(
            raw_thresh.shape, space='raw')
        if excl_raw is not None:
            raw_thresh = cv2.bitwise_and(raw_thresh, excl_raw)

        # Store for cross-camera mask intersection
        self._dart_mask_raw = raw_thresh.copy()

        contours_raw, _ = cv2.findContours(
            raw_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        dart_contours = [
            (c, cv2.contourArea(c)) for c in contours_raw
            if cv2.contourArea(c) > 100
        ]
        if not dart_contours:
            return None

        dart_contours.sort(key=lambda x: x[1], reverse=True)

        # Novelty filter: prefer contour furthest from scored tips
        if len(dart_contours) > 1 and self._scored_tips:
            def _novelty(ca):
                c = ca[0]
                M = cv2.moments(c)
                if M['m00'] > 0:
                    cx = M['m10'] / M['m00']
                    cy = M['m01'] / M['m00']
                else:
                    pts = c.reshape(-1, 2)
                    cx, cy = pts.mean(axis=0)
                pt = np.array([[[cx, cy]]], dtype=np.float32)
                bp = cv2.perspectiveTransform(pt, self.cal.matrix)
                bx, by = float(bp[0, 0, 0]), float(bp[0, 0, 1])
                return min(np.hypot(bx - t[0], by - t[1])
                           for t in self._scored_tips)
            dart_contours.sort(key=_novelty, reverse=True)

        raw_c = dart_contours[0][0]
        raw_area = dart_contours[0][1]
        raw_pts = raw_c.reshape(-1, 2).astype(np.float64)

        if len(raw_pts) < 8:
            return None

        # -- Line-fit tip extraction (same logic as _line_fit_tip) --
        h_img, w_img = raw_thresh.shape[:2]
        blob_mask = np.zeros((h_img, w_img), dtype=np.uint8)
        raw_contour = raw_pts.astype(np.int32).reshape(-1, 1, 2)
        cv2.drawContours(blob_mask, [raw_contour], -1, 255,
                         thickness=cv2.FILLED)
        blob_px = np.column_stack(np.where(blob_mask > 0))
        if len(blob_px) < 8:
            return None

        pts_xy = blob_px[:, ::-1].astype(np.float32)

        line = cv2.fitLine(pts_xy, cv2.DIST_L2, 0, 0.01, 0.01).flatten()
        vx, vy, x0, y0 = (float(line[0]), float(line[1]),
                           float(line[2]), float(line[3]))

        dx = pts_xy[:, 0] - x0
        dy = pts_xy[:, 1] - y0
        projs = dx * vx + dy * vy

        proj_range = projs.max() - projs.min()
        if proj_range < 5.0:
            return None

        n_end = max(3, int(len(projs) * 0.10))
        idx_a = np.argpartition(projs, min(n_end, len(projs) - 1))[:n_end]
        idx_b = np.argpartition(projs, -min(n_end, len(projs) - 1))[-n_end:]

        centroid_a = pts_xy[idx_a].mean(axis=0)
        centroid_b = pts_xy[idx_b].mean(axis=0)

        # Disambiguate tip vs barrel using warped-space distance to centre
        bs_half = self.cal.board_size / 2.0
        pt_a = np.array([[[centroid_a[0], centroid_a[1]]]], dtype=np.float32)
        pt_b = np.array([[[centroid_b[0], centroid_b[1]]]], dtype=np.float32)
        wa = cv2.perspectiveTransform(pt_a, self.cal.matrix)[0, 0]
        wb = cv2.perspectiveTransform(pt_b, self.cal.matrix)[0, 0]
        da = np.hypot(wa[0] - bs_half, wa[1] - bs_half)
        db = np.hypot(wb[0] - bs_half, wb[1] - bs_half)

        # Two-stage tip refinement:
        #   Stage 1 — 25% zone removes shaft/flights.
        #   Stage 2 — most extreme 5% within that zone = actual dart tip.
        tip_zone_size = proj_range * 0.25
        if da < db:
            stage1_mask = projs <= (projs.min() + tip_zone_size)
        else:
            stage1_mask = projs >= (projs.max() - tip_zone_size)
        stage1_pts  = pts_xy[stage1_mask]
        stage1_proj = projs[stage1_mask]
        if len(stage1_pts) >= 3:
            n_tip = max(3, int(len(stage1_pts) * 0.05))
            if da < db:
                tip_idx = np.argpartition(stage1_proj,
                                          min(n_tip, len(stage1_proj) - 1))[:n_tip]
            else:
                tip_idx = np.argpartition(-stage1_proj,
                                          min(n_tip, len(stage1_proj) - 1))[:n_tip]
            tip_raw = stage1_pts[tip_idx].mean(axis=0)
        else:
            tip_pts = pts_xy[idx_a] if da < db else pts_xy[idx_b]
            tip_raw = tip_pts.mean(axis=0)

        # Adaptive dark-segment extrapolation (same logic as _line_fit_tip)
        tip_xi = int(round(float(tip_raw[0])))
        tip_yi = int(round(float(tip_raw[1])))
        if (self.last_frame is not None
                and 0 <= tip_yi < self.last_frame.shape[0]
                and 0 <= tip_xi < self.last_frame.shape[1]):
            brightness = float(self.last_frame[tip_yi, tip_xi].mean())
            extra_frac = 0.18 if brightness < 80 else 0.05
        else:
            extra_frac = 0.08
        tip_dir = np.array([-vx, -vy] if da < db else [vx, vy], dtype=np.float32)
        tip_raw = tip_raw + tip_dir * (proj_range * extra_frac)

        # Project raw tip → mm via calibrator
        try:
            x_mm, y_mm = self.cal.transform_to_mm(
                float(tip_raw[0]), float(tip_raw[1]))
        except Exception:
            return None

        r_mm = float(np.hypot(x_mm, y_mm))
        if r_mm > 180.0:
            return None

        method = 'SCAN_LINE_FIT'
        return (x_mm, y_mm, method)

    # ==================================================================
    # Reset / release
    # ==================================================================

    def reset_to_wait(self, with_cooldown: bool = False) -> None:
        self.state = State.WAIT
        self.dart_tip = None
        self.dart_area = 0
        self.dart_vector = None
        self.dart_roi = None
        self.diff_frame = None
        self.motion_frame = None
        self.motion_change = 0
        self._stable_count = 0
        self._prev_motion = None
        self._hand_count = 0   # clear hysteresis counter between throws
        self._dart_mask_raw = None  # clear for next dart cycle
        if with_cooldown:
            self._cooldown = self._COOLDOWN_FRAMES

    def record_scored_tip(self, tip: Tuple[float, float]) -> None:
        """Remember a scored tip position so future detection can
        distinguish the *new* dart from residual diffs of old darts."""
        self._scored_tips.append(tip)

    def clear_scored_tips(self) -> None:
        """Clear scored tip history (called on takeout)."""
        self._scored_tips.clear()

    def release(self) -> None:
        if self._reader is not None:
            self._reader.release()
        elif self.cap is not None:
            self.cap.release()
