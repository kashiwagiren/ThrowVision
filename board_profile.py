"""ThrowVision – Board Profile Manager.

Supports multiple named board profiles stored in calibration/profiles/.
Each profile saves ORB features + calibration points for auto-calibration
via feature matching.
"""

from __future__ import annotations

import os
import glob
import re
from typing import Optional, Tuple, List, Dict

import cv2
import numpy as np


PROFILES_DIR = os.path.join("calibration", "profiles")


class BoardProfile:
    """Save/load a reference board and match against new frames."""

    def __init__(self) -> None:
        self.name: Optional[str] = None
        self.ref_gray: Optional[np.ndarray] = None
        self.ref_kp_pts: Optional[np.ndarray] = None
        self.ref_desc: Optional[np.ndarray] = None
        self.src_points: Optional[np.ndarray] = None
        self.center: Optional[Tuple[float, float]] = None
        self.radius: Optional[float] = None
        self._orb = cv2.ORB_create(nfeatures=2000)
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    # ── Persistence ──────────────────────────────────────────────

    @property
    def is_registered(self) -> bool:
        return self.ref_gray is not None

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return re.sub(r'[^\w\-]', '_', name.strip())[:50]

    @staticmethod
    def _path_for(name: str) -> str:
        return os.path.join(PROFILES_DIR, f"{BoardProfile._sanitize_name(name)}.npz")

    def save(self) -> None:
        os.makedirs(PROFILES_DIR, exist_ok=True)
        path = self._path_for(self.name)
        np.savez(path,
                 name=np.array([self.name]),
                 ref_gray=self.ref_gray,
                 ref_kp_pts=self.ref_kp_pts,
                 ref_descriptors=self.ref_desc,
                 src_points=self.src_points,
                 center=np.array(self.center),
                 radius=np.array([self.radius]))
        print(f"[BOARD] Profile '{self.name}' saved -> {path}")

    def load(self, name: str) -> bool:
        path = self._path_for(name)
        if not os.path.exists(path):
            return False
        try:
            data = np.load(path, allow_pickle=False)
            self.name = str(data["name"][0])
            self.ref_gray = data["ref_gray"]
            self.ref_kp_pts = data["ref_kp_pts"]
            self.ref_desc = data["ref_descriptors"]
            self.src_points = data["src_points"]
            self.center = tuple(data["center"].tolist())
            self.radius = float(data["radius"][0])
            print(f"[BOARD] Profile '{self.name}' loaded ({len(self.ref_kp_pts)} features)")
            return True
        except Exception as e:
            print(f"[BOARD] Failed to load profile '{name}': {e}")
            return False

    @staticmethod
    def list_profiles() -> List[Dict]:
        """Return list of saved profiles with metadata."""
        os.makedirs(PROFILES_DIR, exist_ok=True)
        profiles = []
        for path in sorted(glob.glob(os.path.join(PROFILES_DIR, "*.npz"))):
            try:
                data = np.load(path, allow_pickle=False)
                name = str(data["name"][0]) if "name" in data else os.path.splitext(os.path.basename(path))[0]
                features = len(data["ref_kp_pts"]) if "ref_kp_pts" in data else 0
                profiles.append({"name": name, "features": features, "file": os.path.basename(path)})
            except Exception:
                continue
        return profiles

    @staticmethod
    def delete_profile(name: str) -> bool:
        path = BoardProfile._path_for(name)
        if os.path.exists(path):
            os.remove(path)
            print(f"[BOARD] Profile '{name}' deleted")
            return True
        return False

    # ── Registration ─────────────────────────────────────────────

    def register(self, frame: np.ndarray,
                 src_points: np.ndarray,
                 center: Tuple[float, float],
                 radius: float,
                 name: str = "default") -> None:
        """Register a board from a camera frame + known calibration."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        kp, desc = self._orb.detectAndCompute(gray, None)
        if desc is None or len(kp) < 20:
            raise ValueError("Too few features detected – try better lighting")
        self.name = name
        self.ref_gray = gray
        self.ref_kp_pts = np.array([k.pt + (k.size, k.angle, k.response, k.octave, k.class_id) for k in kp], dtype=np.float32)
        self.ref_desc = desc
        self.src_points = np.asarray(src_points, dtype=np.float32)
        self.center = center
        self.radius = radius
        self.save()

    # ── Feature Matching ─────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Match against registered board -> return 4 transformed points."""
        if not self.is_registered:
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        kp2, desc2 = self._orb.detectAndCompute(gray, None)
        if desc2 is None or len(kp2) < 10:
            return None

        matches = self._bf.knnMatch(self.ref_desc, desc2, k=2)
        good = []
        for m_pair in matches:
            if len(m_pair) == 2:
                m, n = m_pair
                if m.distance < 0.75 * n.distance:
                    good.append(m)

        if len(good) < 10:
            return None

        ref_kps = [cv2.KeyPoint(x=float(p[0]), y=float(p[1]),
                                size=float(p[2]), angle=float(p[3]),
                                response=float(p[4]),
                                octave=int(p[5]),
                                class_id=int(p[6]))
                   for p in self.ref_kp_pts]

        pts_ref = np.float32([ref_kps[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        pts_new = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(pts_ref, pts_new, cv2.RANSAC, 5.0)
        if H is None:
            return None

        inliers = mask.ravel().sum()
        if inliers < 8:
            return None

        src = self.src_points.reshape(-1, 1, 2)
        dst = cv2.perspectiveTransform(src, H)
        result = dst.reshape(4, 2)

        print(f"[BOARD] Feature match: {len(good)} matches, {inliers} inliers -> 4 points")
        return result

