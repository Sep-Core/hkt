const API = {
  coordinate: "/coordinate?format=debug",
  calibration: "/calibration",
  calibrationReset: "/calibration/reset",
  screen: "/screen"
};

const POLL_MS = 70;
const SCREEN_RESYNC_MS = 2400;
const WARMUP_MS = 320;
const SAMPLE_MS = 1100;
const SAMPLE_INTERVAL_MS = 35;

const dom = {
  syncScreenBtn: document.getElementById("sync-screen-btn"),
  startCalibrationBtn: document.getElementById("start-calibration-btn"),
  resetCalibrationBtn: document.getElementById("reset-calibration-btn"),
  screenSize: document.getElementById("screen-size"),
  rawPoint: document.getElementById("raw-point"),
  mappedPoint: document.getElementById("mapped-point"),
  viewportPoint: document.getElementById("viewport-point"),
  confidence: document.getElementById("confidence"),
  calibrationState: document.getElementById("calibration-state"),
  status: document.getElementById("status"),
  gazeDot: document.getElementById("gaze-dot"),
  calibrationDot: document.getElementById("calibration-dot")
};

let inCalibration = false;
let viewportOrigin = { x: 0, y: 0 };

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatPoint(point) {
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    return "-";
  }
  return `${Math.round(point.x)}, ${Math.round(point.y)}`;
}

function setStatus(message, isError = false) {
  dom.status.textContent = message;
  dom.status.style.color = isError ? "#ff907f" : "#6df6ad";
}

function estimateViewportOrigin() {
  const borderX = Math.max(0, Math.round((window.outerWidth - window.innerWidth) / 2));
  const borderYRaw = window.outerHeight - window.innerHeight - borderX;
  const borderY = Math.max(0, Math.round(borderYRaw));
  return {
    x: Math.round(window.screenX + borderX),
    y: Math.round(window.screenY + borderY)
  };
}

function toViewportFromScreen(screenPoint) {
  return {
    x: screenPoint.x - viewportOrigin.x,
    y: screenPoint.y - viewportOrigin.y
  };
}

function screenTargetFromViewport(viewportPoint) {
  return {
    x: viewportOrigin.x + viewportPoint.x,
    y: viewportOrigin.y + viewportPoint.y
  };
}

function clampViewport(point) {
  return {
    x: Math.max(0, Math.min(window.innerWidth, point.x)),
    y: Math.max(0, Math.min(window.innerHeight, point.y))
  };
}

async function fetchDebugCoordinate() {
  const response = await fetch(API.coordinate, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const payload = await response.json();
  if (!payload?.ok) {
    throw new Error(payload?.error || "invalid payload");
  }
  return payload;
}

async function syncScreenSize() {
  const width = Number(window.screen.width);
  const height = Number(window.screen.height);
  const estimated = estimateViewportOrigin();
  const response = await fetch(API.screen, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      width,
      height,
      viewport_origin_x: estimated.x,
      viewport_origin_y: estimated.y
    })
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  viewportOrigin = {
    x: Number(payload.screen.viewport_origin_x ?? estimated.x),
    y: Number(payload.screen.viewport_origin_y ?? estimated.y)
  };
  dom.screenSize.textContent = `${payload.screen.width} x ${payload.screen.height} @ (${viewportOrigin.x}, ${viewportOrigin.y})`;
}

function getCalibrationTargets() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const marginX = Math.max(60, Math.round(w * 0.14));
  const marginY = Math.max(60, Math.round(h * 0.16));
  const xs = [marginX, Math.round(w / 2), w - marginX];
  const ys = [marginY, Math.round(h / 2), h - marginY];
  const targets = [];
  for (const y of ys) {
    for (const x of xs) {
      targets.push({ x, y });
    }
  }
  return targets;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid];
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

function robustPoint(points) {
  if (!points.length) return null;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const mx = median(xs);
  const my = median(ys);
  if (!Number.isFinite(mx) || !Number.isFinite(my)) return null;

  const dist = points.map((p) => Math.hypot(p.x - mx, p.y - my));
  const md = median(dist) || 0;
  const threshold = Math.max(20, md * 2.4);
  const filtered = points.filter((p) => Math.hypot(p.x - mx, p.y - my) <= threshold);
  if (filtered.length < 5) {
    return { x: mx, y: my, count: points.length, spread: md };
  }
  const fx = median(filtered.map((p) => p.x));
  const fy = median(filtered.map((p) => p.y));
  const spread = median(filtered.map((p) => Math.hypot(p.x - fx, p.y - fy))) || 0;
  return { x: fx, y: fy, count: filtered.length, spread };
}

function selectMostStableWindow(points) {
  if (points.length < 10) return points;

  const win = Math.min(18, Math.max(8, Math.floor(points.length * 0.55)));
  let bestSlice = points;
  let bestSpread = Number.POSITIVE_INFINITY;

  for (let i = 0; i <= points.length - win; i += 1) {
    const slice = points.slice(i, i + win);
    const center = robustPoint(slice);
    if (!center) continue;
    if (center.spread < bestSpread) {
      bestSpread = center.spread;
      bestSlice = slice;
    }
  }

  return bestSlice;
}

