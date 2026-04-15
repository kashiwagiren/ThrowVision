"""ThrowVision – OpenVINO Inference Engine.

Unified model loader for YOLO11 OpenVINO models.  Handles both
**detection** (bounding boxes) and **pose** (bounding boxes + keypoints)
tasks using the metadata.yaml shipped with each exported model.

Usage
-----
    from openvino_inference import OpenVINOModel

    model = OpenVINOModel("models/tip/y11-p-…_openvino_model")
    detections = model.predict(frame)
    # detections = list of Detection(box, conf, cls, keypoints)
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Keypoint:
    """Single keypoint from a pose model."""
    x: float
    y: float
    visibility: float  # 0-1

@dataclass
class Detection:
    """Single detection from a YOLO model."""
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    class_name: str = ""
    keypoints: List[Keypoint] = field(default_factory=list)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


# ── OpenVINO Model ──────────────────────────────────────────────────────────

class OpenVINOModel:
    """Load and run inference on an OpenVINO YOLO model.

    Parameters
    ----------
    model_dir : str | Path
        Directory containing ``*.xml``, ``*.bin``, and ``metadata.yaml``.
    device : str
        OpenVINO device name: ``"CPU"``, ``"GPU"``, ``"AUTO"``.
    conf_threshold : float
        Minimum confidence for detections.
    iou_threshold : float
        IoU threshold for Non-Maximum Suppression.
    """

    def __init__(
        self,
        model_dir: str,
        device: str = "CPU",
        conf_threshold: float = 0.3,
        iou_threshold: float = 0.45,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.device = device
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self._compiled = None
        self._available = False
        self.execution_devices = str(device)

        # ── Parse metadata ───────────────────────────────────────────────
        meta_path = self.model_dir / "metadata.yaml"
        if not meta_path.exists():
            print(f"[OV] metadata.yaml not found in {model_dir}")
            return
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)

        self.task: str = meta.get("task", "detect")      # 'detect' or 'pose'
        self.stride: int = int(meta.get("stride", 32))
        self.names: Dict[int, str] = {
            int(k): str(v) for k, v in meta.get("names", {}).items()
        }
        self.num_classes = len(self.names)
        self.imgsz: Tuple[int, int] = tuple(meta.get("imgsz", [640, 640]))  # (H, W)
        self.use_half: bool = meta.get("args", {}).get("half", False)
        self.kpt_shape: Optional[Tuple[int, int]] = None  # (num_kpts, dims)
        if self.task == "pose":
            kpt = meta.get("kpt_shape", [1, 3])
            self.kpt_shape = (int(kpt[0]), int(kpt[1]))

        # ── Find model files ─────────────────────────────────────────────
        xml_files = list(self.model_dir.glob("*.xml"))
        if not xml_files:
            print(f"[OV] No .xml found in {model_dir}")
            return
        xml_path = xml_files[0]

        # ── Load OpenVINO ────────────────────────────────────────────────
        try:
            import openvino as ov
            core = ov.Core()
            model = core.read_model(str(xml_path))

            # Compile with performance hints
            config = {}
            if device.upper() == "CPU":
                config["PERFORMANCE_HINT"] = "THROUGHPUT"
            self._compiled = core.compile_model(model, device, config)
            self._input_layer = self._compiled.input(0)
            self._output_layer = self._compiled.output(0)
            try:
                exec_devices = self._compiled.get_property("EXECUTION_DEVICES")
                if isinstance(exec_devices, (list, tuple)):
                    exec_devices = ",".join(str(d) for d in exec_devices if d)
                if exec_devices:
                    self.execution_devices = str(exec_devices)
            except Exception:
                self.execution_devices = str(device)

            # Warmup
            t0 = time.perf_counter()
            dummy = np.zeros(
                (1, 3, self.imgsz[0], self.imgsz[1]),
                dtype=np.float16 if self.use_half else np.float32,
            )
            self._compiled(dummy)
            dt = (time.perf_counter() - t0) * 1000
            print(f"[OV] Loaded {xml_path.stem} on {device} "
                  f"(exec={self.execution_devices}) "
                  f"({self.task}, imgsz={self.imgsz}, "
                  f"classes={self.num_classes}, "
                  f"kpt={self.kpt_shape}) — warmup {dt:.0f}ms")
            self._available = True
        except ImportError:
            print("[OV] openvino package not installed — pip install openvino")
        except Exception as e:
            print(f"[OV] Failed to load model: {e}")

    @property
    def available(self) -> bool:
        return self._available

    # ── Preprocessing ────────────────────────────────────────────────────

    def _preprocess(
        self, frame: np.ndarray
    ) -> Tuple[np.ndarray, float, Tuple[int, int]]:
        """Resize + pad frame to model input size (letterbox).

        Returns
        -------
        blob : np.ndarray   (1, 3, H, W) float32/float16
        scale : float        scale factor applied
        pad : (pad_w, pad_h) pixel padding added (top-left)
        """
        h0, w0 = frame.shape[:2]
        target_h, target_w = self.imgsz  # (H, W)

        # Scale to fit within target size
        scale = min(target_w / w0, target_h / h0)
        new_w = int(round(w0 * scale))
        new_h = int(round(h0 * scale))

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Pad to target size (centre-aligned, gray fill)
        pad_w = (target_w - new_w) // 2
        pad_h = (target_h - new_h) // 2
        padded = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized

        # HWC -> CHW, BGR -> RGB, normalise to [0, 1]
        blob = padded[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        if self.use_half:
            blob = blob.astype(np.float16)
        blob = np.expand_dims(blob, axis=0)  # add batch dim
        return blob, scale, (pad_w, pad_h)

    # ── Postprocessing ───────────────────────────────────────────────────

    def _postprocess(
        self,
        output: np.ndarray,
        scale: float,
        pad: Tuple[int, int],
        orig_shape: Tuple[int, int],
    ) -> List[Detection]:
        """Decode YOLO output tensor into Detection objects.

        YOLO output shape: (1, num_outputs, num_predictions)
        For detect: num_outputs = 4 + num_classes
        For pose:   num_outputs = 4 + num_classes + num_kpts * kpt_dims
        """
        output = np.squeeze(output, axis=0)  # (num_outputs, num_preds)
        if output.ndim == 1:
            return []

        # Transpose: (num_outputs, N) -> (N, num_outputs)
        preds = output.T.astype(np.float32)
        if len(preds) == 0:
            return []

        # Parse boxes (cx, cy, w, h) in model input coordinates
        boxes_cxcywh = preds[:, :4]
        class_scores = preds[:, 4:4 + self.num_classes]

        # Keypoints (if pose model)
        kpt_data = None
        if self.task == "pose" and self.kpt_shape is not None:
            n_kpt, kpt_dims = self.kpt_shape
            kpt_start = 4 + self.num_classes
            kpt_end = kpt_start + n_kpt * kpt_dims
            if preds.shape[1] >= kpt_end:
                kpt_data = preds[:, kpt_start:kpt_end].reshape(-1, n_kpt, kpt_dims)

        # Best class per prediction
        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_scores)), class_ids]

        # Confidence filter
        mask = confidences >= self.conf_threshold
        boxes_cxcywh = boxes_cxcywh[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]
        if kpt_data is not None:
            kpt_data = kpt_data[mask]

        if len(boxes_cxcywh) == 0:
            return []

        # Convert cxcywh -> x1y1x2y2 in model input coordinates
        x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
        y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
        x2 = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2
        y2 = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # NMS per class
        keep_indices = self._nms(boxes_xyxy, confidences, class_ids)
        boxes_xyxy = boxes_xyxy[keep_indices]
        confidences = confidences[keep_indices]
        class_ids = class_ids[keep_indices]
        if kpt_data is not None:
            kpt_data = kpt_data[keep_indices]

        # Remove padding and rescale to original frame coordinates
        pad_w, pad_h = pad
        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - pad_w) / scale
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - pad_h) / scale

        # Clip to original frame
        h0, w0 = orig_shape
        boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]], 0, w0)
        boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]], 0, h0)

        # Build Detection objects
        detections: List[Detection] = []
        for i in range(len(boxes_xyxy)):
            cls_id = int(class_ids[i])
            keypoints: List[Keypoint] = []

            if kpt_data is not None:
                for k in range(self.kpt_shape[0]):
                    kx = (float(kpt_data[i, k, 0]) - pad_w) / scale
                    ky = (float(kpt_data[i, k, 1]) - pad_h) / scale
                    kv = float(kpt_data[i, k, 2]) if self.kpt_shape[1] >= 3 else 1.0
                    keypoints.append(Keypoint(x=kx, y=ky, visibility=kv))

            detections.append(Detection(
                x1=float(boxes_xyxy[i, 0]),
                y1=float(boxes_xyxy[i, 1]),
                x2=float(boxes_xyxy[i, 2]),
                y2=float(boxes_xyxy[i, 3]),
                confidence=float(confidences[i]),
                class_id=cls_id,
                class_name=self.names.get(cls_id, str(cls_id)),
                keypoints=keypoints,
            ))

        return detections

    def _nms(
        self,
        boxes: np.ndarray,
        scores: np.ndarray,
        class_ids: np.ndarray,
    ) -> np.ndarray:
        """Per-class Non-Maximum Suppression."""
        keep = []
        unique_classes = np.unique(class_ids)
        for cls in unique_classes:
            cls_mask = class_ids == cls
            cls_indices = np.where(cls_mask)[0]
            cls_boxes = boxes[cls_mask]
            cls_scores = scores[cls_mask]

            # Sort by score descending
            order = cls_scores.argsort()[::-1]
            cls_boxes = cls_boxes[order]
            cls_scores = cls_scores[order]
            cls_indices = cls_indices[order]

            selected = []
            while len(cls_boxes) > 0:
                selected.append(cls_indices[0])
                if len(cls_boxes) == 1:
                    break

                # IoU with remaining boxes
                ious = self._compute_iou(cls_boxes[0], cls_boxes[1:])
                remaining = ious < self.iou_threshold
                cls_boxes = cls_boxes[1:][remaining]
                cls_scores = cls_scores[1:][remaining]
                cls_indices = cls_indices[1:][remaining]

            keep.extend(selected)
        return np.array(keep, dtype=np.int64)

    @staticmethod
    def _compute_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
        """Compute IoU of one box against many."""
        x1 = np.maximum(box[0], boxes[:, 0])
        y1 = np.maximum(box[1], boxes[:, 1])
        x2 = np.minimum(box[2], boxes[:, 2])
        y2 = np.minimum(box[3], boxes[:, 3])

        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        area1 = (box[2] - box[0]) * (box[3] - box[1])
        area2 = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        union = area1 + area2 - inter
        return inter / np.maximum(union, 1e-7)

    # ── Main predict ─────────────────────────────────────────────────────

    def predict(self, frame: np.ndarray) -> List[Detection]:
        """Run inference on a single BGR frame.

        Returns list of Detection objects in original frame coordinates.
        """
        if not self._available:
            return []

        orig_shape = frame.shape[:2]  # (H, W)
        blob, scale, pad = self._preprocess(frame)

        # Run inference
        result = self._compiled(blob)
        output = result[self._output_layer]

        return self._postprocess(output, scale, pad, orig_shape)

    def predict_timed(
        self, frame: np.ndarray
    ) -> Tuple[List[Detection], float]:
        """predict() with timing.  Returns (detections, inference_ms)."""
        if not self._available:
            return [], 0.0
        orig_shape = frame.shape[:2]
        blob, scale, pad = self._preprocess(frame)
        t0 = time.perf_counter()
        result = self._compiled(blob)
        dt = (time.perf_counter() - t0) * 1000
        output = result[self._output_layer]
        dets = self._postprocess(output, scale, pad, orig_shape)
        return dets, dt


# ── Convenience loaders ──────────────────────────────────────────────────────

def find_models_in_dir(
    base_dir: str,
    task: Optional[str] = None,
) -> List[Path]:
    """Scan *base_dir* for OpenVINO model directories (contain metadata.yaml).

    Optionally filter by ``task`` (``"detect"`` or ``"pose"``).
    """
    base = Path(base_dir)
    if not base.is_dir():
        return []
    results = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        meta = d / "metadata.yaml"
        if not meta.exists():
            continue
        if task is not None:
            with open(meta, "r", encoding="utf-8") as f:
                m = yaml.safe_load(f)
            if m.get("task") != task:
                continue
        results.append(d)
    return results
