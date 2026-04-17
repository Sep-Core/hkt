const BOX_SIZE = 140;
const SETTINGS_DEFAULTS = {
  apiUrl: "http://127.0.0.1:3000/coordinate",
  coordinateBasis: "auto",
  pollMs: 80,
  spotlightRadius: 180,
  showDebugBox: true
};

let settings = { ...SETTINGS_DEFAULTS };
let overlay = null;
let debugBox = null;
let statusTag = null;
let pollTimer = null;
let lastMouseViewport = null;
let latestRawViewportCoord = null;
let calibration = null;
let calibrationInProgress = false;
let calibrationTarget = null;

function ensureOverlay() {
  if (overlay && document.contains(overlay)) return;

  overlay = document.createElement("div");
  overlay.id = "__shrimp_spotlight_overlay";
  overlay.style.position = "fixed";
  overlay.style.left = "0";
  overlay.style.top = "0";
  overlay.style.width = "100vw";
  overlay.style.height = "100vh";
  overlay.style.pointerEvents = "none";
  overlay.style.zIndex = "2147483646";
  overlay.style.background = "rgba(20, 20, 20, 0.22)";
  overlay.style.backdropFilter = "brightness(0.9) contrast(0.92) saturate(0.88)";
  overlay.style.webkitBackdropFilter = "brightness(0.9) contrast(0.92) saturate(0.88)";

  debugBox = document.createElement("div");
  debugBox.id = "__shrimp_debug_box";
  debugBox.style.position = "fixed";
  debugBox.style.left = "0";
  debugBox.style.top = "0";
  debugBox.style.width = `${BOX_SIZE}px`;
  debugBox.style.height = `${BOX_SIZE}px`;
  debugBox.style.border = "3px solid #ff2d2d";
  debugBox.style.background = "rgba(255, 0, 0, 0.06)";
  debugBox.style.borderRadius = "8px";
  debugBox.style.boxSizing = "border-box";
  debugBox.style.pointerEvents = "none";
  debugBox.style.zIndex = "2147483647";
  debugBox.style.transform = "translate(-9999px, -9999px)";
  debugBox.style.display = settings.showDebugBox ? "block" : "none";

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
  statusTag.textContent = "Shrimp: ready";

  document.documentElement.appendChild(overlay);
  document.documentElement.appendChild(debugBox);
  document.documentElement.appendChild(statusTag);
}

function setStatus(text) {
  ensureOverlay();
  statusTag.textContent = text;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function applySpotlight(x, y) {
  ensureOverlay();
  const radius = Math.max(60, Number(settings.spotlightRadius) || 180);
  const cx = Math.max(0, Math.min(window.innerWidth, x));
  const cy = Math.max(0, Math.min(window.innerHeight, y));

  overlay.style.webkitMaskImage = `radial-gradient(circle ${radius}px at ${cx}px ${cy}px, transparent 0 ${radius}px, black ${radius + 1}px)`;
  overlay.style.maskImage = `radial-gradient(circle ${radius}px at ${cx}px ${cy}px, transparent 0 ${radius}px, black ${radius + 1}px)`;

  if (settings.showDebugBox) {
    debugBox.style.display = "block";
    debugBox.style.transform = `translate(${Math.round(cx - BOX_SIZE / 2)}px, ${Math.round(cy - BOX_SIZE / 2)}px)`;
  } else {
    debugBox.style.display = "none";
  }
}

function applyCalibration(coord) {
  if (!calibration?.enabled || !calibration?.affine) return coord;
  const a = calibration.affine;
  return {
    x: a.ax * coord.x + a.bx * coord.y + a.cx,
    y: a.ay * coord.x + a.by * coord.y + a.cy
  };
}

function parseCoordinateFromText(text) {
  const match = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text);
  if (!match) return null;
  return { x: Number(match[1]), y: Number(match[2]) };
}

function parseCoordinate(payload) {
  if (Array.isArray(payload) && payload.length >= 2) {
    return { x: Number(payload[0]), y: Number(payload[1]) };
  }
  if (payload && typeof payload === "object") {
    if (typeof payload.x === "number" && typeof payload.y === "number") {
      return { x: payload.x, y: payload.y };
    }
    if (
      payload.coordinate &&
      typeof payload.coordinate.x === "number" &&
      typeof payload.coordinate.y === "number"
    ) {
      return { x: payload.coordinate.x, y: payload.coordinate.y };
    }
  }
  return null;
}

