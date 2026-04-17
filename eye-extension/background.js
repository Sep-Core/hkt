const WS_URL = "ws://127.0.0.1:8765";
let socket = null;
let reconnectTimer = null;
let latestState = "disconnected";

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

function broadcastMessage(message) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (!tab.id) continue;
      chrome.tabs.sendMessage(tab.id, message, () => {
        void chrome.runtime.lastError;
      });
    }
  });
}

function updateState(state) {
  latestState = state;
  broadcastMessage({ type: "gaze_state", state });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, 1500);
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  updateState("connecting");
  setBadge("...", "#9E9E9E");

  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    updateState("connected");
    setBadge("ON", "#D32F2F");
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (typeof data.x !== "number" || typeof data.y !== "number") return;
      broadcastMessage({
        type: "gaze",
        x: Math.max(0, Math.min(1, data.x)),
        y: Math.max(0, Math.min(1, data.y)),
        confidence: typeof data.confidence === "number" ? data.confidence : 0
      });
    } catch (_err) {
      // Ignore malformed payloads.
    }
  };

  socket.onerror = () => {
    // onclose handles reconnect.
  };

  socket.onclose = () => {
    updateState("disconnected");
    setBadge("OFF", "#616161");
    scheduleReconnect();
  };
}

chrome.runtime.onInstalled.addListener(() => {
  setBadge("OFF", "#616161");
  connect();
});

chrome.runtime.onStartup.addListener(() => {
  connect();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "get_status") {
    sendResponse({ state: latestState, url: WS_URL });
    return true;
  }
  if (message?.type === "reconnect") {
    if (socket) {
      try {
        socket.close();
      } catch (_err) {
        // ignore close errors
      }
    }
    connect();
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

connect();
