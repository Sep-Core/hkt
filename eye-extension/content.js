const BOX_SIZE = 140;
const SETTINGS_DEFAULTS = {
  apiUrl: "http://127.0.0.1:3000/coordinate",
  coordinateBasis: "auto",
  pollMs: 80,
  spotlightRadius: 180,
  showDebugBox: true,
  showDebugPanel: false
};

let settings = { ...SETTINGS_DEFAULTS };
let overlay = null;
let debugBox = null;
let statusTag = null;
let debugPanel = null;
let pollTimer = null;
let calibrationTarget = null;
let calibrationInProgress = false;
let lastMouseViewport = null;
let backendCalibrationEnabled = false;

let debugState = {
  source: "init",
  rawApiCoord: null,
  viewportCoord: null,
  mappedCoord: null,
  fallback: "none",
  latencyMs: null
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fmtCoord(coord) {
  if (!coord) return "-";
  return `${Math.round(coord.x)}, ${Math.round(coord.y)}`;
}

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

  debugPanel = document.createElement("pre");
  debugPanel.style.position = "fixed";
  debugPanel.style.right = "12px";
  debugPanel.style.bottom = "12px";
  debugPanel.style.maxWidth = "380px";
  debugPanel.style.padding = "8px 10px";
  debugPanel.style.background = "rgba(0, 0, 0, 0.72)";
  debugPanel.style.color = "#9fffe0";
  debugPanel.style.font = "11px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
  debugPanel.style.whiteSpace = "pre";
  debugPanel.style.borderRadius = "6px";
  debugPanel.style.pointerEvents = "none";
  debugPanel.style.zIndex = "2147483647";
  debugPanel.style.display = settings.showDebugPanel ? "block" : "none";

  document.documentElement.appendChild(overlay);
  document.documentElement.appendChild(debugBox);
  document.documentElement.appendChild(statusTag);
  document.documentElement.appendChild(debugPanel);
}

function setStatus(text) {
  ensureOverlay();
  statusTag.textContent = text;
}

function updateDebugPanel() {
  ensureOverlay();
  debugPanel.style.display = settings.showDebugPanel ? "block" : "none";
  if (!settings.showDebugPanel) return;
  debugPanel.textContent =
    `Shrimp Debug\n` +
    `source: ${debugState.source}\n` +
    `basis: ${settings.coordinateBasis}\n` +
    `pollMs: ${settings.pollMs}\n` +
    `latencyMs: ${debugState.latencyMs ?? "-"}\n` +
    `fallback: ${debugState.fallback}\n` +
    `raw API: ${fmtCoord(debugState.rawApiCoord)}\n` +
    `viewport: ${fmtCoord(debugState.viewportCoord)}\n` +
    `mapped: ${fmtCoord(debugState.mappedCoord)}\n` +
    `calibration(backend): ${backendCalibrationEnabled ? "on" : "off"}`;
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
  debugState.mappedCoord = { x: cx, y: cy };
  updateDebugPanel();
}

function parseCoordinateFromText(text) {
  const match = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text);
  if (!match) return null;
  return { x: Number(match[1]), y: Number(match[2]) };
}

function parseCoordObject(payload) {
  if (Array.isArray(payload) && payload.length >= 2) return { x: Number(payload[0]), y: Number(payload[1]) };
  if (!payload || typeof payload !== "object") return null;
  if (payload.coordinate && typeof payload.coordinate.x === "number" && typeof payload.coordinate.y === "number") {
    return { x: payload.coordinate.x, y: payload.coordinate.y };
  }
  if (typeof payload.x === "number" && typeof payload.y === "number") {
    return { x: payload.x, y: payload.y };
  }
  return null;
}

function toViewportCoordinate(coord) {
  const basis = settings.coordinateBasis;
  if (basis === "viewport") return coord;
  if (basis === "document") return { x: coord.x - window.scrollX, y: coord.y - window.scrollY };
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

function buildControlUrl(pathname) {
  const parsed = new URL(settings.apiUrl);
  parsed.search = "";
  parsed.hash = "";
  parsed.pathname = pathname;
  return parsed.toString();
}

async function fetchApiCoordinateRaw() {
  const startedAt = performance.now();
  const response = await fetch(settings.apiUrl, { cache: "no-store" });
  const text = await response.text();

  let payload = null;
  let mappedCoord = null;
  let rawCoord = null;

  try {
    payload = JSON.parse(text);
    mappedCoord = parseCoordObject(payload);
    if (payload?.coordinate_raw) rawCoord = parseCoordObject(payload.coordinate_raw);
    if (!rawCoord) rawCoord = mappedCoord;
    if (typeof payload?.calibration?.enabled === "boolean") {
      backendCalibrationEnabled = payload.calibration.enabled;
    }
  } catch (_err) {
    mappedCoord = parseCoordinateFromText(text);
    rawCoord = mappedCoord;
  }

  if (!mappedCoord || !Number.isFinite(mappedCoord.x) || !Number.isFinite(mappedCoord.y)) {
    return { ok: false, latencyMs: Math.round(performance.now() - startedAt), error: "invalid-coordinate" };
  }

  const mappedViewport = toViewportCoordinate(mappedCoord);
  const rawViewport = rawCoord ? toViewportCoordinate(rawCoord) : mappedViewport;
  return {
    ok: true,
    rawApiCoord: rawCoord,
    mappedApiCoord: mappedCoord,
    rawViewport,
    mappedViewport,
    latencyMs: Math.round(performance.now() - startedAt)
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
  document.documentElement.appendChild(calibrationTarget);
}

function moveCalibrationTarget(point) {
  ensureCalibrationTarget();
  calibrationTarget.style.display = "block";
  calibrationTarget.style.left = `${Math.round(point.x)}px`;
  calibrationTarget.style.top = `${Math.round(point.y)}px`;
}

function hideCalibrationTarget() {
  if (calibrationTarget) calibrationTarget.style.display = "none";
}

async function collectSamples(durationMs = 900) {
  const samples = [];
  const started = Date.now();
  while (Date.now() - started < durationMs) {
    try {
      const result = await fetchApiCoordinateRaw();
      if (result.ok && result.rawViewport) {
        samples.push({ ...result.rawViewport });
        debugState = {
          ...debugState,
          source: "calibration",
          fallback: "none",
          rawApiCoord: result.rawApiCoord,
          viewportCoord: result.rawViewport,
          latencyMs: result.latencyMs
        };
        updateDebugPanel();
      }
    } catch (_err) {
      // ignore transient errors
    }
    await sleep(45);
  }
  if (samples.length < 6) return null;
  const total = samples.reduce((acc, cur) => ({ x: acc.x + cur.x, y: acc.y + cur.y }), { x: 0, y: 0 });
  return { x: total.x / samples.length, y: total.y / samples.length };
}

async function submitCalibrationSamples(samples) {
  const url = buildControlUrl("/calibration");
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ samples })
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body?.ok) {
    throw new Error(body?.error || `HTTP ${response.status}`);
  }
  backendCalibrationEnabled = Boolean(body?.calibration?.enabled);
}

