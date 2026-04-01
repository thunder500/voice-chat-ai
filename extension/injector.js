// Injector — runs in ISOLATED world, injects content.js into MAIN world
// and bridges messages between MAIN world and chrome.runtime

// Inject the main content script into the page's world
const script = document.createElement('script');
script.src = chrome.runtime.getURL('content.js');
script.onload = () => script.remove();
(document.head || document.documentElement).appendChild(script);

// Bridge: listen for messages from content.js (MAIN world) via window.postMessage
window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  if (!event.data || event.data.source !== 'vcai-content') return;

  const msg = event.data;

  if (msg.type === 'recording_status') {
    chrome.runtime.sendMessage({ type: 'recording_status', recording: msg.recording });
  } else if (msg.type === 'transcript_update') {
    chrome.runtime.sendMessage({ type: 'transcript_update', text: msg.text, index: msg.index });
  } else if (msg.type === 'audio_track_available') {
    chrome.runtime.sendMessage({ type: 'audio_track_available', count: msg.count });
  }
});

// Bridge: forward commands from popup/background to content.js
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'content_start_recording' || msg.type === 'content_stop_recording' || msg.type === 'content_get_status') {
    window.postMessage({ source: 'vcai-injector', ...msg }, '*');
    sendResponse({ ok: true });
  }
  return true;
});
