// Content script — runs in MAIN world inside Google Meet
// Patches RTCPeerConnection to capture audio tracks
// Communicates with extension via window.postMessage

(function() {
  'use strict';

  let ws = null;
  let recording = false;
  let audioContext = null;
  let capturedTracks = [];
  let transcriptOverlay = null;
  let indicatorEl = null;
  let destStream = null;

  // Listen for commands from injector.js
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== 'vcai-injector') return;

    const msg = event.data;
    if (msg.type === 'content_start_recording') {
      startRecording(msg.wsUrl, msg.token);
    } else if (msg.type === 'content_stop_recording') {
      stopRecording();
    } else if (msg.type === 'content_get_status') {
      window.postMessage({
        source: 'vcai-content', type: 'status_response',
        recording: recording, tracks: capturedTracks.length
      }, '*');
    }
  });

  function sendToExtension(data) {
    window.postMessage({ source: 'vcai-content', ...data }, '*');
  }

  // --- Patch RTCPeerConnection ---
  const OrigRTC = window.RTCPeerConnection;
  window.RTCPeerConnection = function(...args) {
    const pc = new OrigRTC(...args);

    pc.addEventListener('track', (event) => {
      if (event.track.kind === 'audio' && event.track.readyState === 'live') {
        console.log('[VoiceChatAI] Captured audio track:', event.track.id, event.track.label);
        capturedTracks.push(event.track);
        sendToExtension({ type: 'audio_track_available', count: capturedTracks.length });

        // If recording, add to mix
        if (recording && audioContext && destStream) {
          addTrackToMix(event.track);
        }
      }
    });

    return pc;
  };
  window.RTCPeerConnection.prototype = OrigRTC.prototype;
  Object.keys(OrigRTC).forEach(k => { window.RTCPeerConnection[k] = OrigRTC[k]; });

  console.log('[VoiceChatAI] RTCPeerConnection patched');

  function addTrackToMix(track) {
    try {
      const stream = new MediaStream([track]);
      const source = audioContext.createMediaStreamSource(stream);
      const gain = audioContext.createGain();
      gain.gain.value = 3.0;
      source.connect(gain);
      gain.connect(destStream);
      console.log('[VoiceChatAI] Track added to mix');
    } catch(e) {
      console.warn('[VoiceChatAI] Add track failed:', e);
    }
  }

  // --- Recording ---
  function startRecording(wsUrl, token) {
    if (recording) return;
    console.log('[VoiceChatAI] Starting recording, tracks available:', capturedTracks.length);

    audioContext = new AudioContext();
    const dest = audioContext.createMediaStreamDestination();
    destStream = dest;

    // Add all captured remote audio tracks
    let added = 0;
    capturedTracks.forEach(track => {
      if (track.readyState === 'live') {
        addTrackToMix(track);
        added++;
      }
    });
    console.log('[VoiceChatAI] Added', added, 'remote tracks');

    // Also add microphone
    navigator.mediaDevices.getUserMedia({ audio: true }).then(micStream => {
      const micSource = audioContext.createMediaStreamSource(micStream);
      const micGain = audioContext.createGain();
      micGain.gain.value = 1.5;
      micSource.connect(micGain);
      micGain.connect(dest);
      console.log('[VoiceChatAI] Mic added');
    }).catch(e => console.warn('[VoiceChatAI] No mic:', e));

    // Connect WebSocket
    ws = new WebSocket(wsUrl + '?token=' + token);

    ws.onopen = () => {
      console.log('[VoiceChatAI] Server connected');
      ws.send(JSON.stringify({ type: 'meeting_start' }));
      recording = true;
      showIndicator();
      recordChunk(dest.stream);
      sendToExtension({ type: 'recording_status', recording: true });
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'meeting_transcript') {
          showTranscript(data.text);
          sendToExtension({ type: 'transcript_update', text: data.text, index: data.chunk_index });
        }
      } catch(err) {}
    };

    ws.onerror = () => {
      console.error('[VoiceChatAI] WS error');
      sendToExtension({ type: 'recording_status', recording: false });
    };
    ws.onclose = () => {
      recording = false;
      sendToExtension({ type: 'recording_status', recording: false });
    };
  }

  function recordChunk(stream) {
    if (!recording || !ws || ws.readyState !== 1) return;

    const rec = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus',
      audioBitsPerSecond: 128000
    });
    const parts = [];

    rec.ondataavailable = (e) => { if (e.data.size > 0) parts.push(e.data); };

    rec.onstop = () => {
      if (parts.length > 0 && ws && ws.readyState === 1) {
        const blob = new Blob(parts, { type: 'audio/webm' });
        console.log('[VoiceChatAI] Chunk:', Math.round(blob.size/1024), 'KB');
        blob.arrayBuffer().then(buf => ws.send(buf));
      }
      if (recording) recordChunk(stream);
    };

    rec.start();
    setTimeout(() => { if (rec.state !== 'inactive') rec.stop(); }, 10000);
  }

  function stopRecording() {
    console.log('[VoiceChatAI] Stopping');
    recording = false;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'meeting_stop' }));
      setTimeout(() => { if (ws) ws.close(); }, 2000);
    }
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    destStream = null;
    hideIndicator();
    sendToExtension({ type: 'recording_status', recording: false });
  }

  // --- UI ---
  function showIndicator() {
    if (indicatorEl) return;
    indicatorEl = document.createElement('div');
    indicatorEl.innerHTML = '<div style="position:fixed;top:10px;right:10px;z-index:99999;display:flex;align-items:center;gap:8px;background:rgba(0,0,0,0.9);color:white;padding:8px 16px;border-radius:20px;font-family:sans-serif;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.4)"><span style="width:10px;height:10px;border-radius:50%;background:#ef4444;animation:vcpulse 1s infinite"></span><span>AI Recording</span><button id="vcai-stop-btn" style="background:#ef4444;color:white;border:none;padding:4px 12px;border-radius:12px;font-size:11px;cursor:pointer;margin-left:8px">Stop</button></div><style>@keyframes vcpulse{0%,100%{opacity:1}50%{opacity:.3}}</style>';
    document.body.appendChild(indicatorEl);
    setTimeout(() => {
      const btn = document.getElementById('vcai-stop-btn');
      if (btn) btn.onclick = stopRecording;
    }, 100);

    transcriptOverlay = document.createElement('div');
    transcriptOverlay.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:99999;width:600px;max-height:150px;overflow-y:auto;background:rgba(0,0,0,0.9);color:white;padding:12px 16px;border-radius:12px;font-family:sans-serif;font-size:14px;line-height:1.5;box-shadow:0 4px 20px rgba(0,0,0,0.4);display:none';
    document.body.appendChild(transcriptOverlay);
  }

  function hideIndicator() {
    if (indicatorEl) { indicatorEl.remove(); indicatorEl = null; }
    if (transcriptOverlay) { transcriptOverlay.remove(); transcriptOverlay = null; }
  }

  function showTranscript(text) {
    if (!transcriptOverlay) return;
    transcriptOverlay.style.display = 'block';
    const p = document.createElement('p');
    p.textContent = text;
    p.style.cssText = 'margin:0 0 6px;padding:0 0 6px;border-bottom:1px solid rgba(255,255,255,0.1)';
    transcriptOverlay.appendChild(p);
    transcriptOverlay.scrollTop = transcriptOverlay.scrollHeight;
    while (transcriptOverlay.children.length > 10) transcriptOverlay.removeChild(transcriptOverlay.firstChild);
  }

})();
