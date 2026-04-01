// Offscreen document — keeps recording alive after popup closes
// This receives a stream ID from the popup, creates a MediaStream,
// records in chunks, and sends to the server via WebSocket

let ws = null;
let recording = false;
let mediaRecorder = null;
let stream = null;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'start_offscreen_recording') {
    startRecording(msg.streamId, msg.wsUrl, msg.token, msg.tabId);
    sendResponse({ ok: true });
  } else if (msg.type === 'stop_offscreen_recording') {
    stopRecording();
    sendResponse({ ok: true });
  } else if (msg.type === 'get_offscreen_status') {
    sendResponse({ recording: recording });
  }
  return true;
});

async function startRecording(streamId, wsUrl, token, tabId) {
  try {
    // Get the stream from the stream ID
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        }
      }
    });

    if (!stream || stream.getAudioTracks().length === 0) {
      chrome.runtime.sendMessage({ type: 'recording_error', message: 'No audio tracks in stream' });
      return;
    }

    // Connect WebSocket
    ws = new WebSocket(wsUrl + '?token=' + token);

    ws.onopen = () => {
      console.log('Offscreen: connected to server');
      ws.send(JSON.stringify({ type: 'meeting_start' }));
      recording = true;
      chrome.runtime.sendMessage({ type: 'recording_status', recording: true });
      recordChunk();
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        // Forward all messages to background/content
        chrome.runtime.sendMessage({ type: 'server_message', data: data });
      } catch (err) {}
    };

    ws.onerror = () => {
      chrome.runtime.sendMessage({ type: 'recording_error', message: 'WebSocket error' });
    };

    ws.onclose = () => {
      recording = false;
      chrome.runtime.sendMessage({ type: 'recording_status', recording: false });
      cleanup();
    };

  } catch (e) {
    chrome.runtime.sendMessage({ type: 'recording_error', message: 'Stream error: ' + e.message });
  }
}

function recordChunk() {
  if (!recording || !ws || ws.readyState !== 1 || !stream) return;

  const rec = new MediaRecorder(stream, {
    mimeType: 'audio/webm;codecs=opus',
    audioBitsPerSecond: 128000
  });
  const parts = [];

  rec.ondataavailable = (e) => {
    if (e.data.size > 0) parts.push(e.data);
  };

  rec.onstop = () => {
    if (parts.length > 0 && ws && ws.readyState === 1) {
      const blob = new Blob(parts, { type: 'audio/webm' });
      console.log('Offscreen: sending', blob.size, 'bytes');
      blob.arrayBuffer().then(buf => ws.send(buf));
    }
    if (recording) recordChunk();
  };

  rec.start();
  mediaRecorder = rec;
  setTimeout(() => {
    if (rec.state !== 'inactive') rec.stop();
  }, 10000);
}

function stopRecording() {
  recording = false;
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'meeting_stop' }));
    ws.close();
  }
  cleanup();
  chrome.runtime.sendMessage({ type: 'recording_status', recording: false });
}

function cleanup() {
  if (stream) {
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  ws = null;
  mediaRecorder = null;
}
