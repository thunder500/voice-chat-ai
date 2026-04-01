// Injector — runs in ISOLATED world at document_start
// 1. Injects content.js into MAIN world (for RTCPeerConnection patching)
// 2. Adds a floating control panel to the Meet page
// 3. The control panel handles recording (stays alive — no popup needed)

// Inject content.js into MAIN world
const script = document.createElement('script');
script.src = chrome.runtime.getURL('content.js');
script.onload = () => script.remove();
(document.head || document.documentElement).appendChild(script);

// Bridge messages between MAIN world and chrome.runtime
window.addEventListener('message', (event) => {
  if (event.source !== window || !event.data || event.data.source !== 'vcai-content') return;
  chrome.runtime.sendMessage(event.data).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  window.postMessage({ source: 'vcai-injector', ...msg }, '*');
  sendResponse({ ok: true });
  return true;
});

// Wait for page to load, then inject the control panel
window.addEventListener('load', () => {
  setTimeout(injectControlPanel, 3000);
});

function injectControlPanel() {
  // Load saved settings
  chrome.storage.local.get(['serverUrl', 'authToken'], (data) => {
    const serverUrl = data.serverUrl || 'http://localhost:8000';
    const token = data.authToken || '';

    // Create floating record button on the Meet page
    const panel = document.createElement('div');
    panel.id = 'vcai-panel';
    panel.style.cssText = 'position:fixed;bottom:20px;left:20px;z-index:99999;font-family:sans-serif;';

    // Record button (always visible)
    const btn = document.createElement('button');
    btn.id = 'vcai-record-btn';
    btn.textContent = token ? '🎙 Record Meeting' : '🔑 Set Token First';
    btn.style.cssText = 'background:#8b5cf6;color:white;border:none;padding:10px 20px;border-radius:24px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(139,92,246,0.4);display:flex;align-items:center;gap:6px;';
    btn.disabled = !token;
    if (!token) btn.style.background = '#666';

    let isRecording = false;

    btn.addEventListener('click', () => {
      if (!token) {
        // Open extension popup for token
        alert('Open the Voice Chat AI extension (puzzle icon in toolbar) and paste your auth token first.');
        return;
      }

      if (!isRecording) {
        // Start recording via content script
        const wsUrl = serverUrl.replace(/^http/, 'ws') + '/ws-meeting';
        window.postMessage({
          source: 'vcai-injector',
          type: 'content_start_recording',
          wsUrl: wsUrl,
          token: token
        }, '*');
        btn.textContent = '⏹ Stop Recording';
        btn.style.background = '#ef4444';
        isRecording = true;
      } else {
        // Stop recording
        window.postMessage({
          source: 'vcai-injector',
          type: 'content_stop_recording'
        }, '*');
        btn.textContent = '🎙 Record Meeting';
        btn.style.background = '#8b5cf6';
        isRecording = false;
      }
    });

    panel.appendChild(btn);
    document.body.appendChild(panel);

    // Listen for recording status from content.js
    window.addEventListener('message', (event) => {
      if (event.source !== window || !event.data || event.data.source !== 'vcai-content') return;
      if (event.data.type === 'recording_status') {
        isRecording = event.data.recording;
        if (isRecording) {
          btn.textContent = '⏹ Stop Recording';
          btn.style.background = '#ef4444';
        } else {
          btn.textContent = '🎙 Record Meeting';
          btn.style.background = '#8b5cf6';
        }
      }
    });
  });
}
