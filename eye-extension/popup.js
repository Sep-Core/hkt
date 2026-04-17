const DEFAULT_SETTINGS = {
  apiUrl: "http://127.0.0.1:3000/coordinate",
  coordinateBasis: "auto",
  pollMs: 80,
  spotlightRadius: 180,
  showDebugBox: true
};

const apiUrlEl = document.getElementById("api-url");
const basisEl = document.getElementById("basis");
const pollMsEl = document.getElementById("poll-ms");
const radiusEl = document.getElementById("radius");
const debugBoxEl = document.getElementById("show-debug-box");
const statusEl = document.getElementById("status");
const saveBtn = document.getElementById("save-btn");
const calibrateBtn = document.getElementById("calibrate-btn");
const resetCalibrationBtn = document.getElementById("reset-calibration-btn");

chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
  const settings = { ...DEFAULT_SETTINGS, ...stored };
  apiUrlEl.value = settings.apiUrl;
  basisEl.value = settings.coordinateBasis;
  pollMsEl.value = String(settings.pollMs);
  radiusEl.value = String(settings.spotlightRadius);
  debugBoxEl.checked = Boolean(settings.showDebugBox);
});

saveBtn.addEventListener("click", () => {
  const data = {
    apiUrl: apiUrlEl.value.trim(),
    coordinateBasis: basisEl.value,
    pollMs: Math.max(30, Number(pollMsEl.value) || DEFAULT_SETTINGS.pollMs),
    spotlightRadius: Math.max(60, Number(radiusEl.value) || DEFAULT_SETTINGS.spotlightRadius),
    showDebugBox: debugBoxEl.checked
  };

  chrome.storage.local.set(data, () => {
    statusEl.textContent = "Saved. Refresh target page if needed.";
  });
});

function sendToActiveTab(message, onDone) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tabId = tabs?.[0]?.id;
    if (!tabId) {
      statusEl.textContent = "No active tab found.";
      return;
    }
    chrome.tabs.sendMessage(tabId, message, (resp) => {
      if (chrome.runtime.lastError) {
        statusEl.textContent = "Open a normal web page, then retry.";
        return;
      }
      if (onDone) onDone(resp);
    });
  });
}

calibrateBtn.addEventListener("click", () => {
  statusEl.textContent = "Calibration started in current tab...";
  sendToActiveTab({ type: "shrimp_start_calibration" }, (resp) => {
    if (!resp?.ok) {
      statusEl.textContent = resp?.error || "Calibration failed to start.";
      return;
    }
    statusEl.textContent = "Calibration complete.";
  });
});

resetCalibrationBtn.addEventListener("click", () => {
  sendToActiveTab({ type: "shrimp_reset_calibration" }, (resp) => {
    if (!resp?.ok) {
      statusEl.textContent = resp?.error || "Reset failed.";
      return;
    }
    statusEl.textContent = "Calibration reset.";
  });
});
