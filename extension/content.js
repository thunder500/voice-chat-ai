// Voice Chat AI — Google Meet Audio Capture
// Runs in MAIN world, captures WebRTC audio + mic, shows audio levels

(function() {
  'use strict';

  let ws = null;
  let recording = false;
  let audioContext = null;
  let capturedTracks = [];
  let destNode = null;
  let indicatorEl = null;
  let transcriptOverlay = null;
  let micAnalyser = null;
  let tabAnalyser = null;
  let levelInterval = null;

  // Listen for commands from injector
  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data || event.data.source !== 'vcai-injector') return;
    const msg = event.data;
    if (msg.type === 'content_start_recording') startRecording(msg.wsUrl, msg.token);
    else if (msg.type === 'content_stop_recording') stopRecording();
    else if (msg.type === 'content_get_status') {
      sendToExt({ type: 'status_response', recording, tracks: capturedTracks.length });
    }
  });

  function sendToExt(data) {
    window.postMessage({ source: 'vcai-content', ...data }, '*');
  }

  // --- Patch RTCPeerConnection to capture remote audio ---
  const OrigRTC = window.RTCPeerConnection;
  window.RTCPeerConnection = function(...args) {
    const pc = new OrigRTC(...args);
    pc.addEventListener('track', (event) => {
      if (event.track.kind === 'audio' && event.track.readyState === 'live') {
        console.log('[VoiceChatAI] Remote audio track captured:', event.track.label || event.track.id);
        capturedTracks.push(event.track);
        sendToExt({ type: 'audio_track_available', count: capturedTracks.length });
        if (recording && audioContext && destNode) addTrackToMix(event.track, 'remote');
      }
    });
    return pc;
  };
  window.RTCPeerConnection.prototype = OrigRTC.prototype;
  Object.keys(OrigRTC).forEach(k => { try { window.RTCPeerConnection[k] = OrigRTC[k]; } catch(e){} });
  console.log('[VoiceChatAI] Ready — waiting for meeting audio');

  function addTrackToMix(track, label) {
    try {
      const stream = new MediaStream([track]);
      const source = audioContext.createMediaStreamSource(stream);
      const gain = audioContext.createGain();
      gain.gain.value = label === 'remote' ? 3.0 : 1.5;
      source.connect(gain);
      gain.connect(destNode);

      // Create analyser for level monitoring
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      if (label === 'remote') tabAnalyser = analyser;
      else micAnalyser = analyser;

      console.log('[VoiceChatAI] Added', label, 'audio to mix');
    } catch(e) { console.warn('[VoiceChatAI] Add track failed:', e); }
  }

  // --- Get audio level from analyser ---
  function getLevel(analyser) {
    if (!analyser) return 0;
    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i];
    return Math.round(sum / data.length);
  }

  // --- Recording ---
  async function startRecording(wsUrl, token) {
    if (recording) return;
    console.log('[VoiceChatAI] Starting. Remote tracks available:', capturedTracks.length);

    audioContext = new AudioContext();
    const dest = audioContext.createMediaStreamDestination();
    destNode = dest;

    // Add remote audio tracks
    let remoteAdded = 0;
    capturedTracks.forEach(track => {
      if (track.readyState === 'live') { addTrackToMix(track, 'remote'); remoteAdded++; }
    });

    // Add microphone
    let micAdded = false;
    try {
      const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const micTrack = micStream.getAudioTracks()[0];
      if (micTrack) { addTrackToMix(micTrack, 'mic'); micAdded = true; }
    } catch(e) { console.warn('[VoiceChatAI] Mic access denied:', e); }

    console.log('[VoiceChatAI] Sources: remote=' + remoteAdded + ' mic=' + micAdded);

    if (remoteAdded === 0 && !micAdded) {
      console.error('[VoiceChatAI] No audio sources!');
      sendToExt({ type: 'recording_error', message: 'No audio sources available' });
      return;
    }

    // Connect WebSocket
    ws = new WebSocket(wsUrl + '?token=' + token);

    ws.onopen = () => {
      console.log('[VoiceChatAI] Server connected');
      ws.send(JSON.stringify({ type: 'meeting_start' }));
      recording = true;
      showIndicator(remoteAdded, micAdded);
      startLevelMonitor();
      recordChunk(dest.stream);
      sendToExt({ type: 'recording_status', recording: true });
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'meeting_transcript') {
          showTranscript(data.text);
          sendToExt({ type: 'transcript_update', text: data.text, index: data.chunk_index });
        }
      } catch(err) {}
    };

    ws.onerror = () => sendToExt({ type: 'recording_status', recording: false });
    ws.onclose = () => { recording = false; sendToExt({ type: 'recording_status', recording: false }); };
  }

  function recordChunk(stream) {
    if (!recording || !ws || ws.readyState !== 1) return;
    const rec = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 });
    const parts = [];
    rec.ondataavailable = (e) => { if (e.data.size > 0) parts.push(e.data); };
    rec.onstop = () => {
      if (parts.length > 0 && ws && ws.readyState === 1) {
        const blob = new Blob(parts, { type: 'audio/webm' });
        console.log('[VoiceChatAI] Chunk:', Math.round(blob.size / 1024), 'KB');
        blob.arrayBuffer().then(buf => ws.send(buf));
      }
      if (recording) recordChunk(stream);
    };
    rec.start();
    setTimeout(() => { if (rec.state !== 'inactive') rec.stop(); }, 10000);
  }

  function stopRecording() {
    recording = false;
    stopLevelMonitor();
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'meeting_stop' }));
      setTimeout(() => { if (ws) ws.close(); }, 2000);
    }
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    destNode = null; micAnalyser = null; tabAnalyser = null;
    hideIndicator();
    sendToExt({ type: 'recording_status', recording: false });
  }

  // --- Audio Level Monitor ---
  function startLevelMonitor() {
    levelInterval = setInterval(() => {
      const micLevel = getLevel(micAnalyser);
      const tabLevel = getLevel(tabAnalyser);
      updateLevelBars(micLevel, tabLevel);
    }, 200);
  }

  function stopLevelMonitor() {
    if (levelInterval) { clearInterval(levelInterval); levelInterval = null; }
  }

  // --- UI ---
  function showIndicator(remoteCount, hasMic) {
    if (indicatorEl) indicatorEl.remove();

    indicatorEl = document.createElement('div');
    indicatorEl.id = 'vcai-root';

    // Use textContent and DOM APIs instead of innerHTML (Trusted Types policy)
    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999;background:rgba(0,0,0,0.92);color:white;padding:12px 16px;border-radius:14px;font-family:sans-serif;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,0.5);min-width:220px';

    // Header row
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:10px';
    const dot = document.createElement('span');
    dot.style.cssText = 'width:10px;height:10px;border-radius:50%;background:#ef4444;display:inline-block';
    dot.id = 'vcai-dot';
    const title = document.createElement('span');
    title.style.cssText = 'font-weight:600;font-size:13px;flex:1';
    title.textContent = 'AI Recording';
    const stopBtn = document.createElement('button');
    stopBtn.textContent = 'Stop';
    stopBtn.style.cssText = 'background:#ef4444;color:white;border:none;padding:4px 12px;border-radius:10px;font-size:11px;cursor:pointer';
    stopBtn.addEventListener('click', stopRecording);
    header.appendChild(dot);
    header.appendChild(title);
    header.appendChild(stopBtn);

    // Mic level
    const micRow = document.createElement('div');
    micRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:4px';
    const micLabel = document.createElement('span');
    micLabel.style.cssText = 'width:70px;font-size:11px;color:#aaa';
    micLabel.textContent = hasMic ? 'Your mic' : 'Mic (off)';
    const micBar = document.createElement('div');
    micBar.style.cssText = 'flex:1;height:6px;background:#333;border-radius:3px;overflow:hidden';
    const micFill = document.createElement('div');
    micFill.id = 'vcai-mic-level';
    micFill.style.cssText = 'height:100%;width:0%;background:#4ade80;border-radius:3px;transition:width 0.15s';
    micBar.appendChild(micFill);
    const micStatus = document.createElement('span');
    micStatus.id = 'vcai-mic-status';
    micStatus.style.cssText = 'width:12px;height:12px;border-radius:50%;background:' + (hasMic ? '#4ade80' : '#666');
    micRow.appendChild(micLabel);
    micRow.appendChild(micBar);
    micRow.appendChild(micStatus);

    // Tab/remote level
    const tabRow = document.createElement('div');
    tabRow.style.cssText = 'display:flex;align-items:center;gap:8px';
    const tabLabel = document.createElement('span');
    tabLabel.style.cssText = 'width:70px;font-size:11px;color:#aaa';
    tabLabel.textContent = remoteCount > 0 ? 'Meeting (' + remoteCount + ')' : 'Meeting (0)';
    const tabBar = document.createElement('div');
    tabBar.style.cssText = 'flex:1;height:6px;background:#333;border-radius:3px;overflow:hidden';
    const tabFill = document.createElement('div');
    tabFill.id = 'vcai-tab-level';
    tabFill.style.cssText = 'height:100%;width:0%;background:#8b5cf6;border-radius:3px;transition:width 0.15s';
    tabBar.appendChild(tabFill);
    const tabStatus = document.createElement('span');
    tabStatus.id = 'vcai-tab-status';
    tabStatus.style.cssText = 'width:12px;height:12px;border-radius:50%;background:' + (remoteCount > 0 ? '#8b5cf6' : '#666');
    tabRow.appendChild(tabLabel);
    tabRow.appendChild(tabBar);
    tabRow.appendChild(tabStatus);

    container.appendChild(header);
    container.appendChild(micRow);
    container.appendChild(tabRow);
    indicatorEl.appendChild(container);

    // Add pulsing animation via style element
    const style = document.createElement('style');
    style.textContent = '@keyframes vcpulse{0%,100%{opacity:1}50%{opacity:.3}}#vcai-dot{animation:vcpulse 1s infinite}';
    indicatorEl.appendChild(style);

    document.body.appendChild(indicatorEl);

    // Transcript overlay
    transcriptOverlay = document.createElement('div');
    transcriptOverlay.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:99999;width:600px;max-height:150px;overflow-y:auto;background:rgba(0,0,0,0.92);color:white;padding:12px 16px;border-radius:12px;font-family:sans-serif;font-size:14px;line-height:1.5;box-shadow:0 4px 20px rgba(0,0,0,0.4);display:none';
    document.body.appendChild(transcriptOverlay);
  }

  function updateLevelBars(micLevel, tabLevel) {
    const micFill = document.getElementById('vcai-mic-level');
    const tabFill = document.getElementById('vcai-tab-level');
    const micStatus = document.getElementById('vcai-mic-status');
    const tabStatus = document.getElementById('vcai-tab-status');
    if (micFill) micFill.style.width = Math.min(micLevel, 100) + '%';
    if (tabFill) tabFill.style.width = Math.min(tabLevel, 100) + '%';
    if (micStatus) micStatus.style.background = micLevel > 5 ? '#4ade80' : '#666';
    if (tabStatus) tabStatus.style.background = tabLevel > 5 ? '#8b5cf6' : '#666';
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
