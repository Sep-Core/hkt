import json
import mimetypes
import os
import threading
import time
import urllib.request
import argparse
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import cv2
import mediapipe as mp


HOST = os.getenv("EYE_SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("EYE_SERVER_PORT", "3000"))
ENDPOINT = os.getenv("EYE_SERVER_ENDPOINT", "/coordinate")
SEND_INTERVAL_SECONDS = 0.03
COORD_FORMAT = os.getenv("EYE_COORD_FORMAT", "object").lower()
COORD_WIDTH = int(os.getenv("EYE_COORD_WIDTH", "1920"))
COORD_HEIGHT = int(os.getenv("EYE_COORD_HEIGHT", "1080"))
FLIP_X = os.getenv("EYE_FLIP_X", "1").lower() not in {"0", "false", "no", "off"}
CAMERA_INDEX = int(os.getenv("EYE_CAMERA_INDEX", "0"))
EYE_VERTICAL_GAIN = float(os.getenv("EYE_VERTICAL_GAIN", "1.6"))
EYE_X_SMOOTHING = float(os.getenv("EYE_X_SMOOTHING", "0.25"))
EYE_Y_SMOOTHING = float(os.getenv("EYE_Y_SMOOTHING", "0.35"))
EYE_SIZE_COMPENSATION = os.getenv("EYE_SIZE_COMPENSATION", "1").lower() not in {"0", "false", "no", "off"}
MODEL_URL = (
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
  "face_landmarker/float16/latest/face_landmarker.task"
)
MODEL_PATH = Path(__file__).parent / "models" / "face_landmarker.task"
WEBUI_DIR = Path(__file__).parent / "webui"

LEFT_UPPER_LID_INDICES = [159, 160, 161, 246]
LEFT_LOWER_LID_INDICES = [145, 144, 163, 7]
RIGHT_UPPER_LID_INDICES = [386, 385, 384, 466]
RIGHT_LOWER_LID_INDICES = [374, 380, 381, 382]


def now_ms() -> int:
  return int(time.time() * 1000)


def solve_3x3(matrix, vector):
  a = [
    [matrix[0][0], matrix[0][1], matrix[0][2], vector[0]],
    [matrix[1][0], matrix[1][1], matrix[1][2], vector[1]],
    [matrix[2][0], matrix[2][1], matrix[2][2], vector[2]],
  ]
  for col in range(3):
    pivot = col
    for row in range(col + 1, 3):
      if abs(a[row][col]) > abs(a[pivot][col]):
        pivot = row
    if abs(a[pivot][col]) < 1e-8:
      return None
    if pivot != col:
      a[col], a[pivot] = a[pivot], a[col]
    base = a[col][col]
    for j in range(col, 4):
      a[col][j] /= base
    for row in range(3):
      if row == col:
        continue
      factor = a[row][col]
      for j in range(col, 4):
        a[row][j] -= factor * a[col][j]
  return [a[0][3], a[1][3], a[2][3]]


def fit_affine(samples):
  if not samples or len(samples) < 5:
    return None

  validated = []
  for pair in samples:
    raw = pair.get("raw", {})
    target = pair.get("target", {})
    if not isinstance(raw.get("x"), (int, float)) or not isinstance(raw.get("y"), (int, float)):
      continue
    if not isinstance(target.get("x"), (int, float)) or not isinstance(target.get("y"), (int, float)):
      continue
    validated.append(
      {
        "raw": {"x": float(raw["x"]), "y": float(raw["y"])},
        "target": {"x": float(target["x"]), "y": float(target["y"])},
      }
    )

  if len(validated) < 5:
    return None

  def solve(pairs):
    ata = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    atbx = [0.0, 0.0, 0.0]
    atby = [0.0, 0.0, 0.0]
    for item in pairs:
      row = [item["raw"]["x"], item["raw"]["y"], 1.0]
      for i in range(3):
        for j in range(3):
          ata[i][j] += row[i] * row[j]
        atbx[i] += row[i] * item["target"]["x"]
        atby[i] += row[i] * item["target"]["y"]
    x_coeffs = solve_3x3(ata, atbx)
    y_coeffs = solve_3x3(ata, atby)
    if not x_coeffs or not y_coeffs:
      return None
    return {
      "ax": x_coeffs[0],
      "bx": x_coeffs[1],
      "cx": x_coeffs[2],
      "ay": y_coeffs[0],
      "by": y_coeffs[1],
      "cy": y_coeffs[2],
    }

  def residual(affine, item):
    rx = item["raw"]["x"]
    ry = item["raw"]["y"]
    tx = item["target"]["x"]
    ty = item["target"]["y"]
    px = affine["ax"] * rx + affine["bx"] * ry + affine["cx"]
    py = affine["ay"] * rx + affine["by"] * ry + affine["cy"]
    return math.sqrt((px - tx) ** 2 + (py - ty) ** 2)

  affine = solve(validated)
  if not affine:
    return None

  # Robust re-fit: drop extreme samples by residual and solve again.
  residuals = [residual(affine, item) for item in validated]
  sorted_r = sorted(residuals)
  median_r = sorted_r[len(sorted_r) // 2]
  threshold = max(24.0, median_r * 2.5)
  filtered = [item for item, r in zip(validated, residuals) if r <= threshold]

  if len(filtered) >= 5:
    refined = solve(filtered)
    if refined:
      return refined

  return affine


class ScreenConfig:
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._state = {
      "width": COORD_WIDTH,
      "height": COORD_HEIGHT,
      "updated_at_ms": now_ms(),
    }

  def get(self) -> dict:
    with self._lock:
      return dict(self._state)

  def set(self, width: int, height: int) -> dict:
    with self._lock:
      w = max(320, min(16384, int(width)))
      h = max(200, min(16384, int(height)))
      self._state = {"width": w, "height": h, "updated_at_ms": now_ms()}
      return dict(self._state)


class CalibrationStore:
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._state = {"enabled": False, "affine": None, "updated_at_ms": None, "sample_count": 0}

  def get(self) -> dict:
    with self._lock:
      return {
        "enabled": bool(self._state["enabled"]),
        "affine": dict(self._state["affine"]) if self._state["affine"] else None,
        "updated_at_ms": self._state["updated_at_ms"],
        "sample_count": self._state["sample_count"],
      }

  def set_affine(self, affine: dict, sample_count: int) -> dict:
    with self._lock:
      self._state = {
        "enabled": True,
        "affine": dict(affine),
        "updated_at_ms": now_ms(),
        "sample_count": int(sample_count),
      }
      return {
        "enabled": bool(self._state["enabled"]),
        "affine": dict(self._state["affine"]) if self._state["affine"] else None,
        "updated_at_ms": self._state["updated_at_ms"],
        "sample_count": self._state["sample_count"],
      }

  def clear(self) -> dict:
    with self._lock:
      self._state = {"enabled": False, "affine": None, "updated_at_ms": now_ms(), "sample_count": 0}
      return {
        "enabled": bool(self._state["enabled"]),
        "affine": None,
        "updated_at_ms": self._state["updated_at_ms"],
        "sample_count": self._state["sample_count"],
      }

  def apply(self, x: float, y: float, width: int, height: int):
    state = self.get()
    if not state["enabled"] or not state["affine"]:
      return x, y, False
    a = state["affine"]
    mapped_x = a["ax"] * x + a["bx"] * y + a["cx"]
    mapped_y = a["ay"] * x + a["by"] * y + a["cy"]
    mapped_x = max(0.0, min(float(width), mapped_x))
    mapped_y = max(0.0, min(float(height), mapped_y))
    return mapped_x, mapped_y, True


class CoordinateStore:
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._coord = {
      "x_norm": 0.5,
      "y_norm": 0.5,
      "x": COORD_WIDTH // 2,
      "y": COORD_HEIGHT // 2,
      "confidence": 0.0,
      "backend": "unknown",
      "last_update_ms": now_ms(),
      "sequence": 0,
    }

  def update(self, x_norm: float, y_norm: float, confidence: float, backend: str) -> None:
    x_norm = max(0.0, min(1.0, x_norm))
    y_norm = max(0.0, min(1.0, y_norm))
    with self._lock:
      self._coord = {
        "x_norm": x_norm,
        "y_norm": y_norm,
        "x": int(round(x_norm * COORD_WIDTH)),
        "y": int(round(y_norm * COORD_HEIGHT)),
        "confidence": confidence,
        "backend": backend,
        "last_update_ms": now_ms(),
        "sequence": self._coord["sequence"] + 1,
      }

  def get(self) -> dict:
    with self._lock:
      return dict(self._coord)


class GazeTracker:
  def __init__(self, camera_index: int = 0, preview_enabled: bool = False) -> None:
    self.backend = "tasks"
    self.face_mesh = None
    self.face_landmarker = None
    self.mp_image = None
    self.preview_enabled = preview_enabled
    self.preview_window_name = "Eye Server Preview"
    self.preview_keypoints = []
    self.last_preview_frame = None
    self.eye_opening_baseline = None
    self.eye_width_baseline = None
    self._init_mediapipe()
    self.cap = cv2.VideoCapture(camera_index)
    if not self.cap.isOpened():
      raise RuntimeError(f"Cannot open camera index {camera_index}. Please check camera permission/device.")
    self.latest = {"x": 0.5, "y": 0.5, "confidence": 0.0, "backend": self.backend}

  def _init_mediapipe(self) -> None:
    if hasattr(mp, "solutions"):
      self.backend = "solutions"
      self.face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
      )
      print("[eye-server] mediapipe backend: solutions")
      return
    self.backend = "tasks"
    self._ensure_task_model()
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    base_options = mp_python.BaseOptions(model_asset_path=str(MODEL_PATH))
    options = vision.FaceLandmarkerOptions(
      base_options=base_options,
      running_mode=vision.RunningMode.IMAGE,
      num_faces=1,
    )
    self.face_landmarker = vision.FaceLandmarker.create_from_options(options)
    self.mp_image = mp.Image
    self.mp_image_format = mp.ImageFormat.SRGB
    print("[eye-server] mediapipe backend: tasks")

  def _ensure_task_model(self) -> None:
    if MODEL_PATH.exists():
      return
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[eye-server] downloading model -> {MODEL_PATH}")
    urllib.request.urlretrieve(MODEL_URL, str(MODEL_PATH))

  def _avg_landmark(self, landmarks, indices):
    xs = [landmarks[i].x for i in indices]
    ys = [landmarks[i].y for i in indices]
    return sum(xs) / len(xs), sum(ys) / len(ys)

  @staticmethod
  def _rotate_xy(x: float, y: float, cx: float, cy: float, sin_a: float, cos_a: float):
    dx = x - cx
    dy = y - cy
    rx = dx * cos_a - dy * sin_a + cx
    ry = dx * sin_a + dy * cos_a + cy
    return rx, ry

  def _avg_rotated_landmark(
    self,
    landmarks,
    indices,
    cx: float,
    cy: float,
    sin_a: float,
    cos_a: float,
  ):
    rotated = [self._rotate_xy(landmarks[i].x, landmarks[i].y, cx, cy, sin_a, cos_a) for i in indices]
    xs = [p[0] for p in rotated]
    ys = [p[1] for p in rotated]
    return sum(xs) / len(xs), sum(ys) / len(ys)

  @staticmethod
  def _ratio(value, start, end) -> float:
    denom = end - start
    if abs(denom) < 1e-6:
      return 0.5
    ratio = (value - start) / denom
    return max(0.0, min(1.0, ratio))

  def read_gaze(self) -> Optional[dict]:
    ok, frame = self.cap.read()
    if not ok:
      return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    lm = self._extract_landmarks(rgb)
    self.preview_keypoints = []
    if lm is None or len(lm) < 478:
      self.latest["confidence"] = 0.0
      self._update_preview_frame(frame, found_face=False)
      return self.latest

    self.preview_keypoints = self._build_preview_points(lm)
    # Head roll compensation:
    # rotate landmarks so the left-right eye line becomes horizontal.
    left_eye_center = ((lm[33].x + lm[133].x) * 0.5, (lm[33].y + lm[133].y) * 0.5)
    right_eye_center = ((lm[263].x + lm[362].x) * 0.5, (lm[263].y + lm[362].y) * 0.5)
    roll = math.atan2(right_eye_center[1] - left_eye_center[1], right_eye_center[0] - left_eye_center[0])
    rot_center_x = (left_eye_center[0] + right_eye_center[0]) * 0.5
    rot_center_y = (left_eye_center[1] + right_eye_center[1]) * 0.5
    sin_a = math.sin(-roll)
    cos_a = math.cos(-roll)

    left_iris_x, left_iris_y = self._avg_rotated_landmark(
      lm, [468, 469, 470, 471, 472], rot_center_x, rot_center_y, sin_a, cos_a
    )
    right_iris_x, right_iris_y = self._avg_rotated_landmark(
      lm, [473, 474, 475, 476, 477], rot_center_x, rot_center_y, sin_a, cos_a
    )

    left_corner_outer_x, _ = self._rotate_xy(lm[33].x, lm[33].y, rot_center_x, rot_center_y, sin_a, cos_a)
    left_corner_inner_x, _ = self._rotate_xy(lm[133].x, lm[133].y, rot_center_x, rot_center_y, sin_a, cos_a)
    right_corner_inner_x, _ = self._rotate_xy(lm[362].x, lm[362].y, rot_center_x, rot_center_y, sin_a, cos_a)
    right_corner_outer_x, _ = self._rotate_xy(lm[263].x, lm[263].y, rot_center_x, rot_center_y, sin_a, cos_a)

    _, left_top_y = self._avg_rotated_landmark(
      lm, LEFT_UPPER_LID_INDICES, rot_center_x, rot_center_y, sin_a, cos_a
    )
    _, left_bottom_y = self._avg_rotated_landmark(
      lm, LEFT_LOWER_LID_INDICES, rot_center_x, rot_center_y, sin_a, cos_a
    )
    _, right_top_y = self._avg_rotated_landmark(
      lm, RIGHT_UPPER_LID_INDICES, rot_center_x, rot_center_y, sin_a, cos_a
    )
    _, right_bottom_y = self._avg_rotated_landmark(
      lm, RIGHT_LOWER_LID_INDICES, rot_center_x, rot_center_y, sin_a, cos_a
    )
    left_x_ratio = self._ratio(
      left_iris_x,
      min(left_corner_outer_x, left_corner_inner_x),
      max(left_corner_outer_x, left_corner_inner_x),
    )
    right_x_ratio = self._ratio(
      right_iris_x,
      min(right_corner_outer_x, right_corner_inner_x),
      max(right_corner_outer_x, right_corner_inner_x),
    )
    left_y_ratio = self._ratio(left_iris_y, min(left_top_y, left_bottom_y), max(left_top_y, left_bottom_y))
    right_y_ratio = self._ratio(
      right_iris_y, min(right_top_y, right_bottom_y), max(right_top_y, right_bottom_y)
    )
    x = (left_x_ratio + right_x_ratio) / 2.0

    # Improve vertical sensitivity with eyelid-opening adaptive gain.
    left_opening = max(1e-4, left_bottom_y - left_top_y)
    right_opening = max(1e-4, right_bottom_y - right_top_y)
    left_width = max(1e-4, abs(left_corner_inner_x - left_corner_outer_x))
    right_width = max(1e-4, abs(right_corner_outer_x - right_corner_inner_x))

    # EAR-like reliability; down-weight eye when it appears too "small" or squinted.
    left_reliability = max(0.2, min(1.0, left_opening / left_width / 0.35))
    right_reliability = max(0.2, min(1.0, right_opening / right_width / 0.35))
    y_raw = (
      left_y_ratio * left_reliability + right_y_ratio * right_reliability
    ) / (left_reliability + right_reliability)

    opening = (left_opening + right_opening) / 2.0
    width = (left_width + right_width) / 2.0
    if self.eye_opening_baseline is None:
      self.eye_opening_baseline = opening
    else:
      self.eye_opening_baseline = self.eye_opening_baseline * 0.95 + opening * 0.05
    if self.eye_width_baseline is None:
      self.eye_width_baseline = width
    else:
      self.eye_width_baseline = self.eye_width_baseline * 0.98 + width * 0.02

    openness_ratio = opening / max(1e-4, self.eye_opening_baseline)
    adaptive_gain = EYE_VERTICAL_GAIN * max(0.85, min(1.2, openness_ratio))

    # Personal eye-size compensation:
    # normalize sensitivity by person's relative eye opening (opening/width).
    if EYE_SIZE_COMPENSATION:
      current_ratio = opening / max(1e-4, width)
      baseline_ratio = self.eye_opening_baseline / max(1e-4, self.eye_width_baseline)
      size_factor = (baseline_ratio / max(1e-4, current_ratio)) ** 0.5
      adaptive_gain *= max(0.8, min(1.25, size_factor))

    y = 0.5 + (y_raw - 0.5) * adaptive_gain
    y = max(0.0, min(1.0, y))
    if FLIP_X:
      x = 1.0 - x
    alpha_x = max(0.01, min(1.0, EYE_X_SMOOTHING))
    alpha_y = max(0.01, min(1.0, EYE_Y_SMOOTHING))
    smoothed_x = self.latest["x"] * (1 - alpha_x) + x * alpha_x
    smoothed_y = self.latest["y"] * (1 - alpha_y) + y * alpha_y
    self.latest = {"x": smoothed_x, "y": smoothed_y, "confidence": 1.0, "backend": self.backend}
    self._update_preview_frame(frame, found_face=True)
    return self.latest

  def _extract_landmarks(self, rgb_frame):
    if self.backend == "solutions":
      result = self.face_mesh.process(rgb_frame)
      if not result.multi_face_landmarks:
        return None
      return result.multi_face_landmarks[0].landmark
    mp_image = self.mp_image(image_format=self.mp_image_format, data=rgb_frame)
    result = self.face_landmarker.detect(mp_image)
    if not result.face_landmarks:
      return None
    return result.face_landmarks[0]

  def close(self) -> None:
    self.cap.release()
    if self.face_mesh is not None:
      self.face_mesh.close()
    if self.face_landmarker is not None:
      self.face_landmarker.close()
    if self.preview_enabled:
      cv2.destroyAllWindows()

  def _build_preview_points(self, landmarks):
    important_indices = [
      33, 133, 362, 263,
      *LEFT_UPPER_LID_INDICES,
      *LEFT_LOWER_LID_INDICES,
      *RIGHT_UPPER_LID_INDICES,
      *RIGHT_LOWER_LID_INDICES,
      468, 469, 470, 471, 472, 473, 474, 475, 476, 477
    ]
    return [{"x": landmarks[i].x, "y": landmarks[i].y} for i in important_indices]

  def _update_preview_frame(self, frame, found_face: bool):
    if not self.preview_enabled:
      return

    canvas = frame.copy()
    h, w = canvas.shape[:2]
    if found_face:
      for pt in self.preview_keypoints:
        px = int(max(0, min(w - 1, pt["x"] * w)))
        py = int(max(0, min(h - 1, pt["y"] * h)))
        cv2.circle(canvas, (px, py), 2, (0, 255, 255), -1)

      gx = int(max(0, min(w - 1, self.latest["x"] * w)))
      gy = int(max(0, min(h - 1, self.latest["y"] * h)))
      cv2.circle(canvas, (gx, gy), 8, (0, 0, 255), 2)
      cv2.putText(
        canvas,
        f"gaze=({self.latest['x']:.3f},{self.latest['y']:.3f}) conf={self.latest['confidence']:.2f}",
        (12, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
      )
    else:
      cv2.putText(
        canvas,
        "No face detected",
        (12, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 255),
        2,
        cv2.LINE_AA,
      )

    cv2.putText(
      canvas,
      "Press Q / ESC to quit",
      (12, h - 12),
      cv2.FONT_HERSHEY_SIMPLEX,
      0.5,
      (255, 255, 255),
      1,
      cv2.LINE_AA,
    )
    self.last_preview_frame = canvas

  def render_preview(self, stop_event: threading.Event):
    if not self.preview_enabled or self.last_preview_frame is None:
      return
    cv2.imshow(self.preview_window_name, self.last_preview_frame)
    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord("q"), ord("Q")):
      stop_event.set()


def build_payload(mapped: dict, response_format: str):
  x = mapped["x"]
  y = mapped["y"]
  if response_format == "nested":
    return {"coordinate": {"x": x, "y": y}}
  if response_format == "array":
    return [x, y]
  if response_format == "text":
    return f"{x},{y}"
  return {"x": x, "y": y}


def build_debug_payload(
  raw: dict,
  mapped: dict,
  calib: dict,
  selected_format: str,
  query: dict,
  request_path: str,
  screen_width: int,
  screen_height: int,
):
  ts = now_ms()
  age_ms = max(0, ts - int(raw.get("last_update_ms", ts)))
  return {
    "ok": True,
    "coordinate": {"x": mapped["x"], "y": mapped["y"]},
    "coordinate_mapped": {"x": mapped["x"], "y": mapped["y"]},
    "coordinate_raw": {"x": raw["x"], "y": raw["y"]},
    "coordinate_norm": {"x": raw["x_norm"], "y": raw["y_norm"]},
    "confidence": raw.get("confidence", 0.0),
    "tracking": {
      "backend": raw.get("backend", "unknown"),
      "sequence": raw.get("sequence", 0),
      "last_update_ms": raw.get("last_update_ms", ts),
      "age_ms": age_ms,
    },
    "calibration": calib,
    "server": {
      "host": HOST,
      "port": PORT,
      "endpoint": ENDPOINT,
      "coord_width": screen_width,
      "coord_height": screen_height,
      "flip_x": FLIP_X,
      "default_format": COORD_FORMAT,
    },
    "request": {
      "path": request_path,
      "selected_format": selected_format,
      "query": query,
      "server_time_ms": ts,
    },
    "compat": {
      "object": {"x": mapped["x"], "y": mapped["y"]},
      "nested": {"coordinate": {"x": mapped["x"], "y": mapped["y"]}},
      "array": [mapped["x"], mapped["y"]],
      "text": f"{mapped['x']},{mapped['y']}",
    },
  }


def make_handler(store: CoordinateStore, calibration_store: CalibrationStore, screen_config: ScreenConfig):
  class CoordinateHandler(BaseHTTPRequestHandler):
    def _serve_static_file(self, path: Path):
      if not path.exists() or not path.is_file():
        self._write_json({"ok": False, "error": "not-found"}, status=404)
        return
      body = path.read_bytes()
      content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
      self.send_response(200)
      self._set_common_headers(content_type, len(body))
      self.end_headers()
      self.wfile.write(body)

    def _raw_from_norm(self, coord: dict, width: int, height: int):
      x_norm = max(0.0, min(1.0, float(coord.get("x_norm", 0.5))))
      y_norm = max(0.0, min(1.0, float(coord.get("y_norm", 0.5))))
      return {
        "x": int(round(x_norm * width)),
        "y": int(round(y_norm * height)),
        "x_norm": x_norm,
        "y_norm": y_norm,
        "confidence": float(coord.get("confidence", 0.0)),
        "backend": coord.get("backend", "unknown"),
        "last_update_ms": int(coord.get("last_update_ms", now_ms())),
        "sequence": int(coord.get("sequence", 0)),
      }

    def _set_common_headers(self, content_type: str, content_length: int):
      self.send_header("Content-Type", content_type)
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "Content-Type")
      self.send_header("Cache-Control", "no-store")
      self.send_header("Content-Length", str(content_length))

    def _write_json(self, payload, status=200):
      body = json.dumps(payload).encode("utf-8")
      self.send_response(status)
      self._set_common_headers("application/json; charset=utf-8", len(body))
      self.end_headers()
      self.wfile.write(body)

    def _write_text(self, text: str, status=200):
      body = text.encode("utf-8")
      self.send_response(status)
      self._set_common_headers("text/plain; charset=utf-8", len(body))
      self.end_headers()
      self.wfile.write(body)

    def _read_json_body(self):
      length = int(self.headers.get("Content-Length", "0"))
      if length <= 0:
        return {}
      raw = self.rfile.read(length).decode("utf-8")
      try:
        return json.loads(raw)
      except json.JSONDecodeError:
        return None

    def do_OPTIONS(self):
      self.send_response(204)
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "Content-Type")
      self.send_header("Access-Control-Max-Age", "86400")
      self.end_headers()

    def do_GET(self):
      parsed = urlparse(self.path)
      if parsed.path in {"/", "/index.html"}:
        self._serve_static_file(WEBUI_DIR / "index.html")
        return
      if parsed.path.startswith("/webui/"):
        rel = parsed.path[len("/webui/"):]
        safe_rel = Path(rel)
        if ".." in safe_rel.parts:
          self._write_json({"ok": False, "error": "not-found"}, status=404)
          return
        self._serve_static_file(WEBUI_DIR / safe_rel)
        return
      if parsed.path == "/health":
        self._write_json({"ok": True})
        return
      if parsed.path == "/screen":
        self._write_json({"ok": True, "screen": screen_config.get()})
        return
      if parsed.path == "/calibration":
        self._write_json({"ok": True, "calibration": calibration_store.get()})
        return
      if parsed.path != ENDPOINT:
        self._write_json({"ok": False, "error": "not-found"}, status=404)
        return

      query = parse_qs(parsed.query)
      response_format = query.get("format", [COORD_FORMAT])[0].lower()
      debug_mode = (
        response_format == "debug"
        or query.get("debug", ["0"])[0].lower() in {"1", "true", "yes", "on"}
        or query.get("verbose", ["0"])[0].lower() in {"1", "true", "yes", "on"}
      )

      screen = screen_config.get()
      raw = self._raw_from_norm(store.get(), screen["width"], screen["height"])
      mapped_x, mapped_y, calibrated = calibration_store.apply(raw["x"], raw["y"], screen["width"], screen["height"])
      mapped = {"x": int(round(mapped_x)), "y": int(round(mapped_y))}
      calib = calibration_store.get()
      calib["applied"] = calibrated

      if debug_mode:
        payload = build_debug_payload(
          raw,
          mapped,
          calib,
          response_format,
          query,
          parsed.path,
          screen["width"],
          screen["height"],
        )
        self._write_json(payload)
        return

      payload = build_payload(mapped, response_format)
      if isinstance(payload, str):
        self._write_text(payload)
      else:
        self._write_json(payload)

    def do_POST(self):
      parsed = urlparse(self.path)
      if parsed.path == "/screen":
        body = self._read_json_body()
        if body is None:
          self._write_json({"ok": False, "error": "invalid-json"}, status=400)
          return
        width = body.get("width")
        height = body.get("height")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
          self._write_json({"ok": False, "error": "invalid-screen-size"}, status=400)
          return
        state = screen_config.set(int(width), int(height))
        self._write_json({"ok": True, "screen": state})
        return
      if parsed.path == "/calibration/reset":
        state = calibration_store.clear()
        self._write_json({"ok": True, "calibration": state})
        return
      if parsed.path != "/calibration":
        self._write_json({"ok": False, "error": "not-found"}, status=404)
        return

      body = self._read_json_body()
      if body is None:
        self._write_json({"ok": False, "error": "invalid-json"}, status=400)
        return

      samples = body.get("samples")
      affine = body.get("affine")
      if samples:
        affine = fit_affine(samples)
        if not affine:
          self._write_json({"ok": False, "error": "invalid-samples"}, status=400)
          return
        state = calibration_store.set_affine(affine, len(samples))
        self._write_json({"ok": True, "calibration": state})
        return
      if affine:
        required = {"ax", "bx", "cx", "ay", "by", "cy"}
        if not required.issubset(set(affine.keys())):
          self._write_json({"ok": False, "error": "invalid-affine"}, status=400)
          return
        state = calibration_store.set_affine(affine, int(body.get("sample_count", 0)))
        self._write_json({"ok": True, "calibration": state})
        return

      self._write_json({"ok": False, "error": "missing-samples-or-affine"}, status=400)

    def log_message(self, _format, *_args):
      return

  return CoordinateHandler


