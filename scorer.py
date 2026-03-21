"""ThrowVision – Score Mapper.

Converts a camera-pixel dart tip into a board score by:
    1. Perspective-transforming the tip -> board coordinates (mm).
    2. Converting to polar (r, theta).
    3. Looking up the segment via standard dartboard geometry.
    4. Fusing detections from up to 3 cameras.
"""

import math
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

    @staticmethod
    def score_from_polar(r: float, theta: float) -> Tuple[str, int]:
        if r <= BULL_INNER_R:
            return ("DB", 50)
        if r <= BULL_OUTER_R:
            return ("SB", 25)
        if r > DOUBLE_OUTER:
            return ("OFF", 0)

        base = (90.0 - SECTOR_ANGLE / 2) % 360.0   # 20 at top (90°)
        offset = (theta - base) % 360.0
        idx = int(offset // SECTOR_ANGLE) % 20
        sector_val = SECTOR_ORDER[idx]

        if TRIPLE_INNER <= r <= TRIPLE_OUTER:
            return (f"T{sector_val}", sector_val * 3)
        if DOUBLE_INNER <= r <= DOUBLE_OUTER:
            return (f"D{sector_val}", sector_val * 2)
        return (f"S{sector_val}", sector_val)

    # ------------------------------------------------------------------
    # Multi-camera consensus
    # ------------------------------------------------------------------
    # Quality weights for tip detection methods
    _METHOD_WEIGHT = {
        'LINE_FIT':      4.0,   # fitted line, clear tip direction
        'HOUGH_LINE':    4.0,   # Hough-line axis, clear tip direction
        'SCAN_LINE_FIT': 3.5,   # opportunistic scan line-fit
        'PROFILE':       3.0,   # width-profile (legacy fallback)
        'LINE_FIT_WEAK': 1.5,   # line fit — tip direction ambiguous
        'HOUGH_LINE_WEAK': 1.5, # Hough line — tip direction ambiguous
        'PROXIMITY':     1.0,   # centre-proximity tiebreaker
        'WARPED':        0.5,   # warped-space fallback, least reliable
        'NONE':          1.0,
    }

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
            print(f"[SCR] Cross-camera tip override: "
                  f"({xc:+.1f},{yc:+.1f})mm r={rc:.1f} "
                  f"-> {lbl_c} = {sc_c}")
            self.last_tips_mm = all_mm_coords
            self.last_final_mm = (xc, yc)
            self.last_label = lbl_c
            self.last_score_val = sc_c
            return (lbl_c, sc_c, (xc, yc))

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
        }
        if len(per_cam_labels) >= 2:
            from collections import Counter
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

                    self.last_tips_mm = all_mm_coords
                    self.last_final_mm = (avg_x, avg_y)
                    self.last_label = majority_label
                    self.last_score_val = sc_m
                    return (majority_label, sc_m, (avg_x, avg_y))

        # --- Outlier rejection -----------------------------------------
        # Compute pairwise distances.  If one camera is far from the
        # others (> 40 mm), discard it.
        #
        # Exception: if the "outlier" was detected with a higher-quality
        # method (e.g. LINE_FIT) than the cameras being kept (e.g.
        # SCAN_LINE_FIT), the outlier is likely *more* reliable.  In that case
        # keep only the high-quality outlier instead of the low-quality
        # agreeing pair.
        _METHOD_RANK = {
            'LINE_FIT':        4,
            'HOUGH_LINE':      4,
            'SCAN_LINE_FIT':   3,
            'PROFILE':         3,
            'LINE_FIT_WEAK':   1,
            'HOUGH_LINE_WEAK': 1,
            'PROXIMITY':       1,
            'WARPED':          0,
            'NONE':            0,
        }
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
                kept_ranks  = [_METHOD_RANK.get(methods[ci], 0) for ci, _ in filtered]
                max_kept_rank = max(kept_ranks)
                for ci_rej, mm_rej, dist_rej in rejected:
                    rej_rank = _METHOD_RANK.get(methods[ci_rej], 0)
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
                    # Closest pair > 40mm — too far to average.
                    # Prefer any camera whose tip is ON the board (r <= DOUBLE_OUTER).
                    # Only fall back to largest area if none are on-board.
                    on_board = [
                        (j, ci, mm)
                        for j, (ci, mm) in enumerate(mm_coords)
                        if math.hypot(mm[0], mm[1]) <= DOUBLE_OUTER
                    ]
                    if on_board:
                        # Among on-board cameras, pick the one with the largest area
                        best_ob = max(on_board,
                                      key=lambda t: areas[t[1]] if areas else 0)
                        j_ob, ci_ob, _ = best_ob
                        mm_coords = [mm_coords[j_ob]]
                        print(f"[SCR] All outliers, pair too far "
                              f"({best_dist:.0f}mm) — on-board fallback "
                              f"Cam {ci_ob} (area={areas[ci_ob] if areas else '?'})")
                    else:
                        # No camera on the board — fall back to largest area
                        cam_areas = [(j, areas[ci])
                                     for j, (ci, _) in enumerate(mm_coords)
                                     if areas[ci] > 0]
                        if cam_areas:
                            best = max(cam_areas, key=lambda x: x[1])
                            if best[1] >= 200:
                                ci_best = mm_coords[best[0]][0]
                                mm_coords = [mm_coords[best[0]]]
                                print(f"[SCR] All outliers, pair too far "
                                      f"({best_dist:.0f}mm) — area fallback "
                                      f"Cam {ci_best} (area={best[1]})")
                            else:
                                print(f"[SCR] All cameras disagree "
                                      f"(pair={best_dist:.0f}mm, "
                                      f"best area={best[1]}) — skipping")
                                return ("SKIP", -1, (0.0, 0.0))
                        else:
                            print(f"[SCR] All cameras disagree "
                                  f"(pair={best_dist:.0f}mm) — skipping")
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
                r0 = _METHOD_RANK.get(methods[ci0], 0) if methods else 0
                r1 = _METHOD_RANK.get(methods[ci1], 0) if methods else 0
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

            # Angular sector boundary check
            if r > BULL_OUTER_R:
                base  = (90.0 - SECTOR_ANGLE / 2) % 360.0
                offset = (theta - base) % 360.0
                frac   = (offset % SECTOR_ANGLE)
                deg_tol = math.degrees(math.atan2(BOUNDARY_TOL, r))
                if frac < deg_tol or frac > (SECTOR_ANGLE - deg_tol):
                    near_boundary = True

            # Radial ring boundary check
            for ring_r in RING_BOUNDARIES:
                if abs(r - ring_r) < BOUNDARY_TOL:
                    near_boundary = True
                    break

            if near_boundary:
                # Rank cameras by detection quality, pick the best
                rank = {
                    'LINE_FIT': 5, 'HOUGH_LINE': 5,
                    'SCAN_LINE_FIT': 4,
                    'PROFILE': 3, 'LINE_FIT_WEAK': 2, 'HOUGH_LINE_WEAK': 2,
                    'PROXIMITY': 1, 'WARPED': 0, 'NONE': 0,
                }
                best_ci, best_mm = max(
                    mm_coords,
                    key=lambda ci_mm: rank.get(methods[ci_mm[0]], 0)
                )
                best_r, best_theta = self.to_polar(best_mm[0], best_mm[1])
                best_label, best_score = self.score_from_polar(best_r, best_theta)
                best_method = methods[best_ci]
                if best_label != label:
                    print(f"[SCR] Near-boundary (r={r:.1f}) "
                          f"-> preferring Cam {best_ci} ({best_method}): "
                          f"{label}->{best_label}")
                    label, score = best_label, best_score
                    fin_x, fin_y = best_mm


        # Store for debug overlay
        self.last_tips_mm = all_mm_coords
        self.last_final_mm = (fin_x, fin_y)
        self.last_label = label
        self.last_score_val = score

        return (label, score, (fin_x, fin_y))

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

