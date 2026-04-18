const API = {
  coordinate: "/coordinate?format=debug",
  calibration: "/calibration",
  calibrationReset: "/calibration/reset",
  screen: "/screen"
};

const POLL_MS = 70;
const WARMUP_MS = 280;
const SAMPLE_MS = 850;
const SAMPLE_INTERVAL_MS = 40;

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

function toViewportFromScreen(screenPoint) {
  return {
    x: screenPoint.x - window.screenX,
    y: screenPoint.y - window.screenY
  };
}

function screenTargetFromViewport(viewportPoint) {
  return {
    x: window.screenX + viewportPoint.x,
    y: window.screenY + viewportPoint.y
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
  const response = await fetch(API.screen, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ width, height })
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  dom.screenSize.textContent = `${payload.screen.width} x ${payload.screen.height}`;
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
    return { x: mx, y: my, count: points.length };
  }
  const fx = median(filtered.map((p) => p.x));
  const fy = median(filtered.map((p) => p.y));
  return { x: fx, y: fy, count: filtered.length };
}

async function collectRawSamples() {
  const points = [];
  const startedAt = Date.now();
  while (Date.now() - startedAt < SAMPLE_MS) {
    try {
      const payload = await fetchDebugCoordinate();
      const raw = payload.coordinate_raw;
      const confidence = Number(payload.confidence || 0);
      if (raw && Number.isFinite(raw.x) && Number.isFinite(raw.y) && confidence >= 0.55) {
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
      const robust = robustPoint(rawPoints);
      if (!robust || robust.count < 5) {
        throw new Error(`第 ${i + 1} 个点采样不足，请重试`);
      }

      allSamples.push({
        raw: { x: robust.x, y: robust.y },
        target: { x: screenTarget.x, y: screenTarget.y }
      });
    }

    const payload = await postCalibrationSamples(allSamples);
    const count = payload?.calibration?.sample_count ?? allSamples.length;
    setStatus(`校准完成，样本数: ${count}`, false);
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
      ? `已启用 (${state.sample_count || 0})`
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
    const viewport = clampViewport(toViewportFromScreen(mapped));

    dom.rawPoint.textContent = formatPoint(raw);
    dom.mappedPoint.textContent = formatPoint(mapped);
    dom.viewportPoint.textContent = formatPoint(viewport);
    dom.confidence.textContent = Number(payload.confidence || 0).toFixed(2);
    dom.calibrationState.textContent = payload?.calibration?.enabled
      ? `已启用 (${payload?.calibration?.sample_count || 0})`
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
}

void init();
