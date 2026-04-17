const stateEl = document.getElementById("state");
const serverUrlEl = document.getElementById("server-url");
const reconnectBtn = document.getElementById("reconnect-btn");

function refreshStatus() {
  chrome.runtime.sendMessage({ type: "get_status" }, (response) => {
    if (!response) return;
    stateEl.textContent = response.state ?? "unknown";
    if (response.url) serverUrlEl.textContent = response.url;
  });
}

reconnectBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "reconnect" }, () => {
    setTimeout(refreshStatus, 300);
  });
});

refreshStatus();
