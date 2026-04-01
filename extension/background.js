// Background service worker — coordinates popup, offscreen doc, and content script

let isRecording = false;
let recordingTabId = null;

// Ensure offscreen document exists
async function ensureOffscreen() {
  const existing = await chrome.offscreen.hasDocument();
  if (!existing) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['USER_MEDIA'],
      justification: 'Recording tab audio for meeting transcription'
    });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'get_status') {
    sendResponse({ recording: isRecording, tabId: recordingTabId });
    return true;
  }

  if (msg.type === 'start_capture') {
    // Popup captured the tab, now hand off to offscreen document
    handleStartCapture(msg).then(r => sendResponse(r));
    return true;
  }

  if (msg.type === 'stop_recording') {
    handleStopRecording().then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === 'recording_status') {
    isRecording = msg.recording;
    // Update badge
    chrome.action.setBadgeText({ text: isRecording ? 'REC' : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#ef4444' });
    return;
  }

  if (msg.type === 'recording_error') {
    console.error('Recording error:', msg.message);
    isRecording = false;
    chrome.action.setBadgeText({ text: '' });
    return;
  }

  if (msg.type === 'server_message') {
    // Forward transcript to content script
    if (msg.data && msg.data.type === 'meeting_transcript' && recordingTabId) {
      chrome.tabs.sendMessage(recordingTabId, {
        type: 'transcript',
        text: msg.data.text
      }).catch(() => {});
    }
    return;
  }
});

async function handleStartCapture(msg) {
  try {
    await ensureOffscreen();
    recordingTabId = msg.tabId;

    // Tell offscreen doc to start recording with the stream ID
    chrome.runtime.sendMessage({
      type: 'start_offscreen_recording',
      streamId: msg.streamId,
      wsUrl: msg.wsUrl,
      token: msg.token,
      tabId: msg.tabId,
    });

    isRecording = true;
    chrome.action.setBadgeText({ text: 'REC' });
    chrome.action.setBadgeBackgroundColor({ color: '#ef4444' });

    // Tell content script
    if (recordingTabId) {
      chrome.tabs.sendMessage(recordingTabId, { type: 'recording_started' }).catch(() => {});
    }

    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function handleStopRecording() {
  try {
    chrome.runtime.sendMessage({ type: 'stop_offscreen_recording' });
  } catch (e) {}
  isRecording = false;
  chrome.action.setBadgeText({ text: '' });
  if (recordingTabId) {
    chrome.tabs.sendMessage(recordingTabId, { type: 'recording_stopped' }).catch(() => {});
    recordingTabId = null;
  }
}
