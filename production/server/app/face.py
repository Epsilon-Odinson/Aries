"""Face-recognition login — lightweight, honest about its limits.

Uses OpenCV's LBPH recogniser (the fixed spec's fallback when dlib
will not build) plus Haar face detection. No dlib, no face_recognition
dependency, so it installs cleanly in the Render Docker image.

Accuracy note: LBPH on a single enrolled photo is a convenience
lock, not biometric-grade security. It is always paired with a
fallback unlock PIN (SHA-256 hash only — the PIN itself is never
stored). If OpenCV is unavailable the endpoints report that plainly
instead of pretending to check a face.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
from PIL import Image

FACES_DIR = os.getenv("FACES_DIR", "data/faces")
CASCADE = "haarcascade_frontalface_default.xml"


def _cv2():
    try:
        import cv2

        return cv2
    except ImportError as exc:
        raise RuntimeError(
            "Face recognition is unavailable: OpenCV is not installed "
            "on this server."
        ) from exc


def _face_crop(image_bytes: bytes) -> np.ndarray | None:
    """Detect the largest face and return a 200x200 greyscale crop."""
    cv2 = _cv2()
    img = Image.open(__import__("io").BytesIO(image_bytes)).convert("L")
    arr = np.array(img)
    cascade_path = os.path.join(cv2.data.haarcascades, CASCADE)
    detector = cv2.CascadeClassifier(cascade_path)
    faces = detector.detectMultiScale(arr, scaleFactor=1.1, minNeighbors=5)
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    crop = cv2.resize(arr[y : y + h, x : x + w], (200, 200))
    return crop


def _model_path(user_key: str) -> str:
    return os.path.join(FACES_DIR, user_key, "lbph.yml")


def enroll(user_key: str, image_bytes: bytes) -> dict:
    """Enroll a face. Returns {ok, error?}. Rejects photos with no face."""
    cv2 = _cv2()
    crop = _face_crop(image_bytes)
    if crop is None:
        return {"ok": False, "error": "No clear face found in that photo."}
    os.makedirs(os.path.join(FACES_DIR, user_key), exist_ok=True)
    recogniser = cv2.face.LBPHFaceRecognizer_create()
    recogniser.train([crop], np.array([0]))
    recogniser.write(_model_path(user_key))
    return {"ok": True}


def verify(user_key: str, image_bytes: bytes) -> dict:
    """Check a photo against the enrollment. Returns {match, confidence}."""
    cv2 = _cv2()
    path = _model_path(user_key)
    if not os.path.exists(path):
        return {"ok": False, "error": "No face enrolled yet."}
    crop = _face_crop(image_bytes)
    if crop is None:
        return {"ok": False, "error": "No clear face found in that photo."}
    recogniser = cv2.face.LBPHFaceRecognizer_create()
    recogniser.read(path)
    _, confidence = recogniser.predict(crop)
    # LBPH: lower confidence = closer match. 70 is a pragmatic
    # threshold for a single-photo enrollment; tune per user.
    return {"ok": True, "match": bool(confidence < 70), "confidence": float(confidence)}


def remove(user_key: str) -> None:
    path = _model_path(user_key)
    if os.path.exists(path):
        os.remove(path)


def pin_hash(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()
