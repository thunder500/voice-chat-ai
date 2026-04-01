// Content script — runs inside Google Meet tab
// Shows a recording indicator and live transcript overlay

let transcriptOverlay = null;
let indicatorEl = null;

// Listen for messages from background script
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'transcript') {
    showTranscript(msg.text);
  } else if (msg.type === 'recording_started') {
    showRecordingIndicator();
  } else if (msg.type === 'recording_stopped') {
    hideRecordingIndicator();
  }
});

function showRecordingIndicator() {
  if (indicatorEl) return;
  indicatorEl = document.createElement('div');
  indicatorEl.id = 'vcai-indicator';
  indicatorEl.innerHTML = `
    <div style="position:fixed;top:10px;right:10px;z-index:99999;display:flex;align-items:center;gap:8px;
      background:rgba(0,0,0,0.8);color:white;padding:8px 16px;border-radius:20px;font-family:sans-serif;font-size:13px;
      box-shadow:0 4px 12px rgba(0,0,0,0.3)">
      <span style="width:10px;height:10px;border-radius:50%;background:#ef4444;animation:vcai-pulse 1s infinite"></span>
      <span>AI Recording</span>
    </div>
    <style>@keyframes vcai-pulse{0%,100%{opacity:1}50%{opacity:.3}}</style>
  `;
  document.body.appendChild(indicatorEl);

  // Create transcript overlay
  if (!transcriptOverlay) {
    transcriptOverlay = document.createElement('div');
    transcriptOverlay.id = 'vcai-transcript';
    transcriptOverlay.style.cssText = `
      position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:99999;
      width:600px;max-height:150px;overflow-y:auto;
      background:rgba(0,0,0,0.85);color:white;padding:12px 16px;border-radius:12px;
      font-family:sans-serif;font-size:14px;line-height:1.5;
      box-shadow:0 4px 20px rgba(0,0,0,0.4);
      display:none;
    `;
    document.body.appendChild(transcriptOverlay);
  }
}

function hideRecordingIndicator() {
  if (indicatorEl) { indicatorEl.remove(); indicatorEl = null; }
  if (transcriptOverlay) { transcriptOverlay.remove(); transcriptOverlay = null; }
}

function showTranscript(text) {
  if (!transcriptOverlay) return;
  transcriptOverlay.style.display = 'block';
  const p = document.createElement('p');
  p.textContent = text;
  p.style.margin = '0 0 6px 0';
  p.style.padding = '0 0 6px 0';
  p.style.borderBottom = '1px solid rgba(255,255,255,0.1)';
  transcriptOverlay.appendChild(p);
  transcriptOverlay.scrollTop = transcriptOverlay.scrollHeight;

  // Keep only last 10 lines
  while (transcriptOverlay.children.length > 10) {
    transcriptOverlay.removeChild(transcriptOverlay.firstChild);
  }
}

// Notify the popup that we're on a Meet page
chrome.runtime.sendMessage({ type: 'on_meet_page' });
