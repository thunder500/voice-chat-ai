// Simple popup — just saves settings. Recording happens in content script.

document.getElementById('saveBtn').addEventListener('click', saveSettings);

// Load saved settings
chrome.storage.local.get(['serverUrl', 'authToken'], function(data) {
  if (data.serverUrl) document.getElementById('serverUrl').value = data.serverUrl;
  if (data.authToken) document.getElementById('authToken').value = data.authToken;
});

function saveSettings() {
  var serverUrl = document.getElementById('serverUrl').value.trim();
  var token = document.getElementById('authToken').value.trim();

  if (!token) {
    alert('Please paste your auth token');
    return;
  }

  chrome.storage.local.set({ serverUrl: serverUrl, authToken: token }, function() {
    document.getElementById('savedMsg').style.display = 'block';
    setTimeout(function() { document.getElementById('savedMsg').style.display = 'none'; }, 3000);
  });
}
