const ENDPOINT = "http://127.0.0.1:8765/result";

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message || message.type !== "qa-result") {
    return;
  }
  const tabId = sender.tab && sender.tab.id;
  fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: message.url,
      status: message.status,
      reason: message.reason,
    }),
  })
    .catch(() => {})
    .finally(() => {
      setTimeout(() => {
        if (typeof tabId === "number") {
          chrome.tabs.remove(tabId).catch(() => {});
        }
      }, 1800);
    });
});
