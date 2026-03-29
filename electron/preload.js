// Preload script — runs in renderer context before page loads.
// Keeps nodeIntegration OFF for security; only exposes safe version info.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('throwvision', {
  version: process.env.npm_package_version || '1.0.0',
  platform: process.platform,
  setFullScreen: (enabled) => ipcRenderer.send('set-fullscreen', enabled),
});