async function resetCalibrationRemote() {
  const url = buildControlUrl("/calibration/reset");
  const response = await fetch(url, { method: "POST" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body?.ok) {
    throw new Error(body?.error || `HTTP ${response.status}`);
  }
  backendCalibrationEnabled = false;
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
      const target = targets[i];
      moveCalibrationTarget(target);
      applySpotlight(target.x, target.y);
      setStatus(`Calibrating ${i + 1}/${targets.length}: stare at blue dot`);
      await sleep(500);
      const raw = await collectSamples(900);
      if (!raw) return { ok: false, error: "No gaze samples from API." };
      pairs.push({ raw, target });
    }
    await submitCalibrationSamples(pairs);
    setStatus("Shrimp: calibration saved (backend)");
    return { ok: true };
  } catch (err) {
    setStatus("Calibration failed");
    return { ok: false, error: err?.message || "calibration failed" };
  } finally {
    hideCalibrationTarget();
    calibrationInProgress = false;
    startPolling();
  }
}

async function fetchCoordinate() {
  if (!settings.apiUrl) {
    setStatus("Shrimp: set API URL in popup");
    debugState = { ...debugState, source: "config", fallback: "center-no-url", rawApiCoord: null, viewportCoord: null, latencyMs: null };
    applySpotlight(window.innerWidth / 2, window.innerHeight / 2);
    return;
  }

  try {
    const result = await fetchApiCoordinateRaw();
    if (!result.ok) {
      const fallback = fallbackFocusPoint();
      setStatus("Shrimp: fallback mouse/center");
      debugState = {
        ...debugState,
        source: "api",
        fallback: lastMouseViewport ? "mouse" : "center",
        rawApiCoord: null,
        viewportCoord: null,
        latencyMs: result.latencyMs ?? null
      };
      applySpotlight(fallback.x, fallback.y);
      return;
    }

    debugState = {
      ...debugState,
      source: "api",
      fallback: "none",
      rawApiCoord: result.rawApiCoord,
      viewportCoord: result.rawViewport,
      latencyMs: result.latencyMs
    };
    applySpotlight(result.mappedViewport.x, result.mappedViewport.y);
    setStatus(backendCalibrationEnabled ? "Shrimp: tracking (backend calibrated)" : "Shrimp: tracking");
  } catch (_err) {
    const fallback = fallbackFocusPoint();
    setStatus("Shrimp: request failed, fallback");
    debugState = {
      ...debugState,
      source: "request-error",
      fallback: lastMouseViewport ? "mouse" : "center",
      rawApiCoord: null,
      viewportCoord: null,
      latencyMs: null
    };
    applySpotlight(fallback.x, fallback.y);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const interval = Math.max(30, Number(settings.pollMs) || 80);
  pollTimer = setInterval(() => void fetchCoordinate(), interval);
  void fetchCoordinate();
}

function loadSettingsAndStart() {
  chrome.storage.local.get(SETTINGS_DEFAULTS, (stored) => {
    settings = { ...SETTINGS_DEFAULTS, ...stored };
    if (debugBox) debugBox.style.display = settings.showDebugBox ? "block" : "none";
    updateDebugPanel();
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
    if (key in settings) {
      settings[key] = value.newValue;
      changed = true;
    }
  }
  if (changed) startPolling();
  updateDebugPanel();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "shrimp_start_calibration") {
    void runCalibration().then(sendResponse);
    return true;
  }
  if (message?.type === "shrimp_reset_calibration") {
    void resetCalibrationRemote()
      .then(() => {
        setStatus("Shrimp: calibration reset (backend)");
        updateDebugPanel();
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err?.message || "reset failed" }));
    return true;
  }
  return false;
});

ensureOverlay();
loadSettingsAndStart();