function toViewportCoordinate(coord) {
  const basis = settings.coordinateBasis;
  if (basis === "viewport") return coord;
  if (basis === "document") {
    return { x: coord.x - window.scrollX, y: coord.y - window.scrollY };
  }

  const viewTry = { x: coord.x, y: coord.y };
  if (
    viewTry.x >= -40 &&
    viewTry.x <= window.innerWidth + 40 &&
    viewTry.y >= -40 &&
    viewTry.y <= window.innerHeight + 40
  ) {
    return viewTry;
  }
  return { x: coord.x - window.scrollX, y: coord.y - window.scrollY };
}

function fallbackFocusPoint() {
  if (lastMouseViewport) return lastMouseViewport;
  return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
}

function solve3x3(matrix, vector) {
  const a = [
    [matrix[0][0], matrix[0][1], matrix[0][2], vector[0]],
    [matrix[1][0], matrix[1][1], matrix[1][2], vector[1]],
    [matrix[2][0], matrix[2][1], matrix[2][2], vector[2]]
  ];

  for (let col = 0; col < 3; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < 3; row += 1) {
      if (Math.abs(a[row][col]) > Math.abs(a[pivot][col])) pivot = row;
    }
    if (Math.abs(a[pivot][col]) < 1e-8) return null;
    if (pivot !== col) {
      const tmp = a[col];
      a[col] = a[pivot];
      a[pivot] = tmp;
    }
    const base = a[col][col];
    for (let j = col; j < 4; j += 1) a[col][j] /= base;
    for (let row = 0; row < 3; row += 1) {
      if (row === col) continue;
      const factor = a[row][col];
      for (let j = col; j < 4; j += 1) {
        a[row][j] -= factor * a[col][j];
      }
    }
  }
  return [a[0][3], a[1][3], a[2][3]];
}

function fitAffine(pairs) {
  if (!pairs || pairs.length < 3) return null;

  const ata = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0]
  ];
  const atbx = [0, 0, 0];
  const atby = [0, 0, 0];

  for (const p of pairs) {
    const row = [p.raw.x, p.raw.y, 1];
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) {
        ata[i][j] += row[i] * row[j];
      }
      atbx[i] += row[i] * p.target.x;
      atby[i] += row[i] * p.target.y;
    }
  }

  const xCoeffs = solve3x3(ata, atbx);
  const yCoeffs = solve3x3(ata, atby);
  if (!xCoeffs || !yCoeffs) return null;

  return {
    ax: xCoeffs[0],
    bx: xCoeffs[1],
    cx: xCoeffs[2],
    ay: yCoeffs[0],
    by: yCoeffs[1],
    cy: yCoeffs[2]
  };
}

function getCalibrationTargets() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const marginX = Math.max(40, Math.round(w * 0.15));
  const marginY = Math.max(40, Math.round(h * 0.15));
  const left = marginX;
  const right = Math.max(marginX, w - marginX);
  const top = marginY;
  const bottom = Math.max(marginY, h - marginY);
  const centerX = Math.round(w / 2);
  const centerY = Math.round(h / 2);
  return [
    { x: left, y: top },
    { x: right, y: top },
    { x: centerX, y: centerY },
    { x: left, y: bottom },
    { x: right, y: bottom }
  ];
}

function ensureCalibrationTarget() {
  if (calibrationTarget && document.contains(calibrationTarget)) return;
  calibrationTarget = document.createElement("div");
  calibrationTarget.style.position = "fixed";
  calibrationTarget.style.width = "26px";
  calibrationTarget.style.height = "26px";
  calibrationTarget.style.borderRadius = "50%";
  calibrationTarget.style.border = "3px solid #00e5ff";
  calibrationTarget.style.background = "rgba(0, 229, 255, 0.25)";
  calibrationTarget.style.boxSizing = "border-box";
  calibrationTarget.style.pointerEvents = "none";
  calibrationTarget.style.zIndex = "2147483647";
  calibrationTarget.style.left = "0";
  calibrationTarget.style.top = "0";
  calibrationTarget.style.transform = "translate(-50%, -50%)";
  calibrationTarget.style.display = "none";
  calibrationTarget.style.margin = "0";
  calibrationTarget.style.padding = "0";
  document.documentElement.appendChild(calibrationTarget);
}

function moveCalibrationTarget(point) {
  ensureCalibrationTarget();
  calibrationTarget.style.display = "block";
  calibrationTarget.style.left = `${Math.round(point.x)}px`;
  calibrationTarget.style.top = `${Math.round(point.y)}px`;
}

function hideCalibrationTarget() {
  if (calibrationTarget) {
    calibrationTarget.style.display = "none";
  }
}

