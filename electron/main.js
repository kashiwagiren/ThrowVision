'use strict';

const { app, BrowserWindow, shell, Menu, dialog } = require('electron');
const { spawn, execFileSync } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

// ─── Config ────────────────────────────────────────────────────────────────
const SERVER_URL = 'http://localhost:5000';
const READY_POLL_MS = 500;
const READY_TIMEOUT_MS = 40000;
const isDev = process.argv.includes('--dev') || !app.isPackaged;

// ─── State ─────────────────────────────────────────────────────────────────
let pyProcess = null;
let mainWindow = null;
let splashWindow = null;

// ─── Resolve server command (dev vs production) ───────────────────────────
function getServerConfig() {
  const root = app.getAppPath();

  if (app.isPackaged) {
    // Production: use the PyInstaller-bundled server executable
    // electron-builder places extraResources in process.resourcesPath
    const serverExe = process.platform === 'win32'
      ? path.join(process.resourcesPath, 'server', 'server.exe')
      : path.join(process.resourcesPath, 'server', 'server');

    // Writable user data directory — calibration/stats files go here
    const userDataDir = app.getPath('userData');
    fs.mkdirSync(path.join(userDataDir, 'calibration', 'profiles'), { recursive: true });
    fs.mkdirSync(path.join(userDataDir, 'data'), { recursive: true });

    return {
      cmd: serverExe,
      args: [],
      cwd: userDataDir,
      isExe: true,
    };
  } else {
    // Development: use the virtualenv Python
    const venvWin   = path.join(root, '.venv', 'Scripts', 'python.exe');
    const venvLinux = path.join(root, '.venv', 'bin', 'python');
    const venvMac   = path.join(root, '.venv', 'bin', 'python3');

    let pythonPath;
    if (process.platform === 'win32'  && fs.existsSync(venvWin))   pythonPath = venvWin;
    else if (process.platform === 'linux'  && fs.existsSync(venvLinux)) pythonPath = venvLinux;
    else if (process.platform === 'darwin' && fs.existsSync(venvMac))   pythonPath = venvMac;
    else pythonPath = process.platform === 'win32' ? 'python' : 'python3';

    return {
      cmd: pythonPath,
      args: [path.join(root, 'server.py')],
      cwd: root,
      isExe: false,
    };
  }
}

// ─── Start Python/server backend ──────────────────────────────────────────
function startPython() {
  const cfg = getServerConfig();
  console.log(`[ELECTRON] Starting server: ${cfg.cmd} ${cfg.args.join(' ')}`);
  console.log(`[ELECTRON] CWD: ${cfg.cwd}`);

  pyProcess = spawn(cfg.cmd, cfg.args, {
    cwd: cfg.cwd,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  pyProcess.stdout.on('data', (d) => process.stdout.write(`[PY] ${d}`));
  pyProcess.stderr.on('data', (d) => process.stderr.write(`[PY] ${d}`));

  pyProcess.on('exit', (code, signal) => {
    console.log(`[ELECTRON] Server exited: code=${code} signal=${signal}`);
    pyProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showErrorBox(
        'Server Stopped',
        `The ThrowVision backend stopped unexpectedly (code ${code}).\nClose and relaunch the app.`
      );
    }
  });
}

// ─── Kill server and its children ─────────────────────────────────────────
function killPython() {
  if (!pyProcess) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/F', '/T', '/PID', String(pyProcess.pid)]);
    } else {
      process.kill(-pyProcess.pid, 'SIGTERM');
    }
  } catch (e) {
    console.error('[ELECTRON] Could not kill server:', e.message);
  }
  pyProcess = null;
}

// ─── Poll until Flask is ready ─────────────────────────────────────────────
function waitForServer() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    const check = () => {
      const req = http.get(SERVER_URL + '/api/status', (res) => {
        res.resume();
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

  Menu.setApplicationMenu(null);
  mainWindow.loadURL(SERVER_URL);

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.destroy();
      splashWindow = null;
    }
    mainWindow.show();
  });

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
      `Could not connect to the ThrowVision backend.\n\n${err.message}\n\nMake sure all dependencies are installed correctly.`
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
  if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
    createMain();
  }
});
