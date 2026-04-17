const BOX_SIZE = 140;
let overlay = null;
let statusTag = null;

function ensureOverlay() {
  if (overlay && document.contains(overlay)) return;

  overlay = document.createElement("div");
  overlay.id = "__python_gaze_box_overlay";
  overlay.style.position = "fixed";
  overlay.style.left = "0px";
  overlay.style.top = "0px";
  overlay.style.width = `${BOX_SIZE}px`;
  overlay.style.height = `${BOX_SIZE}px`;
  overlay.style.border = "3px solid red";
  overlay.style.background = "rgba(255, 0, 0, 0.08)";
  overlay.style.borderRadius = "8px";
  overlay.style.boxSizing = "border-box";
  overlay.style.pointerEvents = "none";
  overlay.style.zIndex = "2147483647";
  overlay.style.transition = "transform 50ms linear";
  overlay.style.transform = "translate(-9999px, -9999px)";

  statusTag = document.createElement("div");
  statusTag.style.position = "fixed";
  statusTag.style.right = "12px";
  statusTag.style.top = "12px";
  statusTag.style.padding = "4px 8px";
  statusTag.style.background = "rgba(0, 0, 0, 0.65)";
  statusTag.style.color = "#fff";
  statusTag.style.font = "12px/1.2 sans-serif";
  statusTag.style.borderRadius = "4px";
  statusTag.style.pointerEvents = "none";
  statusTag.style.zIndex = "2147483647";
  statusTag.textContent = "Gaze: waiting";

  document.documentElement.appendChild(overlay);
  document.documentElement.appendChild(statusTag);
}

function moveBox(normalizedX, normalizedY) {
  ensureOverlay();

  const clampedX = Math.max(0, Math.min(1, normalizedX));
  const clampedY = Math.max(0, Math.min(1, normalizedY));

  const px = clampedX * window.innerWidth - BOX_SIZE / 2;
  const py = clampedY * window.innerHeight - BOX_SIZE / 2;
  overlay.style.transform = `translate(${Math.round(px)}px, ${Math.round(py)}px)`;
}

function setState(state) {
  ensureOverlay();
  statusTag.textContent = `Gaze: ${state}`;
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || typeof message.type !== "string") return;

  if (message.type === "gaze") {
    moveBox(message.x, message.y);
  }

  if (message.type === "gaze_state") {
    setState(message.state);
  }
});

ensureOverlay();
