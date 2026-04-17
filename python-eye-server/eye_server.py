import asyncio
import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
import websockets


HOST = "127.0.0.1"
PORT = int(os.getenv("EYE_SERVER_PORT", "8765"))
SEND_INTERVAL_SECONDS = 0.03
MODEL_URL = (
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
  "face_landmarker/float16/latest/face_landmarker.task"
)
MODEL_PATH = Path(__file__).parent / "models" / "face_landmarker.task"


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
    # Newer mediapipe on Python 3.13 exposes only tasks APIs.
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
    if lm is None:
      self.latest["confidence"] = 0.0
      return self.latest

    # Some models may return only 468 landmarks; iris points then do not exist.
    if len(lm) < 478:
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

    # Light smoothing to make the box movement less jittery.
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


class EyeServer:
  def __init__(self) -> None:
    self.clients = set()
    self.tracker = GazeTracker()

  async def handler(self, websocket):
    self.clients.add(websocket)
    try:
      async for _ in websocket:
        # This demo server is push-only.
        pass
    finally:
      self.clients.discard(websocket)

  async def producer_loop(self) -> None:
    while True:
      payload = self.tracker.read_gaze()
      if payload is not None and self.clients:
        message = json.dumps(payload)
        dead = []
        for client in self.clients:
          try:
            await client.send(message)
          except Exception:
            dead.append(client)
        for client in dead:
          self.clients.discard(client)

      await asyncio.sleep(SEND_INTERVAL_SECONDS)

  async def run(self):
    try:
      async with websockets.serve(self.handler, HOST, PORT):
        print(f"[eye-server] ws://{HOST}:{PORT} started")
        print("[eye-server] keep this process running while extension is active")
        await self.producer_loop()
    except OSError as err:
      if err.errno == 10048:
        raise RuntimeError(
          f"Port {PORT} is already in use. Another eye_server may still be running."
        ) from err
      raise


async def main():
  server = EyeServer()
  try:
    await server.run()
  except RuntimeError as err:
    print(f"[eye-server] {err}")
    print("[eye-server] Tips:")
    print("[eye-server] 1) stop old process: taskkill /PID <pid> /F")
    print("[eye-server] 2) or choose another port: set EYE_SERVER_PORT=8766")
  finally:
    server.tracker.close()


if __name__ == "__main__":
  asyncio.run(main())
