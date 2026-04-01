// Runs in ISOLATED world — CSP safe, has chrome.* APIs
// Adds a Record button to Google Meet and handles recording

console.log('[VoiceChatAI] Extension loaded on Meet page');

let ws = null;
let recording = false;
let mediaRecorder = null;
let stream = null;
let savedServerUrl = 'http://localhost:8000';
let savedToken = '';

// Load settings immediately (chrome.storage works at top level in content scripts)
chrome.storage.local.get(['serverUrl', 'authToken'], (data) => {
  savedServerUrl = data.serverUrl || 'http://localhost:8000';
  savedToken = data.authToken || '';
  console.log('[VoiceChatAI] Token loaded:', savedToken ? 'yes' : 'no');
});

// Wait for page to load then add our button
if (document.readyState === 'complete') {
  setTimeout(addRecordButton, 2000);
} else {
  window.addEventListener('load', () => setTimeout(addRecordButton, 2000));
}

function addRecordButton() {
  if (document.getElementById('vcai-panel')) return;

  const panel = document.createElement('div');
  panel.id = 'vcai-panel';
  panel.style.cssText = 'position:fixed;bottom:20px;left:20px;z-index:99999;font-family:-apple-system,sans-serif';

  const btn = document.createElement('button');
  btn.id = 'vcai-btn';
  btn.style.cssText = 'background:#8b5cf6;color:white;border:none;padding:10px 20px;border-radius:24px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(139,92,246,0.4);display:flex;align-items:center;gap:6px';
  btn.textContent = '\u{1F399} Record Meeting';

  btn.addEventListener('click', toggleRecording);
  panel.appendChild(btn);

  // Status text
  const status = document.createElement('div');
  status.id = 'vcai-status';
  status.style.cssText = 'color:white;font-size:11px;margin-top:6px;background:rgba(0,0,0,0.8);padding:4px 10px;border-radius:8px;display:none';
  panel.appendChild(status);

  document.body.appendChild(panel);
  console.log('[VoiceChatAI] Record button added');
}

async function toggleRecording() {
  if (recording) {
    stopRecording();
  } else {
    startRecording();
  }
}

async function startRecording() {
  const serverUrl = savedServerUrl;
  const token = savedToken;

  if (!token) {
    showStatus('Open the extension popup and paste your auth token first!', true);
    return;
  }

  showStatus('Capturing audio...');
  updateButton(true);

  // Get mic audio directly (content script can do getUserMedia)
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    console.log('[VoiceChatAI] Mic captured');
  } catch(e) {
    showStatus('Mic access denied: ' + e.message, true);
    updateButton(false);
    return;
  }

  // Also request tab capture via background script
  try {
    const tabStream = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: 'do_tab_capture' }, (resp) => {
        if (chrome.runtime.lastError) {
          console.log('[VoiceChatAI] Tab capture not available, mic only');
          resolve(null);
          return;
        }
        if (resp && resp.streamId) {
          // Got a stream ID from tabCapture.getMediaStreamId
          navigator.mediaDevices.getUserMedia({
            audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: resp.streamId } }
          }).then(resolve).catch(() => resolve(null));
        } else {
          resolve(null);
        }
      });
    });
    if (tabStream) {
      tabStream.getAudioTracks().forEach(t => stream.addTrack(t));
      console.log('[VoiceChatAI] Tab audio added');
    }
  } catch(e) {
    console.log('[VoiceChatAI] Tab capture skipped:', e.message);
  }

  showStatus('Connecting to server...');

  // Connect WebSocket
  const wsUrl = serverUrl.replace(/^http/, 'ws') + '/ws-meeting?token=' + token;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[VoiceChatAI] Server connected');
    ws.send(JSON.stringify({ type: 'meeting_start' }));
    recording = true;
    showStatus('Recording... (audio captured)');
    recordChunk();
  };

  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'meeting_transcript') {
        showTranscript(data.text);
        showStatus('Transcript #' + (data.chunk_index + 1));
      } else if (data.type === 'meeting_error') {
        showStatus('Error: ' + data.message, true);
      }
    } catch(err) {}
  };

  ws.onerror = () => {
    showStatus('Connection error', true);
    stopRecording();
  };

  ws.onclose = () => {
    if (recording) stopRecording();
  };
}

function recordChunk() {
  if (!recording || !ws || ws.readyState !== 1 || !stream) return;

  const rec = new MediaRecorder(stream, {
    mimeType: 'audio/webm;codecs=opus',
    audioBitsPerSecond: 128000
  });
  const parts = [];

  rec.ondataavailable = (e) => { if (e.data.size > 0) parts.push(e.data); };

  rec.onstop = () => {
    if (parts.length > 0 && ws && ws.readyState === 1) {
      const blob = new Blob(parts, { type: 'audio/webm' });
      console.log('[VoiceChatAI] Chunk:', Math.round(blob.size / 1024), 'KB');
      blob.arrayBuffer().then(buf => ws.send(buf));
    }
    if (recording) recordChunk();
  };

  rec.start();
  setTimeout(() => { if (rec.state !== 'inactive') rec.stop(); }, 10000);
}

function stopRecording() {
  recording = false;
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'meeting_stop' }));
    setTimeout(() => { if (ws) ws.close(); }, 2000);
  }
  if (stream) {
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  updateButton(false);
  showStatus('Stopped. Check Voice Chat AI for results.');
  setTimeout(() => hideStatus(), 5000);
}

function updateButton(isRecording) {
  const btn = document.getElementById('vcai-btn');
  if (!btn) return;
  if (isRecording) {
    btn.textContent = '\u23F9 Stop Recording';
    btn.style.background = '#ef4444';
  } else {
    btn.textContent = '\u{1F399} Record Meeting';
    btn.style.background = '#8b5cf6';
  }
}

function showStatus(text, isError) {
  const el = document.getElementById('vcai-status');
  if (!el) return;
  el.textContent = text;
  el.style.display = 'block';
  el.style.color = isError ? '#ef4444' : '#4ade80';
}

function hideStatus() {
  const el = document.getElementById('vcai-status');
  if (el) el.style.display = 'none';
}

// Transcript overlay
let transcriptEl = null;
function showTranscript(text) {
  if (!transcriptEl) {
    transcriptEl = document.createElement('div');
    transcriptEl.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:99999;width:600px;max-height:150px;overflow-y:auto;background:rgba(0,0,0,0.92);color:white;padding:12px 16px;border-radius:12px;font-family:sans-serif;font-size:14px;line-height:1.5;box-shadow:0 4px 20px rgba(0,0,0,0.4)';
    document.body.appendChild(transcriptEl);
  }
  const p = document.createElement('p');
  p.textContent = text;
  p.style.cssText = 'margin:0 0 6px;padding:0 0 6px;border-bottom:1px solid rgba(255,255,255,0.1)';
  transcriptEl.appendChild(p);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  while (transcriptEl.children.length > 10) transcriptEl.removeChild(transcriptEl.firstChild);
}