async function collectRawSamples() {
  const points = [];
  const startedAt = Date.now();
  while (Date.now() - startedAt < SAMPLE_MS) {
    try {
      const payload = await fetchDebugCoordinate();
      const raw = payload.coordinate_raw;
      const confidence = Number(payload.confidence || 0);
      if (raw && Number.isFinite(raw.x) && Number.isFinite(raw.y) && confidence >= 0.52) {
        points.push({ x: raw.x, y: raw.y });
      }
    } catch (_err) {
      // Ignore transient fetch errors during sampling.
    }
    await sleep(SAMPLE_INTERVAL_MS);
  }
  return points;
}

async function postCalibrationSamples(samples) {
  const response = await fetch(API.calibration, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ samples })
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function resetCalibration() {
  const response = await fetch(API.calibrationReset, { method: "POST" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload;
}

function lockControls(locked) {
  dom.syncScreenBtn.disabled = locked;
  dom.startCalibrationBtn.disabled = locked;
  dom.resetCalibrationBtn.disabled = locked;
}

function showCalibrationDot(point) {
  dom.calibrationDot.style.display = "block";
  dom.calibrationDot.style.left = `${Math.round(point.x)}px`;
  dom.calibrationDot.style.top = `${Math.round(point.y)}px`;
}

function hideCalibrationDot() {
  dom.calibrationDot.style.display = "none";
}

async function runCalibration() {
  if (inCalibration) return;
  inCalibration = true;
  lockControls(true);

  try {
    await syncScreenSize();
    const targets = getCalibrationTargets();
    const allSamples = [];

    setStatus("校准开始，请盯住蓝点。", false);

    for (let i = 0; i < targets.length; i += 1) {
      const viewportTarget = targets[i];
      const screenTarget = screenTargetFromViewport(viewportTarget);
      showCalibrationDot(viewportTarget);

      setStatus(`正在采样 ${i + 1}/${targets.length}...`, false);
      await sleep(WARMUP_MS);
      const rawPoints = await collectRawSamples();
      const stableWindow = selectMostStableWindow(rawPoints);
      const robust = robustPoint(stableWindow);
      if (!robust || robust.count < 6 || robust.spread > 42) {
        throw new Error(`第 ${i + 1} 个点采样不足，请重试`);
      }

      allSamples.push({
        raw: { x: robust.x, y: robust.y },
        target: { x: screenTarget.x, y: screenTarget.y }
      });
    }

    const payload = await postCalibrationSamples(allSamples);
    const count = payload?.calibration?.sample_count ?? allSamples.length;
    const modelType = payload?.calibration?.model_type || "unknown";
    setStatus(`校准完成，样本数: ${count}，模型: ${modelType}`, false);
  } catch (err) {
    setStatus(`校准失败: ${err.message || String(err)}`, true);
  } finally {
    hideCalibrationDot();
    lockControls(false);
    inCalibration = false;
  }
}

async function refreshCalibrationStatus() {
  try {
    const response = await fetch(API.calibration, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload?.ok) {
      return;
    }
    const state = payload.calibration;
    dom.calibrationState.textContent = state.enabled
      ? `已启用 ${state.model_type || "-"} (${state.sample_count || 0})`
      : "未启用";
  } catch (_err) {
    // Ignore status refresh errors.
  }
}

async function pollTracking() {
  if (inCalibration) return;

  try {
    const payload = await fetchDebugCoordinate();
    const raw = payload.coordinate_raw;
    const mapped = payload.coordinate_mapped || payload.coordinate;
    if (payload?.server) {
      const sx = Number(payload.server.viewport_origin_x);
      const sy = Number(payload.server.viewport_origin_y);
      if (Number.isFinite(sx) && Number.isFinite(sy)) {
        viewportOrigin = { x: sx, y: sy };
      }
    }
    const viewport = clampViewport(toViewportFromScreen(mapped));

    dom.rawPoint.textContent = formatPoint(raw);
    dom.mappedPoint.textContent = formatPoint(mapped);
    dom.viewportPoint.textContent = formatPoint(viewport);
    dom.confidence.textContent = Number(payload.confidence || 0).toFixed(2);
    dom.calibrationState.textContent = payload?.calibration?.enabled
      ? `已启用 ${payload?.calibration?.model_type || "-"} (${payload?.calibration?.sample_count || 0})`
      : "未启用";

    dom.gazeDot.style.left = `${Math.round(viewport.x)}px`;
    dom.gazeDot.style.top = `${Math.round(viewport.y)}px`;
  } catch (err) {
    setStatus(`追踪中断: ${err.message || String(err)}`, true);
  }
}

async function init() {
  dom.syncScreenBtn.addEventListener("click", async () => {
    try {
      await syncScreenSize();
      await refreshCalibrationStatus();
      setStatus("屏幕尺寸已同步", false);
    } catch (err) {
      setStatus(`同步失败: ${err.message || String(err)}`, true);
    }
  });

  dom.startCalibrationBtn.addEventListener("click", () => {
    void runCalibration();
  });

  dom.resetCalibrationBtn.addEventListener("click", async () => {
    try {
      await resetCalibration();
      await refreshCalibrationStatus();
      setStatus("校准已重置", false);
    } catch (err) {
      setStatus(`重置失败: ${err.message || String(err)}`, true);
    }
  });

  try {
    await syncScreenSize();
    await refreshCalibrationStatus();
    setStatus("已连接后端，开始追踪", false);
  } catch (err) {
    setStatus(`初始化失败: ${err.message || String(err)}`, true);
  }

  setInterval(() => {
    void pollTracking();
  }, POLL_MS);

  setInterval(() => {
    if (!inCalibration) {
      void syncScreenSize().catch(() => {});
    }
  }, SCREEN_RESYNC_MS);
}

void init();
