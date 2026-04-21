"""ThrowVision – Score Mapper.

Converts a camera-pixel dart tip into a board score by:
    1. Perspective-transforming the tip -> board coordinates (mm).
    2. Converting to polar (r, theta).
    3. Looking up the segment via standard dartboard geometry.
    4. Fusing detections from up to 3 cameras.
"""

import math
from collections import Counter
from typing import List, Optional, Tuple

import numpy as np

from calibrator import (
    BULL_INNER_R,
    BULL_OUTER_R,
    DOUBLE_INNER,
    DOUBLE_OUTER,
    SECTOR_ANGLE,
    SECTOR_ORDER,
    TRIPLE_INNER,
    TRIPLE_OUTER,
    BoardCalibrator,
)
from config import ConfigManager


class ScoreMapper:
    """Maps (x, y) camera pixels -> dartboard score."""

    def __init__(self, cfg: ConfigManager,
                 calibrators: List[BoardCalibrator]) -> None:
        self.cfg = cfg
        self.cals = calibrators
        self._history: List[dict] = []
        # Last consensus per-camera mm coords for debug overlay
        self.last_tips_mm: List[Tuple[int, Tuple[float, float]]] = []
        self.last_final_mm: Optional[Tuple[float, float]] = None
        self.last_label: str = ""
        self.last_score_val: int = 0
        # Log derived camera azimuths at startup
        for i, cal in enumerate(self.cals):
            az = cal.camera_azimuth()
            if az is not None:
                print(f"[SCR] Cam {i} azimuth: {az:.0f}°")
            else:
                print(f"[SCR] Cam {i} azimuth: not calibrated")

    # ------------------------------------------------------------------
    # Single-camera tip -> score
    # ------------------------------------------------------------------
    def tip_to_board_mm(self, cam_idx: int,
                        tip_px: Tuple[float, float]) -> Tuple[float, float]:
        """Convert tip pixel (warped board space) to mm from centre."""
        cal = self.cals[cam_idx]
        return cal.board_px_to_mm(tip_px[0], tip_px[1])

    @staticmethod
    def to_polar(x_mm: float, y_mm: float) -> Tuple[float, float]:
        r = math.hypot(x_mm, y_mm)
        theta = math.degrees(math.atan2(y_mm, x_mm)) % 360.0
        return r, theta

    # Physical board edge (number ring outer wire) plus a small tolerance for
    # homography drift / parallax at the very edge. This keeps near-edge
    # outside throws registering as MISS instead of dropping to OFF too early.
    BOARD_OUTER_R = 233.0  # mm

    @staticmethod
    def score_from_polar(r: float, theta: float) -> Tuple[str, int]:
        if r <= BULL_INNER_R:
            return ("DB", 50)
        if r <= BULL_OUTER_R:
            return ("SB", 25)
        if r > DOUBLE_OUTER:
            if r <= ScoreMapper.BOARD_OUTER_R:
                return ("MISS", 0)   # on board, outside scoring area
            return ("OFF", 0)        # fully off board

        base = (90.0 - SECTOR_ANGLE / 2) % 360.0   # 20 at top (90°)
        offset = (theta - base) % 360.0
        idx = int(offset // SECTOR_ANGLE) % 20
        sector_val = SECTOR_ORDER[idx]

        if TRIPLE_INNER <= r <= TRIPLE_OUTER:
            return (f"T{sector_val}", sector_val * 3)
        if DOUBLE_INNER <= r <= DOUBLE_OUTER:
            return (f"D{sector_val}", sector_val * 2)
        return (f"S{sector_val}", sector_val)

    @staticmethod
    def _angle_delta_deg(a: float, b: float) -> float:
        return ((a - b + 180.0) % 360.0) - 180.0

    @staticmethod
    def _polar_to_cart(r: float, theta: float) -> Tuple[float, float]:
        rad = math.radians(theta)
        return (r * math.cos(rad), r * math.sin(rad))

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _coord_in_label(self, coord: Tuple[float, float], label: str) -> bool:
        r, theta = self.to_polar(coord[0], coord[1])
        got_label, _ = self.score_from_polar(r, theta)
        return got_label == label

    def _project_coord_into_label(
        self,
        coord: Tuple[float, float],
        label: str,
    ) -> Tuple[float, float]:
        if label in ("OFF", "MISS", "BOUNCE", "SKIP"):
            return coord
        if self._coord_in_label(coord, label):
            return coord

        r, theta = self.to_polar(coord[0], coord[1])
        radial_margin = 0.75
        angle_margin = 0.35

        if label in ("DB", "BULL", "D25"):
            new_r = self._clamp(r, 0.0, max(0.0, BULL_INNER_R - radial_margin))
            return self._polar_to_cart(new_r, theta)

        if label in ("SB", "25"):
            new_r = self._clamp(
                r,
                BULL_INNER_R + radial_margin,
                max(BULL_INNER_R + radial_margin, BULL_OUTER_R - radial_margin),
            )
            return self._polar_to_cart(new_r, theta)

        if len(label) < 2:
            return coord

        ring_code = label[0]
        try:
            sector_val = int(label[1:])
            sector_idx = SECTOR_ORDER.index(sector_val)
        except (ValueError, IndexError):
            return coord

        center_theta = (90.0 + sector_idx * SECTOR_ANGLE) % 360.0
        max_delta = max(0.0, SECTOR_ANGLE / 2.0 - angle_margin)
        theta_delta = self._angle_delta_deg(theta, center_theta)
        new_theta = (center_theta + self._clamp(theta_delta, -max_delta, max_delta)) % 360.0

        if ring_code == "T":
            new_r = self._clamp(
                r,
                TRIPLE_INNER + radial_margin,
                max(TRIPLE_INNER + radial_margin, TRIPLE_OUTER - radial_margin),
            )
            return self._polar_to_cart(new_r, new_theta)

        if ring_code == "D":
            new_r = self._clamp(
                r,
                DOUBLE_INNER + radial_margin,
                max(DOUBLE_INNER + radial_margin, DOUBLE_OUTER - radial_margin),
            )
            return self._polar_to_cart(new_r, new_theta)

        if ring_code != "S":
            return coord

        inner_single = (
            BULL_OUTER_R + radial_margin,
            max(BULL_OUTER_R + radial_margin, TRIPLE_INNER - radial_margin),
        )
        outer_single = (
            TRIPLE_OUTER + radial_margin,
            max(TRIPLE_OUTER + radial_margin, DOUBLE_INNER - radial_margin),
        )

        if inner_single[0] <= r <= inner_single[1]:
            return self._polar_to_cart(r, new_theta)
        if outer_single[0] <= r <= outer_single[1]:
            return self._polar_to_cart(r, new_theta)

        single_candidates = [
            inner_single[0], inner_single[1],
            outer_single[0], outer_single[1],
        ]
        new_r = min(single_candidates, key=lambda rr: abs(rr - r))
        return self._polar_to_cart(new_r, new_theta)

    def _finalize_result(
        self,
        label: str,
        score: int,
        coord: Tuple[float, float],
        all_mm_coords: List[Tuple[int, Tuple[float, float]]],
        per_cam_labels: Optional[dict] = None,
        methods: Optional[List[str]] = None,
        prefer_matching_camera: bool = False,
    ) -> Tuple[str, int, Tuple[float, float]]:
        final_coord = coord

        if prefer_matching_camera and per_cam_labels:
            matching = []
            for ci, (cam_label, _, mm) in per_cam_labels.items():
                if cam_label != label:
                    continue
                weight = self._METHOD_WEIGHT.get(methods[ci], 1.0) if methods else 1.0
                matching.append((ci, mm, weight))

            if matching:
                spread = 0.0
                for i in range(len(matching)):
                    for j in range(i + 1, len(matching)):
                        a = matching[i][1]
                        b = matching[j][1]
                        spread = max(spread, math.hypot(a[0] - b[0], a[1] - b[1]))

                if spread >= 8.0 or not self._coord_in_label(final_coord, label):
                    _, best_mm, _ = max(
                        matching,
                        key=lambda item: (
                            item[2],
                            -math.hypot(item[1][0] - final_coord[0],
                                        item[1][1] - final_coord[1]),
                        ),
                    )
                    final_coord = best_mm

        final_coord = self._project_coord_into_label(final_coord, label)
        self.last_tips_mm = all_mm_coords
        self.last_final_mm = final_coord
        self.last_label = label
        self.last_score_val = score
        return (label, score, final_coord)

    # ------------------------------------------------------------------
    # Multi-camera consensus
    # ------------------------------------------------------------------
    # Quality weights for tip detection methods
    _METHOD_WEIGHT = {
        'POSE':          5.0,   # direct ML keypoint — highest accuracy
        'POSE+LINE_FIT': 5.5,   # ML keypoint confirmed by CV line-fit
        'LINE_FIT':      4.0,   # fitted line, clear tip direction
        'HOUGH_LINE':    4.0,   # Hough-line axis, clear tip direction
        'SCAN_LINE_FIT': 3.5,   # opportunistic scan line-fit
        'PROFILE':       3.0,   # width-profile (legacy fallback)
        'LINE_FIT_WEAK': 1.5,   # line fit — tip direction ambiguous
        'HOUGH_LINE_WEAK': 1.5, # Hough line — tip direction ambiguous
        'PROXIMITY':     1.0,   # centre-proximity tiebreaker
        'WARPED':        0.5,   # warped-space fallback, least reliable
        'NONE':          1.0,
        # YOLO-confirmed methods (CV tip confirmed by YOLO presence check)
        'POSE+YOLO':            5.5,
        'LINE_FIT+YOLO':        4.5,
        'LINE_FIT+YOLO_RESCUE': 4.2,
        'HOUGH_LINE+YOLO':      4.5,
        'SCAN_LINE_FIT+YOLO':   4.0,
        'SCAN_LINE_FIT+YOLO_RESCUE': 3.8,
        'LINE_FIT_WEAK+YOLO':   2.5,
        'WARPED+YOLO':          1.5,
    }

    # Tier ranking for outlier rejection (YOLO = same tier as base method)
    _METHOD_RANK = {
        'POSE': 5, 'POSE+LINE_FIT': 5, 'POSE+YOLO': 5,
        'LINE_FIT': 4, 'HOUGH_LINE': 4,
        'SCAN_LINE_FIT': 3, 'PROFILE': 3,
        'LINE_FIT_WEAK': 1, 'HOUGH_LINE_WEAK': 1, 'PROXIMITY': 1,
        'WARPED': 0, 'NONE': 0,
        'LINE_FIT+YOLO': 4, 'HOUGH_LINE+YOLO': 4,
        'LINE_FIT+YOLO_RESCUE': 4,
        'SCAN_LINE_FIT+YOLO': 3, 'SCAN_LINE_FIT+YOLO_RESCUE': 3,
        'LINE_FIT_WEAK+YOLO': 1, 'WARPED+YOLO': 0,
    }

    # Boundary ranking (YOLO boosts rank — more trustworthy at wire)
    _BOUNDARY_RANK = {
        'POSE': 7, 'POSE+LINE_FIT': 7, 'POSE+YOLO': 7,
        'LINE_FIT': 5, 'HOUGH_LINE': 5,
        'LINE_FIT+YOLO': 6, 'HOUGH_LINE+YOLO': 6,
        'LINE_FIT+YOLO_RESCUE': 6,
        'SCAN_LINE_FIT': 4, 'SCAN_LINE_FIT+YOLO': 5,
        'SCAN_LINE_FIT+YOLO_RESCUE': 5,
        'PROFILE': 3, 'LINE_FIT_WEAK': 2, 'HOUGH_LINE_WEAK': 2,
        'LINE_FIT_WEAK+YOLO': 3,
        'PROXIMITY': 1, 'WARPED': 0, 'NONE': 0, 'WARPED+YOLO': 1,
    }

    _RING_MID = {
        'triple': (TRIPLE_INNER + TRIPLE_OUTER) / 2.0,
        'double': (DOUBLE_INNER + DOUBLE_OUTER) / 2.0,
    }

    def _geometry_weight(self, cam_idx: int, dart_angle_rad: float) -> float:
        """Camera geometry weight: perpendicular view = 1.0, parallel = 0.15."""
        if cam_idx < len(self.cals):
            az = self.cals[cam_idx].camera_azimuth()
            if az is not None:
                return max(abs(math.sin(dart_angle_rad - math.radians(az))), 0.15)
        return 0.5

    def consensus(
        self,
        tips: List[Optional[Tuple[float, float]]],
        areas: Optional[List[int]] = None,
        methods: Optional[List[str]] = None,
        mm_coords_direct: Optional[List[Optional[Tuple[float, float]]]] = None,
        cross_camera_mm: Optional[Tuple[float, float]] = None,
    ) -> Tuple[str, int, Tuple[float, float]]:
        mm_coords: List[Tuple[int, Tuple[float, float]]] = []  # (cam_idx, mm)
        per_cam_labels: dict = {}  # cam_idx -> (label, score, mm)
        for i, tip in enumerate(tips):
            if tip is not None:
                # Use pre-computed mm coords (direct raw→mm) when available
                if mm_coords_direct and mm_coords_direct[i] is not None:
                    mm = mm_coords_direct[i]
                else:
                    mm = self.tip_to_board_mm(i, tip)
                r, theta = self.to_polar(mm[0], mm[1])
                # Apply per-camera radial bias correction
                if i < len(self.cals):
                    r_corr = self.cals[i].correct_radius(r)
                    if r_corr != r:
                        ang_rad = math.atan2(mm[1], mm[0])
                        mm = (r_corr * math.cos(ang_rad),
                              r_corr * math.sin(ang_rad))
                        r = r_corr
                lbl, sc = self.score_from_polar(r, theta)
                area = areas[i] if areas else 0
                method = methods[i] if methods else 'NONE'
                print(f"[DART] Cam {i}: "
                      f"({mm[0]:+.1f},{mm[1]:+.1f})mm "
                      f"r={r:.1f} -> {lbl}  (area={area} method={method})")
                mm_coords.append((i, mm))
                per_cam_labels[i] = (lbl, sc, mm)

        # Keep a copy of ALL per-camera coords (before filtering)
        all_mm_coords = list(mm_coords)

        if not mm_coords:
            self.last_tips_mm = []
            self.last_final_mm = None
            return ("OFF", 0, (0.0, 0.0))

        # ── Cross-camera mask intersection override ─────────────────────
        # When server.py successfully intersected 2+ cameras' warped diff
        # masks, the resulting tip position eliminates shaft parallax and
        # is the most accurate available.  Use it directly.
        if cross_camera_mm is not None:
            xc, yc = cross_camera_mm
            rc, tc = self.to_polar(xc, yc)
            lbl_c, sc_c = self.score_from_polar(rc, tc)

            # Suppress cross-camera override when individual cameras have a clear
            # majority — a majority individual reading is more reliable than the
            # intersection which can detect barrel/shaft above the board surface.
            individual_labels = [lbl for lbl, _, _ in per_cam_labels.values()]

            label_counts = Counter(individual_labels)
            majority_label, majority_count = label_counts.most_common(1)[0]
            majority_agrees = majority_count > len(individual_labels) / 2

            _suppress_xcam = False
            if majority_agrees and lbl_c != majority_label:
                # Only suppress if xcam position is far from the majority cameras.
                # A small distance means the dart is near a wire and the label
                # disagreement is just calibration drift — xcam is still correct.
                # A large distance (> 30 mm) suggests xcam found a barrel/shaft
                # artifact that individual cameras correctly ignored.
                _maj_mms = [mm for ci, (lbl, _, mm) in per_cam_labels.items()
                            if lbl == majority_label]
                _maj_x = sum(m[0] for m in _maj_mms) / len(_maj_mms)
                _maj_y = sum(m[1] for m in _maj_mms) / len(_maj_mms)
                _dist_to_maj = math.hypot(xc - _maj_x, yc - _maj_y)
                if _dist_to_maj > 30.0:
                    _suppress_xcam = True
                    print(f"[SCR] Cross-camera override SUPPRESSED — "
                          f"{majority_count}/{len(individual_labels)} cams agree on "
                          f"{majority_label}, xcam says {lbl_c}, "
                          f"dist={_dist_to_maj:.1f}mm > 30mm (barrel suspected)")
                else:
                    print(f"[SCR] Cross-camera KEPT despite label disagreement — "
                          f"{majority_count}/{len(individual_labels)} cams agree on "
                          f"{majority_label} but xcam says {lbl_c}, "
                          f"dist={_dist_to_maj:.1f}mm ≤ 30mm (near-wire offset)")

            if not _suppress_xcam:
                # ── RING CORRECTION (v4 — one-directional) ─────────────────
                # Core rule: xcam inside a narrow ring (triple 8mm wide,
                # double 8mm wide) is a STRONG signal — never override it.
                # Cameras have 20-40mm radial scatter and can't reliably
                # distinguish these thin rings.
                #
                # Ring correction ONLY fires when xcam is in the wide
                # single zone (55mm between triple-outer and double-inner)
                # and cameras with good geometry weight agree on triple or
                # double.  Direction is always single → triple/double.
                #
                # TO REVERT: delete from here to "── END RING CORRECTION ──"
                if rc > BULL_OUTER_R and per_cam_labels:
                    _xcam_in_triple = TRIPLE_INNER <= rc <= TRIPLE_OUTER
                    _xcam_in_double = DOUBLE_INNER <= rc <= DOUBLE_OUTER
                    _xcam_in_single = not _xcam_in_triple and not _xcam_in_double

                    if _xcam_in_single:
                        _dart_angle_rad = math.atan2(yc, xc)
                        _target_rings = {}
                        _single_weight = 0.0
                        for ci, (_, _, mm) in per_cam_labels.items():
                            _cr = math.hypot(mm[0], mm[1])
                            if _cr <= BULL_OUTER_R:
                                continue
                            _gw = self._geometry_weight(ci, _dart_angle_rad)
                            if TRIPLE_INNER <= _cr <= TRIPLE_OUTER:
                                _cam_ring = 'triple'
                            elif DOUBLE_INNER <= _cr <= DOUBLE_OUTER:
                                _cam_ring = 'double'
                            else:
                                _single_weight += _gw
                                continue
                            _target_rings[_cam_ring] = (
                                _target_rings.get(_cam_ring, 0.0) + _gw)

                        if _target_rings:
                            _best = max(_target_rings, key=_target_rings.get)
                            _best_w = _target_rings[_best]
                            # Need at least 0.5 total weight from cameras
                            # that actually read inside the target ring,
                            # AND ring evidence must outweigh single evidence
                            if _best_w >= 0.5 and _best_w > _single_weight:
                                _new_r = self._RING_MID[_best]
                                _new_x = _new_r * math.cos(math.radians(tc))
                                _new_y = _new_r * math.sin(math.radians(tc))
                                _new_lbl, _new_sc = self.score_from_polar(
                                    _new_r, tc)
                                print(f"[SCR] Ring correction: {lbl_c}->{_new_lbl} "
                                      f"(single->{_best}, "
                                      f"cam_weight={_best_w:.2f} "
                                      f"vs single={_single_weight:.2f}, "
                                      f"r={rc:.1f}->{_new_r:.1f}mm)")
                                xc, yc, rc = _new_x, _new_y, _new_r
                                lbl_c, sc_c = _new_lbl, _new_sc
                            elif _best_w >= 0.5:
                                print(f"[SCR] Ring correction BLOCKED — "
                                      f"{_best} cam_weight={_best_w:.2f} "
                                      f"but single={_single_weight:.2f} "
                                      f"(single cams outweigh)")
                    elif _xcam_in_triple or _xcam_in_double:
                        _ring_name = 'triple' if _xcam_in_triple else 'double'
                        print(f"[SCR] Xcam in {_ring_name} ring "
                              f"(r={rc:.1f}mm) — trusted, no correction")
                # ── END RING CORRECTION ──────────────────────────────────────

                # ── WIRE-ZONE TOLERANCE ─────────────────────────────────────
                # When xcam's radius is within WIRE_ZONE mm of a ring
                # boundary, check if any camera with good geometry weight
                # clearly reads inside that ring.  If so, snap to the ring.
                # This catches 0.2-2mm calibration errors at the wire.
                # TO REVERT: delete from here to "── END WIRE-ZONE ──"
                _WIRE_ZONE = 3.0  # mm either side of boundary
                if rc > BULL_OUTER_R and per_cam_labels:
                    _boundaries = [
                        ('triple', TRIPLE_INNER, TRIPLE_OUTER),
                        ('double', DOUBLE_INNER, DOUBLE_OUTER),
                    ]
                    for _bname, _b_inner, _b_outer in _boundaries:
                        _near_inner = abs(rc - _b_inner) <= _WIRE_ZONE
                        _near_outer = abs(rc - _b_outer) <= _WIRE_ZONE
                        if not (_near_inner or _near_outer):
                            continue
                        if _b_inner <= rc <= _b_outer:
                            break  # already in this ring, no fix needed
                        # Never snap from outside the board into a scoring
                        # ring — that would convert a genuine MISS to a
                        # score.  Only snap inward (single→triple, single→double).
                        if rc > _b_outer:
                            break  # dart is beyond this ring's outer edge
                        # Check if any camera with decent geometry weight
                        # clearly reads inside this ring (not just at edge)
                        _dart_ang = math.atan2(yc, xc)
                        _ring_mid = (_b_inner + _b_outer) / 2.0
                        _evidence = 0.0
                        _total_gw = 0.0
                        for ci, (_, _, mm) in per_cam_labels.items():
                            _cr = math.hypot(mm[0], mm[1])
                            _gw = self._geometry_weight(ci, _dart_ang)
                            _total_gw += _gw
                            if _b_inner <= _cr <= _b_outer:
                                _evidence += _gw
                        if _total_gw > 0 and _evidence / _total_gw >= 0.30:
                            _new_r = _ring_mid
                            _new_x = _new_r * math.cos(math.radians(tc))
                            _new_y = _new_r * math.sin(math.radians(tc))
                            _new_lbl, _new_sc = self.score_from_polar(_new_r, tc)
                            print(f"[SCR] Wire-zone snap: {lbl_c}→{_new_lbl} "
                                  f"(r={rc:.1f}mm within {_WIRE_ZONE}mm of "
                                  f"{_bname} boundary, "
                                  f"evidence={_evidence/_total_gw:.0%})")
                            xc, yc, rc = _new_x, _new_y, _new_r
                            lbl_c, sc_c = _new_lbl, _new_sc
                        break  # only check nearest boundary
                # ── END WIRE-ZONE ───────────────────────────────────────────

                print(f"[SCR] Cross-camera tip override: "
                      f"({xc:+.1f},{yc:+.1f})mm r={rc:.1f} "
                      f"-> {lbl_c} = {sc_c}")
                return self._finalize_result(
                    lbl_c,
                    sc_c,
                    (xc, yc),
                    all_mm_coords,
                    per_cam_labels=per_cam_labels,
                    methods=methods,
                    prefer_matching_camera=False,
                )

        # --- Majority voting (2/3 cameras agree) -----------------------
        # If 2+ cameras independently produce the same score label,
        # use that label — BUT only if the dissenting camera's method
        # is not significantly better-quality.  A single high-quality
        # LINE_FIT camera near a wire boundary may be more accurate
        # than two lower-quality SCAN_LINE_FIT cameras that agree on the
        # wrong segment.
        _MV_RANK = {
            'LINE_FIT': 4, 'HOUGH_LINE': 4, 'SCAN_LINE_FIT': 3,
            'PROFILE': 3, 'LINE_FIT_WEAK': 1, 'HOUGH_LINE_WEAK': 1,
            'PROXIMITY': 1, 'WARPED': 0, 'NONE': 0,
            # YOLO-confirmed (same rank as base method — YOLO is a validator)
            'LINE_FIT+YOLO': 4, 'HOUGH_LINE+YOLO': 4,
            'LINE_FIT+YOLO_RESCUE': 4,
            'SCAN_LINE_FIT+YOLO': 3,
            'SCAN_LINE_FIT+YOLO_RESCUE': 3,
            'LINE_FIT_WEAK+YOLO': 1, 'WARPED+YOLO': 0,
        }
        if len(per_cam_labels) >= 2:
            label_counts = Counter(
                lbl for lbl, _, _ in per_cam_labels.values())
            majority_label, majority_n = label_counts.most_common(1)[0]
            if majority_n >= 2:
                agreeing = [(ci, mm) for ci, (lbl, _, mm)
                            in per_cam_labels.items()
                            if lbl == majority_label]
                dissenting = [(ci, lbl, mm) for ci, (lbl, _, mm)
                              in per_cam_labels.items()
                              if lbl != majority_label]

                # Check if dissenting camera has a much better method
                use_majority = True
                if dissenting and methods:
                    agree_ranks = [_MV_RANK.get(methods[ci], 0) for ci, _ in agreeing]
                    max_agree = max(agree_ranks)
                    for ci_d, lbl_d, mm_d in dissenting:
                        rank_d = _MV_RANK.get(methods[ci_d], 0)
                        if rank_d >= max_agree + 2:
                            # High-quality dissent — don't use majority
                            print(f"[SCR] Majority {majority_label} overridden:"
                                  f" Cam {ci_d} ({methods[ci_d]} rank={rank_d})"
                                  f" disagrees with {lbl_d}")
                            use_majority = False
                            break

                if use_majority:
                    # Quality-weighted average of agreeing cameras
                    if methods:
                        a_weights = [self._METHOD_WEIGHT.get(methods[ci], 1.0)
                                     for ci, _ in agreeing]
                        tw = sum(a_weights)
                        avg_x = sum(w * mm[0] for w, (_, mm) in zip(a_weights, agreeing)) / tw
                        avg_y = sum(w * mm[1] for w, (_, mm) in zip(a_weights, agreeing)) / tw
                    else:
                        avg_x = sum(mm[0] for _, mm in agreeing) / len(agreeing)
                        avg_y = sum(mm[1] for _, mm in agreeing) / len(agreeing)
                    _, sc_m = per_cam_labels[agreeing[0][0]][:2]
                    cams = [str(ci) for ci, _ in agreeing]
                    print(f"[SCR] Majority vote: {majority_n}/{len(per_cam_labels)}"
                          f" cameras agree on {majority_label}"
                          f" (Cams {','.join(cams)})")

                    return self._finalize_result(
                        majority_label,
                        sc_m,
                        (avg_x, avg_y),
                        all_mm_coords,
                        per_cam_labels=per_cam_labels,
                        methods=methods,
                        prefer_matching_camera=True,
                    )

        # --- Outlier rejection -----------------------------------------
        # Compute pairwise distances.  If one camera is far from the
        # others (> 40 mm), discard it.
        #
        # Exception: if the "outlier" was detected with a higher-quality
        # method (e.g. LINE_FIT) than the cameras being kept (e.g.
        # SCAN_LINE_FIT), the outlier is likely *more* reliable.  In that case
        # keep only the high-quality outlier instead of the low-quality
        # agreeing pair.
        if len(mm_coords) >= 3:
            filtered = []
            rejected = []
            for j, (ci, mmi) in enumerate(mm_coords):
                others = [mm for k, (_, mm) in enumerate(mm_coords)
                          if k != j]
                min_dist = min(math.hypot(mmi[0] - o[0], mmi[1] - o[1])
                               for o in others)
                if min_dist < 40.0:
                    filtered.append((ci, mmi))
                else:
                    rejected.append((ci, mmi, min_dist))
                    print(f"[SCR] Cam {ci}: rejected as outlier "
                          f"(dist={min_dist:.0f}mm)")
            # Check if any rejected camera has a better method than all
            # kept cameras — if so, prefer the high-quality outlier.
            if rejected and filtered and methods:
                kept_ranks  = [self._METHOD_RANK.get(methods[ci], 0) for ci, _ in filtered]
                max_kept_rank = max(kept_ranks)
                for ci_rej, mm_rej, dist_rej in rejected:
                    rej_rank = self._METHOD_RANK.get(methods[ci_rej], 0)
                    if rej_rank > max_kept_rank:
                        print(f"[SCR] Cam {ci_rej}: outlier has better method "
                              f"({methods[ci_rej]} rank={rej_rank}) than kept cams "
                              f"(max rank={max_kept_rank}) — preferring outlier")
                        mm_coords = [(ci_rej, mm_rej)]
                        filtered = mm_coords  # skip normal filtered assignment
                        break
                else:
                    if filtered:
                        mm_coords = filtered
            elif filtered:
                mm_coords = filtered
            else:
                # All cameras disagree — try the closest pair
                best_dist = float('inf')
                best_j, best_k = 0, 1
                for j in range(len(mm_coords)):
                    for k in range(j + 1, len(mm_coords)):
                        d = math.hypot(
                            mm_coords[j][1][0] - mm_coords[k][1][0],
                            mm_coords[j][1][1] - mm_coords[k][1][1])
                        if d < best_dist:
                            best_dist = d
                            best_j, best_k = j, k
                if best_dist < 40.0:
                    mm_coords = [mm_coords[best_j], mm_coords[best_k]]
                    print(f"[SCR] All outliers — using closest pair "
                          f"(dist={best_dist:.0f}mm)")
                elif areas:
                    # Closest pair > 40mm — too far to average. All cameras disagree.
                    on_board = [
                        (j, ci, mm)
                        for j, (ci, mm) in enumerate(mm_coords)
                        if math.hypot(mm[0], mm[1]) <= DOUBLE_OUTER
                    ]

                    def _fallback_score(t):
                        j, ci, mm = t
                        m = methods[ci] if methods else 'NONE'
                        m_rank = self._METHOD_RANK.get(m, 0)
                        a = areas[ci] if areas else 0
                        # Penalize massive blobs — but NOT if YOLO confirmed
                        # (YOLO proves a physical dart exists at that location)
                        if a > 2500 and '+YOLO' not in m:
                            m_rank -= 5
                        return (m_rank, a)

                    if on_board:
                        # Smart Fallback: Prioritize Method Rank, then area
                        best_ob = max(on_board, key=_fallback_score)
                        j_ob, ci_ob, _ = best_ob
                        mm_coords = [mm_coords[j_ob]]
                        best_method = methods[ci_ob] if methods else 'UNKNOWN'
                        best_area = areas[ci_ob] if areas else '?'
                        print(f"[SCR] All outliers, pair too far "
                              f"({best_dist:.0f}mm) — smart on-board fallback "
                              f"Cam {ci_ob} (method={best_method}, area={best_area})")
                    else:
                        # No camera on the board — fall back to highest rank/area overall
                        cam_areas = [(j, ci, mm) for j, (ci, mm) in enumerate(mm_coords) if areas[ci] > 0]
                        if cam_areas:
                            best = max(cam_areas, key=_fallback_score)
                            j_best, ci_best, _ = best
                            if areas[ci_best] >= 200 and areas[ci_best] < 3000:
                                mm_coords = [mm_coords[j_best]]
                                best_method = methods[ci_best] if methods else 'UNKNOWN'
                                print(f"[SCR] All outliers, pair too far "
                                      f"({best_dist:.0f}mm) — smart area fallback "
                                      f"Cam {ci_best} (method={best_method}, area={areas[ci_best]})")
                            else:
                                print(f"[SCR] All cameras disagree "
                                      f"(pair={best_dist:.0f}mm, best area invalid) — skipping")
                                return ("SKIP", -1, (0.0, 0.0))
                        else:
                            print(f"[SCR] All cameras disagree "
                                  f"(pair={best_dist:.0f}mm, no areas) — skipping")
                            return ("SKIP", -1, (0.0, 0.0))
                else:
                    print(f"[SCR] All cameras disagree "
                          f"(pair={best_dist:.0f}mm, no areas) — skipping")
                    return ("SKIP", -1, (0.0, 0.0))

        # 2-camera check — if they disagree more than 35 mm, tiebreak.
        # The tip is a single physical point; averaging two readings 35-60mm
        # apart gives a THIRD wrong position.  Tiebreak priority:
        #   1. Higher method rank (LINE_FIT > SCAN_LINE_FIT etc.)
        #   2. Smaller radius from board centre — barrel parallax at 45°
        #      elevation ALWAYS pushes readings OUTWARD (larger r), so the
        #      camera reporting smaller r suffered less barrel contamination
        #      and is closer to the true tip insertion point.
        #   3. Area (last resort, only if radii are within 10mm of each other)
        if len(mm_coords) == 2:
            d = math.hypot(mm_coords[0][1][0] - mm_coords[1][1][0],
                           mm_coords[0][1][1] - mm_coords[1][1][1])
            if d > 35.0:
                ci0, ci1 = mm_coords[0][0], mm_coords[1][0]
                r0 = self._METHOD_RANK.get(methods[ci0], 0) if methods else 0
                r1 = self._METHOD_RANK.get(methods[ci1], 0) if methods else 0
                a0 = areas[ci0] if areas else 0
                a1 = areas[ci1] if areas else 0
                rad0 = math.hypot(mm_coords[0][1][0], mm_coords[0][1][1])
                rad1 = math.hypot(mm_coords[1][1][0], mm_coords[1][1][1])

                if r0 != r1:
                    # Different method quality → prefer higher rank
                    best_idx = 0 if r0 > r1 else 1
                    chosen_ci = mm_coords[best_idx][0]
                    print(f"[SCR] 2 cams disagree {d:.0f}mm "
                          f"— Cam {chosen_ci} wins "
                          f"(method rank {max(r0,r1)} > {min(r0,r1)})")
                elif abs(rad0 - rad1) > 10.0:
                    # Same method — prefer smaller radius (less barrel bias)
                    best_idx = 0 if rad0 < rad1 else 1
                    chosen_ci = mm_coords[best_idx][0]
                    print(f"[SCR] 2 cams disagree {d:.0f}mm same method "
                          f"— Cam {chosen_ci} wins "
                          f"(r={min(rad0,rad1):.0f}mm < r={max(rad0,rad1):.0f}mm, "
                          f"barrel bias correction)")
                elif a0 > 0 or a1 > 0:
                    # Radii very similar — fall back to area
                    best_idx = 0 if a0 >= a1 else 1
                    chosen_ci = mm_coords[best_idx][0]
                    print(f"[SCR] 2 cams disagree {d:.0f}mm same method/radius "
                          f"— Cam {chosen_ci} wins (area={areas[chosen_ci]})")
                else:
                    print(f"[SCR] 2 cams disagree {d:.0f}mm — skipping")
                    return ("SKIP", -1, (0.0, 0.0))
                mm_coords = [mm_coords[best_idx]]

        coords = [mm for _, mm in mm_coords]

        # Quality-weighted average (better detection method = higher weight)
        # Falls back to equal weighting when methods not provided.
        if methods and len(coords) > 1:
            weights = [self._METHOD_WEIGHT.get(methods[ci], 1.0)
                       for ci, _ in mm_coords]
            total_w = sum(weights)
            fin_x = sum(w * mm[0] for w, (_, mm) in zip(weights, mm_coords)) / total_w
            fin_y = sum(w * mm[1] for w, (_, mm) in zip(weights, mm_coords)) / total_w
            used = [(methods[ci], w) for (ci, _), w in zip(mm_coords, weights)]
            print(f"[SCR] Weighted avg {used} -> "
                  f"({fin_x:+.1f},{fin_y:+.1f})mm")
        else:
            # Simple median (no method info)
            xs = sorted(c[0] for c in coords)
            ys = sorted(c[1] for c in coords)
            mid = len(xs) // 2
            fin_x = xs[mid] if len(xs) % 2 else (xs[mid-1] + xs[mid]) / 2
            fin_y = ys[mid] if len(ys) % 2 else (ys[mid-1] + ys[mid]) / 2

        r, theta = self.to_polar(fin_x, fin_y)
        label, score = self.score_from_polar(r, theta)

        # ── Sector-boundary tolerance ─────────────────────────────────────────
        # When the tip is within BOUNDARY_TOL mm of an angular sector wire
        # or a radial ring wire (single/triple/double), the averaged
        # position may land in the wrong zone.  In this boundary zone,
        # prefer the individual camera with the best detection quality.
        BOUNDARY_TOL = 6.0   # mm — wire + detection uncertainty
        RING_BOUNDARIES = [
            TRIPLE_INNER, TRIPLE_OUTER,
            DOUBLE_INNER, DOUBLE_OUTER,
            BULL_INNER_R, BULL_OUTER_R,
        ]

        if methods and len(mm_coords) > 1:
            near_boundary = False
            near_sector_boundary = False
            near_ring_boundary = False

            # Angular sector boundary check
            if r > BULL_OUTER_R:
                base  = (90.0 - SECTOR_ANGLE / 2) % 360.0
                offset = (theta - base) % 360.0
                frac   = (offset % SECTOR_ANGLE)
                deg_tol = math.degrees(math.atan2(BOUNDARY_TOL, r))
                if frac < deg_tol or frac > (SECTOR_ANGLE - deg_tol):
                    near_boundary = True
                    near_sector_boundary = True

            # Radial ring boundary check
            for ring_r in RING_BOUNDARIES:
                if abs(r - ring_r) < BOUNDARY_TOL:
                    near_boundary = True
                    near_ring_boundary = True
                    break

            if near_boundary:
                # Rank cameras by detection quality, pick the best
                _br = self._BOUNDARY_RANK
                ranked = sorted(
                    mm_coords,
                    key=lambda ci_mm: _br.get(methods[ci_mm[0]], 0),
                    reverse=True,
                )
                best_ci, best_mm = ranked[0]
                best_r, best_theta = self.to_polar(best_mm[0], best_mm[1])
                best_label, best_score = self.score_from_polar(best_r, best_theta)
                best_method = methods[best_ci]
                best_rank = _br.get(best_method, 0)
                second_rank = _br.get(methods[ranked[1][0]], 0) if len(ranked) > 1 else -1

                # Do not let equal-quality cameras near an angular wire
                # arbitrarily override the weighted-average consensus.
                if (
                    near_sector_boundary
                    and not near_ring_boundary
                    and best_rank == second_rank
                ):
                    print(f"[SCR] Near-sector boundary tie (r={r:.1f}) "
                          f"— keeping weighted average: {label}")
                elif best_label != label:
                    print(f"[SCR] Near-boundary (r={r:.1f}) "
                          f"-> preferring Cam {best_ci} ({best_method}): "
                          f"{label}->{best_label}")
                    label, score = best_label, best_score
                    fin_x, fin_y = best_mm


        # Store for debug overlay
        return self._finalize_result(
            label,
            score,
            (fin_x, fin_y),
            all_mm_coords,
            per_cam_labels=per_cam_labels,
            methods=methods,
            prefer_matching_camera=len(mm_coords) > 1,
        )

    # ------------------------------------------------------------------
    # Score history
    # ------------------------------------------------------------------
    @property
    def history(self) -> List[dict]:
        return self._history

    @property
    def last_score(self) -> Optional[dict]:
        return self._history[-1] if self._history else None

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------
    def distance_from_center(self, tips, areas, methods):
        """Compute consensus distance (mm) from bullseye center.

        Returns ``(distance_mm, label, score, coord)`` or ``None``
        if no reliable consensus could be reached.
        """
        label, score, coord = self.consensus(tips, areas, methods)
        if score < 0 or coord is None:
            return None
        x_mm, y_mm = coord
        dist = math.sqrt(x_mm ** 2 + y_mm ** 2)
        return (dist, label, score, coord)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------
    def broadcast(self, label: str, score: int,
                  coord_mm: Tuple[float, float]) -> dict:
        payload = {
            "event": "dart_scored",
            "label": label,
            "score": score,
            "x_mm": round(coord_mm[0], 2),
            "y_mm": round(coord_mm[1], 2),
        }
        self._history.append(payload)
        print(f"[SCORE] >>> {label} = {score} pts <<<")
        return payload

