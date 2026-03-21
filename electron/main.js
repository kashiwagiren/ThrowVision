'use strict';

const { app, BrowserWindow, shell, Menu, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

// ─── Config ────────────────────────────────────────────────────────────────
const SERVER_URL = 'http://localhost:5000';
const READY_POLL_MS = 500;
const READY_TIMEOUT_MS = 40000;
const isDev = process.argv.includes('--dev');

// ─── State ─────────────────────────────────────────────────────────────────
let pyProcess = null;
let mainWindow = null;
let splashWindow = null;

// ─── Resolve Python executable ─────────────────────────────────────────────
function getPythonPath() {
  const root = app.getAppPath();
  // Prefer the project virtualenv if present
  const venvWin   = path.join(root, '.venv', 'Scripts', 'python.exe');
  const venvLinux = path.join(root, '.venv', 'bin', 'python');
  const venvMac   = path.join(root, '.venv', 'bin', 'python3');

  if (process.platform === 'win32'   && fs.existsSync(venvWin))   return venvWin;
  if (process.platform === 'linux'   && fs.existsSync(venvLinux)) return venvLinux;
  if (process.platform === 'darwin'  && fs.existsSync(venvMac))   return venvMac;

  // Fall back to system Python
  return process.platform === 'win32' ? 'python' : 'python3';
}

// ─── Start Python backend ──────────────────────────────────────────────────
function startPython() {
  const pythonPath = getPythonPath();
  const serverScript = path.join(app.getAppPath(), 'server.py');

  console.log(`[ELECTRON] Starting Python: ${pythonPath} ${serverScript}`);

  pyProcess = spawn(pythonPath, [serverScript], {
    cwd: app.getAppPath(),
    // Windows needs a detached process group so we can kill the whole tree
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  pyProcess.stdout.on('data', (d) => process.stdout.write(`[PY] ${d}`));
  pyProcess.stderr.on('data', (d) => process.stderr.write(`[PY] ${d}`));

  pyProcess.on('exit', (code, signal) => {
    console.log(`[ELECTRON] Python exited: code=${code} signal=${signal}`);
    pyProcess = null;
    // If the main window is still open, the backend crashed — show an error
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showErrorBox(
        'Server Stopped',
        `The ThrowVision backend stopped unexpectedly (code ${code}).\nClose and relaunch the app.`
      );
    }
  });
}

// ─── Kill Python (and its children) ───────────────────────────────────────
function killPython() {
  if (!pyProcess) return;
  try {
    if (process.platform === 'win32') {
      // taskkill kills the entire process tree on Windows
      spawn('taskkill', ['/F', '/T', '/PID', String(pyProcess.pid)]);
    } else {
      // On Linux/macOS, kill the process group
      process.kill(-pyProcess.pid, 'SIGTERM');
    }
  } catch (e) {
    console.error('[ELECTRON] Could not kill Python:', e.message);
  }
  pyProcess = null;
}

// ─── Poll until Flask is ready ─────────────────────────────────────────────
function waitForServer() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    const check = () => {
      const req = http.get(SERVER_URL + '/api/status', (res) => {
        res.resume(); // consume response
        if (res.statusCode === 200) return resolve();
        if (Date.now() > deadline) return reject(new Error('Server timeout'));
        setTimeout(check, READY_POLL_MS);
      });
      req.on('error', () => {
        if (Date.now() > deadline) return reject(new Error('Server timeout'));
        setTimeout(check, READY_POLL_MS);
      });
      req.end();
    };
    check();
  });
}

// ─── Splash window ─────────────────────────────────────────────────────────
function createSplash() {
  splashWindow = new BrowserWindow({
    width: 420,
    height: 300,
    frame: false,
    resizable: false,
    center: true,
    alwaysOnTop: true,
    transparent: true,
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
}

// ─── Main application window ───────────────────────────────────────────────
function createMain() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    center: true,
    show: false,
    title: 'ThrowVision',
    backgroundColor: '#0a0a12',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // Remove default menu bar (ThrowVision has its own UI)
  Menu.setApplicationMenu(null);

  mainWindow.loadURL(SERVER_URL);

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.destroy();
      splashWindow = null;
    }
    mainWindow.show();
    if (isDev) mainWindow.webContents.openDevTools();
  });

  // Open links that target _blank in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ─── App lifecycle ─────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  createSplash();
  startPython();

  try {
    await waitForServer();
    createMain();
  } catch (err) {
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy();
    dialog.showErrorBox(
      'ThrowVision — Failed to Start',
      `Could not connect to the Python backend.\n\n${err.message}\n\nMake sure Python and all dependencies are installed.`
    );
    killPython();
    app.quit();
  }
});

app.on('window-all-closed', () => {
  killPython();
  app.quit();
});

app.on('will-quit', () => {
  killPython();
});

app.on('activate', () => {
  // macOS: re-create window if dock icon clicked and no windows open
  if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
    createMain();
  }
});