def run_tracking_loop(store: CoordinateStore, stop_event: threading.Event, camera_index: int, preview: bool):
  tracker = GazeTracker(camera_index=camera_index, preview_enabled=preview)
  try:
    while not stop_event.is_set():
      gaze = tracker.read_gaze()
      if gaze:
        store.update(gaze["x"], gaze["y"], gaze["confidence"], gaze.get("backend", "unknown"))
      tracker.render_preview(stop_event)
      stop_event.wait(SEND_INTERVAL_SECONDS)
  finally:
    tracker.close()


def main():
  parser = argparse.ArgumentParser(description="Shrimp eye tracking backend")
  parser.add_argument(
    "--preview",
    action="store_true",
    help="Show webcam preview with keypoint overlays (press Q/ESC to quit).",
  )
  parser.add_argument(
    "--camera-index",
    type=int,
    default=CAMERA_INDEX,
    help=f"Camera index for OpenCV (default: {CAMERA_INDEX}).",
  )
  args = parser.parse_args()

  store = CoordinateStore()
  calibration_store = CalibrationStore()
  screen_config = ScreenConfig()
  stop_event = threading.Event()

  tracking_thread = threading.Thread(
    target=run_tracking_loop, args=(store, stop_event, args.camera_index, args.preview), daemon=True
  )
  tracking_thread.start()

  handler_cls = make_handler(store, calibration_store, screen_config)
  server = ThreadingHTTPServer((HOST, PORT), handler_cls)
  print(f"[eye-server] http://{HOST}:{PORT}{ENDPOINT} started")
  print(f"[eye-server] web UI: http://{HOST}:{PORT}/")
  print("[eye-server] formats: object | nested | array | text | debug")
  print("[eye-server] calibration APIs: GET/POST /calibration, POST /calibration/reset")
  print("[eye-server] screen APIs: GET/POST /screen")
  print(f"[eye-server] horizontal flip: {'on' if FLIP_X else 'off'} (EYE_FLIP_X)")
  print(
    f"[eye-server] vertical tuning: gain={EYE_VERTICAL_GAIN}, "
    f"x_smoothing={EYE_X_SMOOTHING}, y_smoothing={EYE_Y_SMOOTHING}, "
    f"size_compensation={'on' if EYE_SIZE_COMPENSATION else 'off'}"
  )
  if args.preview:
    print("[eye-server] preview enabled: webcam + keypoints overlay")

  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("[eye-server] stopping...")
  except OSError as err:
    if err.errno == 10048:
      print(f"[eye-server] Port {PORT} is already in use. Stop old process or set EYE_SERVER_PORT.")
    else:
      raise
  finally:
    stop_event.set()
    server.server_close()
    tracking_thread.join(timeout=2)


if __name__ == "__main__":
  main()
