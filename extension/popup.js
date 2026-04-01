document.getElementById('startBtn').addEventListener('click', startRecording);
document.getElementById('stopBtn').addEventListener('click', stopRecording);

// Load saved settings
chrome.storage.local.get(['serverUrl', 'authToken'], function(data) {
  if (data.serverUrl) document.getElementById('serverUrl').value = data.serverUrl;
  if (data.authToken) document.getElementById('authToken').value = data.authToken;
});

// Check if already recording
chrome.runtime.sendMessage({ type: 'get_status' }, function(resp) {
  if (chrome.runtime.lastError) return;
  if (resp && resp.recording) showRecordingUI();
});

function showMsg(text, isError) {
  var el = document.getElementById('msgArea');
  el.textContent = text;
  el.className = 'msg ' + (isError ? 'error' : 'ok');
}

async function startRecording() {
  var serverUrl = document.getElementById('serverUrl').value.trim();
  var token = document.getElementById('authToken').value.trim();

  if (!token) {
    showMsg('Paste your auth token first!', true);
    return;
  }

  // Refresh the token first (it might be expired)
  try {
    var refreshResp = await fetch(serverUrl + '/api/auth/refresh', { method: 'POST', credentials: 'include' });
    if (refreshResp.ok) {
      var refreshData = await refreshResp.json();
      token = refreshData.access_token;
      document.getElementById('authToken').value = token;
    }
  } catch(e) { /* ignore, use existing token */ }

  chrome.storage.local.set({ serverUrl: serverUrl, authToken: token });
  document.getElementById('startBtn').disabled = true;
  showMsg('Capturing tab audio...', false);

  try {
    // Get active tab
    var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    var tab = tabs[0];
    if (!tab) {
      showMsg('No active tab', true);
      document.getElementById('startBtn').disabled = false;
      return;
    }

    // Capture tab audio — this MUST happen in the popup (user gesture)
    chrome.tabCapture.capture({ audio: true, video: false }, function(stream) {
      if (chrome.runtime.lastError || !stream) {
        showMsg('Capture failed: ' + (chrome.runtime.lastError ? chrome.runtime.lastError.message : 'no stream'), true);
        document.getElementById('startBtn').disabled = false;
        return;
      }

      showMsg('Audio captured! Starting recording...', false);

      // Get the stream ID to pass to offscreen
      var audioTrack = stream.getAudioTracks()[0];
      var streamId = audioTrack ? audioTrack.id : '';

      // Store the stream in a global so it stays alive even if popup would close
      // But actually in MV3 popup closing kills the stream...
      // So we record DIRECTLY in the popup and keep it alive via port

      // Actually: the simplest reliable approach for MV3 is to record here
      // and keep the popup open by NOT closing it (user keeps it open)
      // OR use the offscreen document approach

      // Let's try: pass streamId to background which creates offscreen doc
      var wsUrl = serverUrl.replace(/^http/, 'ws') + '/ws-meeting';

      // Unfortunately tabCapture streams can't be transferred to offscreen docs in MV3
      // So we record right here in the popup and warn user to keep it open
      startRecordingHere(stream, wsUrl, token, tab.id);
    });

  } catch(e) {
    showMsg('Error: ' + e.message, true);
    document.getElementById('startBtn').disabled = false;
  }
}

var ws = null;
var recording = false;

function startRecordingHere(stream, wsUrl, token, tabId) {
  ws = new WebSocket(wsUrl + '?token=' + token);

  ws.onopen = function() {
    showMsg('Connected! Recording... (keep this popup open)', false);
    ws.send(JSON.stringify({ type: 'meeting_start' }));
    recording = true;
    showRecordingUI();
    doRecordChunk(stream);

    // Tell background we're recording (for badge)
    chrome.runtime.sendMessage({ type: 'recording_status', recording: true });

    // Tell content script to show overlay
    chrome.tabs.sendMessage(tabId, { type: 'recording_started' }).catch(function(){});
  };

  ws.onmessage = function(e) {
    try {
      var data = JSON.parse(e.data);
      if (data.type === 'meeting_transcript') {
        showMsg('Chunk #' + (data.chunk_index + 1) + ': ' + data.text.substring(0, 80), false);
        chrome.tabs.sendMessage(tabId, { type: 'transcript', text: data.text }).catch(function(){});
      } else if (data.type === 'meeting_error') {
        showMsg('Error: ' + data.message, true);
      } else if (data.type === 'meeting_stopped') {
        showMsg('Meeting saved! ' + data.duration + 's', false);
      }
    } catch(err) {}
  };

  ws.onerror = function() { showMsg('Connection error', true); };
  ws.onclose = function() {
    recording = false;
    stream.getTracks().forEach(function(t) { t.stop(); });
    chrome.runtime.sendMessage({ type: 'recording_status', recording: false });
    showStoppedUI();
  };

  // Save tabId
  chrome.storage.local.set({ recordingTabId: tabId });
}

function doRecordChunk(stream) {
  if (!recording || !ws || ws.readyState !== 1) return;

  var rec = new MediaRecorder(stream, {
    mimeType: 'audio/webm;codecs=opus',
    audioBitsPerSecond: 128000
  });
  var parts = [];

  rec.ondataavailable = function(e) {
    if (e.data.size > 0) parts.push(e.data);
  };

  rec.onstop = function() {
    if (parts.length > 0 && ws && ws.readyState === 1) {
      var blob = new Blob(parts, { type: 'audio/webm' });
      showMsg('Sending ' + Math.round(blob.size / 1024) + 'KB...', false);
      blob.arrayBuffer().then(function(buf) { ws.send(buf); });
    }
    if (recording) doRecordChunk(stream);
  };

  rec.start();
  setTimeout(function() {
    if (rec.state !== 'inactive') rec.stop();
  }, 10000);
}

function stopRecording() {
  recording = false;
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'meeting_stop' }));
    // Don't close WS yet — wait for server response
    setTimeout(function() { if (ws) ws.close(); }, 3000);
  }
  chrome.runtime.sendMessage({ type: 'recording_status', recording: false });
  chrome.storage.local.get(['recordingTabId'], function(data) {
    if (data.recordingTabId) {
      chrome.tabs.sendMessage(data.recordingTabId, { type: 'recording_stopped' }).catch(function(){});
    }
  });
  showStoppedUI();
  showMsg('Stopped. Check Voice Chat AI for transcript.', false);
}

function showRecordingUI() {
  document.getElementById('statusDot').className = 'dot rec';
  document.getElementById('statusText').textContent = 'Recording... (keep popup open)';
  document.getElementById('startBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display = 'block';
  document.getElementById('configSection').style.display = 'none';
}

function showStoppedUI() {
  document.getElementById('statusDot').className = 'dot off';
  document.getElementById('statusText').textContent = 'Not recording';
  document.getElementById('startBtn').style.display = 'block';
  document.getElementById('startBtn').disabled = false;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('configSection').style.display = 'block';
}

// Pop out to its own window so it stays alive
document.getElementById('popoutBtn').addEventListener('click', function() {
  chrome.windows.create({
    url: chrome.runtime.getURL('popup.html'),
    type: 'popup',
    width: 360,
    height: 500,
    top: 100,
    left: screen.width - 400
  });
  window.close();
});