async function collectSamples(durationMs = 900) {
  const samples = [];
  const started = Date.now();
  while (Date.now() - started < durationMs) {
    if (latestRawViewportCoord) {
      samples.push({ ...latestRawViewportCoord });
    }
    await sleep(45);
  }
  if (samples.length < 6) return null;
  const avg = samples.reduce(
    (acc, s) => ({ x: acc.x + s.x, y: acc.y + s.y }),
    { x: 0, y: 0 }
  );
  return { x: avg.x / samples.length, y: avg.y / samples.length };
}

async function runCalibration() {
  if (calibrationInProgress) return { ok: false, error: "Calibration already running." };
  calibrationInProgress = true;

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;

  try {
    const targets = getCalibrationTargets();
    const pairs = [];

    setStatus("Shrimp: calibration start");
    await sleep(400);

    for (let i = 0; i < targets.length; i += 1) {
      const t = targets[i];
      moveCalibrationTarget(t);
      applySpotlight(t.x, t.y);
      setStatus(`Calibrating ${i + 1}/${targets.length}: stare at blue dot`);
      await sleep(500);
      const raw = await collectSamples(900);
      if (!raw) {
        setStatus("Calibration failed: no gaze samples.");
        return { ok: false, error: "No gaze samples from API." };
      }
      pairs.push({ raw, target: t });
    }

    hideCalibrationTarget();
    const affine = fitAffine(pairs);
    if (!affine) {
      setStatus("Calibration failed: cannot fit mapping.");
      return { ok: false, error: "Calibration matrix solve failed." };
    }

    calibration = { enabled: true, affine, updatedAt: Date.now() };
    chrome.storage.local.set({ calibration });
    setStatus("Shrimp: calibration saved");
    return { ok: true };
  } finally {
    hideCalibrationTarget();
    calibrationInProgress = false;
    startPolling();
  }
}

function resetCalibration() {
  calibration = null;
  chrome.storage.local.remove("calibration");
  setStatus("Shrimp: calibration reset");
}

async function fetchCoordinate() {
  if (!settings.apiUrl) {
    setStatus("Shrimp: set API URL in popup");
    applySpotlight(window.innerWidth / 2, window.innerHeight / 2);
    return;
  }

  try {
    const response = await fetch(settings.apiUrl, { cache: "no-store" });
    const text = await response.text();
    let coord = null;

    try {
      const jsonPayload = JSON.parse(text);
      coord = parseCoordinate(jsonPayload);
    } catch (_err) {
      coord = parseCoordinateFromText(text);
    }

    if (!coord || !Number.isFinite(coord.x) || !Number.isFinite(coord.y)) {
      const fallback = fallbackFocusPoint();
      setStatus("Shrimp: fallback mouse/center");
      applySpotlight(fallback.x, fallback.y);
      return;
    }

    const viewportRaw = toViewportCoordinate(coord);
    latestRawViewportCoord = viewportRaw;
    const viewportCoord = applyCalibration(viewportRaw);
    applySpotlight(viewportCoord.x, viewportCoord.y);
    setStatus(calibration?.enabled ? "Shrimp: tracking (calibrated)" : "Shrimp: tracking");
  } catch (_err) {
    const fallback = fallbackFocusPoint();
    setStatus("Shrimp: request failed, fallback");
    applySpotlight(fallback.x, fallback.y);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const interval = Math.max(30, Number(settings.pollMs) || 80);
  pollTimer = setInterval(() => {
    void fetchCoordinate();
  }, interval);
  void fetchCoordinate();
}

function loadSettingsAndStart() {
  chrome.storage.local.get({ ...SETTINGS_DEFAULTS, calibration: null }, (stored) => {
    settings = { ...SETTINGS_DEFAULTS, ...stored };
    calibration = stored.calibration;
    if (debugBox) {
      debugBox.style.display = settings.showDebugBox ? "block" : "none";
    }
    startPolling();
  });
}

window.addEventListener("mousemove", (event) => {
  lastMouseViewport = { x: event.clientX, y: event.clientY };
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  let changed = false;
  for (const [key, value] of Object.entries(changes)) {
    if (key === "calibration") {
      calibration = value.newValue ?? null;
      changed = true;
      continue;
    }
    if (key in settings) {
      settings[key] = value.newValue;
      changed = true;
    }
  }
  if (changed) startPolling();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "shrimp_start_calibration") {
    void runCalibration().then(sendResponse);
    return true;
  }
  if (message?.type === "shrimp_reset_calibration") {
    resetCalibration();
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

ensureOverlay();
loadSettingsAndStart();
