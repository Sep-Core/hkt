import json
import os
import threading
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


class CoordinateStore:
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._coord = {
      "x_norm": 0.5,
      "y_norm": 0.5,
      "x": COORD_WIDTH // 2,
      "y": COORD_HEIGHT // 2,
      "confidence": 0.0,
    }

  def update(self, x_norm: float, y_norm: float, confidence: float) -> None:
    x_norm = max(0.0, min(1.0, x_norm))
    y_norm = max(0.0, min(1.0, y_norm))
    with self._lock:
      self._coord = {
        "x_norm": x_norm,
        "y_norm": y_norm,
        "x": int(round(x_norm * COORD_WIDTH)),
        "y": int(round(y_norm * COORD_HEIGHT)),
        "confidence": confidence,
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
    self.latest = {"x": 0.5, "y": 0.5, "confidence": 0.0}

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

    left_y_ratio = self._ratio(
      left_iris_y,
      min(left_top_y, left_bottom_y),
      max(left_top_y, left_bottom_y),
    )
    right_y_ratio = self._ratio(
      right_iris_y,
      min(right_top_y, right_bottom_y),
      max(right_top_y, right_bottom_y),
    )

    x = (left_x_ratio + right_x_ratio) / 2.0
    y = (left_y_ratio + right_y_ratio) / 2.0
    if FLIP_X:
      x = 1.0 - x

    alpha = 0.25
    smoothed_x = self.latest["x"] * (1 - alpha) + x * alpha
    smoothed_y = self.latest["y"] * (1 - alpha) + y * alpha

    self.latest = {"x": smoothed_x, "y": smoothed_y, "confidence": 1.0}
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


def build_payload(coord: dict, response_format: str):
  x = coord["x"]
  y = coord["y"]
  if response_format == "nested":
    return {"coordinate": {"x": x, "y": y}}
  if response_format == "array":
    return [x, y]
  if response_format == "text":
    return f"{x},{y}"
  return {"x": x, "y": y}


def make_handler(store: CoordinateStore):
  class CoordinateHandler(BaseHTTPRequestHandler):
    def _write_json(self, payload):
      body = json.dumps(payload).encode("utf-8")
      self.send_response(200)
      self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Cache-Control", "no-store")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def _write_text(self, text: str):
      body = text.encode("utf-8")
      self.send_response(200)
      self.send_header("Content-Type", "text/plain; charset=utf-8")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Cache-Control", "no-store")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def do_GET(self):
      parsed = urlparse(self.path)
      if parsed.path == "/health":
        self._write_json({"ok": True})
        return

      if parsed.path != ENDPOINT:
        self.send_response(404)
        self.end_headers()
        return

      query = parse_qs(parsed.query)
      response_format = query.get("format", [COORD_FORMAT])[0].lower()
      payload = build_payload(store.get(), response_format)
      if isinstance(payload, str):
        self._write_text(payload)
      else:
        self._write_json(payload)

    def log_message(self, _format, *_args):
      return

  return CoordinateHandler


def run_tracking_loop(store: CoordinateStore, stop_event: threading.Event):
  tracker = GazeTracker()
  try:
    while not stop_event.is_set():
      gaze = tracker.read_gaze()
      if gaze:
        store.update(gaze["x"], gaze["y"], gaze["confidence"])
      stop_event.wait(SEND_INTERVAL_SECONDS)
  finally:
    tracker.close()


def main():
  store = CoordinateStore()
  stop_event = threading.Event()

  tracking_thread = threading.Thread(
    target=run_tracking_loop, args=(store, stop_event), daemon=True
  )
  tracking_thread.start()

  handler_cls = make_handler(store)
  server = ThreadingHTTPServer((HOST, PORT), handler_cls)
  print(f"[eye-server] http://{HOST}:{PORT}{ENDPOINT} started")
  print("[eye-server] coordinate formats: object | nested | array | text")
  print("[eye-server] set default format via EYE_COORD_FORMAT")
  print(f"[eye-server] horizontal flip: {'on' if FLIP_X else 'off'} (EYE_FLIP_X)")

  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("[eye-server] stopping...")
  except OSError as err:
    if err.errno == 10048:
      print(
        f"[eye-server] Port {PORT} is already in use. "
        "Stop old process or set EYE_SERVER_PORT."
      )
    else:
      raise
  finally:
    stop_event.set()
    server.server_close()
    tracking_thread.join(timeout=2)


if __name__ == "__main__":
  main()
