document.getElementById('startBtn').addEventListener('click', startRecording);
document.getElementById('stopBtn').addEventListener('click', stopRecording);
document.getElementById('popoutBtn').addEventListener('click', popOut);

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

  chrome.storage.local.set({ serverUrl: serverUrl, authToken: token });
  document.getElementById('startBtn').disabled = true;
  showMsg('Starting recording...', false);

  // Get the active Meet tab
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  var tab = tabs[0];

  if (!tab || !tab.url || !tab.url.includes('meet.google.com')) {
    showMsg('Open Google Meet first!', true);
    document.getElementById('startBtn').disabled = false;
    return;
  }

  var wsUrl = serverUrl.replace(/^http/, 'ws') + '/ws-meeting';

  // Send command to content script (via injector bridge)
  chrome.tabs.sendMessage(tab.id, {
    type: 'content_start_recording',
    wsUrl: wsUrl,
    token: token
  }, function(resp) {
    if (chrome.runtime.lastError) {
      showMsg('Extension not loaded on Meet page. Refresh the Meet tab and try again.', true);
      document.getElementById('startBtn').disabled = false;
      return;
    }
    showMsg('Recording started! You can close this popup.', false);
    showRecordingUI();
  });
}

function stopRecording() {
  chrome.tabs.query({ url: 'https://meet.google.com/*' }, function(tabs) {
    tabs.forEach(function(tab) {
      chrome.tabs.sendMessage(tab.id, { type: 'content_stop_recording' });
    });
  });
  chrome.runtime.sendMessage({ type: 'recording_status', recording: false });
  showStoppedUI();
  showMsg('Stopped. Check Voice Chat AI for transcript.', false);
}

function popOut() {
  chrome.windows.create({
    url: chrome.runtime.getURL('popup.html'),
    type: 'popup',
    width: 360,
    height: 450
  });
}

function showRecordingUI() {
  document.getElementById('statusDot').className = 'dot rec';
  document.getElementById('statusText').textContent = 'Recording...';
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

// Listen for updates from background
chrome.runtime.onMessage.addListener(function(msg) {
  if (msg.type === 'recording_status') {
    if (msg.recording) showRecordingUI();
    else showStoppedUI();
  }
  if (msg.type === 'transcript_update') {
    showMsg('Transcript #' + (msg.index + 1) + ': ' + msg.text.substring(0, 60), false);
  }
});
