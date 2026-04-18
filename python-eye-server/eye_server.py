import json
import os
import threading
import time
import urllib.request
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
MODEL_URL = (
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
  "face_landmarker/float16/latest/face_landmarker.task"
)
MODEL_PATH = Path(__file__).parent / "models" / "face_landmarker.task"


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
  if not samples or len(samples) < 3:
    return None
  ata = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
  atbx = [0.0, 0.0, 0.0]
  atby = [0.0, 0.0, 0.0]
  for pair in samples:
    raw = pair.get("raw", {})
    target = pair.get("target", {})
    if not isinstance(raw.get("x"), (int, float)) or not isinstance(raw.get("y"), (int, float)):
      return None
    if not isinstance(target.get("x"), (int, float)) or not isinstance(target.get("y"), (int, float)):
      return None
    row = [float(raw["x"]), float(raw["y"]), 1.0]
    for i in range(3):
      for j in range(3):
        ata[i][j] += row[i] * row[j]
      atbx[i] += row[i] * float(target["x"])
      atby[i] += row[i] * float(target["y"])
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

  def apply(self, x: float, y: float):
    state = self.get()
    if not state["enabled"] or not state["affine"]:
      return x, y, False
    a = state["affine"]
    mapped_x = a["ax"] * x + a["bx"] * y + a["cx"]
    mapped_y = a["ay"] * x + a["by"] * y + a["cy"]
    mapped_x = max(0.0, min(float(COORD_WIDTH), mapped_x))
    mapped_y = max(0.0, min(float(COORD_HEIGHT), mapped_y))
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
  def __init__(self) -> None:
    self.backend = "tasks"
    self.face_mesh = None
    self.face_landmarker = None
    self.mp_image = None
    self._init_mediapipe()
    self.cap = cv2.VideoCapture(0)
    if not self.cap.isOpened():
      raise RuntimeError("Cannot open camera. Please check camera permission/device.")
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
    if lm is None or len(lm) < 478:
      self.latest["confidence"] = 0.0
      return self.latest
    left_iris_x, left_iris_y = self._avg_landmark(lm, [468, 469, 470, 471, 472])
    right_iris_x, right_iris_y = self._avg_landmark(lm, [473, 474, 475, 476, 477])
    left_corner_outer_x = lm[33].x
    left_corner_inner_x = lm[133].x
    right_corner_inner_x = lm[362].x
    right_corner_outer_x = lm[263].x
    left_top_y = lm[159].y
    left_bottom_y = lm[145].y
    right_top_y = lm[386].y
    right_bottom_y = lm[374].y
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
    y = (left_y_ratio + right_y_ratio) / 2.0
    if FLIP_X:
      x = 1.0 - x
    alpha = 0.25
    smoothed_x = self.latest["x"] * (1 - alpha) + x * alpha
    smoothed_y = self.latest["y"] * (1 - alpha) + y * alpha
    self.latest = {"x": smoothed_x, "y": smoothed_y, "confidence": 1.0, "backend": self.backend}
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


def build_debug_payload(raw: dict, mapped: dict, calib: dict, selected_format: str, query: dict, request_path: str):
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
      "coord_width": COORD_WIDTH,
      "coord_height": COORD_HEIGHT,
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


def make_handler(store: CoordinateStore, calibration_store: CalibrationStore):
  class CoordinateHandler(BaseHTTPRequestHandler):
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
      if parsed.path == "/health":
        self._write_json({"ok": True})
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

      raw = store.get()
      mapped_x, mapped_y, calibrated = calibration_store.apply(raw["x"], raw["y"])
      mapped = {"x": int(round(mapped_x)), "y": int(round(mapped_y))}
      calib = calibration_store.get()
      calib["applied"] = calibrated

      if debug_mode:
        payload = build_debug_payload(raw, mapped, calib, response_format, query, parsed.path)
        self._write_json(payload)
        return

      payload = build_payload(mapped, response_format)
      if isinstance(payload, str):
        self._write_text(payload)
      else:
        self._write_json(payload)

    def do_POST(self):
      parsed = urlparse(self.path)
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


def run_tracking_loop(store: CoordinateStore, stop_event: threading.Event):
  tracker = GazeTracker()
  try:
    while not stop_event.is_set():
      gaze = tracker.read_gaze()
      if gaze:
        store.update(gaze["x"], gaze["y"], gaze["confidence"], gaze.get("backend", "unknown"))
      stop_event.wait(SEND_INTERVAL_SECONDS)
  finally:
    tracker.close()


def main():
  store = CoordinateStore()
  calibration_store = CalibrationStore()
  stop_event = threading.Event()

  tracking_thread = threading.Thread(
    target=run_tracking_loop, args=(store, stop_event), daemon=True
  )
  tracking_thread.start()

  handler_cls = make_handler(store, calibration_store)
  server = ThreadingHTTPServer((HOST, PORT), handler_cls)
  print(f"[eye-server] http://{HOST}:{PORT}{ENDPOINT} started")
  print("[eye-server] formats: object | nested | array | text | debug")
  print("[eye-server] calibration APIs: GET/POST /calibration, POST /calibration/reset")
  print(f"[eye-server] horizontal flip: {'on' if FLIP_X else 'off'} (EYE_FLIP_X)")

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
