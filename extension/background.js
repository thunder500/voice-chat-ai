// Background service worker

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'do_tab_capture') {
    // Get the tab that sent this message
    const tabId = sender.tab ? sender.tab.id : null;
    if (!tabId) {
      sendResponse({ error: 'No tab' });
      return true;
    }

    // Use tabCapture.getMediaStreamId to get a stream ID the content script can use
    chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (streamId) => {
      if (chrome.runtime.lastError) {
        console.log('Tab capture error:', chrome.runtime.lastError.message);
        sendResponse({ error: chrome.runtime.lastError.message });
        return;
      }
      sendResponse({ streamId: streamId });
    });

    return true; // async response
  }

  if (msg.type === 'recording_status') {
    chrome.action.setBadgeText({ text: msg.recording ? 'REC' : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#ef4444' });
  }
});
