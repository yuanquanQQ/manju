# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0.
"""InsightFace selector with optional cast-reference speaker locking.

LatentSync's upstream detector selects the largest face in every frame.  That
is fine for a close-up, but makes a random actor speak in a multi-person shot.
When LATENTSYNC_FACE_REFERENCE is set, this drop-in module loads buffalo_l's
recognition head and selects the face closest to the locked cast portrait.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import torch
from insightface.app import FaceAnalysis

INSIGHTFACE_DETECT_SIZE = 512


class FaceDetector:
    def __init__(self, device: str = "cuda") -> None:
        reference = os.environ.get("LATENTSYNC_FACE_REFERENCE", "").strip()
        modules = ["detection", "landmark_2d_106"]
        if reference:
            modules.append("recognition")
        self.app = FaceAnalysis(
            allowed_modules=modules,
            root="checkpoints/auxiliary",
            providers=["CUDAExecutionProvider"],
        )
        self.app.prepare(
            ctx_id=cuda_to_int(device),
            det_size=(INSIGHTFACE_DETECT_SIZE, INSIGHTFACE_DETECT_SIZE),
        )
        self.reference_embedding: np.ndarray | None = None
        self.previous_bbox: np.ndarray | None = None
        self.minimum_similarity = float(
            os.environ.get("LATENTSYNC_FACE_MIN_SIMILARITY", "0.18")
        )
        if reference:
            self.reference_embedding = self._load_reference(Path(reference))
            print(
                "Speaker face lock enabled: "
                f"{reference} (minimum similarity {self.minimum_similarity:.2f})"
            )

    def _load_reference(self, path: Path) -> np.ndarray:
        if not path.is_file():
            raise RuntimeError(f"Speaker face reference not found: {path}")
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"Speaker face reference is unreadable: {path}")
        faces = self.app.get(image)
        usable = [face for face in faces if getattr(face, "embedding", None) is not None]
        if not usable:
            raise RuntimeError(f"No recognizable face in speaker reference: {path}")
        face = max(usable, key=lambda item: self._bbox_area(item.bbox))
        return self._normalize(face.embedding)

    def __call__(self, frame: np.ndarray, threshold: float = 0.5):
        frame_array = np.asarray(frame)
        frame_bgr = cv2.cvtColor(frame_array, cv2.COLOR_RGB2BGR)
        frame_height, frame_width, _ = frame_array.shape
        faces = [
            face
            for face in self.app.get(frame_bgr)
            if self._is_usable(face, threshold)
        ]
        if not faces:
            return None, None

        if self.reference_embedding is None:
            selected = max(faces, key=lambda item: self._bbox_area(item.bbox))
        else:
            scored: list[tuple[float, float, object]] = []
            for face in faces:
                embedding = getattr(face, "embedding", None)
                if embedding is None:
                    continue
                similarity = float(
                    np.dot(self.reference_embedding, self._normalize(embedding))
                )
                continuity = self._iou(self.previous_bbox, face.bbox)
                score = similarity + continuity * 0.08
                scored.append((score, similarity, face))
            if not scored:
                raise RuntimeError("Detected faces do not contain recognition embeddings")
            _score, similarity, selected = max(scored, key=lambda item: item[0])
            if similarity < self.minimum_similarity:
                raise RuntimeError(
                    "Target speaker face match is unreliable: "
                    f"similarity {similarity:.3f} < {self.minimum_similarity:.3f}"
                )
            if self.previous_bbox is None:
                bbox = np.round(selected.bbox).astype(np.int_).tolist()
                print(
                    "SPEAKER_FACE_MATCH "
                    f"similarity={similarity:.4f} bbox={bbox}"
                )
        self.previous_bbox = np.asarray(selected.bbox, dtype=np.float32)
        return self._face_geometry(selected, frame_width, frame_height)

    @staticmethod
    def _is_usable(face, threshold: float) -> bool:
        x1, y1, x2, y2 = face.bbox.astype(np.int_).tolist()
        width, height = x2 - x1, y2 - y1
        return (
            width >= 50
            and height >= 80
            and 0.2 <= width / max(height, 1) <= 1.5
            and face.det_score >= threshold
        )

    @staticmethod
    def _face_geometry(face, frame_width: int, frame_height: int):
        landmarks = np.round(face.landmark_2d_106).astype(np.int_)
        half_face = np.mean([landmarks[74], landmarks[73]], axis=0)
        subset = landmarks[LMK_ADAPT_ORIGIN_ORDER]
        half_face_distance = np.max(subset[:, 1]) - half_face[1]
        upper_bound = half_face[1] - half_face_distance
        x1, y1, x2, y2 = (
            np.min(subset[:, 0]),
            int(upper_bound),
            np.max(subset[:, 0]),
            np.max(subset[:, 1]),
        )
        if y2 - y1 <= 0 or x2 - x1 <= 0 or x1 < 0:
            x1, y1, x2, y2 = face.bbox.astype(np.int_).tolist()
        y2 += int((x2 - x1) * 0.1)
        x1 -= int((x2 - x1) * 0.05)
        x2 += int((x2 - x1) * 0.05)
        return (
            max(0, x1),
            max(0, y1),
            min(frame_width, x2),
            min(frame_height, y2),
        ), landmarks

    @staticmethod
    def _normalize(embedding: np.ndarray) -> np.ndarray:
        value = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(value))
        if norm <= 1e-8:
            raise RuntimeError("Face recognition returned an empty embedding")
        return value / norm

    @staticmethod
    def _bbox_area(bbox: np.ndarray) -> float:
        x1, y1, x2, y2 = np.asarray(bbox, dtype=np.float32)
        return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

    @staticmethod
    def _iou(previous: np.ndarray | None, current: np.ndarray) -> float:
        if previous is None:
            return 0.0
        a = np.asarray(previous, dtype=np.float32)
        b = np.asarray(current, dtype=np.float32)
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = FaceDetector._bbox_area(a) + FaceDetector._bbox_area(b) - intersection
        return float(intersection / union) if union > 0 else 0.0


def cuda_to_int(cuda_str: str) -> int:
    if cuda_str == "cuda":
        return 0
    device = torch.device(cuda_str)
    if device.type != "cuda":
        raise ValueError(f"Device type must be 'cuda', got: {device.type}")
    return int(device.index or 0)


LMK_ADAPT_ORIGIN_ORDER = [
    1, 10, 12, 14, 16, 3, 5, 7, 0, 23, 21, 19, 32, 30, 28, 26, 17,
    43, 48, 49, 51, 50, 102, 103, 104, 105, 101, 73, 74, 86,
]
