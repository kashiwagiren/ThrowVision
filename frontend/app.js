/**
 * ThrowVision – Dashboard App
 * Split-layout SPA: Board + Score Panel
 * Socket.IO for real-time dart scoring.
 */

// ══════════════════════════════════════════════════════════════════════
// SVG Dartboard Builder
// ══════════════════════════════════════════════════════════════════════

const SVG_NS = 'http://www.w3.org/2000/svg';
const BOARD_CX = 221, BOARD_CY = 220;
const TOTAL_R = 200;

const R = {
  bullInner: (6.35 / 170) * TOTAL_R,
  bullOuter: (15.9 / 170) * TOTAL_R,
  tripleInner: (99 / 170) * TOTAL_R,
  tripleOuter: (107 / 170) * TOTAL_R,
  doubleInner: (162 / 170) * TOTAL_R,
  doubleOuter: (170 / 170) * TOTAL_R,
};

const SECTOR_ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5];
const SECTOR_ANGLE = 360 / 20;

const COL = {
  darkBase: '#1c1c1c', lightBase: '#e8d8b0',
  tripleDark: '#6b1a1a', tripleLight: '#1a6b1a',
  doubleDark: '#6b1a1a', doubleLight: '#1a6b1a',
  bullGreen: '#2d6b2d', bullRed: '#6b1a1a', wire: '#888',
};

function polarToCart(cx, cy, r, angleDeg) {
  const rad = (angleDeg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function makeSlice(cx, cy, r1, r2, a1, a2, fill) {
  const [x1, y1] = polarToCart(cx, cy, r1, a1);
  const [x2, y2] = polarToCart(cx, cy, r2, a1);
  const [x3, y3] = polarToCart(cx, cy, r2, a2);
  const [x4, y4] = polarToCart(cx, cy, r1, a2);
  const large = (a2 - a1) > 180 ? 1 : 0;
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d',
    `M${x1},${y1} L${x2},${y2} A${r2},${r2} 0 ${large},1 ${x3},${y3} L${x4},${y4} A${r1},${r1} 0 ${large},0 ${x1},${y1} Z`
  );
  path.setAttribute('fill', fill);
  return path;
}

function buildDartboard(svgEl) {
  svgEl.innerHTML = '';
  // Expand viewBox by 20px on all sides so larger segment numbers don't clip
  svgEl.setAttribute('viewBox', '-20 -20 482 480');
  const gSec = document.createElementNS(SVG_NS, 'g');
  const gWire = document.createElementNS(SVG_NS, 'g');
  const gNums = document.createElementNS(SVG_NS, 'g');
  const gBull = document.createElementNS(SVG_NS, 'g');
  const gDots = document.createElementNS(SVG_NS, 'g');
  gDots.setAttribute('id', svgEl.id + '-dots');
  const cx = BOARD_CX, cy = BOARD_CY;

  SECTOR_ORDER.forEach((val, i) => {
    const a1 = i * SECTOR_ANGLE - SECTOR_ANGLE / 2;
    const a2 = a1 + SECTOR_ANGLE;
    const even = i % 2 === 0;
    gSec.appendChild(makeSlice(cx, cy, R.bullOuter, R.tripleInner, a1, a2, even ? COL.darkBase : COL.lightBase));
    gSec.appendChild(makeSlice(cx, cy, R.tripleInner, R.tripleOuter, a1, a2, even ? COL.tripleDark : COL.tripleLight));
    gSec.appendChild(makeSlice(cx, cy, R.tripleOuter, R.doubleInner, a1, a2, even ? COL.darkBase : COL.lightBase));
    gSec.appendChild(makeSlice(cx, cy, R.doubleInner, R.doubleOuter, a1, a2, even ? COL.doubleDark : COL.doubleLight));
  });

  [R.bullOuter, R.tripleInner, R.tripleOuter, R.doubleInner, R.doubleOuter].forEach(r => {
    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('cx', cx); c.setAttribute('cy', cy); c.setAttribute('r', r);
    c.setAttribute('fill', 'none'); c.setAttribute('stroke', COL.wire); c.setAttribute('stroke-width', '1');
    gWire.appendChild(c);
  });

  SECTOR_ORDER.forEach((_, i) => {
    const angle = i * SECTOR_ANGLE - SECTOR_ANGLE / 2;
    const [x1, y1] = polarToCart(cx, cy, R.bullOuter, angle);
    const [x2, y2] = polarToCart(cx, cy, R.doubleOuter, angle);
    const line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('x1', x1); line.setAttribute('y1', y1);
    line.setAttribute('x2', x2); line.setAttribute('y2', y2);
    line.setAttribute('stroke', COL.wire); line.setAttribute('stroke-width', '0.8');
    gWire.appendChild(line);
  });

  SECTOR_ORDER.forEach((val, i) => {
    const angle = i * SECTOR_ANGLE;
    const nr = R.doubleOuter + 20;
    const [nx, ny] = polarToCart(cx, cy, nr, angle);
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('x', nx); t.setAttribute('y', ny);
    t.setAttribute('text-anchor', 'middle'); t.setAttribute('dominant-baseline', 'central');
    t.setAttribute('font-size', '30'); t.setAttribute('font-weight', '800');
    t.setAttribute('font-family', "'Inter', sans-serif"); t.setAttribute('fill', '#ffffff');
    t.textContent = val;
    gNums.appendChild(t);
  });

  const sb = document.createElementNS(SVG_NS, 'circle');
  sb.setAttribute('cx', cx); sb.setAttribute('cy', cy); sb.setAttribute('r', R.bullOuter);
  sb.setAttribute('fill', COL.bullGreen); gBull.appendChild(sb);
  const db = document.createElementNS(SVG_NS, 'circle');
  db.setAttribute('cx', cx); db.setAttribute('cy', cy); db.setAttribute('r', R.bullInner);
  db.setAttribute('fill', COL.bullRed); gBull.appendChild(db);
  [R.bullInner, R.bullOuter].forEach(r => {
    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('cx', cx); c.setAttribute('cy', cy); c.setAttribute('r', r);
    c.setAttribute('fill', 'none'); c.setAttribute('stroke', COL.wire); c.setAttribute('stroke-width', '0.8');
    gWire.appendChild(c);
  });

  svgEl.appendChild(gSec); svgEl.appendChild(gWire);
  svgEl.appendChild(gBull); svgEl.appendChild(gNums); svgEl.appendChild(gDots);
}

// Build boards
const $homeBoard = document.getElementById('dartboard-svg');
const $practiceBoard = document.getElementById('practice-board');
buildDartboard($homeBoard);
buildDartboard($practiceBoard);
// Build game-page boards
const $bullseyeBoard = document.getElementById('bullseye-board');
const $gameBoard = document.getElementById('game-board');
if ($bullseyeBoard) buildDartboard($bullseyeBoard);
if ($gameBoard) buildDartboard($gameBoard);

const PRACTICE_WARP_CANVAS_MM = 451;
const PRACTICE_WARP_CENTER_MM = PRACTICE_WARP_CANVAS_MM / 2;
let activeCam = 0;
let _practiceWarpVisible = false;
let _practiceWarpLoadToken = 0;
let _camerasKnownOpen = false;
let _practiceTakeoutPending = false;
let _practiceResetMode = null;
let _statsMode = null;
let _statsSection = 'games';
let _gameTurnReviewReady = false;
let _lastGameState = null;
let _practiceAccuracySession = {
  sessionId: null,
  turnId: null,
  context: 'practice',
  sessionMode: 'practice',
  modelName: '',
  executionDevice: '',
  detectionProfile: '',
  boardProfile: '',
  predictions: [],
  actualDarts: [],
  notes: '',
  lastSavedSignature: '',
  saveTimer: null,
};
let _missedDartDraft = {
  mode: 'add',
  predictionId: null,
  actualId: null,
  turnSlot: 1,
  multiplier: 'single',
  singleRing: 'inner',
  number: 20,
};
let _practiceReviewModalOpen = false;
let _practiceReviewFeedbackTimer = null;
let _practiceReviewFocusPredictionId = null;

function _clearSvgGroup(id) {
  const group = document.getElementById(id);
  if (group) group.innerHTML = '';
}

function clearPracticeBoardDots() {
  _clearSvgGroup('practice-board-dots');
  _clearSvgGroup('practice-board-warp-dots');
}

function clearBoardDots() {
  ['practice-board-dots', 'practice-board-warp-dots', 'bullseye-board-dots', 'game-board-dots']
    .forEach(_clearSvgGroup);
}

function _syncPracticeCamButtons() {
  document.querySelectorAll('.p-cam-btn[data-cam]').forEach((btn) => {
    btn.classList.toggle('active', parseInt(btn.dataset.cam, 10) === activeCam);
  });
}

function _setPracticeCameraSelectorVisible(visible) {
  const selector = document.querySelector('.prac-cam-selector');
  if (selector) {
    selector.hidden = !visible;
    selector.style.display = visible ? '' : 'none';
  }
}

function _clearPracticeResetFlash() {
  const side = document.querySelector('.prac-score-side');
  if (side) side.classList.remove('reset-flash');
}

function _setPracticeTakeoutBannerVisible(visible) {
  const banner = document.getElementById('practice-takeout-banner');
  _practiceTakeoutPending = !!visible;
  if (banner) banner.style.display = visible ? 'grid' : 'none';
  window._awaitingTakeoutGame = !!visible;
}

function _clearPracticeTransientUi() {
  _practiceResetMode = null;
  _setPracticeTakeoutBannerVisible(false);
  _clearPracticeResetFlash();
}

function _setPracticeBoardStatic(options = {}) {
  const { clearStream = false } = options;
  const stage = document.getElementById('practice-board-stage');
  const stream = document.getElementById('practice-board-stream');
  _practiceWarpVisible = false;
  if (stage) stage.classList.remove('is-loading', 'is-warped');
  if (stream) {
    stream.onload = null;
    stream.onerror = null;
    if (clearStream) {
      stream.removeAttribute('src');
      stream.src = '';
    }
  }
}

function _armPracticeWarpFeed() {
  if (!practiceActive) return;
  const stage = document.getElementById('practice-board-stage');
  const stream = document.getElementById('practice-board-stream');
  if (!stage || !stream) return;

  const token = ++_practiceWarpLoadToken;
  stage.classList.add('is-loading');

  stream.onload = () => {
    if (token !== _practiceWarpLoadToken) return;
    _practiceWarpVisible = true;
    stage.classList.remove('is-loading');
    stage.classList.add('is-warped');
  };

  stream.onerror = () => {
    if (token !== _practiceWarpLoadToken) return;
    _practiceWarpVisible = false;
    stage.classList.remove('is-loading', 'is-warped');
  };

  stream.src = `/api/stream/warped/${activeCam}?clean=1&t=${Date.now()}`;
}

_syncPracticeCamButtons();
_setPracticeCameraSelectorVisible(false);


// ══════════════════════════════════════════════════════════════════════
// Page Navigation
// ══════════════════════════════════════════════════════════════════════

let currentPage = 'home';

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => {
    p.classList.remove('active', 'fade-in', 'transitioning');
  });
  const target = document.getElementById('page-' + name);
  if (target) {
    target.classList.add('active');
    if (name === 'practice') target.classList.add('fade-in');
  }
  const prevPage = currentPage;
  currentPage = name;
  // Fullscreen: re-enter fullscreen whenever leaving the home page
  if (window.throwvision && window.throwvision.setFullScreen) {
    if (name !== 'home') {
      window.throwvision.setFullScreen(true);
    }
  }
  if (name === 'home') {
    $homeBoard.classList.remove('launch-practice');
    if (socket && socket.connected) $homeBoard.classList.add('spinning');
  }
  if (name === 'practice' && !practiceActive) {
    setStatus('idle', 'Idle');
    _clearPracticeTransientUi();
    _setPracticeCameraSelectorVisible(false);
    _setPracticeBoardStatic({ clearStream: true });
    _setPracticeFinishTurnVisible(false);
  }
  if (name === 'game-select') {
    if (!_selectedGameMode) {
      _selectedGameMode = 'x01';
    }
    updateGameConfigView();
  }
  if (!['practice', 'game'].includes(name)) {
    closePracticeReviewModal();
  }
  // Start/stop camera preview streams
  if (name === 'settings') {
    // Always reset preview state on every visit — cameras may have been
    // released since the last time settings was open.
    const previewSection = document.getElementById('cam-preview-section');
    if (previewSection) previewSection.style.display = 'none';
    const btn = document.getElementById('btn-toggle-cams');
    if (btn) btn.textContent = 'Open Cameras';
    _camsManuallyOpen = false;
    stopCamPreview();
    checkBoardProfile();
    lensRefreshAll();
    // Rebuild resolution dropdown — pass saved resolution so it stays selected
    loadServerSettings();
  } else {
    stopCamPreview();
    // Close cameras when leaving settings — but NOT when going to calibration,
    // because calibration opens cameras itself immediately after.
    if (prevPage === 'settings' && name !== 'calibration') {
      if (socket) socket.emit('close_cameras');
    }
  }
  // Calibration offline check
  if (name === 'calibration') {
    const online = socket && socket.connected;
    const calOffline = document.getElementById('cal-offline-overlay');
    if (calOffline) calOffline.style.display = online ? 'none' : 'flex';
    const zoomBox = document.getElementById('cal-zoom-box');
    if (zoomBox) zoomBox.style.display = online ? '' : 'none';
    const warpBox = document.getElementById('cal-warp-box');
    if (warpBox) warpBox.style.display = online ? '' : 'none';
  }
  // Stats page — auto-load stats
  if (name === 'stats') {
    setStatsSection(_statsSection || 'games');
  }
}

// ── Fullscreen: Esc on home page toggles fullscreen ───────────────────
let _appFullscreen = true; // starts fullscreen
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && currentPage === 'home') {
    if (window.throwvision && window.throwvision.setFullScreen) {
      _appFullscreen = !_appFullscreen;
      window.throwvision.setFullScreen(_appFullscreen);
    }
  }
});

// ── Camera Preview Streams ─────────────────────────────────────────────
let _camViewMode = 'raw';
let _hasActiveProfile = false;
let _selectedPreviewCam = 0;

function selectPreviewCam(camId) {
  _selectedPreviewCam = camId;
  // Update tab buttons
  document.querySelectorAll('.cam-select-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.cam) === camId);
  });
  // Show only the selected camera item
  document.querySelectorAll('.cam-preview-item[data-cam-item]').forEach(item => {
    const id = parseInt(item.dataset.camItem);
    item.style.display = id === camId ? '' : 'none';
  });
}

function setCamView(mode) {
  _camViewMode = mode;
  document.querySelectorAll('.cam-toggle-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  updateNoProfileOverlays();
  startCamPreview();
}

function updateNoProfileOverlays() {
  // In warped mode with no active profile, show a per-panel indicator instead of stream
  const showOverlay = _camViewMode === 'warped' && !_hasActiveProfile;
  for (let i = 0; i < 3; i++) {
    const overlay = document.getElementById('cam-no-profile-' + i);
    const img = document.getElementById('cam-preview-' + i);
    const ph = document.getElementById('cam-offline-' + i);
    if (overlay) overlay.style.display = showOverlay ? 'flex' : 'none';
    if (showOverlay) {
      // Hide the stream image and the "System Offline" placeholder so only
      // the "No Board Profile" overlay is visible (prevents warped overlap).
      if (img) { img.onerror = null; img.src = ''; img.style.display = 'none'; }
      if (ph) ph.classList.add('hidden');
    }
  }
}

function startCamPreview() {
  // Only start streams if server is connected
  if (!socket || !socket.connected) { stopCamPreview(); return; }
  // In warped mode without a profile, show overlay instead of stream
  updateNoProfileOverlays();
  for (let i = 0; i < 3; i++) {
    const img = document.getElementById('cam-preview-' + i);
    const ph = document.getElementById('cam-offline-' + i);
    const noProf = document.getElementById('cam-no-profile-' + i);
    const showOverlay = _camViewMode === 'warped' && !_hasActiveProfile;
    if (showOverlay) continue; // overlay is already shown by updateNoProfileOverlays
    if (img) {
      img.style.display = 'block';
      img.onerror = () => { img.style.display = 'none'; if (ph) ph.classList.remove('hidden'); };
      img.src = '/api/stream/' + _camViewMode + '/' + i;
    }
    if (ph) ph.classList.add('hidden');
    if (noProf) noProf.style.display = 'none';
  }
}

function stopCamPreview() {
  for (let i = 0; i < 3; i++) {
    const img = document.getElementById('cam-preview-' + i);
    const ph = document.getElementById('cam-offline-' + i);
    if (img) { img.onerror = null; img.src = ''; img.style.display = 'none'; }
    if (ph) ph.classList.remove('hidden');
  }
}

let _camsManuallyOpen = false;

function toggleCamerasManually() {
  const btn = document.getElementById('btn-toggle-cams');
  const previewSection = document.getElementById('cam-preview-section');
  
  if (!_camsManuallyOpen) {
    if (previewSection) previewSection.style.display = 'block';
    if (socket) socket.emit('open_cameras');
    startCamPreview();
    if (btn) btn.textContent = 'Close Cameras';
    _camsManuallyOpen = true;
  } else {
    if (previewSection) previewSection.style.display = 'none';
    if (socket) socket.emit('close_cameras');
    stopCamPreview();
    if (btn) btn.textContent = 'Open Cameras';
    _camsManuallyOpen = false;
  }
}

// ══════════════════════════════════════════════════════════════════════
// Calibration
// ══════════════════════════════════════════════════════════════════════

let calCamId = 0, calImage = null, calPoints = [], calDragging = -1, calOpacity = 0.45;
const CAL_POINT_R = 8;
// 0-3: outer double ring (cyan); 4-7: inner triple ring (orange)
const CAL_COLORS = [
  '#00d4ff', '#00d4ff', '#00d4ff', '#00d4ff',   // outer double (cyan)
  '#ff8c00', '#ff8c00', '#ff8c00', '#ff8c00',   // inner triple (orange)
];
const CAL_LABELS = [
  'D20/D1', 'D11/D14', 'D3/D19', 'D6/D10',      // outer double ring
  'T20/T1', 'T11/T14', 'T3/T19', 'T6/T10',      // inner triple ring
];
let calActivePoint = 0;  // which point zoom follows
let calMode = 8;          // 4 = legacy outer-only, 8 = outer+inner (precise)
let calNoticeTimer = null;

async function readJsonSafe(res) {
  const text = await res.text();
  try {
    return { data: JSON.parse(text), raw: text };
  } catch {
    return { data: null, raw: text };
  }
}

function showCalibrationNotice(message, kind = 'warn', timeoutMs = 4200) {
  let toast = document.getElementById('calibration-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'calibration-toast';
    toast.dataset.baseStyle = [
      'position:fixed',
      'top:72px',
      'left:50%',
      'transform:translateX(-50%)',
      'z-index:10001',
      'min-width:320px',
      'max-width:min(88vw,780px)',
      'padding:12px 16px',
      'border-radius:10px',
      'box-shadow:0 10px 28px rgba(0,0,0,.35)',
      'font:600 14px Inter, sans-serif',
      'display:none',
      'align-items:center',
      'justify-content:center',
      'text-align:center',
      'cursor:pointer',
      'backdrop-filter:blur(8px)',
    ].join(';');
    toast.style.cssText = toast.dataset.baseStyle;
    toast.addEventListener('click', () => { toast.style.display = 'none'; });
    document.body.appendChild(toast);
  }

  const styles = {
    ok: 'background:rgba(16,185,129,.92);color:#f0fdf4;border:1px solid rgba(167,243,208,.45)',
    warn: 'background:rgba(245,158,11,.94);color:#111827;border:1px solid rgba(253,230,138,.45)',
    error: 'background:rgba(239,68,68,.94);color:#fff;border:1px solid rgba(254,202,202,.45)',
  };
  toast.style.cssText = toast.dataset.baseStyle + ';' + (styles[kind] || styles.warn);
  toast.textContent = message;
  toast.style.display = 'flex';

  if (calNoticeTimer) clearTimeout(calNoticeTimer);
  calNoticeTimer = setTimeout(() => {
    if (toast) toast.style.display = 'none';
  }, timeoutMs);
}

let _toastTimer = null;
function showToast(message, kind = 'warn', timeoutMs = 4000) {
  let el = document.getElementById('app-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'app-toast';
    el.dataset.baseStyle = [
      'position:fixed', 'top:72px', 'left:50%', 'transform:translateX(-50%)',
      'z-index:10002', 'min-width:300px', 'max-width:min(88vw,680px)',
      'padding:12px 20px', 'border-radius:10px',
      'box-shadow:0 10px 28px rgba(0,0,0,.35)',
      'font:700 15px Inter, sans-serif',
      'display:none', 'align-items:center', 'justify-content:center',
      'text-align:center', 'cursor:pointer', 'backdrop-filter:blur(8px)',
    ].join(';');
    el.style.cssText = el.dataset.baseStyle;
    el.addEventListener('click', () => { el.style.display = 'none'; });
    document.body.appendChild(el);
  }
  const styles = {
    ok: 'background:rgba(16,185,129,.92);color:#f0fdf4;border:1px solid rgba(167,243,208,.45)',
    warn: 'background:rgba(245,158,11,.94);color:#111827;border:1px solid rgba(253,230,138,.45)',
    foul: 'background:rgba(220,30,30,.94);color:#fff;border:1px solid rgba(254,150,150,.45)',
    error: 'background:rgba(239,68,68,.94);color:#fff;border:1px solid rgba(254,202,202,.45)',
  };
  el.style.cssText = el.dataset.baseStyle + ';' + (styles[kind] || styles.warn);
  el.textContent = message;
  el.style.display = 'flex';
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { if (el) el.style.display = 'none'; }, timeoutMs);
}

let _foulNotifTimer = null;
function _showFoulNotification(msg) {
  let el = document.getElementById('foul-notification');
  if (!el) {
    el = document.createElement('div');
    el.id = 'foul-notification';
    el.style.cssText = [
      'position:fixed',
      'bottom:48px',         // sits just above the bottom bar
      'right:16px',
      'z-index:10003',
      'max-width:320px',
      'min-width:220px',
      'padding:10px 14px',
      'border-radius:8px',
      'border:1px solid rgba(224,82,82,0.5)',
      'background:rgba(30,8,8,0.94)',
      'backdrop-filter:blur(10px)',
      'box-shadow:0 4px 20px rgba(220,30,30,.35)',
      'font:600 12px Inter,sans-serif',
      'color:#ffaaaa',
      'display:none',
      'align-items:flex-start',
      'gap:8px',
      'cursor:pointer',
      'line-height:1.45',
    ].join(';');
    el.addEventListener('click', () => { el.style.display = 'none'; });
    document.body.appendChild(el);
  }
  el.innerHTML = '<span style="font-size:14px;flex-shrink:0">⚠</span><span>' + msg + '</span>';
  el.style.display = 'flex';
  if (_foulNotifTimer) clearTimeout(_foulNotifTimer);
  _foulNotifTimer = setTimeout(() => { if (el) el.style.display = 'none'; }, 6000);
}

// Update toolbar toggle + instruction hint to match calMode
function calSyncModeUI() {
  document.getElementById('btn-cal-4pt').classList.toggle('active', calMode === 4);
  document.getElementById('btn-cal-8pt').classList.toggle('active', calMode === 8);
  const nEl = document.getElementById('cal-hint-n');
  const innerEl = document.getElementById('cal-hint-inner');
  if (nEl) nEl.textContent = calMode === 8 ? '8 points' : '4 points';
  if (innerEl) innerEl.style.display = calMode === 8 ? '' : 'none';
}

// Switch between 4-pt (legacy) and 8-pt (precise) modes
function calSetMode(n) {
  calMode = n;
  calSyncModeUI();
  if (!calImage) return;
  const cx = calImage.width / 2, cy = calImage.height / 2;
  const ro = Math.min(calImage.width, calImage.height) * 0.40;
  const ri = ro * (107.0 / 170.0);
  const angs = [81, 171, 261, 351].map(a => a * Math.PI / 180);
  const outer = angs.map(a => ({ x: cx + ro * Math.cos(a), y: cy - ro * Math.sin(a) }));
  if (n === 4) {
    calPoints = outer;
  } else {
    const inner = angs.map(a => ({ x: cx + ri * Math.cos(a), y: cy - ri * Math.sin(a) }));
    calPoints = [...outer, ...inner];
  }
  calActivePoint = 0;
  calDraw();
}

async function launchCalibration() {
  showPage('calibration');
  const loading = document.getElementById('cal-loading');
  if (loading) { loading.classList.remove('hidden'); loading.textContent = 'Opening cameras…'; }
  if (socket) {
    socket.emit('open_cameras');
    // Wait for cameras_state {open: true} or timeout after 8s
    await new Promise(resolve => {
      let done = false;
      const onReady = (data) => {
        if (data.open && !done) { done = true; socket.off('cameras_state', onReady); resolve(); }
      };
      socket.on('cameras_state', onReady);
      fetch('/api/status').then(r => r.json()).then(d => {
        const states = d.cam_states || {};
        if (Object.values(states).some(c => c.active) && !done) {
          done = true; socket.off('cameras_state', onReady); resolve();
        }
      }).catch(() => { });
      setTimeout(() => { if (!done) { done = true; socket.off('cameras_state', onReady); resolve(); } }, 8000);
    });
    // Give cameras a moment to produce usable frames after warmup
    if (loading) loading.textContent = 'Loading camera…';
    await new Promise(r => setTimeout(r, 1000));
  }
  calSelectCam(0);
}
function cancelCalibration() {
  if (socket) socket.emit('close_cameras');
  showPage('home');
}

function calSelectCam(id) {
  calCamId = id;
  document.getElementById('cal-cam-label').textContent = 'Camera ' + (id + 1);
  document.querySelectorAll('.cal-cam-btn').forEach((b, i) => b.classList.toggle('active', i === id));
  calCaptureFrame();
}

async function calCaptureFrame(retries = 5, keepPoints = false) {
  const loading = document.getElementById('cal-loading');
  loading.classList.remove('hidden');
  loading.textContent = 'Loading camera…';
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const infoRes = await fetch(`/api/cal/info/${calCamId}`);
      const info = await infoRes.json();
      const img = new Image();
      img.crossOrigin = 'anonymous';
      await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = `/api/cal/frame/${calCamId}?t=` + Date.now(); });
      calImage = img;
      const canvas = document.getElementById('cal-canvas');
      canvas.width = img.width; canvas.height = img.height;
      if (!keepPoints) {
        const n = info.src_points ? info.src_points.length : 0;
        if (n === 4 || n === 8) {
          // Scale saved points from calibration resolution to current frame resolution
          const calW = info.resolution ? info.resolution[0] : img.width;
          const calH = info.resolution ? info.resolution[1] : img.height;
          const sx = img.width / calW;
          const sy = img.height / calH;
          calPoints = info.src_points.map(p => ({ x: p[0] * sx, y: p[1] * sy }));
          calMode = n;  // sync toggle to saved mode
          calSyncModeUI();
        } else {
          // Default: 8-point layout
          const cx = img.width / 2, cy = img.height / 2;
          const ro = Math.min(img.width, img.height) * 0.40;  // outer double ring radius
          const ri = ro * (107.0 / 170.0);                     // triple ring radius
          // Same 4 angles as server: D20/D1, D11/D14, D3/D19, D6/D10
          const angs = [81, 171, 261, 351].map(a => a * Math.PI / 180);
          calPoints = [
            // 4 outer double ring
            ...angs.map(a => ({ x: cx + ro * Math.cos(a), y: cy - ro * Math.sin(a) })),
            // 4 triple ring (same angles, inner radius)
            ...angs.map(a => ({ x: cx + ri * Math.cos(a), y: cy - ri * Math.sin(a) })),
          ];
          calMode = 8;
          calSyncModeUI();
        }
      }
      // Sync resolution dropdown with actual frame size
      const resSel = document.getElementById('cal-resolution');
      if (resSel) {
        const resKey = img.width + 'x' + img.height;
        if ([...resSel.options].some(o => o.value === resKey)) {
          resSel.value = resKey;
        } else {
          const opt = document.createElement('option');
          opt.value = resKey;
          opt.textContent = img.width + '×' + img.height;
          resSel.appendChild(opt);
          resSel.value = resKey;
        }
      }
      loading.classList.add('hidden');
      calDraw(); calSetupEvents();
      return; // success
    } catch (err) {
      console.warn(`[CAL] Frame capture attempt ${attempt + 1}/${retries} failed`, err);
      if (attempt < retries - 1) {
        loading.textContent = `Loading camera… (retry ${attempt + 2}/${retries})`;
        await new Promise(r => setTimeout(r, 1000));
      } else {
        loading.textContent = 'Camera unavailable — click a camera button to retry';
      }
    }
  }
}




function calDraw() {
  const canvas = document.getElementById('cal-canvas');
  const ctx = canvas.getContext('2d');
  if (!calImage) return;
  ctx.drawImage(calImage, 0, 0);
  ctx.fillStyle = `rgba(0,0,0,${calOpacity})`; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.globalAlpha = 1 - calOpacity; ctx.drawImage(calImage, 0, 0); ctx.globalAlpha = 1.0;

  // Draw lines connecting outer-ring points (first 4)
  const nOuter = Math.min(4, calPoints.length);
  if (nOuter >= 4) {
    ctx.beginPath(); ctx.moveTo(calPoints[0].x, calPoints[0].y);
    for (let i = 1; i < nOuter; i++) ctx.lineTo(calPoints[i].x, calPoints[i].y);
    ctx.closePath(); ctx.strokeStyle = 'rgba(0,220,255,0.4)'; ctx.lineWidth = 1.5; ctx.stroke();
  }
  // Draw lines connecting inner-ring points (points 4-7)
  if (calPoints.length === 8) {
    ctx.beginPath(); ctx.moveTo(calPoints[4].x, calPoints[4].y);
    for (let i = 5; i < 8; i++) ctx.lineTo(calPoints[i].x, calPoints[i].y);
    ctx.closePath(); ctx.strokeStyle = 'rgba(255,165,0,0.4)'; ctx.lineWidth = 1.5; ctx.stroke();
    // Lines connecting corresponding outer/inner pairs
    ctx.strokeStyle = 'rgba(255,255,255,0.18)'; ctx.lineWidth = 1;
    for (let i = 0; i < 4; i++) {
      ctx.beginPath(); ctx.moveTo(calPoints[i].x, calPoints[i].y);
      ctx.lineTo(calPoints[i + 4].x, calPoints[i + 4].y); ctx.stroke();
    }
  }
  // Draw points
  calPoints.forEach((p, i) => {
    const isInner = i >= 4;
    const color = isInner ? CAL_COLORS[i] : CAL_COLORS[i];
    ctx.beginPath(); ctx.arc(p.x, p.y, CAL_POINT_R + 2, 0, Math.PI * 2); ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fill();
    ctx.beginPath(); ctx.arc(p.x, p.y, CAL_POINT_R, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.font = '11px Inter, sans-serif'; ctx.fillStyle = '#fff'; ctx.textAlign = 'center';
    ctx.fillText(CAL_LABELS[i], p.x, p.y - CAL_POINT_R - 6);
  });
  // Draw perspective wireframe overlay
  if (calPoints.length >= 4) drawPerspectiveWireframe(ctx);
  calDrawZoom();
  calScheduleWarpPreview();
}

function calDrawZoom() {
  const zoomCanvas = document.getElementById('cal-zoom-canvas');
  const zCtx = zoomCanvas.getContext('2d');
  if (!calImage || !calPoints.length) return;

  const pt = calPoints[calActivePoint] || calPoints[0];
  const zoom = 4;
  const srcSize = zoomCanvas.width / zoom;  // how many source pixels to show
  const sx = pt.x - srcSize / 2;
  const sy = pt.y - srcSize / 2;

  // Draw magnified camera image (no overlay, raw view)
  zCtx.clearRect(0, 0, zoomCanvas.width, zoomCanvas.height);
  zCtx.drawImage(calImage,
    sx, sy, srcSize, srcSize,
    0, 0, zoomCanvas.width, zoomCanvas.height);

  // Draw the point marker in zoom space
  const cx = zoomCanvas.width / 2;
  const cy = zoomCanvas.height / 2;
  const color = CAL_COLORS[calActivePoint] || '#fff';
  zCtx.beginPath();
  zCtx.arc(cx, cy, CAL_POINT_R * zoom * 0.6, 0, Math.PI * 2);
  zCtx.strokeStyle = color; zCtx.lineWidth = 2; zCtx.stroke();
  zCtx.beginPath();
  zCtx.arc(cx, cy, 2, 0, Math.PI * 2);
  zCtx.fillStyle = color; zCtx.fill();

  // Show label
  const label = document.getElementById('cal-zoom-label');
  if (label) label.textContent = `${CAL_LABELS[calActivePoint]} · 4×`;
}

function calSetupEvents() {
  const canvas = document.getElementById('cal-canvas');
  canvas.onmousedown = (e) => {
    const rect = canvas.getBoundingClientRect();
    const sx = canvas.width / rect.width, sy = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * sx, my = (e.clientY - rect.top) * sy;

    calDragging = -1;
    for (let i = 0; i < calPoints.length; i++) {
      const dx = mx - calPoints[i].x, dy = my - calPoints[i].y;
      if (Math.sqrt(dx * dx + dy * dy) < CAL_POINT_R * 3) { calDragging = i; calActivePoint = i; break; }
    }
  };
  canvas.onmousemove = (e) => {
    if (calDragging < 0) return;
    const rect = canvas.getBoundingClientRect();
    const sx = canvas.width / rect.width, sy = canvas.height / rect.height;
    calPoints[calDragging].x = (e.clientX - rect.left) * sx;
    calPoints[calDragging].y = (e.clientY - rect.top) * sy;
    calDraw();
  };
  canvas.onmouseup = () => { calDragging = -1; };
  canvas.onmouseleave = () => { calDragging = -1; };
  document.onkeydown = (e) => {
    if (currentPage !== 'calibration' || calDragging < 0) return;
    const step = e.shiftKey ? 5 : 1;
    if (e.key === 'ArrowUp') calPoints[calDragging].y -= step;
    if (e.key === 'ArrowDown') calPoints[calDragging].y += step;
    if (e.key === 'ArrowLeft') calPoints[calDragging].x -= step;
    if (e.key === 'ArrowRight') calPoints[calDragging].x += step;
    e.preventDefault(); calDraw();
  };
}

function updateCalOpacity(val) { calOpacity = val / 100; calDraw(); }

function resetCalPoints() {
  if (!calImage) return;
  const cx = calImage.width / 2, cy = calImage.height / 2;
  const ro = Math.min(calImage.width, calImage.height) * 0.40;
  const ri = ro * (107.0 / 170.0);
  const angs = [81, 171, 261, 351].map(a => a * Math.PI / 180);
  calPoints = [
    ...angs.map(a => ({ x: cx + ro * Math.cos(a), y: cy - ro * Math.sin(a) })),
    ...angs.map(a => ({ x: cx + ri * Math.cos(a), y: cy - ri * Math.sin(a) })),
  ];
  calDraw();
}

async function acceptCalibration() {
  if (calPoints.length !== 4 && calPoints.length !== 8) {
    showCalibrationNotice('Place all calibration points (4 or 8) before accepting.', 'warn');
    return;
  }
  const points = calPoints.map(p => [p.x, p.y]);
  const frameW = calImage ? calImage.width : null;
  const frameH = calImage ? calImage.height : null;
  try {
    const res = await fetch('/api/cal/accept', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cam_id: calCamId, points, frame_width: frameW, frame_height: frameH }),
    });
    const data = await res.json();
    if (data.ok) {
      addLog(`Calibration accepted for Camera ${calCamId + 1} (${data.n_points || points.length} points)`);
      if (calCamId < 2) {
        calSelectCam(calCamId + 1);
      } else {
        showPage('home');
        showProfileModal();
      }
    } else { showCalibrationNotice('Calibration failed: ' + (data.error || 'Unknown'), 'error'); }
  } catch (err) { showCalibrationNotice('Calibration failed: ' + err.message, 'error'); }
}

function showProfileModal() {
  const modal = document.getElementById('profile-modal');
  const input = document.getElementById('profile-modal-name');
  if (modal) { modal.style.display = 'flex'; }
  if (input) { input.value = ''; setTimeout(() => input.focus(), 100); }
}

async function closeProfileModal(save) {
  const modal = document.getElementById('profile-modal');
  if (!save) {
    if (modal) modal.style.display = 'none';
    if (socket) socket.emit('close_cameras');
    return;
  }

  const input = document.getElementById('profile-modal-name');
  const saveBtn = document.getElementById('profile-modal-save-btn');
  const skipBtn = document.getElementById('profile-modal-skip-btn');
  const name = (input && input.value.trim()) || 'default';
  if (saveBtn) saveBtn.disabled = true;
  if (skipBtn) skipBtn.disabled = true;

  const ok = await registerBoard({
    name,
    camId: calCamId,
    btnEl: saveBtn,
  });

  if (saveBtn) saveBtn.disabled = false;
  if (skipBtn) skipBtn.disabled = false;
  if (!ok) return;

  if (modal) modal.style.display = 'none';
  if (socket) socket.emit('close_cameras');
}

function requestRecalibrate() { launchCalibration(); }

async function autoCalibrate() {
  const btn = document.getElementById('btn-auto-cal');
  const orig = btn.textContent;
  btn.textContent = '⏳ Detecting…'; btn.disabled = true;
  try {
    const res = await fetch(`/api/cal/auto/${calCamId}`);
    const { data, raw } = await readJsonSafe(res);

    if (!data) {
      const snippet = raw ? raw.slice(0, 140).replace(/\s+/g, ' ') : res.statusText;
      showCalibrationNotice(`Auto-calibration error: ${snippet}`, 'error', 6500);
      btn.textContent = '✗ Failed';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = orig; }, 2000);
      return;
    }

    // ── New response format: {success, H, rings_found, reprojection_error, preview_b64}
    // The server has already committed the homography — just refresh the frame.
    if (data.success === true) {
      const nRings = data.rings_found || 0;
      const err    = data.reprojection_error != null ? data.reprojection_error.toFixed(1) : '?';
      addLog(`Auto-calibrate Cam ${calCamId + 1}: ${nRings} rings, reproj=${err}px — calibration committed`);
      btn.textContent = `✓ ${nRings} rings`;

      // Show preview image if provided
      if (data.preview_b64) {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.82);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;cursor:pointer';
        const imgEl = document.createElement('img');
        imgEl.src = 'data:image/jpeg;base64,' + data.preview_b64;
        imgEl.style.cssText = 'max-width:90vw;max-height:72vh;border-radius:8px';
        const cap = document.createElement('p');
        cap.style.cssText = 'color:#e5e7eb;font-size:14px;margin:0';
        cap.textContent = `✓ ${nRings} rings detected · reproj ${err}px · Click to dismiss`;
        overlay.append(imgEl, cap);
        overlay.addEventListener('click', () => overlay.remove());
        document.body.appendChild(overlay);
        setTimeout(() => overlay.remove(), 6000);
      }

      // Reload the calibration frame (homography is now live)
      await new Promise(r => setTimeout(r, 400));
      await calCaptureFrame(3, /*keepPoints=*/false);

    // ── Legacy response format: {ok, points, method, n_points}
    // (board-profile feature matching still uses this path)
    } else if (data.ok && data.points) {
      const np = data.n_points || data.points.length;
      if (calMode === 8 && np === 4 && calPoints.length === 8) {
        const outer = data.points.map(p => ({ x: p[0], y: p[1] }));
        calPoints = [...outer, ...calPoints.slice(4)];
      } else {
        calPoints = data.points.map(p => ({ x: p[0], y: p[1] }));
        calMode = np;
        calSyncModeUI();
      }
      calActivePoint = 0;
      calDraw();
      addLog(`Auto-calibrate Cam ${calCamId + 1}: ${np} points detected (${data.method})`);
      btn.textContent = `✓ ${np} pts`;

    // ── Failure path
    } else {
      const reason = data.reason || data.error || 'Auto-detection failed';
      const rings  = data.rings_found != null ? ` (${data.rings_found} rings found)` : '';
      showCalibrationNotice(`Auto-calibration failed: ${reason}${rings}`, 'warn', 5200);
      addLog(`Auto-calibrate Cam ${calCamId + 1}: failed — ${reason}${rings}`);
      btn.textContent = '✗ Failed';
    }
  } catch (err) {
    showCalibrationNotice('Auto-calibration error: ' + err.message, 'error', 5200);
    btn.textContent = '✗ Failed';
  }
  btn.disabled = false;
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

async function calAutoRefine() {
  if (!calPoints || calPoints.length < 4) {
    showCalibrationNotice('Place at least 4 rough calibration points first, then click Refine.', 'warn');
    return;
  }
  const btn = document.getElementById('btn-refine-cal');
  const orig = btn ? btn.textContent : '🎯 Refine';
  if (btn) { btn.textContent = '⏳ Detecting rings…'; btn.disabled = true; }

  try {
    const pts = calPoints.map(p => [p.x, p.y]);
    const res = await fetch(`/api/cal/refine/${calCamId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pts }),
    });

    if (!res.ok) {
      const parsed = await readJsonSafe(res);
      const err = parsed.data || {};
      const fallback = parsed.raw ? parsed.raw.slice(0, 140).replace(/\s+/g, ' ') : res.statusText;
      const msg = 'Ring detection failed: ' + (err.error || fallback || res.statusText);
      showCalibrationNotice(msg, 'warn', 5200);
      addLog(`Refine Cam ${calCamId + 1}: ${msg}`);
      if (btn) btn.textContent = '✗ Failed';
      setTimeout(() => { if (btn) btn.textContent = orig; }, 2000);
      if (btn) btn.disabled = false;
      return;
    }

    const nRings    = parseInt(res.headers.get('X-Refine-Rings')    || '0');
    const accepted  = res.headers.get('X-Refine-Accepted') === '1';

    // Show annotated warp visualisation as a brief dismissible overlay
    const blob   = await res.blob();
    const imgUrl = URL.createObjectURL(blob);
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.8);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;cursor:pointer';
    const imgEl = document.createElement('img');
    imgEl.src = imgUrl;
    imgEl.style.cssText = 'max-width:90vw;max-height:75vh;border-radius:8px';
    const cap = document.createElement('p');
    cap.style.cssText = 'color:#e5e7eb;font-size:14px;margin:0';
    if (accepted) {
      cap.textContent = `✓ ${nRings} ring(s) detected — calibration refined and applied! Click to dismiss.`;
    } else {
      cap.textContent = `⚠ ${nRings} ring(s) detected but refinement could not be committed. Check lighting. Click to dismiss.`;
    }
    overlay.append(imgEl, cap);
    overlay.addEventListener('click', () => overlay.remove());
    document.body.appendChild(overlay);
    setTimeout(() => overlay.remove(), 6000);

    if (accepted) {
      // Server has already updated + saved the calibration.
      // Reload the frame keeping current rough points so the user can see result.
      addLog(`🎯 Refine Cam ${calCamId + 1}: ${nRings} rings detected — calibration committed`);
      if (btn) btn.textContent = `✓ ${nRings} rings`;
      // Brief delay to let server settle, then refresh frame
      await new Promise(r => setTimeout(r, 600));
      await calCaptureFrame(3, /*keepPoints=*/true);
    } else {
      addLog(`🎯 Refine Cam ${calCamId + 1}: ${nRings} rings detected (not committed — check lighting)`);
      if (btn) btn.textContent = nRings > 0 ? `⚠ ${nRings} rings` : '✗ No rings';
    }
  } catch (err) {
    showCalibrationNotice('Refine error: ' + err.message, 'error', 5200);
    if (btn) btn.textContent = '✗ Error';
  }

  if (btn) btn.disabled = false;
  setTimeout(() => { if (btn) btn.textContent = orig; }, 3000);
}

/**
 * Rotate all 4 calibration points by one dartboard segment (18°).
 * Uses the homography to rotate in BOARD SPACE (perspective-correct)
 * then transforms back to camera space.
 * direction: -1 = counter-clockwise (left), +1 = clockwise (right)
 */
function calRotatePoints(direction) {
  if (calPoints.length !== 4 && calPoints.length !== 8) return;

  // Board space setup (same as drawPerspectiveWireframe)
  const BS = 500, bCx = BS / 2, bCy = BS / 2;
  const sc = BS / BOARD_CANVAS_MM;

  // Current board-space destination points
  const boardPts = BOARD_DST_WIRE_ANGLES.map(a => {
    const rad = a * Math.PI / 180;
    return {
      x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad),
      y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad),
    };
  });

  const outerCamPts = calPoints.slice(0, 4).map(p => ({ x: p.x, y: p.y }));
  const H = computeHomography(boardPts, outerCamPts);
  if (!H) return;

  // Compute NEW board-space positions (shifted by one 18° segment)
  const shift = direction * BOARD_SECTOR_ANGLE;
  const newOuter = BOARD_DST_WIRE_ANGLES.map(a => {
    const rad = (a + shift) * Math.PI / 180;
    const bp = { x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad), y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad) };
    const cp = applyH(H, bp.x, bp.y);
    return { x: cp.x, y: cp.y };
  });

  if (calPoints.length === 8) {
    // Also rotate inner triple-ring pts by the same board-space shift
    const newInner = BOARD_DST_WIRE_ANGLES.map(a => {
      const rad = (a + shift) * Math.PI / 180;
      const bp = { x: bCx + BOARD_RADII_MM.triple_outer * sc * Math.cos(rad), y: bCy - BOARD_RADII_MM.triple_outer * sc * Math.sin(rad) };
      const cp = applyH(H, bp.x, bp.y);
      return { x: cp.x, y: cp.y };
    });
    calPoints = [...newOuter, ...newInner];
  } else {
    calPoints = newOuter;
  }

  calDraw();
}

// ── Calibration board geometry & homography math ──────────────────────
// (These constants and helpers are shared between calRotatePoints() above
//  and drawPerspectiveWireframe() below — they are NOT annotation-specific.)

const BOARD_SECTOR_ORDER = [20, 5, 12, 9, 14, 11, 8, 16, 7, 19, 3, 17, 2, 15, 10, 6, 13, 4, 18, 1];
const BOARD_SECTOR_ANGLE = 18;  // degrees
const BOARD_RADII_MM = {
  bull_inner: 6.35, bull_outer: 15.9,
  triple_inner: 99, triple_outer: 107,
  double_inner: 162, double_outer: 170,
};
const BOARD_CANVAS_MM = 451;
const BOARD_DST_WIRE_ANGLES = [81, 171, 261, 351]; // D20/D1, D11/D14, D3/D19, D6/D10

function boardSectorBoundaryAngles() {
  const start = 90 - BOARD_SECTOR_ANGLE / 2;
  return Array.from({ length: 20 }, (_, i) => (start + i * BOARD_SECTOR_ANGLE) % 360);
}

function _solveLinear8(A, b) {
  // Gaussian elimination with partial pivoting for 8×8 system
  const n = 8;
  const aug = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let maxR = col;
    for (let r = col + 1; r < n; r++)
      if (Math.abs(aug[r][col]) > Math.abs(aug[maxR][col])) maxR = r;
    [aug[col], aug[maxR]] = [aug[maxR], aug[col]];
    if (Math.abs(aug[col][col]) < 1e-12) return null;
    for (let r = col + 1; r < n; r++) {
      const f = aug[r][col] / aug[col][col];
      for (let j = col; j <= n; j++) aug[r][j] -= f * aug[col][j];
    }
  }
  const x = new Array(n);
  for (let r = n - 1; r >= 0; r--) {
    x[r] = aug[r][n];
    for (let c = r + 1; c < n; c++) x[r] -= aug[r][c] * x[c];
    x[r] /= aug[r][r];
  }
  return x;
}

function computeHomography(src, dst) {
  // src/dst: 4× {x,y}.  Returns 3×3 matrix mapping src→dst.
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const { x: sx, y: sy } = src[i], { x: dx, y: dy } = dst[i];
    A.push([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy]);
    b.push(dx);
    A.push([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy]);
    b.push(dy);
  }
  const h = _solveLinear8(A, b);
  if (!h) return null;
  return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
}

function applyH(H, px, py) {
  const w = H[2][0] * px + H[2][1] * py + H[2][2];
  return {
    x: (H[0][0] * px + H[0][1] * py + H[0][2]) / w,
    y: (H[1][0] * px + H[1][1] * py + H[1][2]) / w,
  };
}

function drawPerspectiveWireframe(ctx) {
  const BS = 500, bCx = BS / 2, bCy = BS / 2;
  const sc = BS / BOARD_CANVAS_MM;

  // In 8-pt mode use all 8 correspondences for a more accurate perspective.
  // We build the homography from board-space → camera-space.
  let H = null;
  if (calMode === 8 && calPoints.length === 8) {
    // Build board-space points: 4 outer double + 4 outer triple
    const tripleInnerPts = BOARD_DST_WIRE_ANGLES.map(a => {
      const rad = a * Math.PI / 180;
      return {
        x: bCx + BOARD_RADII_MM.triple_outer * sc * Math.cos(rad),
        y: bCy - BOARD_RADII_MM.triple_outer * sc * Math.sin(rad),
      };
    });
    const outerPts = BOARD_DST_WIRE_ANGLES.map(a => {
      const rad = a * Math.PI / 180;
      return {
        x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad),
        y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad),
      };
    });
    // Use least-squares over all 8 pairs: pick 4 best-spread pairs to keep
    // the 4x4 DLT stable, then refine with the inner 4 for a better fit.
    // Simplest robust approach: use outer 4 for the base H, then use all 8
    // as a secondary refinement via average correction.
    const H4 = computeHomography(outerPts, calPoints.slice(0, 4).map(p => ({ x: p.x, y: p.y })));
    const H4i = computeHomography(tripleInnerPts, calPoints.slice(4, 8).map(p => ({ x: p.x, y: p.y })));
    if (H4 && H4i) {
      // Blend: average the two homographies (works well when both are valid)
      H = H4.map((row, r) => row.map((v, c) => (v + H4i[r][c]) / 2));
      // Re-normalise so H[2][2] = 1
      const s = H[2][2];
      H = H.map(row => row.map(v => v / s));
    } else {
      H = H4;
    }
  } else {
    const boardPts = BOARD_DST_WIRE_ANGLES.map(a => {
      const rad = a * Math.PI / 180;
      return {
        x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad),
        y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad),
      };
    });
    H = computeHomography(boardPts, calPoints.slice(0, 4).map(p => ({ x: p.x, y: p.y })));
  }
  if (!H) return;

  function boardToCamera(angleDeg, rMM) {
    const rad = angleDeg * Math.PI / 180;
    const bx = bCx + rMM * sc * Math.cos(rad);
    const by = bCy - rMM * sc * Math.sin(rad);
    return applyH(H, bx, by);
  }

  ctx.strokeStyle = 'rgba(0, 255, 255, 0.7)';
  ctx.lineWidth = 1;

  // Rings
  const ringRadii = [
    BOARD_RADII_MM.bull_inner, BOARD_RADII_MM.bull_outer,
    BOARD_RADII_MM.triple_inner, BOARD_RADII_MM.triple_outer,
    BOARD_RADII_MM.double_inner, BOARD_RADII_MM.double_outer,
  ];
  for (const rMM of ringRadii) {
    ctx.beginPath();
    for (let deg = 0; deg <= 360; deg += 3) {
      const cp = boardToCamera(deg, rMM);
      if (deg === 0) ctx.moveTo(cp.x, cp.y);
      else ctx.lineTo(cp.x, cp.y);
    }
    ctx.closePath();
    ctx.stroke();
  }

  // In 8-pt mode: highlight the triple ring band in orange as an alignment
  // reference for the 4 inner (orange) calibration points
  if (calMode === 8) {
    ctx.strokeStyle = 'rgba(255, 140, 0, 0.85)';
    ctx.lineWidth = 2.5;
    for (const rMM of [BOARD_RADII_MM.triple_inner, BOARD_RADII_MM.triple_outer]) {
      ctx.beginPath();
      for (let deg = 0; deg <= 360; deg += 3) {
        const cp = boardToCamera(deg, rMM);
        if (deg === 0) ctx.moveTo(cp.x, cp.y);
        else ctx.lineTo(cp.x, cp.y);
      }
      ctx.closePath();
      ctx.stroke();
    }
  }

  // Restore cyan for sector wires
  ctx.strokeStyle = 'rgba(0, 255, 255, 0.7)';
  ctx.lineWidth = 1;

  // Sector wires
  const angles = boardSectorBoundaryAngles();
  for (const ang of angles) {
    const cp1 = boardToCamera(ang, BOARD_RADII_MM.bull_outer);
    const cp2 = boardToCamera(ang, BOARD_RADII_MM.double_outer);
    ctx.beginPath();
    ctx.moveTo(cp1.x, cp1.y);
    ctx.lineTo(cp2.x, cp2.y);
    ctx.stroke();
  }

  // Sector numbers
  ctx.font = 'bold 11px Inter, sans-serif';
  ctx.fillStyle = 'rgba(0, 255, 255, 0.9)';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const numR = BOARD_RADII_MM.double_outer + 14;
  BOARD_SECTOR_ORDER.forEach((num, i) => {
    const midAng = angles[i] + BOARD_SECTOR_ANGLE / 2;
    const cp = boardToCamera(midAng, numR);
    ctx.fillText(String(num), cp.x, cp.y);
  });
}


// ══════════════════════════════════════════════════════════════════════
// Practice Navigation
// ══════════════════════════════════════════════════════════════════════
// Shared system-check flag — once passed in either mode, skips for the other
let _systemChecked = false;

async function launchGame() {
  if (!socket || !socket.connected) {
    const m = document.getElementById('offline-modal');
    if (m) {
      m.querySelector('p').innerHTML =
        'Cannot start game mode — the server is not connected.<br>Start the server and try again.';
      m.style.display = 'flex';
    }
    return;
  }

  // Skip system-check overlay on subsequent launches this session
  if (_systemChecked) {
    const homePage = document.getElementById('page-home');
    homePage.classList.add('transitioning');
    $homeBoard.classList.remove('spinning');
    $homeBoard.classList.add('launch-practice');
    setTimeout(() => { showPage('game-select'); }, 700);
    return;
  }

  // Show game-loading overlay, hide home
  const overlay = document.getElementById('game-loading');
  overlay.classList.remove('fade-out');
  overlay.style.display = 'flex';
  document.getElementById('page-home').classList.remove('active');

  // Reset all steps
  const steps = ['gs-connection', 'gs-cameras', 'gs-calibration', 'gs-profile', 'gs-detection'];
  steps.forEach(id => {
    const el = document.getElementById(id);
    el.className = 'loading-step';
    el.querySelector('.ls-icon').textContent = '⏳';
  });
  const statusEl = document.getElementById('game-loading-status');

  function setStep(id, state, icon) {
    const el = document.getElementById(id);
    el.className = 'loading-step ' + state;
    el.querySelector('.ls-icon').textContent = icon;
  }

  const failures = [];

  try {
    // 1. Connection
    setStep('gs-connection', 'active', '🔄');
    statusEl.textContent = 'Checking server connection…';
    await new Promise(r => setTimeout(r, 300));
    if (socket && socket.connected) {
      setStep('gs-connection', 'done', '✓');
    } else {
      setStep('gs-connection', 'fail', '✗');
      statusEl.textContent = 'Connection failed';
      await new Promise(r => setTimeout(r, 2000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    // 2. Cameras
    setStep('gs-cameras', 'active', '🔄');
    statusEl.textContent = 'Checking cameras…';
    let numCamsRequired = 3;
    let activeCams = 0;
    try {
      const camRes = await fetch('/api/cameras/probe');
      const camData = await camRes.json();
      numCamsRequired = camData.total || 3;
      activeCams = camData.connected || 0;
    } catch (e) {}
    if (activeCams >= numCamsRequired) {
      setStep('gs-cameras', 'done', '✓');
    } else {
      setStep('gs-cameras', 'fail', '✗');
      failures.push(`Only ${activeCams}/${numCamsRequired} cameras connected — plug in all cameras and try again.`);
    }

    // 3. Calibration
    setStep('gs-calibration', 'active', '🔄');
    statusEl.textContent = 'Verifying calibration…';
    await new Promise(r => setTimeout(r, 400));
    let calCount = 0;
    for (let i = 0; i < 3; i++) {
      try {
        const r = await fetch(`/api/cal/info/${i}`);
        const d = await r.json();
        if (d.calibrated) calCount++;
      } catch (e) {}
    }
    if (calCount === 3) {
      setStep('gs-calibration', 'done', '✓');
    } else {
      setStep('gs-calibration', 'fail', '✗');
      failures.push(`Only ${calCount}/3 cameras calibrated — run Calibration for all cameras first.`);
    }

    // 4. Board profile
    setStep('gs-profile', 'active', '🔄');
    statusEl.textContent = 'Loading board profile…';
    await new Promise(r => setTimeout(r, 300));
    try {
      const pRes = await fetch('/api/board/status');
      const pData = await pRes.json();
      if (pData.registered) {
        setStep('gs-profile', 'done', '✓');
      } else {
        setStep('gs-profile', 'fail', '✗');
        failures.push('No board profile saved — calibrate and save a board profile first.');
      }
    } catch (e) {
      setStep('gs-profile', 'fail', '✗');
      failures.push('Could not load board profile — server error.');
    }

    // 5. Detection engine
    setStep('gs-detection', 'active', '🔄');
    statusEl.textContent = 'Starting detection engine…';
    await new Promise(r => setTimeout(r, 500));
    setStep('gs-detection', 'done', '✓');

    // Block if any critical failures
    if (failures.length > 0) {
      statusEl.innerHTML = failures.map(f => '❌ ' + f).join('<br>');
      await new Promise(r => setTimeout(r, 4000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    statusEl.textContent = 'Ready!';
    _systemChecked = true;
    await new Promise(r => setTimeout(r, 600));

  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    await new Promise(r => setTimeout(r, 2000));
    overlay.classList.add('fade-out');
    setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
    return;
  }

  overlay.classList.add('fade-out');
  setTimeout(() => {
    overlay.style.display = 'none';
    showPage('game-select');
  }, 500);
}



function openStats() {
  if (!socket || !socket.connected) {
    const m = document.getElementById('offline-modal');
    if (m) {
      m.querySelector('p').innerHTML =
        'Cannot view stats — the server is not connected.<br>Start the server and try again.';
      m.style.display = 'flex';
    }
    return;
  }
  showPage('stats');
}

function quitApp() {
  window.close();
}


async function launchPractice() {

  if (!socket || !socket.connected) {
    addLog('⚠ Cannot start practice — system is offline');
    const m = document.getElementById('offline-modal');
    if (m) m.style.display = 'flex';
    return;
  }

  // Skip loading screen if already loaded once this session
  if (_systemChecked) {
    const homePage = document.getElementById('page-home');
    homePage.classList.add('transitioning');
    $homeBoard.classList.remove('spinning');
    $homeBoard.classList.add('launch-practice');
    _resetPracticeState();
    setTimeout(() => {
      showPage('practice');
      const pb = document.getElementById('practice-board');
      pb.classList.add('board-entrance');
      setTimeout(() => pb.classList.remove('board-entrance'), 1200);
    }, 700);
    return;
  }

  // Show loading screen and hide homepage immediately
  const overlay = document.getElementById('practice-loading');
  overlay.classList.remove('fade-out');
  overlay.style.display = 'flex';
  // Hide home page so it's not visible behind overlay
  document.getElementById('page-home').classList.remove('active');

  // Reset all steps
  const steps = ['ls-connection', 'ls-cameras', 'ls-calibration', 'ls-profile', 'ls-detection'];
  steps.forEach(id => {
    const el = document.getElementById(id);
    el.className = 'loading-step';
    el.querySelector('.ls-icon').textContent = '⏳';
  });
  const statusEl = document.getElementById('loading-status');

  function setStep(id, state, icon) {
    const el = document.getElementById(id);
    el.className = 'loading-step ' + state;
    el.querySelector('.ls-icon').textContent = icon;
  }

  const failures = [];

  try {
    // 1. Connection check
    setStep('ls-connection', 'active', '🔄');
    statusEl.textContent = 'Checking server connection…';
    await new Promise(r => setTimeout(r, 300));
    if (socket && socket.connected) {
      setStep('ls-connection', 'done', '✓');
    } else {
      setStep('ls-connection', 'fail', '✗');
      statusEl.textContent = 'Connection failed';
      await new Promise(r => setTimeout(r, 2000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    // 2. Camera check — verify all required cameras are connected
    setStep('ls-cameras', 'active', '🔄');
    statusEl.textContent = 'Checking cameras…';
    let numCamsRequired = 3;
    let activeCams = 0;
    try {
      const camRes = await fetch('/api/cameras/probe');
      const camData = await camRes.json();
      numCamsRequired = camData.total || 3;
      activeCams = camData.connected || 0;
    } catch (e) {}
    if (activeCams >= numCamsRequired) {
      setStep('ls-cameras', 'done', '✓');
    } else {
      setStep('ls-cameras', 'fail', '✗');
      failures.push(`Only ${activeCams}/${numCamsRequired} cameras connected — plug in all cameras and try again.`);
    }

    // 3. Calibration check
    setStep('ls-calibration', 'active', '🔄');
    statusEl.textContent = 'Verifying calibration…';
    await new Promise(r => setTimeout(r, 400));
    let calCount = 0;
    for (let i = 0; i < 3; i++) {
      try {
        const r = await fetch(`/api/cal/info/${i}`);
        const d = await r.json();
        if (d.calibrated) calCount++;
      } catch (e) {}
    }
    if (calCount === 3) {
      setStep('ls-calibration', 'done', '✓');
    } else {
      setStep('ls-calibration', 'fail', '✗');
      failures.push(`Only ${calCount}/3 cameras calibrated — run Calibration for all cameras first.`);
    }

    // 4. Board profile
    setStep('ls-profile', 'active', '🔄');
    statusEl.textContent = 'Loading board profile…';
    await new Promise(r => setTimeout(r, 300));
    try {
      const pRes = await fetch('/api/board/status');
      const pData = await pRes.json();
      if (pData.registered) {
        setStep('ls-profile', 'done', '✓');
      } else {
        setStep('ls-profile', 'fail', '✗');
        failures.push('No board profile saved — calibrate and save a board profile first.');
      }
    } catch (e) {
      setStep('ls-profile', 'fail', '✗');
      failures.push('Could not load board profile — server error.');
    }

    // 5. Detection engine
    setStep('ls-detection', 'active', '🔄');
    statusEl.textContent = 'Starting detection engine…';
    await new Promise(r => setTimeout(r, 500));
    setStep('ls-detection', 'done', '✓');

    // Block if any critical failures
    if (failures.length > 0) {
      statusEl.innerHTML = failures.map(f => '❌ ' + f).join('<br>');
      await new Promise(r => setTimeout(r, 4000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    // All done
    statusEl.textContent = 'Ready!';
    _systemChecked = true;
    await new Promise(r => setTimeout(r, 600));

  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    await new Promise(r => setTimeout(r, 2000));
    overlay.classList.add('fade-out');
    setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
    return;
  }


  // Fade out loading screen and go straight to practice (no board animation on first load)
  overlay.classList.add('fade-out');

  _resetPracticeState();
  setTimeout(() => {
    overlay.style.display = 'none';
    showPage('practice');
  }, 500);
}

// Keyboard shortcuts & navigation
const _homeBtns = ['launchCalibration', 'launchPractice', 'launchGame', 'showStats', 'showSettings'];
let _homeFocus = -1;
const _gameModes = ['x01', 'cricket', 'countup'];
let _gameCardFocus = -1;

function _highlightHomeBtn(idx) {
  document.querySelectorAll('#page-home .home-actions .btn').forEach((b, i) => {
    b.classList.toggle('kb-focus', i === idx);
  });
}

function _highlightGameCard(idx) {
  _gameCardFocus = idx;
  if (idx >= 0 && idx < _gameModes.length) {
    selectGameMode(_gameModes[idx]);
  }
}

document.addEventListener('keydown', (e) => {
  if (isConfirmModalOpen()) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeConfirmModal(false);
    }
    return;
  }

  // Ignore if user is typing in an input/select
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

  // ── HOME page ──
  if (currentPage === 'home') {
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault();
      _homeFocus = Math.min(_homeFocus + 1, _homeBtns.length - 1);
      _highlightHomeBtn(_homeFocus);
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault();
      _homeFocus = Math.max(_homeFocus - 1, 0);
      _highlightHomeBtn(_homeFocus);
    } else if (e.key === 'Enter' && _homeFocus >= 0) {
      e.preventDefault();
      const actions = [launchCalibration, launchPractice, launchGame, openStats, () => showPage('settings')];
      actions[_homeFocus]();
    }
    // Letter shortcuts (existing)
    if (e.key === 'c' || e.key === 'C') launchCalibration();
    if (e.key === 'p' || e.key === 'P') launchPractice();
    if (e.key === 's' || e.key === 'S') showPage('settings');
    if (e.key === 'g' || e.key === 'G') launchGame();
    if (e.key === 't' || e.key === 'T') openStats();
    return;
  }

  // ── GAME SELECT page ──
  if (currentPage === 'game-select') {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      _gameCardFocus = Math.min((_gameCardFocus < 0 ? 0 : _gameCardFocus + 1), _gameModes.length - 1);
      _highlightGameCard(_gameCardFocus);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      _gameCardFocus = Math.max((_gameCardFocus < 0 ? 0 : _gameCardFocus - 1), 0);
      _highlightGameCard(_gameCardFocus);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (_selectedGameMode) startGame();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      showPage('home');
    }
    return;
  }

  // ── GAME page (bullseye + active game) ──
  if (currentPage === 'game') {
    if (e.key === 'Escape') {
      e.preventDefault();
      endGame();
    } else if (e.key === 'z' || e.key === 'Z') {
      undoGameDart();
    }
    return;
  }

  // ── STATS page ──
  if (currentPage === 'stats') {
    if (e.key === 'Escape') {
      e.preventDefault();
      showPage('home');
    }
    return;
  }

  // ── MATCH REVIEW page ──
  if (currentPage === 'match-review') {
    if (e.key === 'Escape') {
      e.preventDefault();
      const lb = document.getElementById('mr-lightbox');
      if (lb && lb.style.display !== 'none' && lb.offsetParent !== null) {
        if (typeof closeMatchReviewLightbox === 'function') closeMatchReviewLightbox();
        else lb.style.display = 'none';
      } else {
        showPage('stats');
      }
    }
    return;
  }

  // ── SETTINGS page ──
  if (currentPage === 'settings') {
    if (e.key === 'Escape') {
      e.preventDefault();
      showPage('home');
    }
    return;
  }

  // ── PRACTICE page ──
  if (currentPage === 'practice') {
    if (e.key === 'Escape') {
      e.preventDefault();
      leavePractice();
    }
    return;
  }

  // ── CALIBRATION page ──
  if (currentPage === 'calibration') {
    if (e.key === 'Escape') {
      e.preventDefault();
      showPage('home');
    }
    return;
  }
});

// ══════════════════════════════════════════════════════════════════════
// Socket.IO
// ══════════════════════════════════════════════════════════════════════

let socket = null, practiceActive = false;
let throwData = [], totalScore = 0, throwCount = 0;

function connectSocket() {
  socket = io({ reconnection: true, reconnectionDelay: 1000 });
  socket.on('connect', () => {
    document.getElementById('badge-online').innerHTML = '<span class="dot dot-green"></span> Online';
    $homeBoard.classList.add('spinning');
    updateCamDots({});
    // Load server settings and sync UI
    loadServerSettings();
  });
  socket.on('disconnect', () => {
    document.getElementById('badge-online').innerHTML = '<span class="dot dot-gray"></span> Offline';
    $homeBoard.classList.remove('spinning');
    [0, 1, 2].forEach(i => {
      const d = document.getElementById('cam-dot-' + i); if (d) d.className = 'bar-dot dot-off';
      const fb = document.getElementById('cam-fps-' + i); if (fb) { fb.textContent = ''; fb.classList.remove('visible'); }
    });
    const calOff = document.getElementById('cal-offline-overlay');
    if (calOff) calOff.style.display = 'flex';
    const warpBox = document.getElementById('cal-warp-box');
    if (warpBox) warpBox.style.display = 'none';
    const zoomBox = document.getElementById('cal-zoom-box');
    if (zoomBox) zoomBox.style.display = 'none';
    stopCamPreview();

    // If user is outside home, show toast and redirect back
    if (currentPage !== 'home') {
      const toast = document.getElementById('offline-toast');
      const toastMsg = document.getElementById('offline-toast-msg');
      if (toast) {
        if (currentPage === 'game' || currentPage === 'game-select') {
          if (toastMsg) toastMsg.textContent = 'Server disconnected — game mode ended. Returning to home…';
        } else if (currentPage === 'stats') {
          if (toastMsg) toastMsg.textContent = 'Server disconnected — returning to home…';
        } else {
          if (toastMsg) toastMsg.textContent = 'Server disconnected — returning to home…';
        }
        toast.style.display = 'flex';
      }
      setTimeout(() => {
        // Reset practice state
        if (practiceActive) {
          practiceActive = false;
          _systemChecked = false;
          _camerasKnownOpen = false;
          _setPracticeBoardStatic({ clearStream: true });
          _setPracticeFinishTurnVisible(false);
          const btn = document.getElementById('btn-practice-toggle');
          if (btn) { btn.textContent = 'Start'; btn.classList.remove('btn-reset'); btn.classList.add('btn-primary'); }
          const dbg = document.getElementById('toggle-debug');
          if (dbg) { dbg.checked = false; dbg.disabled = true; toggleDebugCams(); }
        }
        // Reset game state — requires re-check on next launch
        if (_gameMode) { _gameMode = null; }
        _systemChecked = false;
        setStatus('idle', 'Idle');
        showPage('home');
        if (toast) { toast.style.display = 'none'; }
      }, 2500);
    }
  });

  socket.on('dart_scored', onDartScored);
  socket.on('cam_status', onCamStatus);
  socket.on('srv_status', (data) => {
    // Server-side phase updates (Opening cameras…, Cameras ready, etc.)
    // Show on ALL pages so the user always sees camera state transitions
    const type = data.type || 'idle';
    const msg  = data.message || '';
    if (type === 'loading') {
      _camerasKnownOpen = false;
      if (practiceActive) _setPracticeBoardStatic();
      setStatus('waiting', msg);
    } else if (type === 'ready') {
      _camerasKnownOpen = true;
      if (practiceActive) _armPracticeWarpFeed();
      setStatus('ready', msg);
    } else {
      setStatus('idle', msg);
    }
  });

  socket.on('state', onState);
  socket.on('takeout', onTakeout);
  socket.on('server_log', (data) => appendDebugLog(data.msg, data.ts));
  socket.on('cameras_state', (data) => {
    _camerasKnownOpen = !!(data && data.open);
    if (_camerasKnownOpen && practiceActive) {
      _armPracticeWarpFeed();
      setStatus('ready', 'Waiting for Throw');
    } else if (!_camerasKnownOpen) {
      _setPracticeBoardStatic({ clearStream: true });
    }
  });

  // ── Game mode events
  socket.on('bullseye_state', onBullseyeState);
  socket.on('bullseye_result', onBullseyeResult);
  socket.on('game_state', onGameState);
  socket.on('game_over', onGameOver);
  socket.on('stats_data', onStatsData);
  socket.on('accuracy_session', (data) => applyPracticeAccuracySession(data));
  socket.on('player_names', (data) => {
    if (data && Array.isArray(data.names)) applyScoreboardPlayerNames(data.names);
  });

  // Clear dart dots from board when turn takeout is confirmed
  socket.on('clear_board_dots', () => {
    clearBoardDots();
    _gameTurnReviewReady = false;
    _syncAccuracyReviewActionButtons();
    // Clear the Remove Darts banner in the game turn info
    const turnInfo = document.getElementById('game-turn-info');
    if (turnInfo) turnInfo.innerHTML = '';
    window._awaitingTakeoutGame = false;
    setStatus('ready', 'Waiting for Throw');
  });
  socket.on('awaiting_takeout', (data) => {
    window._awaitingTakeoutGame = true;
    const prompt = document.getElementById('bullseye-prompt');
    if (prompt) {
      prompt.innerHTML = '🎯 Remove darts from the board';
      prompt.className = 'bullseye-prompt awaiting-takeout';
    }
    setStatus('takeout', 'Remove Darts');
  });

  socket.on('takeout_ready', (data = {}) => {
    const reason = data.reason || 'bullseye';
    if (reason === 'turn_review' && _isX01AccuracyContext()) {
      window._awaitingTakeoutGame = false;
      _gameTurnReviewReady = true;
      void _syncPracticeAccuracyTurnFromServer();
      const turnInfo = document.getElementById('game-turn-info');
      if (turnInfo) {
        turnInfo.innerHTML = '<span style="color:var(--green);font-size:16px;">✅ Darts removed. Review the turn, then continue the match.</span>';
      }
      _syncAccuracyReviewActionButtons();
      setStatus('ready', 'Review Turn');
      openPracticeReviewModal();
      return;
    }
    // Update bullseye prompt (for bullseye→game transition)
    const prompt = document.getElementById('bullseye-prompt');
    if (prompt && prompt.offsetParent !== null) {
      prompt.innerHTML = '✅ Darts removed! <button class="btn btn-sm btn-accent" style="margin-left:12px;font-size:16px;padding:8px 24px;" onclick="skipTakeout()">▶ Continue</button>';
      prompt.className = 'bullseye-prompt awaiting-takeout';
    }
    // Update game turn info (for between-turn transition)
    const turnInfo = document.getElementById('game-turn-info');
    if (turnInfo) {
      turnInfo.innerHTML = '✅ Darts removed! <button class="btn btn-sm btn-accent" style="margin-left:8px;font-size:14px;padding:6px 18px;" onclick="skipTakeout()">▶ Continue</button>';
    }
    setStatus('ready', 'Press Continue');
  });

  // Between-turn takeout: show prompt in game turn info area
  socket.on('turn_takeout', (data) => {
    window._awaitingTakeoutGame = true;
    _gameTurnReviewReady = false;
    const turnInfo = document.getElementById('game-turn-info');
    if (turnInfo) {
      turnInfo.innerHTML = _isX01AccuracyContext()
        ? '<span style="color:var(--yellow);font-size:18px;">🎯 Remove darts from the board to unlock turn review.</span>'
        : '<span style="color:var(--yellow);font-size:18px;">🎯 Remove darts from the board!</span>';
    }
    _syncAccuracyReviewActionButtons();
    setStatus('takeout', 'Remove Darts');
  });

  // Undo during takeout: cancel the Remove Darts prompt and resume the same player
  socket.on('cancel_takeout', () => {
    window._awaitingTakeoutGame = false;
    _gameTurnReviewReady = false;
    _syncAccuracyReviewActionButtons();
    if (currentPage === 'game') closePracticeReviewModal();
    // Restore normal turn-info display — game_state event will update dart count
    const turnInfo = document.getElementById('game-turn-info');
    if (turnInfo) turnInfo.innerHTML = '';
    setStatus('detecting', 'Waiting for Throw');
  });

  // Practice 3-throw takeout events
  // ── Oche distance sensor ───────────────────────────────────────────
  socket.on('distance_update', (data) => {
    const group = document.getElementById('oche-status');
    const label = document.getElementById('oche-label');
    const dot = document.getElementById('oche-dot');
    const banner = document.getElementById('oche-trespass-banner');
    const bannerDist = document.getElementById('oche-trespass-dist');

    // Update live distance display on settings page
    _updateTFLunaLive(data);

    if (!data.connected) {
      if (group) group.style.display = 'none';
      if (banner) banner.style.display = 'none';
      return;
    }
    if (group) group.style.display = '';
    if (label) label.textContent = data.distance_cm + ' cm';

    if (data.is_trespassing) {
      if (dot) { dot.className = 'bar-dot dot-foul'; }
      if (banner && (currentPage === 'game' || currentPage === 'practice')) {
        banner.style.display = '';
        if (bannerDist) bannerDist.textContent = data.distance_cm + ' cm / ' + data.foul_threshold_cm + ' cm';
      }
    } else {
      if (dot) { dot.className = 'bar-dot dot-on'; }
      if (banner) banner.style.display = 'none';
    }
  });

  socket.on('foul_warning', (data) => {
    const msg = 'FOUL — dart voided (' + data.distance_cm + ' cm from board, min ' + data.threshold_cm + ' cm)';
    addPracticeLog('[FOUL] ' + msg);
    // Show as a fixed notification at the bottom-right, not overlapping the board
    _showFoulNotification(msg);
  });

  // ── TF-Luna hotplug auto-detection ─────────────────────────────────
  // NO toast: the sensor's connection state is already visible in the
  // Settings panel (status dot + "Auto-detected on COMx" label). Toasts
  // were firing on every startup because the server emits tfluna_status
  // at least twice during boot (once offline during probe, once online
  // after auto-detect), which no amount of client-side gating could
  // reliably distinguish from a genuine plug event. Drop the toast
  // entirely — real unplug/replug events still update the status dot
  // silently.
  socket.on('tfluna_status', (data) => {
    const enabledEl = document.getElementById('set-tfluna-enabled');
    const portEl = document.getElementById('set-tfluna-port');
    if (enabledEl) enabledEl.checked = !!data.enabled;
    if (portEl && data.port != null) portEl.textContent = data.port;

    const online = !!(data.detected && data.connected);
    if (online) {
      _setTFLunaStatus('tfluna-ok', `Auto-detected on ${data.port}`);
    } else if (!data.detected) {
      _setTFLunaStatus('tfluna-fail', 'Sensor removed');
      _updateTFLunaLive({ connected: false });
    }
  });

  socket.on('practice_awaiting_takeout', (data) => {
    if (!practiceActive) {
      _clearPracticeTransientUi();
      return;
    }
    _practiceResetMode = 'auto';
    _setPracticeTakeoutBannerVisible(true);
    setStatus('takeout', 'Remove Darts — 3 Thrown');
    addPracticeLog('[PRACTICE] 3 darts thrown — remove from board');
  });

  socket.on('practice_reset', (data) => {
    const wasManualReset = _practiceResetMode === 'manual';
    _clearPracticeTransientUi();
    if (!practiceActive) return;
    if (data && data.session_id) {
      _practiceAccuracySession.sessionId = data.session_id;
      _practiceAccuracySession.turnId = data.turn_id || _practiceAccuracySession.turnId;
      _practiceAccuracySession.predictions = [];
      _practiceAccuracySession.actualDarts = [];
      _practiceAccuracySession.lastSavedSignature = '';
    }
    resetTurn({ clearAccuracy: false, clearLog: true, logMessage: null });
    closePracticeReviewModal();
    renderPracticeAccuracyReview();
    if (wasManualReset) {
      setStatus('ready', 'Waiting for Throw');
      addPracticeLog('[PRACTICE] Continue pressed — ready for next throw');
    } else {
      setStatus('ready', 'Board Reset — Throw Again');
      addPracticeLog('[PRACTICE] Board auto-reset after takeout');
      const side = document.querySelector('.prac-score-side');
      if (side) {
        side.classList.remove('reset-flash');
        void side.offsetWidth;
        side.classList.add('reset-flash');
        setTimeout(() => side.classList.remove('reset-flash'), 700);
      }
    }
  });
}

// ══════════════════════════════════════════════════════════════════════
// Event Handlers
// ══════════════════════════════════════════════════════════════════════

function onDartScored(data) {
  // Normalize OFF → MISS: dart outside the board is a miss for scoring purposes
  const label = data.label === 'OFF' ? 'MISS' : data.label;
  const score = data.label === 'OFF' ? 0 : data.score;
  const { x_mm, y_mm } = data;
  const shouldTrackAccuracyPrediction = currentPage === 'practice'
    || (currentPage === 'game' && _isX01AccuracyContext());

  if (shouldTrackAccuracyPrediction) {
    _upsertPracticePrediction({
      ...data,
      label,
      score,
    });
  }

  if (currentPage === 'practice') {
    throwCount++; totalScore += score;
    updateDartCount();
    const scoreEl = document.getElementById('score-current');
    scoreEl.textContent = _formatAccuracyLabel(label);
    scoreEl.classList.toggle('label-bounce', label === 'BOUNCE');
    scoreEl.classList.toggle('label-miss', label === 'MISS');
    scoreEl.classList.toggle('label-foul', label === 'FOUL');
    const ringEl = document.getElementById('score-ring');
    if (ringEl) ringEl.textContent = label === 'FOUL' ? '\u00a0' : getRingName(label);
    document.getElementById('stat-throws').textContent = throwCount;
    document.getElementById('stat-total').textContent = totalScore;
    document.getElementById('stat-avg').textContent =
      throwCount > 0 ? (totalScore / throwCount).toFixed(1) : '–';
    addHistoryRow(label, score);
    setStatus('scored', label + ' = ' + score);
    setTimeout(() => {
      if (window._awaitingTakeoutGame) return;
      const banner = document.getElementById('practice-takeout-banner');
      if (!banner || banner.style.display === 'none' || banner.style.display === '') {
        setStatus('ready', 'Waiting for Throw');
      }
    }, 2000);
  }

  // Game page: update status indicator (otherwise "Detecting..." stays stuck)
  if (currentPage === 'game') {
    setStatus('scored', label + ' = ' + score);
    setTimeout(() => {
      if (window._awaitingTakeoutGame) return;
      setStatus('ready', 'Waiting for Throw');
    }, 2000);
  }

  // Always: dot + log + debug (page-aware dot placement handled inside placeDot)
  placeDot(x_mm, y_mm, label);
  if (currentPage === 'practice') {
    addPracticeLog(`[SCORE] ${label} = ${score} pts (${x_mm.toFixed(1)}, ${y_mm.toFixed(1)})mm`);
  }
  if (data.cam_details) updateDebugCamInfo(data.cam_details);
}

function onCamStatus(data) { updateCamDots(data); }
function onState(data) {
  const s = data.state || '';
  // Skip status overwrite while we're waiting for takeout (game/bullseye/turn)
  if (window._awaitingTakeoutGame) return;
  // Only update the indicator when detection is active (practice started or game mode)
  if (!practiceActive && currentPage !== 'game') return;
  if (s === 'WAIT') setStatus('ready', 'Waiting for Throw');
  else if (s === 'STABLE') setStatus('waiting', 'Detecting...');
  else if (s === 'DART') setStatus('scored', 'Dart Detected');
  else if (s === 'HAND') setStatus('takeout', 'Hand Detected');
  else if (s === 'TAKEOUT') setStatus('takeout', 'Takeout');
}
function onTakeout() {
  if (!practiceActive && currentPage !== 'game') return;
  setStatus('takeout', 'Takeout — Darts Removed');
  if (currentPage === 'practice') addPracticeLog('[DET] Takeout detected');
  // Clear board dots on all SVGs
  clearBoardDots();
  setTimeout(() => { setStatus('ready', 'Waiting for Throw'); }, 2000);
}

// ══════════════════════════════════════════════════════════════════════

function leavePractice() {
  practiceActive = false;
  _camerasKnownOpen = false;
  _setPracticeCameraSelectorVisible(false);
  _clearPracticeTransientUi();
  _setPracticeBoardStatic({ clearStream: true });
  _setPracticeFinishTurnVisible(false);
  const btn = document.getElementById('btn-practice-toggle');
  if (btn) {
    btn.textContent = 'Start';
    btn.classList.remove('btn-reset');
    btn.classList.add('btn-primary');
  }
  if (socket) socket.emit('stop_detection');
  if (socket) socket.emit('close_cameras');
  resetTurn({ clearAccuracy: true, clearLog: true, logMessage: null });
  const dbg = document.getElementById('toggle-debug');
  if (dbg) { dbg.checked = false; dbg.disabled = true; toggleDebugCams(); }
  showPage('home');
}

/** Reset practice runtime counters without touching DOM (safe pre-game). */
function _resetPracticeRuntimeState() {
  throwData = []; throwCount = 0; totalScore = 0;
  practiceActive = false;
  _setPracticeCameraSelectorVisible(false);
  _clearPracticeTransientUi();
  _setPracticeBoardStatic({ clearStream: true });
  _setPracticeFinishTurnVisible(false);
  window._awaitingTakeoutGame = false;
  _resetPracticeAccuracyState();
}

/** Reset all practice-mode state for clean mode transitions. */
function _resetPracticeState() {
  throwData = []; throwCount = 0; totalScore = 0;
  window._awaitingTakeoutGame = false;
  _setPracticeCameraSelectorVisible(practiceActive);
  _clearPracticeTransientUi();
  _setPracticeBoardStatic({ clearStream: true });
  _setPracticeFinishTurnVisible(practiceActive);
  _resetPracticeAccuracyState({ closeModal: true });
  // Reset practice DOM elements (safe to call even when not on practice page)
  const sc = document.getElementById('score-current'); if (sc) sc.textContent = '–';
  const sr = document.getElementById('score-ring'); if (sr) sr.innerHTML = '&nbsp;';
  const st = document.getElementById('stat-throws'); if (st) st.textContent = '0';
  const sa = document.getElementById('stat-avg'); if (sa) sa.textContent = '–';
  const sl = document.getElementById('stat-total'); if (sl) sl.textContent = '0';
  const hl = document.getElementById('history-list'); if (hl) hl.innerHTML = '';
  const dn = document.getElementById('p-dart-n'); if (dn) dn.textContent = '0';
  clearPracticeBoardDots();
}

// ══════════════════════════════════════════════════════════════════════
// Practice Mode
// ══════════════════════════════════════════════════════════════════════

function togglePractice() {
  practiceActive = !practiceActive;
  const btn = document.getElementById('btn-practice-toggle');
  if (practiceActive) {
    _setPracticeCameraSelectorVisible(true);
    _clearPracticeTransientUi();
    _setPracticeFinishTurnVisible(true);
    btn.textContent = 'Stop'; btn.classList.remove('btn-primary'); btn.classList.add('btn-reset');
    if (_camerasKnownOpen) {
      _armPracticeWarpFeed();
      setStatus('ready', 'Waiting for Throw');
    } else {
      _setPracticeBoardStatic({ clearStream: true });
      setStatus('loading', 'Opening cameras…');
    }
    addPracticeLog('Practice started');
    // Enable debug toggle
    const dbg = document.getElementById('toggle-debug');
    if (dbg) dbg.disabled = false;
    if (socket) socket.emit('start_detection');
  } else {
    _setPracticeCameraSelectorVisible(false);
    _clearPracticeTransientUi();
    _setPracticeFinishTurnVisible(false);
    btn.textContent = 'Start'; btn.classList.remove('btn-reset'); btn.classList.add('btn-primary');
    _camerasKnownOpen = false;
    _setPracticeBoardStatic({ clearStream: true });
    closePracticeReviewModal();
    setStatus('stopped', 'Stopped'); addPracticeLog('Practice stopped');
    // Disable debug toggle
    const dbg = document.getElementById('toggle-debug');
    if (dbg) { dbg.checked = false; dbg.disabled = true; toggleDebugCams(); }
    if (socket) socket.emit('stop_detection');
    if (socket) socket.emit('close_cameras');
    // Clear all scores and dots
    document.getElementById('score-current').textContent = '–';
    document.getElementById('score-ring').innerHTML = '&nbsp;';
    document.getElementById('stat-throws').textContent = '0';
    document.getElementById('stat-avg').textContent = '–';
    document.getElementById('stat-total').textContent = '0';
    document.getElementById('history-list').innerHTML = '';
    document.getElementById('practice-log').innerHTML = '';
    document.getElementById('p-dart-n').textContent = '0';
    clearPracticeBoardDots();
    throwData = []; throwCount = 0; totalScore = 0;
    _resetPracticeAccuracyState();
  }
}

function resetTurn(options = {}) {
  const { clearAccuracy = false, clearLog = true, logMessage = 'Session reset' } = options;
  throwData = []; throwCount = 0; totalScore = 0;
  document.getElementById('score-current').textContent = '–';
  document.getElementById('score-ring').innerHTML = '&nbsp;';
  document.getElementById('stat-throws').textContent = '0';
  document.getElementById('stat-avg').textContent = '–';
  document.getElementById('stat-total').textContent = '0';
  document.getElementById('history-list').innerHTML = '';
  if (clearLog) document.getElementById('practice-log').innerHTML = '';
  updateDartCount();
  clearPracticeBoardDots();
  if (clearAccuracy) {
    _resetPracticeAccuracyState({ keepSession: false, keepTurn: false, closeModal: true });
  }
  if (logMessage) addPracticeLog(logMessage);
}

function selectCam(n) {
  activeCam = n;
  _syncPracticeCamButtons();
  if (typeof _debugActiveCam !== 'undefined') _debugActiveCam = n;
  if (practiceActive && _camerasKnownOpen) _armPracticeWarpFeed();
}

async function skipPracticeTakeout() {
  if (!_practiceTakeoutPending) return;
  _practiceResetMode = 'manual';
  _setPracticeTakeoutBannerVisible(false);
  if (practiceActive) setStatus('waiting', 'Resetting Board…');
  await savePracticeAccuracyReview(true);
  if (socket) socket.emit('skip_takeout');
}

function updateDartCount() {
  const el = document.getElementById('p-dart-n');
  if (el) el.textContent = Math.min(throwCount, 3);
}

function _isPracticeAccuracyContext() {
  return _practiceAccuracySession.context === 'practice';
}

function _isX01AccuracyContext() {
  return _practiceAccuracySession.context === 'game'
    && _practiceAccuracySession.sessionMode === 'x01';
}

function _setVisible(el, visible, displayValue = '') {
  if (!el) return;
  el.hidden = !visible;
  el.style.display = visible ? displayValue : 'none';
}

function _setGameReviewButtonVisible(visible) {
  const reviewBtn = document.getElementById('btn-game-review');
  _setVisible(reviewBtn, visible, '');
}

function _x01ReviewTurnStartScore() {
  if (!_lastGameState || _lastGameState.type !== 'x01') return null;
  const currentPlayer = Math.max(1, Number(_lastGameState.current_player || 1));
  const scores = Array.isArray(_lastGameState.scores) ? _lastGameState.scores : [];
  const currentScore = Number(scores[currentPlayer - 1] || 0);
  const liveDarts = Array.isArray(_lastGameState.darts_this_turn) ? _lastGameState.darts_this_turn : [];
  const liveTurnScore = liveDarts.reduce((sum, dart) => sum + (dart && dart.bust ? 0 : Number(dart?.score || 0)), 0);
  return currentScore + liveTurnScore;
}

function _isDoubleCheckoutLabel(label) {
  const normalized = String(label || '').toUpperCase();
  return normalized === 'BULL' || normalized === 'DB' || normalized === 'D25' || normalized.startsWith('D');
}

function _x01ReviewWouldFinishTurn(actualDarts = _practiceAccuracySession.actualDarts) {
  if (_gameTurnReviewReady || window._awaitingTakeoutGame) return false;
  const turnStartScore = _x01ReviewTurnStartScore();
  if (turnStartScore == null) return false;

  const finishRule = String(_lastGameState?.finish_rule || 'straight_out');
  let remaining = turnStartScore;
  let dartsThrown = 0;
  for (const actual of actualDarts.slice(0, 3)) {
    const label = String(actual?.label || '').toUpperCase();
    const score = Number(actual?.score || 0);
    dartsThrown += 1;

    if (label === 'MISS' || label === 'BOUNCE' || label === 'FOUL' || label === 'OFF') {
      if (dartsThrown >= 3) return true;
      continue;
    }

    const nextRemaining = remaining - score;
    let bust = nextRemaining < 0;
    if (finishRule === 'double_out') {
      if (nextRemaining === 1) {
        bust = true;
      } else if (nextRemaining === 0 && !_isDoubleCheckoutLabel(label)) {
        bust = true;
      }
    }

    if (bust) {
      return true;
    }
    remaining = nextRemaining;

    if (nextRemaining === 0) {
      return true;
    }

    if (dartsThrown >= 3) {
      return true;
    }
  }

  return false;
}

function _currentX01ReviewAction() {
  if (currentPage !== 'game' || !_isX01AccuracyContext()) return null;
  if (_gameTurnReviewReady) return 'continue_turn';
  return _x01ReviewWouldFinishTurn() ? 'finish_turn' : null;
}

function _syncAccuracyReviewActionButtons() {
  const addMissedBtn = document.getElementById('btn-add-missed-dart');
  const finishPracticeBtn = document.getElementById('btn-finish-practice-turn-alt');
  const finishGameBtn = document.getElementById('btn-finish-game-review');
  const showPracticeActions = _isPracticeAccuracyContext();
  const showGameActions = _isX01AccuracyContext() && currentPage === 'game';
  const gameReviewAction = _currentX01ReviewAction();

  _setVisible(addMissedBtn, _practiceAccuracySession.sessionId != null, '');
  _setVisible(finishPracticeBtn, showPracticeActions && !!practiceActive, '');
  _setVisible(finishGameBtn, showGameActions, '');
  if (finishGameBtn) {
    finishGameBtn.disabled = !gameReviewAction;
    finishGameBtn.textContent = gameReviewAction === 'continue_turn'
      ? 'Continue Turn'
      : (gameReviewAction === 'finish_turn' ? 'Finish Turn' : 'Remove Darts First');
  }
  _setGameReviewButtonVisible(showGameActions && (_practiceAccuracySession.predictions.length > 0 || _practiceAccuracySession.actualDarts.length > 0));
}

function _newReviewId(prefix = 'actual') {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function _resetPracticeAccuracyState(options = {}) {
  const { keepSession = false, keepTurn = false, closeModal = true } = options;
  if (_practiceAccuracySession.saveTimer) {
    clearTimeout(_practiceAccuracySession.saveTimer);
    _practiceAccuracySession.saveTimer = null;
  }
  if (!keepSession) {
    _practiceAccuracySession.sessionId = null;
    _practiceAccuracySession.context = 'practice';
    _practiceAccuracySession.sessionMode = 'practice';
    _practiceAccuracySession.modelName = '';
    _practiceAccuracySession.executionDevice = '';
    _practiceAccuracySession.detectionProfile = '';
    _practiceAccuracySession.boardProfile = '';
  }
  if (!keepTurn) {
    _practiceAccuracySession.turnId = null;
  }
  _practiceAccuracySession.predictions = [];
  _practiceAccuracySession.actualDarts = [];
  _practiceAccuracySession.notes = '';
  _practiceAccuracySession.lastSavedSignature = '';
  _practiceReviewFocusPredictionId = null;
  _gameTurnReviewReady = false;
  _hidePracticeReviewFeedback();
  if (closeModal) {
    closeMissedDartModal();
    closePracticeReviewModal();
  }
  renderPracticeAccuracyReview();
}

function _setPracticeFinishTurnVisible(visible) {
  const manualBtn = document.getElementById('btn-manual-event');
  if (manualBtn) {
    manualBtn.hidden = !visible;
    manualBtn.style.display = visible ? '' : 'none';
  }
  const finishBtn = document.getElementById('btn-reset');
  if (finishBtn) {
    finishBtn.hidden = !visible;
    finishBtn.style.display = visible ? '' : 'none';
  }
  const modalFinishBtn = document.getElementById('btn-finish-practice-turn-alt');
  if (modalFinishBtn) {
    modalFinishBtn.hidden = !visible;
    modalFinishBtn.style.display = visible ? '' : 'none';
  }
  _syncAccuracyReviewActionButtons();
}

function _hidePracticeReviewFeedback() {
  if (_practiceReviewFeedbackTimer) {
    clearTimeout(_practiceReviewFeedbackTimer);
    _practiceReviewFeedbackTimer = null;
  }
  const feedback = document.getElementById('practice-review-feedback');
  if (!feedback) return;
  feedback.style.display = 'none';
  feedback.className = 'practice-review-feedback';
  feedback.textContent = '';
}

function _showPracticeReviewFeedback(message, tone = 'ok') {
  const feedback = document.getElementById('practice-review-feedback');
  if (!feedback) return;
  if (_practiceReviewFeedbackTimer) {
    clearTimeout(_practiceReviewFeedbackTimer);
    _practiceReviewFeedbackTimer = null;
  }
  feedback.textContent = message;
  feedback.className = `practice-review-feedback is-${tone}`;
  feedback.style.display = 'block';
  _practiceReviewFeedbackTimer = setTimeout(() => {
    _hidePracticeReviewFeedback();
  }, 2200);
}

function openPracticeReviewModal(predictionId = null) {
  if (currentPage !== 'practice' && currentPage !== 'game') return;
  if (currentPage === 'game' && !_isX01AccuracyContext()) return;
  const overlay = document.getElementById('practice-review-overlay');
  if (!overlay) return;
  _practiceReviewFocusPredictionId = predictionId || null;
  _syncAccuracyReviewActionButtons();
  renderPracticeAccuracyReview();
  _hidePracticeReviewFeedback();
  overlay.style.display = 'flex';
  _practiceReviewModalOpen = true;
}

function closePracticeReviewModal(event) {
  if (event && event.target !== event.currentTarget) return;
  const overlay = document.getElementById('practice-review-overlay');
  if (overlay) overlay.style.display = 'none';
  _practiceReviewModalOpen = false;
  _practiceReviewFocusPredictionId = null;
  _hidePracticeReviewFeedback();
}

function _decodeAccuracyLabel(label) {
  const normalized = String(label || '').toUpperCase();
  if (!normalized) return { multiplier: 'single', number: 20, singleRing: 'inner' };
  if (normalized === 'BOUNCE') return { multiplier: 'bounce', number: 20, singleRing: 'inner' };
  if (normalized === 'MISS' || normalized === 'OFF' || normalized === 'FOUL') {
    return { multiplier: 'miss', number: 20, singleRing: 'inner' };
  }
  if (normalized === 'DB' || normalized === 'D25' || normalized === 'BULL') {
    return { multiplier: 'double', number: 25, singleRing: 'inner' };
  }
  if (normalized === 'SB' || normalized === 'S25') {
    return { multiplier: 'single', number: 25, singleRing: 'outer' };
  }
  if (/^D\d+$/.test(normalized)) {
    return { multiplier: 'double', number: parseInt(normalized.slice(1), 10), singleRing: 'inner' };
  }
  if (/^T\d+$/.test(normalized)) {
    return { multiplier: 'triple', number: parseInt(normalized.slice(1), 10), singleRing: 'inner' };
  }
  if (/^S\d+$/.test(normalized)) {
    return { multiplier: 'single', number: parseInt(normalized.slice(1), 10), singleRing: 'inner' };
  }
  return { multiplier: 'single', number: 20, singleRing: 'inner' };
}

function _buildActualFromDraft(draft) {
  const multiplier = draft.multiplier || 'single';
  const number = Number(draft.number || 20);
  const singleRing = draft.singleRing || 'inner';

  if (multiplier === 'bounce') {
    return { label: 'BOUNCE', score: 0, multiplier: 'bounce', number: null, singleRing: null };
  }
  if (multiplier === 'miss') {
    return { label: 'MISS', score: 0, multiplier: 'miss', number: null, singleRing: null };
  }
  if (multiplier === 'triple' && number === 25) {
    showToast('Bull can only be recorded as single or double.', 'warn', 2500);
    return null;
  }
  if (multiplier === 'single' && number === 25) {
    return { label: 'SB', score: 25, multiplier: 'single', number: 25, singleRing: null };
  }
  if (multiplier === 'double' && number === 25) {
    return { label: 'DB', score: 50, multiplier: 'double', number: 25, singleRing: null };
  }
  if (multiplier === 'single') {
    return { label: `S${number}`, score: number, multiplier, number, singleRing };
  }
  if (multiplier === 'double') {
    return { label: `D${number}`, score: number * 2, multiplier, number, singleRing: null };
  }
  return { label: `T${number}`, score: number * 3, multiplier, number, singleRing: null };
}

function _formatAccuracyLabel(label) {
  const normalized = String(label || '').toUpperCase();
  if (normalized === 'SB') return 'S25';
  if (normalized === 'DB') return 'D25';
  return normalized || '—';
}

function _formatAgreementBucket(bucket) {
  const map = {
    '3cam_all_match': '3-Cam Match',
    '3cam_two_match': '3-Cam 2/3',
    '3cam_all_diff': '3-Cam Split',
    '2cam_match': '2-Cam Match',
    '2cam_disagree': '2-Cam Split',
    '1cam_only': '1-Cam Only',
    'no_detection': 'No Detection',
  };
  return map[bucket] || 'Pending';
}

function _practiceActualForPrediction(predictionId) {
  return _practiceAccuracySession.actualDarts.find((item) => item.predictionId === predictionId) || null;
}

function _practicePredictionById(predictionId) {
  return _practiceAccuracySession.predictions.find((item) => item.predictionId === predictionId) || null;
}

function _practiceManualActuals() {
  return _practiceAccuracySession.actualDarts.filter((item) => !item.predictionId);
}

function _sanitizeReviewTurnSlot(slot) {
  const parsed = Number(slot);
  if (!Number.isFinite(parsed)) return null;
  const normalized = Math.trunc(parsed);
  return normalized >= 1 && normalized <= 3 ? normalized : null;
}

function _practicePredictionTurnSlot(predictionId, predictions = _practiceAccuracySession.predictions) {
  const index = (predictions || []).findIndex((item) => item.predictionId === predictionId);
  return index >= 0 ? index + 1 : null;
}

function _isPracticeActualSlotPinned(actual) {
  const slot = _sanitizeReviewTurnSlot(actual?.turnSlot);
  if (!slot) return false;
  return !actual?.predictionId || String(actual?.source || '').toLowerCase() === 'manual';
}

function _resolvePracticeReviewActuals(
  predictions = _practiceAccuracySession.predictions,
  actualDarts = _practiceAccuracySession.actualDarts,
) {
  const normalizedActuals = (actualDarts || []).map((item, index) => ({
    ...item,
    turnSlot: _sanitizeReviewTurnSlot(item.turnSlot),
    _orderIndex: index,
  }));
  const slotMap = new Map();
  const usedActualIds = new Set();

  const explicitActuals = normalizedActuals
    .filter((item) => _isPracticeActualSlotPinned(item))
    .sort((left, right) => {
      const leftPriority = !left.predictionId ? 2 : 1;
      const rightPriority = !right.predictionId ? 2 : 1;
      return (left.turnSlot - right.turnSlot)
        || (rightPriority - leftPriority)
        || (left._orderIndex - right._orderIndex);
    });

  const nextFreeSlot = () => {
    for (let slot = 1; slot <= 3; slot += 1) {
      if (!slotMap.has(slot)) return slot;
    }
    return null;
  };

  explicitActuals.forEach((item) => {
    if (!item.actualId || usedActualIds.has(item.actualId) || !item.turnSlot || slotMap.has(item.turnSlot)) return;
    slotMap.set(item.turnSlot, { ...item, resolvedTurnSlot: item.turnSlot });
    usedActualIds.add(item.actualId);
  });

  (predictions || []).forEach((prediction) => {
    const actual = normalizedActuals.find((item) => item.predictionId === prediction.predictionId && !usedActualIds.has(item.actualId));
    if (!actual) return;
    const slot = nextFreeSlot();
    if (!slot) return;
    slotMap.set(slot, { ...actual, resolvedTurnSlot: slot });
    usedActualIds.add(actual.actualId);
  });

  normalizedActuals.forEach((item) => {
    if (!item.actualId || usedActualIds.has(item.actualId)) return;
    const slot = nextFreeSlot();
    if (!slot) return;
    slotMap.set(slot, { ...item, resolvedTurnSlot: slot });
    usedActualIds.add(item.actualId);
  });

  return [1, 2, 3].map((slot) => slotMap.get(slot)).filter(Boolean);
}

function _nextPracticeReviewTurnSlot() {
  const resolved = _resolvePracticeReviewActuals();
  const used = new Set(resolved.map((item) => item.resolvedTurnSlot).filter(Boolean));
  for (let slot = 1; slot <= 3; slot += 1) {
    if (!used.has(slot)) return slot;
  }
  return 3;
}

function _serializePracticeActual(actual, resolvedTurnSlot = null) {
  return {
    id: actual.actualId,
    prediction_id: actual.predictionId || null,
    source: actual.source,
    label: actual.label,
    score: actual.score,
    multiplier: actual.multiplier || null,
    number: actual.number ?? null,
    single_ring: actual.singleRing || null,
    turn_slot: _sanitizeReviewTurnSlot(resolvedTurnSlot ?? actual.turnSlot),
  };
}

function _practiceReviewMetrics(predictions = _practiceAccuracySession.predictions, actualDarts = _practiceAccuracySession.actualDarts) {
  const predictionMap = new Map(predictions.map((item) => [item.predictionId, item]));
  let exactMatches = 0;
  let corrections = 0;

  actualDarts.forEach((actual) => {
    if (!actual.predictionId) {
      corrections += 1;
      return;
    }
    const prediction = predictionMap.get(actual.predictionId);
    if (!prediction) {
      corrections += 1;
      return;
    }
    if (prediction.label === actual.label && Number(prediction.score) === Number(actual.score)) {
      exactMatches += 1;
    } else {
      corrections += 1;
    }
  });

  const falsePositives = predictions.filter((prediction) => {
    const actual = actualDarts.find((item) => item.predictionId === prediction.predictionId);
    return !actual;
  }).length;
  corrections += falsePositives;

  return {
    predicted: predictions.length,
    actual: actualDarts.length,
    corrections,
    exactMatches,
    accuracyPct: actualDarts.length
      ? (exactMatches / actualDarts.length) * 100
      : null,
  };
}

function _practiceReviewScope() {
  const focusPredictionId = _practiceReviewFocusPredictionId;
  if (!focusPredictionId) {
    return {
      predictions: _practiceAccuracySession.predictions.slice(),
      actualDarts: _practiceAccuracySession.actualDarts.slice(),
      manualActuals: _practiceManualActuals(),
      focusedPrediction: null,
      focusedIndex: -1,
    };
  }

  const focusedIndex = _practiceAccuracySession.predictions.findIndex((item) => item.predictionId === focusPredictionId);
  const focusedPrediction = focusedIndex >= 0 ? _practiceAccuracySession.predictions[focusedIndex] : null;
  if (!focusedPrediction) {
    _practiceReviewFocusPredictionId = null;
    return {
      predictions: _practiceAccuracySession.predictions.slice(),
      actualDarts: _practiceAccuracySession.actualDarts.slice(),
      manualActuals: _practiceManualActuals(),
      focusedPrediction: null,
      focusedIndex: -1,
    };
  }

  return {
    predictions: [focusedPrediction],
    actualDarts: _practiceAccuracySession.actualDarts.filter((item) => item.predictionId === focusPredictionId),
    manualActuals: [],
    focusedPrediction,
    focusedIndex,
  };
}

function _practiceReviewPayload() {
  const orderedActuals = _resolvePracticeReviewActuals();
  return {
    session_id: _practiceAccuracySession.sessionId,
    turn_id: _practiceAccuracySession.turnId,
    actual_darts: orderedActuals.map((item) => _serializePracticeActual(item, item.resolvedTurnSlot)),
    notes: _practiceAccuracySession.notes || '',
  };
}

async function savePracticeAccuracyReview(force = false) {
  if (!_practiceAccuracySession.sessionId || !_practiceAccuracySession.turnId) return;
  const payload = _practiceReviewPayload();
  const signature = JSON.stringify(payload);
  if (!force && signature === _practiceAccuracySession.lastSavedSignature) return;
  try {
    const res = await fetch('/api/accuracy/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return;
    _practiceAccuracySession.lastSavedSignature = signature;
  } catch (err) {
    console.warn('Failed to save practice accuracy review', err);
  }
}

async function _submitX01ReviewAction(action) {
  if (!_practiceAccuracySession.sessionId || !_practiceAccuracySession.turnId) return null;
  const payload = {
    ..._practiceReviewPayload(),
    action,
  };
  const res = await fetch('/api/accuracy/review/x01-action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    const error = data.error || `Failed to ${action === 'finish_turn' ? 'finish' : 'continue'} X01 turn.`;
    throw new Error(error);
  }
  _practiceAccuracySession.lastSavedSignature = JSON.stringify(_practiceReviewPayload());
  return data;
}

function queuePracticeAccuracySave(immediate = false) {
  if (_practiceAccuracySession.saveTimer) {
    clearTimeout(_practiceAccuracySession.saveTimer);
    _practiceAccuracySession.saveTimer = null;
  }
  if (immediate) {
    void savePracticeAccuracyReview(true);
    return;
  }
  _practiceAccuracySession.saveTimer = setTimeout(() => {
    _practiceAccuracySession.saveTimer = null;
    void savePracticeAccuracyReview(false);
  }, 250);
}

async function _syncPracticeAccuracyTurnFromServer() {
  if (!_practiceAccuracySession.sessionId) return;
  try {
    const res = await fetch(`/api/accuracy/session/${_practiceAccuracySession.sessionId}`);
    if (!res.ok) return;
    const session = await res.json();
    const turns = session.turns || [];
    const turn = turns.find((item) => item.id === _practiceAccuracySession.turnId)
      || turns.slice().reverse().find((item) => item.status === 'open')
      || turns[turns.length - 1];
    if (!turn) return;
    _practiceAccuracySession.turnId = turn.id;
    _practiceAccuracySession.predictions = (turn.predictions || []).map((item) => ({
      predictionId: item.id,
      label: item.label,
      score: item.score,
      agreementBucket: item.agreement_bucket || '',
      timings: item.timings || {},
      modelName: item.model_name || _practiceAccuracySession.modelName,
      executionDevice: item.execution_device || _practiceAccuracySession.executionDevice,
      detectionProfile: item.detection_profile || _practiceAccuracySession.detectionProfile,
    }));
    _practiceAccuracySession.actualDarts = ((turn.review && turn.review.actual_darts) || []).map((item) => ({
      actualId: item.id,
      predictionId: item.prediction_id || null,
      source: item.source || (item.prediction_id ? 'prediction' : 'manual'),
      label: item.label,
      score: item.score,
      multiplier: item.multiplier || null,
      number: item.number ?? null,
      singleRing: item.single_ring || null,
      turnSlot: (() => {
        const slot = _sanitizeReviewTurnSlot(item.turn_slot);
        const source = String(item.source || (item.prediction_id ? 'prediction' : 'manual')).toLowerCase();
        return slot && (!item.prediction_id || source === 'manual') ? slot : null;
      })(),
    }));
    _practiceAccuracySession.lastSavedSignature = JSON.stringify(_practiceReviewPayload());
    renderPracticeAccuracyReview();
  } catch (err) {
    console.warn('Failed to sync practice accuracy turn', err);
  }
}

function applyPracticeAccuracySession(data) {
  if (!data || !data.session_id) {
    _resetPracticeAccuracyState();
    return;
  }

  const prevTurnId = _practiceAccuracySession.turnId;
  const nextTurnId = data.turn_id || _practiceAccuracySession.turnId;
  const turnChanged = !!nextTurnId && nextTurnId !== prevTurnId;

  _practiceAccuracySession.sessionId = data.session_id;
  _practiceAccuracySession.turnId = nextTurnId;
  _practiceAccuracySession.context = data.context || _practiceAccuracySession.context || 'practice';
  _practiceAccuracySession.sessionMode = data.session_mode || _practiceAccuracySession.sessionMode || _practiceAccuracySession.context;
  _practiceAccuracySession.modelName = data.model_name || _practiceAccuracySession.modelName;
  _practiceAccuracySession.executionDevice = data.execution_device || _practiceAccuracySession.executionDevice;
  _practiceAccuracySession.detectionProfile = data.detection_profile || _practiceAccuracySession.detectionProfile;
  _practiceAccuracySession.boardProfile = data.board_profile || _practiceAccuracySession.boardProfile;

  if (turnChanged) {
    if (_practiceAccuracySession.saveTimer) {
      clearTimeout(_practiceAccuracySession.saveTimer);
      _practiceAccuracySession.saveTimer = null;
    }
    _practiceAccuracySession.predictions = [];
    _practiceAccuracySession.actualDarts = [];
    _practiceAccuracySession.lastSavedSignature = '';
  }

  renderPracticeAccuracyReview();
  _syncAccuracyReviewActionButtons();
  if (turnChanged) {
    void _syncPracticeAccuracyTurnFromServer();
  }
}

function _upsertPracticePrediction(data) {
  if (data.turn_id && _practiceAccuracySession.turnId && data.turn_id !== _practiceAccuracySession.turnId) {
    if (_practiceAccuracySession.saveTimer) {
      clearTimeout(_practiceAccuracySession.saveTimer);
      _practiceAccuracySession.saveTimer = null;
    }
    _practiceAccuracySession.predictions = [];
    _practiceAccuracySession.actualDarts = [];
    _practiceAccuracySession.lastSavedSignature = '';
  }
  if (data.session_id) {
    _practiceAccuracySession.sessionId = data.session_id;
  }
  if (data.turn_id) {
    _practiceAccuracySession.turnId = data.turn_id;
  }
  if (data.model_name) {
    _practiceAccuracySession.modelName = data.model_name;
  }
  if (data.execution_device) {
    _practiceAccuracySession.executionDevice = data.execution_device;
  }
  if (data.detection_profile) {
    _practiceAccuracySession.detectionProfile = data.detection_profile;
  }
  if (data.context) {
    _practiceAccuracySession.context = data.context;
  }
  if (data.session_mode) {
    _practiceAccuracySession.sessionMode = data.session_mode;
  }

  const predictionId = data.prediction_id || _newReviewId('pred');
  const prediction = {
    predictionId,
    label: data.label,
    score: data.score,
    agreementBucket: data.agreement_bucket || '',
    timings: data.timings || {},
    modelName: data.model_name || _practiceAccuracySession.modelName,
    executionDevice: data.execution_device || _practiceAccuracySession.executionDevice,
    detectionProfile: data.detection_profile || _practiceAccuracySession.detectionProfile,
  };

  const existingIndex = _practiceAccuracySession.predictions.findIndex((item) => item.predictionId === predictionId);
  if (existingIndex >= 0) {
    _practiceAccuracySession.predictions[existingIndex] = prediction;
  } else {
    _practiceAccuracySession.predictions.push(prediction);
  }

  const existingActual = _practiceActualForPrediction(predictionId);
  if (!existingActual) {
    const decoded = _decodeAccuracyLabel(prediction.label);
    _practiceAccuracySession.actualDarts.push({
      actualId: _newReviewId('actual'),
      predictionId,
      source: 'prediction',
      label: prediction.label,
      score: prediction.score,
      multiplier: decoded.multiplier,
      number: decoded.number,
      singleRing: decoded.singleRing,
      turnSlot: null,
    });
  } else if (existingActual.source === 'prediction') {
    const decoded = _decodeAccuracyLabel(prediction.label);
    existingActual.label = prediction.label;
    existingActual.score = prediction.score;
    existingActual.multiplier = decoded.multiplier;
    existingActual.number = decoded.number;
    existingActual.singleRing = decoded.singleRing;
    existingActual.turnSlot = null;
  }

  renderPracticeAccuracyReview();
  _syncAccuracyReviewActionButtons();
  queuePracticeAccuracySave();
}

function renderPracticeAccuracyReview() {
  const list = document.getElementById('practice-review-list');
  const sub = document.getElementById('practice-review-sub');
  const meta = document.getElementById('practice-review-meta');
  const predictedEl = document.getElementById('practice-review-predicted');
  const actualEl = document.getElementById('practice-review-actual');
  const correctionsEl = document.getElementById('practice-review-corrections');
  if (!list || !sub || !meta || !predictedEl || !actualEl || !correctionsEl) return;

  const scope = _practiceReviewScope();
  const metrics = _practiceReviewMetrics(scope.predictions, scope.actualDarts);
  const resolvedActuals = _resolvePracticeReviewActuals();
  const resolvedSlotByPredictionId = new Map(
    resolvedActuals
      .filter((item) => item.predictionId && item.resolvedTurnSlot)
      .map((item) => [item.predictionId, item.resolvedTurnSlot]),
  );
  const manualActuals = scope.focusedPrediction
    ? []
    : resolvedActuals.filter((item) => !item.predictionId);
  predictedEl.textContent = String(metrics.predicted);
  actualEl.textContent = String(metrics.actual);
  correctionsEl.textContent = String(metrics.corrections);
  _syncAccuracyReviewActionButtons();

  if (!_practiceAccuracySession.sessionId) {
    meta.textContent = 'No active session';
    sub.textContent = 'Predictions will appear here for review.';
    list.innerHTML = '<div class="practice-review-empty">Start practice or X01 to review detection accuracy turn by turn.</div>';
    return;
  }

  const metaBits = [];
  metaBits.push(_isX01AccuracyContext() ? 'X01 Review' : 'Practice Review');
  if (_practiceAccuracySession.modelName) metaBits.push(_practiceAccuracySession.modelName);
  if (_practiceAccuracySession.detectionProfile) metaBits.push(_practiceAccuracySession.detectionProfile);
  if (_practiceAccuracySession.executionDevice) metaBits.push(_practiceAccuracySession.executionDevice);
  meta.textContent = metaBits.join(' • ') || 'Active review';
  if (scope.focusedPrediction && scope.focusedIndex >= 0) {
    sub.textContent = metrics.actual > 0 && metrics.accuracyPct != null
      ? `Reviewing Dart ${scope.focusedIndex + 1} • Accuracy: ${metrics.accuracyPct.toFixed(1)}%`
      : `Reviewing Dart ${scope.focusedIndex + 1} of this ${_isX01AccuracyContext() ? 'X01' : 'practice'} turn.`;
  } else if (_isX01AccuracyContext()) {
    const gameAction = _currentX01ReviewAction();
    if (gameAction === 'continue_turn') {
      sub.textContent = `Turn review ready • Accuracy: ${metrics.accuracyPct != null ? metrics.accuracyPct.toFixed(1) + '%' : '—'}`;
    } else if (gameAction === 'finish_turn') {
      sub.textContent = 'Review complete. Finish this corrected turn to move into takeout.';
    } else {
      sub.textContent = 'Review the scored turn, then remove darts to unlock Continue.';
    }
  } else {
    sub.textContent = metrics.actual > 0 && metrics.accuracyPct != null
      ? `Current turn accuracy: ${metrics.accuracyPct.toFixed(1)}%`
      : 'Review the detected darts before resetting the board.';
  }

  const predictionRows = scope.predictions.map((prediction) => {
    const index = _practiceAccuracySession.predictions.findIndex((item) => item.predictionId === prediction.predictionId);
    const actual = _practiceActualForPrediction(prediction.predictionId);
    const turnSlot = resolvedSlotByPredictionId.get(prediction.predictionId) || _practicePredictionTurnSlot(prediction.predictionId);
    const isFalsePositive = !actual;
    const isExact = !!actual
      && actual.label === prediction.label
      && Number(actual.score) === Number(prediction.score);
    const isReviewed = isExact && actual && actual.source === 'confirmed';
    const stateClass = isFalsePositive
      ? 'is-false-positive'
      : (isReviewed ? 'is-confirmed' : (isExact ? 'is-pending' : 'is-corrected'));
    const stateLabel = isFalsePositive
      ? 'False Positive'
      : (isReviewed ? 'Confirmed' : (isExact ? 'Matches Prediction' : 'Corrected'));
    const actualLabel = actual ? `${_formatAccuracyLabel(actual.label)} • ${actual.score}` : 'No actual dart linked';
    const confirmDisabled = isReviewed ? 'disabled' : '';
    const confirmText = isReviewed ? 'Confirmed' : 'Confirm';
    return `
      <div class="practice-review-row ${stateClass}">
        <div class="practice-review-row-top">
          <div class="practice-review-row-title">Dart ${turnSlot || (index + 1)}</div>
          <div class="practice-review-state">${stateLabel}</div>
        </div>
        <div class="practice-review-row-body">
          <div class="practice-review-col">
            <span class="practice-review-col-label">Predicted</span>
            <strong>${_formatAccuracyLabel(prediction.label)} • ${prediction.score}</strong>
          </div>
          <div class="practice-review-col">
            <span class="practice-review-col-label">Actual</span>
            <strong>${actualLabel}</strong>
          </div>
          <div class="practice-review-col">
            <span class="practice-review-col-label">Agreement</span>
            <span class="practice-review-badge">${_formatAgreementBucket(prediction.agreementBucket)}</span>
          </div>
        </div>
        <div class="practice-review-row-actions">
          <button class="btn btn-sm ${isReviewed ? 'btn-accent' : 'btn-secondary'}" ${confirmDisabled} onclick="confirmPracticePrediction('${prediction.predictionId}')">${confirmText}</button>
          <button class="btn btn-sm btn-secondary" onclick="editPracticePrediction('${prediction.predictionId}')">Edit</button>
          <button class="btn btn-sm btn-secondary" onclick="togglePracticeFalsePositive('${prediction.predictionId}')">${isFalsePositive ? 'Restore' : 'False Positive'}</button>
        </div>
      </div>
    `;
  });

  const manualRows = manualActuals.map((actual, index) => `
    <div class="practice-review-row is-manual">
      <div class="practice-review-row-top">
        <div class="practice-review-row-title">Dart ${actual.resolvedTurnSlot || actual.turnSlot || (index + 1)}</div>
        <div class="practice-review-state">No Detection</div>
      </div>
      <div class="practice-review-row-body">
        <div class="practice-review-col">
          <span class="practice-review-col-label">Actual</span>
          <strong>${_formatAccuracyLabel(actual.label)} • ${actual.score}</strong>
        </div>
        <div class="practice-review-col">
          <span class="practice-review-col-label">Ring</span>
          <strong>${getRingName(actual.label)}</strong>
        </div>
      </div>
      <div class="practice-review-row-actions">
        <button class="btn btn-sm btn-secondary" onclick="editManualPracticeActual('${actual.actualId}')">Edit</button>
        <button class="btn btn-sm btn-secondary" onclick="removeManualPracticeActual('${actual.actualId}')">Remove</button>
      </div>
    </div>
  `);

  if (predictionRows.length === 0 && manualRows.length === 0) {
    list.innerHTML = '<div class="practice-review-empty">No darts recorded yet for this turn.</div>';
    return;
  }

  let html = predictionRows.join('');
  if (manualRows.length > 0) {
    html += '<div class="practice-review-section-title">Missed Darts</div>' + manualRows.join('');
  }
  list.innerHTML = html;
}

function confirmPracticePrediction(predictionId) {
  const prediction = _practicePredictionById(predictionId);
  if (!prediction) return;
  const decoded = _decodeAccuracyLabel(prediction.label);
  const actual = _practiceActualForPrediction(predictionId);
  const updated = {
    actualId: actual ? actual.actualId : _newReviewId('actual'),
    predictionId,
    source: 'confirmed',
    label: prediction.label,
    score: prediction.score,
    multiplier: decoded.multiplier,
    number: decoded.number,
    singleRing: decoded.singleRing,
    turnSlot: null,
  };
  if (actual) {
    Object.assign(actual, updated);
  } else {
    _practiceAccuracySession.actualDarts.push(updated);
  }
  renderPracticeAccuracyReview();
  queuePracticeAccuracySave(true);
  _showPracticeReviewFeedback(`${_formatAccuracyLabel(prediction.label)} confirmed for this turn.`, 'ok');
}

function togglePracticeFalsePositive(predictionId) {
  const index = _practiceAccuracySession.actualDarts.findIndex((item) => item.predictionId === predictionId);
  if (index >= 0) {
    _practiceAccuracySession.actualDarts.splice(index, 1);
    renderPracticeAccuracyReview();
    queuePracticeAccuracySave(true);
    _showPracticeReviewFeedback('Marked as false positive.', 'warn');
  } else {
    confirmPracticePrediction(predictionId);
  }
}

function editPracticePrediction(predictionId) {
  const prediction = _practicePredictionById(predictionId);
  if (!prediction) return;
  const actual = _practiceActualForPrediction(predictionId);
  const decoded = _decodeAccuracyLabel(actual ? actual.label : prediction.label);
  _missedDartDraft = {
    mode: 'edit_prediction',
    predictionId,
    actualId: actual ? actual.actualId : null,
    turnSlot: _sanitizeReviewTurnSlot(actual?.turnSlot) || _practicePredictionTurnSlot(predictionId) || _nextPracticeReviewTurnSlot(),
    multiplier: decoded.multiplier,
    singleRing: decoded.singleRing,
    number: decoded.number,
  };
  const title = document.getElementById('missed-dart-modal-title');
  const subtitle = document.getElementById('missed-dart-modal-subtitle');
  const submit = document.getElementById('missed-dart-submit-btn');
  if (title) title.textContent = 'Edit Detected Dart';
  if (subtitle) subtitle.textContent = `Prediction: ${_formatAccuracyLabel(prediction.label)} • ${prediction.score}`;
  if (submit) submit.textContent = 'Save Dart';
  document.getElementById('missed-dart-modal').style.display = 'flex';
  _renderMissedDartModal();
}

function editManualPracticeActual(actualId) {
  const actual = _practiceAccuracySession.actualDarts.find((item) => item.actualId === actualId && !item.predictionId);
  if (!actual) return;
  const decoded = _decodeAccuracyLabel(actual.label);
  _missedDartDraft = {
    mode: 'edit_manual',
    predictionId: null,
    actualId,
    turnSlot: _sanitizeReviewTurnSlot(actual?.turnSlot) || _nextPracticeReviewTurnSlot(),
    multiplier: decoded.multiplier,
    singleRing: decoded.singleRing,
    number: decoded.number,
  };
  const title = document.getElementById('missed-dart-modal-title');
  const subtitle = document.getElementById('missed-dart-modal-subtitle');
  const submit = document.getElementById('missed-dart-submit-btn');
  if (title) title.textContent = 'Edit Missed Dart';
  if (subtitle) subtitle.textContent = 'Update the manually-added dart result.';
  if (submit) submit.textContent = 'Save Dart';
  document.getElementById('missed-dart-modal').style.display = 'flex';
  _renderMissedDartModal();
}

function removeManualPracticeActual(actualId) {
  _practiceAccuracySession.actualDarts = _practiceAccuracySession.actualDarts.filter((item) => item.actualId !== actualId);
  renderPracticeAccuracyReview();
  queuePracticeAccuracySave(true);
  _showPracticeReviewFeedback('Removed missed dart from this turn.', 'warn');
}

function openMissedDartModal() {
  if (_resolvePracticeReviewActuals().length >= 3) {
    showToast('This turn already has 3 recorded darts. Edit an existing dart to adjust the review.', 'warn', 2600);
    return;
  }
  _missedDartDraft = {
    mode: 'add',
    predictionId: null,
    actualId: null,
    turnSlot: _nextPracticeReviewTurnSlot(),
    multiplier: 'single',
    singleRing: 'inner',
    number: 20,
  };
  const title = document.getElementById('missed-dart-modal-title');
  const subtitle = document.getElementById('missed-dart-modal-subtitle');
  const submit = document.getElementById('missed-dart-submit-btn');
  if (title) title.textContent = 'Add Missed Dart';
  if (subtitle) subtitle.textContent = 'Record the actual dart result for this turn.';
  if (submit) submit.textContent = 'Add Dart';
  document.getElementById('missed-dart-modal').style.display = 'flex';
  _renderMissedDartModal();
}

function openPracticeManualEventModal() {
  if (!practiceActive || currentPage !== 'practice') return;
  if (throwCount >= 3) {
    showToast('This turn already has 3 darts. Finish the turn before adding another event.', 'warn', 2500);
    return;
  }
  _missedDartDraft = {
    mode: 'practice_manual_event',
    predictionId: null,
    actualId: null,
    turnSlot: Math.min(3, throwCount + 1),
    multiplier: 'miss',
    singleRing: 'inner',
    number: 20,
  };
  const title = document.getElementById('missed-dart-modal-title');
  const subtitle = document.getElementById('missed-dart-modal-subtitle');
  const submit = document.getElementById('missed-dart-submit-btn');
  if (title) title.textContent = 'Add Practice Event';
  if (subtitle) subtitle.textContent = 'Record an undetected throw such as a bounce, fall, or missed hit.';
  if (submit) submit.textContent = 'Add Event';
  document.getElementById('missed-dart-modal').style.display = 'flex';
  _renderMissedDartModal();
}

function closeMissedDartModal() {
  const modal = document.getElementById('missed-dart-modal');
  if (modal) modal.style.display = 'none';
}

function setMissedDartMultiplier(multiplier) {
  _missedDartDraft.multiplier = multiplier;
  if (multiplier === 'triple' && Number(_missedDartDraft.number) === 25) {
    _missedDartDraft.number = 20;
  }
  _renderMissedDartModal();
}

function setMissedDartSingleRing(singleRing) {
  _missedDartDraft.singleRing = singleRing;
  _renderMissedDartModal();
}

function setMissedDartNumber(number) {
  _missedDartDraft.number = number;
  _renderMissedDartModal();
}

function setMissedDartSlot(slot) {
  _missedDartDraft.turnSlot = _sanitizeReviewTurnSlot(slot) || _missedDartDraft.turnSlot || 1;
  _renderMissedDartModal();
}

function _renderMissedDartModal() {
  const multiplier = _missedDartDraft.multiplier || 'single';
  document.querySelectorAll('.accuracy-choice-btn[data-multiplier]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.multiplier === multiplier);
  });
  document.querySelectorAll('.accuracy-choice-btn[data-turn-slot]').forEach((btn) => {
    btn.classList.toggle('active', Number(btn.dataset.turnSlot) === Number(_missedDartDraft.turnSlot || 1));
  });
  document.querySelectorAll('.accuracy-choice-btn[data-single-ring]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.singleRing === (_missedDartDraft.singleRing || 'inner'));
  });
  document.querySelectorAll('.accuracy-choice-btn[data-number]').forEach((btn) => {
    const value = Number(btn.dataset.number);
    const invalidBull = multiplier === 'triple' && value === 25;
    btn.classList.toggle('active', value === Number(_missedDartDraft.number));
    btn.disabled = invalidBull;
  });
  const singleRingGroup = document.getElementById('missed-dart-single-ring-group');
  const numberGroup = document.getElementById('missed-dart-number-group');
  const slotGroup = document.getElementById('missed-dart-slot-group');
  if (slotGroup) {
    slotGroup.style.display = _missedDartDraft.mode === 'practice_manual_event' ? 'none' : '';
  }
  if (singleRingGroup) {
    const show = multiplier === 'single' && Number(_missedDartDraft.number) !== 25;
    singleRingGroup.style.display = show ? '' : 'none';
  }
  if (numberGroup) {
    numberGroup.style.display = (multiplier === 'bounce' || multiplier === 'miss') ? 'none' : '';
  }
  const built = _buildActualFromDraft(_missedDartDraft);
  const finalScore = document.getElementById('missed-dart-final-score');
  if (finalScore) finalScore.textContent = built ? String(built.score) : '—';
}

function _applyPracticeManualEvent(built) {
  if (throwCount >= 3) {
    showToast('This turn already has 3 darts. Finish the turn before adding another event.', 'warn', 2500);
    return;
  }

  const turnSlot = _sanitizeReviewTurnSlot(_missedDartDraft.turnSlot) || Math.min(3, throwCount + 1);
  const actualId = _newReviewId('actual');
  _practiceAccuracySession.actualDarts.push({
    actualId,
    predictionId: null,
    source: 'manual',
    label: built.label,
    score: built.score,
    multiplier: built.multiplier,
    number: built.number,
    singleRing: built.singleRing,
    turnSlot,
  });

  throwCount += 1;
  totalScore += built.score;
  updateDartCount();

  const scoreEl = document.getElementById('score-current');
  if (scoreEl) {
    scoreEl.textContent = _formatAccuracyLabel(built.label);
    scoreEl.classList.toggle('label-bounce', built.label === 'BOUNCE');
    scoreEl.classList.toggle('label-miss', built.label === 'MISS');
    scoreEl.classList.toggle('label-foul', built.label === 'FOUL');
  }

  const ringEl = document.getElementById('score-ring');
  if (ringEl) ringEl.textContent = built.label === 'FOUL' ? '\u00a0' : getRingName(built.label);

  const throwsEl = document.getElementById('stat-throws');
  if (throwsEl) throwsEl.textContent = String(throwCount);
  const totalEl = document.getElementById('stat-total');
  if (totalEl) totalEl.textContent = String(totalScore);
  const avgEl = document.getElementById('stat-avg');
  if (avgEl) avgEl.textContent = throwCount > 0 ? (totalScore / throwCount).toFixed(1) : '–';

  addHistoryRow(built.label, built.score, { predictionId: null });
  renderPracticeAccuracyReview();
  queuePracticeAccuracySave(true);

  const logLabel = _formatAccuracyLabel(built.label);
  addPracticeLog(`[MANUAL] ${logLabel} = ${built.score} pts`);
  showToast(`Manual event added: ${logLabel} (${built.score})`, 'ok', 2200);

  if (throwCount >= 3) {
    setStatus('takeout', '3 Darts Recorded - Finish Turn');
    showToast('3 darts recorded. Use Finish Turn when ready.', 'warn', 2600);
    return;
  }

  setStatus('scored', `Manual ${logLabel} = ${built.score}`);
  setTimeout(() => {
    if (window._awaitingTakeoutGame) return;
    const banner = document.getElementById('practice-takeout-banner');
    if (!banner || banner.style.display === 'none' || banner.style.display === '') {
      setStatus('ready', 'Waiting for Throw');
    }
  }, 1800);
}

function submitMissedDartModal() {
  const built = _buildActualFromDraft(_missedDartDraft);
  if (!built) return;
  const turnSlot = _sanitizeReviewTurnSlot(_missedDartDraft.turnSlot) || _nextPracticeReviewTurnSlot();

  if (_missedDartDraft.mode === 'practice_manual_event') {
    closeMissedDartModal();
    _applyPracticeManualEvent(built);
    _showPracticeReviewFeedback('Manual event added to this turn.', 'ok');
    return;
  }

  if (_missedDartDraft.mode === 'edit_prediction' && _missedDartDraft.predictionId) {
    const existing = _practiceActualForPrediction(_missedDartDraft.predictionId);
    const nextValue = {
      actualId: existing ? existing.actualId : (_missedDartDraft.actualId || _newReviewId('actual')),
      predictionId: _missedDartDraft.predictionId,
      source: 'manual',
      label: built.label,
      score: built.score,
      multiplier: built.multiplier,
      number: built.number,
      singleRing: built.singleRing,
      turnSlot,
    };
    if (existing) {
      Object.assign(existing, nextValue);
    } else {
      _practiceAccuracySession.actualDarts.push(nextValue);
    }
  } else if (_missedDartDraft.mode === 'edit_manual' && _missedDartDraft.actualId) {
    const existing = _practiceAccuracySession.actualDarts.find((item) => item.actualId === _missedDartDraft.actualId);
    if (existing) {
      Object.assign(existing, {
        label: built.label,
        score: built.score,
        multiplier: built.multiplier,
        number: built.number,
        singleRing: built.singleRing,
        turnSlot,
      });
    }
  } else {
    _practiceAccuracySession.actualDarts.push({
      actualId: _newReviewId('actual'),
      predictionId: null,
      source: 'manual',
      label: built.label,
      score: built.score,
      multiplier: built.multiplier,
      number: built.number,
      singleRing: built.singleRing,
      turnSlot,
    });
  }

  closeMissedDartModal();
  renderPracticeAccuracyReview();
  queuePracticeAccuracySave(true);
  _showPracticeReviewFeedback(
    (_missedDartDraft.mode === 'add' || _missedDartDraft.mode === 'practice_manual_event')
      ? 'Missed dart added to this turn.'
      : 'Review updated.',
    'ok'
  );
}

async function finishPracticeTurn() {
  if (!socket || !socket.connected) return;
  _practiceResetMode = 'manual';
  _setPracticeTakeoutBannerVisible(false);
  closePracticeReviewModal();
  if (practiceActive) setStatus('waiting', 'Resetting Board…');
  await savePracticeAccuracyReview(true);
  if (socket && socket.connected) socket.emit('practice_reset_turn');
}

async function continueGameTurnReview() {
  if (!socket || !socket.connected || !_isX01AccuracyContext()) return;
  const action = _currentX01ReviewAction();
  if (!action) return;

  try {
    setStatus(action === 'continue_turn' ? 'ready' : 'takeout',
      action === 'continue_turn' ? 'Continuing Match…' : 'Finishing Turn…');
    const result = await _submitX01ReviewAction(action);
    closePracticeReviewModal();
    if (action === 'finish_turn' && result?.status === 'awaiting_takeout') {
      showToast('Turn finished from review. Remove darts to continue.', 'ok', 2400);
    }
  } catch (err) {
    console.warn('Failed to apply X01 review action', err);
    showToast(err?.message || 'Unable to apply X01 review.', 'error', 2800);
    _syncAccuracyReviewActionButtons();
  }
}

// ══════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════

function setStatus(cls, text) {
  const ind = document.getElementById('status-indicator');
  ind.className = 'status-indicator ' + cls;
  document.getElementById('status-text').textContent = text;
  // Also update game page indicators if present
  const gInd = document.getElementById('game-status-indicator');
  if (gInd) { gInd.className = 'status-indicator game-status ' + cls; }
  const gText = document.getElementById('game-status-text');
  if (gText) { gText.textContent = text; }
  const gaInd = document.getElementById('game-active-status-indicator');
  if (gaInd) { gaInd.className = 'status-indicator game-status ' + cls; }
  const gaText = document.getElementById('game-active-status-text');
  if (gaText) { gaText.textContent = text; }
}

function updateCamDots(data) {
  [0, 1, 2].forEach(i => {
    const dot = document.getElementById('cam-dot-' + i);
    const fpsBadge = document.getElementById('cam-fps-' + i);
    if (!dot) return;
    const camData = data && (data[i] || data[String(i)]);
    const online = camData && camData.active;
    dot.className = online ? 'bar-dot dot-on' : 'bar-dot dot-off';
    // Update FPS badge
    if (fpsBadge) {
      if (online && camData.fps) {
        fpsBadge.textContent = camData.fps + ' fps';
        fpsBadge.classList.add('visible');
      } else {
        fpsBadge.textContent = '';
        fpsBadge.classList.remove('visible');
      }
    }
  });
  // Hide calibration offline overlay if we have cam data
  if (data && Object.keys(data).length > 0) {
    const calOff = document.getElementById('cal-offline-overlay');
    if (calOff) calOff.style.display = 'none';
  }
}

function getRingName(label) {
  if (!label || label === 'OFF') return 'Off Board';
  if (label === 'MISS') return 'Off Board';
  if (label === 'BOUNCE') return 'Bounce Out';
  if (label === 'FOUL') return 'Foul';
  if (label === 'BULL') return 'Bullseye';
  if (label === 'DB' || label === 'D25') return 'Double Bull';
  if (label === 'SB' || label === 'S25') return 'Outer Bull';
  if (label.startsWith('D')) return 'Double ' + label.substring(1);
  if (label.startsWith('T')) return 'Triple ' + label.substring(1);
  if (label.startsWith('S')) return 'Single ' + label.substring(1);
  return label;
}

function addHistoryRow(label, score, options = {}) {
  const list = document.getElementById('history-list');
  const row = document.createElement('div');
  row.className = 'history-item';
  const prediction = _practiceAccuracySession.predictions[_practiceAccuracySession.predictions.length - 1] || null;
  const predictionId = Object.prototype.hasOwnProperty.call(options, 'predictionId')
    ? options.predictionId
    : (prediction ? prediction.predictionId : null);
  const scoreClass = score === 0
    ? (label === 'BOUNCE' ? 'miss-bounce' : label === 'MISS' ? 'miss-board' : label === 'FOUL' ? 'miss-foul' : 'miss')
    : (label === 'BULL' || label === 'D25' || label === 'DB' || label === 'SB' || label === 'S25') ? 'bull' : '';
  const onClick = predictionId
    ? `openPracticeReviewModal('${predictionId}')`
    : 'openPracticeReviewModal()';
  row.innerHTML = `
    <span class="h-num">#${throwCount}</span>
    <span class="h-ring">${getRingName(label)}</span>
    <button type="button" class="h-score h-score-btn ${scoreClass}" onclick="${onClick}" title="Open accuracy review for this dart">${score}</button>
  `;
  list.insertBefore(row, list.firstChild);
}

function appendPrecisionDot(group, sx, sy, fill, opts = {}) {
  if (!group) return;
  const outerR = opts.outerR ?? 3.5;
  const innerR = opts.innerR ?? 1.2;
  const strokeWidth = opts.strokeWidth ?? 1.0;

  const dot = document.createElementNS(SVG_NS, 'circle');
  dot.setAttribute('cx', sx);
  dot.setAttribute('cy', sy);
  dot.setAttribute('r', String(outerR));
  dot.setAttribute('fill', fill);
  dot.setAttribute('stroke', '#fff');
  dot.setAttribute('stroke-width', String(strokeWidth));
  dot.setAttribute('opacity', '0.92');
  dot.classList.add('dart-dot');
  group.appendChild(dot);

  const core = document.createElementNS(SVG_NS, 'circle');
  core.setAttribute('cx', sx);
  core.setAttribute('cy', sy);
  core.setAttribute('r', String(innerR));
  core.setAttribute('fill', '#fff');
  core.setAttribute('opacity', '0.95');
  core.classList.add('dart-dot-core');
  group.appendChild(core);
}

function placeDot(x_mm, y_mm, label) {
  if (label === 'BOUNCE' || label === 'MISS' || label === 'FOUL') return;  // off-board / foul — no dot
  // Practice currently uses the warped live board, and the projected
  // marker alignment is intentionally hidden there for now.
  if (currentPage === 'practice') return;
  const fill = (label === 'MISS') ? '#f59e0b' : '#ff4444';

  const scale = TOTAL_R / 170;
  const sx = BOARD_CX + x_mm * scale;
  const sy = BOARD_CY - y_mm * scale;

  // Resolve which SVG group is currently visible
  let groupId;
  if (currentPage === 'practice') {
    groupId = 'practice-board-dots';
  } else if (currentPage === 'game') {
    // During bullseye throw-off, onBullseyeState() manages its own dots
    const bullPhase = document.getElementById('bullseye-phase');
    const inBullseye = bullPhase && bullPhase.style.display !== 'none';
    if (inBullseye) return;   // onBullseyeState handles these dots
    groupId = 'game-board-dots';
  } else {
    return;   // Settings, Calibration, Stats — no board visible
  }

  const g = document.getElementById(groupId);
  if (!g) return;
  appendPrecisionDot(g, sx, sy, fill, { outerR: 3.4, innerR: 1.15, strokeWidth: 1.0 });
}

function _appendPracticeLog(msg) {
  const log = document.getElementById('practice-log');
  if (!log) return;
  const ts = new Date().toLocaleTimeString();
  const entry = document.createElement('div');
  entry.className = 'log-entry';

  // Determine message color class
  let msgClass = 'log-msg';
  if (msg.includes('[FOUL]') || msg.includes('FOUL')) msgClass += ' log-foul';
  else if (msg.includes('[SCORE]')) msgClass += ' log-score';
  else if (msg.includes('[PRACTICE]') || msg.includes('Practice')) msgClass += ' log-practice';

  entry.innerHTML = `<span class="log-ts">${ts}</span><span class="${msgClass}">${msg.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>`;
  log.appendChild(entry);

  // Keep at most 40 entries
  while (log.children.length > 40) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

function addPracticeLog(msg) {
  if (currentPage !== 'practice') {
    console.info(msg);
    return;
  }
  _appendPracticeLog(msg);
  console.info(msg);
}

function addLog(msg) {
  console.info(msg);
}

// ══════════════════════════════════════════════════════════════════════
// Settings
// ══════════════════════════════════════════════════════════════════════
// Lens Calibration helpers
// ══════════════════════════════════════════════════════════════════════

function _lensSetStatus(camId, text, ok) {
  const el = document.getElementById(`lens-status-${camId}`);
  if (el) { el.textContent = text; el.style.color = ok === true ? '#4ade80' : ok === false ? '#f87171' : ''; }
}

async function lensRefreshStatus(camId) {
  try {
    const d = await fetch(`/api/lens/status/${camId}`).then(r => r.json());
    if (d.calibrated) {
      _lensSetStatus(camId, `✓ Calibrated  RMS: ${d.rms} px`, true);
    } else if (d.count > 0) {
      _lensSetStatus(camId, `${d.count}/20 frames`, null);
    } else {
      _lensSetStatus(camId, 'Uncalibrated', null);
    }
  } catch { _lensSetStatus(camId, '—', null); }
}

async function lensPreview(camId) {
  const wrap = document.getElementById('lens-preview-wrap');
  const img  = document.getElementById('lens-preview-img');
  if (!wrap || !img) return;
  wrap.style.display = '';

  // Fetch so we can detect non-image error responses
  try {
    const res = await fetch(`/api/lens/frame/${camId}?t=${Date.now()}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Camera unavailable' }));
      wrap.innerHTML = `<p style="color:#f87171;padding:10px;font-size:13px">⚠ ${err.error || 'Camera not available'} — click <strong>Open Cameras</strong> above first</p>`;
      return;
    }
    const blob = await res.blob();
    img.src = URL.createObjectURL(blob);
    // Make sure the img is visible (wrap may have been replaced)
    if (!wrap.querySelector('img')) {
      wrap.innerHTML = '';
      wrap.appendChild(img);
    }
  } catch (e) {
    wrap.innerHTML = `<p style="color:#f87171;padding:10px;font-size:13px">⚠ ${e.message}</p>`;
  }
}

async function lensCapture(camId) {
  const btn = document.getElementById(`lens-cap-${camId}`);
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
  try {
    const d = await fetch(`/api/lens/capture/${camId}`, { method: 'POST' }).then(r => r.json());
    if (d.error) {
      _lensSetStatus(camId, `⚠ ${d.error}`, false);
    } else if (d.ok) {
      _lensSetStatus(camId, `${d.count}/20 frames`, null);
      addLog(`Lens Cam ${camId + 1}: frame ${d.count}/${d.need} captured`);
    } else {
      _lensSetStatus(camId, 'No pattern found — adjust angle or open cameras', false);
    }
    // Refresh preview to show corners
    lensPreview(camId);
  } catch (e) { _lensSetStatus(camId, 'Error: ' + e.message, false); }
  if (btn) { btn.textContent = 'Capture Frame'; btn.disabled = false; }
}

async function lensCompute(camId) {
  const btn = document.getElementById(`lens-cmp-${camId}`);
  if (btn) { btn.textContent = '⏳ Computing…'; btn.disabled = true; }
  try {
    const d = await fetch(`/api/lens/compute/${camId}`, { method: 'POST' }).then(r => r.json());
    if (d.ok) {
      _lensSetStatus(camId, `✓ Calibrated  RMS: ${d.rms} px`, true);
      addLog(`Lens Cam ${camId + 1}: computed — RMS ${d.rms} px`);
    } else {
      _lensSetStatus(camId, d.error || d.message || 'Compute failed', false);
    }
  } catch (e) { _lensSetStatus(camId, 'Error: ' + e.message, false); }
  if (btn) { btn.textContent = 'Compute'; btn.disabled = false; }
}

async function lensReset(camId) {
  await fetch(`/api/lens/reset/${camId}`, { method: 'POST' });
  lensRefreshStatus(camId);
  addLog(`Lens Cam ${camId + 1}: reset`);
}

async function lensRefreshAll() {
  for (const camId of [0, 1, 2]) {
    try {
      const d = await fetch(`/api/lens/status/${camId}`).then(r => r.json());
      const badge = document.getElementById(`lens-badge-${camId}`);
      if (badge) {
        if (d.calibrated) {
          badge.textContent = `Cam ${camId + 1} ✓ ${d.rms}px`;
          badge.className = 'lens-badge ok';
        } else {
          badge.textContent = `Cam ${camId + 1} —`;
          badge.className = 'lens-badge';
        }
      }
      // also keep old status spans if they exist
      _lensSetStatus(camId, d.calibrated ? `✓ Calibrated RMS: ${d.rms} px` : 'Uncalibrated', d.calibrated || null);
    } catch { /* ignore */ }
  }
}

// ── Fullscreen Lens Calibration Modal ─────────────────────────────────
let _lcmCam = 0;
let _lcmTimer = null;

function lensCalOpen(camId) {
  _lcmCam = camId;
  lensCalSetCam(camId);
  const modal = document.getElementById('lens-cal-modal');
  if (modal) modal.style.display = 'flex';
  // Reset UI
  _lcmUpdateProgress(0, 0);
  lensReset(camId);
  _lcmStartPoll();
}

function lensCalSetCam(camId) {
  _lcmCam = camId;
  document.querySelectorAll('.lcm-tab').forEach((t, i) => {
    t.classList.toggle('active', i === camId);
  });
  _lcmUpdateProgress(0, 0);
  if (_lcmTimer) { clearInterval(_lcmTimer); _lcmTimer = null; }
  _lcmStartPoll();
}

function lensCalClose() {
  if (_lcmTimer) { clearInterval(_lcmTimer); _lcmTimer = null; }
  const modal = document.getElementById('lens-cal-modal');
  if (modal) modal.style.display = 'none';
}

function _lcmStartPoll() {
  if (_lcmTimer) clearInterval(_lcmTimer);
  _lcmTimer = setInterval(_lcmPollFrame, 400);
}

async function _lcmPollFrame() {
  const feed     = document.getElementById('lcm-feed');
  const noFeed   = document.getElementById('lcm-no-cam');
  try {
    const res = await fetch(`/api/lens/autoframe/${_lcmCam}?t=${Date.now()}`);
    if (!res.ok) {
      // Cameras not open
      if (feed) feed.style.display = 'none';
      if (noFeed) noFeed.style.display = '';
      return;
    }
    if (noFeed) noFeed.style.display = 'none';
    if (feed) feed.style.display = '';
    const count    = parseInt(res.headers.get('X-Lens-Count')    || '0');
    const coverage = parseInt(res.headers.get('X-Lens-Coverage') || '0');
    _lcmUpdateProgress(count, coverage);
    const blob = await res.blob();
    if (feed) feed.src = URL.createObjectURL(blob);

    // Auto-finish: ≥20 images OR coverage ≥ 95%
    if (count >= 20 || coverage >= 95) {
      clearInterval(_lcmTimer); _lcmTimer = null;
      lensCalClose();
      await lensCompute(_lcmCam);
      lensRefreshAll();
    }
  } catch { /* network err - ignore */ }
}

function _lcmUpdateProgress(count, coverage) {
  const imgBar  = document.getElementById('lcm-img-bar');
  const imgTxt  = document.getElementById('lcm-img-text');
  const covBar  = document.getElementById('lcm-cov-bar');
  const covTxt  = document.getElementById('lcm-cov-text');
  if (imgBar) imgBar.style.width = Math.min(100, count / 50 * 100) + '%';
  if (imgTxt) imgTxt.textContent = `${count}/50`;
  if (covBar) covBar.style.width = Math.min(100, coverage / 95 * 100) + '%';
  if (covTxt) covTxt.textContent = `${coverage}%/95%`;
}

async function saveSettings(btnEl) {
  const settings = {
    detection_speed: document.getElementById('set-speed').value,
    tip_offset_px: parseFloat(document.getElementById('set-tip-offset').value),
    min_dart_area: parseInt(document.getElementById('set-dart-min').value),
    max_dart_area: parseInt(document.getElementById('set-dart-max').value),
    stable_frames: parseInt(document.getElementById('set-stable').value),
    resolution: document.getElementById('set-resolution').value,
    fps: parseInt(document.getElementById('set-fps').value),
    standby_time: document.getElementById('set-standby').value,
    triangle_k_factor: parseFloat(document.getElementById('set-triangle-k').value),
    approximate_distortion: document.getElementById('set-approx-dist').checked,
    calibrate_on_startup: document.getElementById('set-auto-cal-startup').checked,
    blur_kernel: parseInt(document.getElementById('set-blur').value),
    binary_thresh: parseInt(document.getElementById('set-bin-thresh').value),
    tfluna_enabled: document.getElementById('set-tfluna-enabled').checked,
    tfluna_port: (document.getElementById('set-tfluna-port').textContent || '').trim(),
    tfluna_foul_distance_cm: parseInt(document.getElementById('set-tfluna-foul').value) || 237,
    tfluna_tolerance_cm: parseInt(document.getElementById('set-tfluna-tolerance').value) || 5,
  };

  const btn = btnEl || document.querySelector('#page-settings .page-header .btn-primary');

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    const data = await res.json();
    if (data.ok) {
      addLog('Settings saved to disk');
      // Sync calibration resolution dropdown
      const calRes = document.getElementById('cal-resolution');
      if (calRes && settings.resolution) {
        if ([...calRes.options].some(o => o.value === settings.resolution)) {
          calRes.value = settings.resolution;
        }
      }
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = '\u2713 Saved'; btn.style.background = 'var(--green)';
        setTimeout(() => { btn.textContent = orig; btn.style.background = ''; }, 1500);
      }
    } else {
      addLog('Settings save failed: ' + (data.error || 'unknown'));
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = '\u2717 Failed'; btn.style.background = 'var(--red, #ff4444)';
        setTimeout(() => { btn.textContent = orig; btn.style.background = ''; }, 2000);
      }
    }
  } catch (e) {
    addLog('Settings save error: ' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════════════
// Debug Console
// ══════════════════════════════════════════════════════════════════════

const MAX_DEBUG_LINES = 500;

function toggleDebugPanel() {
  const panel = document.getElementById('debug-panel');
  const btn = document.getElementById('debug-toggle-btn');
  panel.classList.toggle('open');
  btn.classList.toggle('active');
}

function clearDebugLogs() {
  const body = document.getElementById('debug-body');
  if (body) body.innerHTML = '';
}

function appendDebugLog(msg, ts) {
  const body = document.getElementById('debug-body');
  if (!body) return;

  const line = document.createElement('div');
  line.className = 'debug-line';

  // Color-code by prefix
  if (msg.includes('[DART]')) line.classList.add('log-dart');
  else if (msg.includes('[SCORE]')) line.classList.add('log-score');
  else if (msg.includes('[SCR]')) line.classList.add('log-scr');
  else if (msg.includes('[DET]')) line.classList.add('log-det');
  else if (msg.includes('[CAM]')) line.classList.add('log-cam');
  else if (msg.includes('[SRV]')) line.classList.add('log-srv');
  else if (msg.includes('[RAW]')) line.classList.add('log-raw');
  else if (msg.includes('WARNING') || msg.includes('WARN')) line.classList.add('log-warn');
  else if (msg.includes('ERROR') || msg.includes('ERR')) line.classList.add('log-err');

  const time = ts ? new Date(ts * 1000).toLocaleTimeString() : '';
  line.innerHTML = `<span class="ts">${time}</span>${msg.replace(/</g, '&lt;').replace(/>/g, '&gt;')}`;

  body.appendChild(line);

  // Cap lines
  while (body.children.length > MAX_DEBUG_LINES) body.removeChild(body.firstChild);

  // Auto-scroll
  body.scrollTop = body.scrollHeight;
}


// ══════════════════════════════════════════════════════════════════════
// System Stats Polling
// ══════════════════════════════════════════════════════════════════════

let _sysStatsInterval = null;

function pollSystemStats() {
  fetch('/api/system-stats')
    .then(r => r.json())
    .then(d => {
      // RAM
      const ramEl = document.getElementById('ram-label');
      if (ramEl) ramEl.textContent = `RAM ${d.ram_used_gb}/${d.ram_total_gb} GB (${d.ram_percent}%)`;

      // GPU
      const gpuEl = document.getElementById('gpu-label');
      const gpuDot = document.getElementById('gpu-dot');
      const vramEl = document.getElementById('vram-label');

      if (d.gpu) {
        if (gpuEl) gpuEl.textContent = `GPU ${d.gpu.util_percent}%`;
        if (vramEl) {
          const usedGB = (d.gpu.mem_used_mb / 1024).toFixed(1);
          const totalGB = (d.gpu.mem_total_mb / 1024).toFixed(1);
          vramEl.textContent = `VRAM ${usedGB}/${totalGB} GB`;
        }
        if (gpuDot) {
          gpuDot.className = 'bar-dot';
          if (d.gpu.util_percent < 50) gpuDot.classList.add('dot-on');
          else if (d.gpu.util_percent < 80) { gpuDot.style.background = 'var(--yellow, #f0ad4e)'; }
          else { gpuDot.style.background = 'var(--red, #ff4444)'; }
        }
      } else {
        if (gpuEl) gpuEl.textContent = 'GPU N/A';
        if (vramEl) vramEl.textContent = 'VRAM N/A';
      }
    })
    .catch(() => { });
}

function startSysStats() {
  if (_sysStatsInterval) return;
  pollSystemStats();
  _sysStatsInterval = setInterval(pollSystemStats, 3000);
}

function stopSysStats() {
  if (_sysStatsInterval) { clearInterval(_sysStatsInterval); _sysStatsInterval = null; }
}


// ══════════════════════════════════════════════════════════════════════
// Board Profile Management
// ══════════════════════════════════════════════════════════════════════

async function checkBoardProfile() {
  try {
    const res = await fetch('/api/board/list');
    const data = await res.json();
    const statusEl = document.getElementById('board-profile-status');
    const listEl = document.getElementById('board-profile-list');
    if (!listEl) return;

    if (data.profiles && data.profiles.length > 0) {
      _hasActiveProfile = !!data.active;
      if (statusEl) {
        statusEl.textContent = `${data.profiles.length} profile(s) · Active: ${data.active || 'none'}`;
        statusEl.style.color = data.active ? '#5eeaaa' : '#ffaa00';
      }
      listEl.innerHTML = data.profiles.map(p => {
        const isActive = p.name === data.active;
        return `<div class="profile-item${isActive ? ' active' : ''}">
          <span class="profile-name">${isActive ? '✓ ' : ''}${p.name}</span>
          <span class="profile-features">${p.features} features</span>
          <button class="btn-profile-sel" onclick="selectProfile('${p.name}')">Use</button>
          <button class="btn-profile-del" onclick="deleteProfile('${p.name}')" title="Delete">✕</button>
        </div>`;
      }).join('');
    } else {
      _hasActiveProfile = false;
      if (statusEl) { statusEl.textContent = 'No profiles saved'; statusEl.style.color = '#888'; }
      listEl.innerHTML = '<div style="color:#666;font-size:12px;padding:4px 0">No board profiles yet. Calibrate cameras first, then save a profile.</div>';
    }
    // Stop the stream first, THEN apply overlay state — order matters:
    // stopCamPreview() un-hides the offline placeholder, so updateNoProfileOverlays()
    // must run last to re-hide it and show only the "No Board Profile" overlay.
    if (_camViewMode === 'warped' && !_hasActiveProfile) stopCamPreview();
    updateNoProfileOverlays();
    // If cameras are open and a profile just became active (e.g. first profile saved),
    // restart the preview stream — the img elements were cleared when there was no profile.
    if (_camsManuallyOpen && _hasActiveProfile) startCamPreview();
  } catch (e) { /* ignore */ }
}

async function registerBoard(opts = {}) {
  const modalInput = document.getElementById('profile-modal-name');
  const legacyInput = document.getElementById('profile-name-input');
  const name = (opts.name || (modalInput && modalInput.value.trim()) || (legacyInput && legacyInput.value.trim()) || 'default');
  const camId = Number.isFinite(opts.camId) ? opts.camId : calCamId;
  const btn = opts.btnEl || document.getElementById('btn-register-board');
  const orig = btn ? btn.textContent : '';
  if (btn) {
    btn.textContent = '⏳ Saving…';
    btn.disabled = true;
  }
  try {
    const res = await fetch('/api/board/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cam_id: camId, name }),
    });
    const data = await res.json();
    if (data.ok) {
      if (btn) btn.textContent = `✓ Saved "${data.name}"`;
      showToast(`Board profile "${data.name}" saved`, 'ok', 3200);
      addLog(`Board profile "${data.name}" saved with ${data.features} features`);
      if (modalInput) modalInput.value = '';
      if (legacyInput) legacyInput.value = '';
      await checkBoardProfile();
      return true;
    } else {
      showCalibrationNotice(data.error || 'Save failed', 'error', 5200);
      if (btn) btn.textContent = '✗ Failed';
    }
  } catch (err) {
    showCalibrationNotice('Save error: ' + err.message, 'error', 5200);
    if (btn) btn.textContent = '✗ Failed';
  }
  if (btn) {
    btn.disabled = false;
    setTimeout(() => { btn.textContent = orig; }, 2500);
  }
  return false;
}

async function selectProfile(name) {
  try {
    const res = await fetch('/api/board/select', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (data.ok) {
      addLog(`Board profile "${name}" activated`);
      checkBoardProfile();
    } else { alert(data.error || 'Select failed'); }
  } catch (err) { alert('Select error: ' + err.message); }
}

let _confirmModalResolve = null;

function isConfirmModalOpen() {
  const modal = document.getElementById('confirm-modal');
  return !!modal && modal.style.display === 'flex';
}

function showConfirmModal(options = {}) {
  const modal = document.getElementById('confirm-modal');
  const kicker = document.getElementById('confirm-modal-kicker');
  const icon = document.getElementById('confirm-modal-icon');
  const title = document.getElementById('confirm-modal-title');
  const message = document.getElementById('confirm-modal-message');
  const target = document.getElementById('confirm-modal-target');
  const confirmBtn = document.getElementById('confirm-modal-confirm');
  const cancelBtn = document.getElementById('confirm-modal-cancel');

  if (!modal || !title || !message || !confirmBtn || !cancelBtn) {
    console.warn('[UI] Confirmation modal is unavailable.');
    return Promise.resolve(false);
  }

  if (_confirmModalResolve) {
    _confirmModalResolve(false);
    _confirmModalResolve = null;
  }

  if (kicker) kicker.textContent = options.kicker || 'Confirm Action';
  if (icon) icon.textContent = options.icon || '🗑️';
  title.textContent = options.title || 'Confirm Action';
  message.textContent = options.message || 'Are you sure you want to continue?';
  confirmBtn.textContent = options.confirmText || 'Confirm';
  cancelBtn.textContent = options.cancelText || 'Cancel';

  if (target) {
    const targetText = String(options.target ?? '').trim();
    target.textContent = targetText;
    target.style.display = targetText ? 'block' : 'none';
  }

  modal.style.display = 'flex';
  setTimeout(() => cancelBtn.focus(), 0);

  return new Promise((resolve) => {
    _confirmModalResolve = resolve;
  });
}

function closeConfirmModal(confirmed) {
  const modal = document.getElementById('confirm-modal');
  if (modal) modal.style.display = 'none';
  if (_confirmModalResolve) {
    _confirmModalResolve(Boolean(confirmed));
    _confirmModalResolve = null;
  }
}

async function deleteProfile(name) {
  const confirmed = await showConfirmModal({
    kicker: 'Board Profile',
    icon: '🗑️',
    title: 'Delete Profile',
    message: 'Remove this saved board profile from Throw Vision? This action cannot be undone.',
    target: `"${name}"`,
    confirmText: 'Delete Profile',
  });
  if (!confirmed) return;

  try {
    const res = await fetch('/api/board/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Board profile "${name}" deleted.`, 'ok', 2800);
      addLog(`Board profile "${name}" deleted`);
      checkBoardProfile();
    } else {
      showToast(data.error || 'Delete failed.', 'warn', 2800);
    }
  } catch (err) {
    showToast(`Delete error: ${err.message}`, 'warn', 3200);
  }
}

// ── Warped Homography Preview (on calibration page) ─────────────────
let _warpPreviewTimer = null;

function calUpdateWarpPreview() {
  if (!calImage || calPoints.length < 4) return;
  const pts = calPoints.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(',');
  const img = document.getElementById('cal-warp-img');
  if (img) img.src = `/api/cal/preview/${calCamId}?pts=${pts}&t=${Date.now()}`;
}

function calScheduleWarpPreview() {
  if (_warpPreviewTimer) clearTimeout(_warpPreviewTimer);
  _warpPreviewTimer = setTimeout(calUpdateWarpPreview, 300);
}

async function changeCalResolution(val) {
  const [w, h] = val.split('x').map(Number);
  // Remember old image dimensions for scaling points
  const oldW = calImage ? calImage.width : 0;
  const oldH = calImage ? calImage.height : 0;
  try {
    await fetch('/api/cal/resolution', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ width: w, height: h }),
    });
    await new Promise(r => setTimeout(r, 500));
    // Scale existing calibration points to the new resolution
    if (oldW > 0 && oldH > 0 && calPoints.length === 4) {
      const sx = w / oldW;
      const sy = h / oldH;
      calPoints = calPoints.map(p => ({ x: p.x * sx, y: p.y * sy }));
    }
    // Sync settings resolution dropdown — rebuild from camera-supported list
    populateResolutionDropdown(w + 'x' + h);
    calCaptureFrame(5, true); // keepPoints=true to preserve scaled points
  } catch (e) { console.error('Resolution change failed', e); }
}

// ══════════════════════════════════════════════════════════════════════
// Boot
// ══════════════════════════════════════════════════════════════════════

connectSocket();
startSysStats();

// ── Debug Camera Panel ──────────────────────────────────────────────
let _debugCamsActive = false;
let _debugActiveCam = 0;

function toggleDebugCams() {
  const panel = document.getElementById('debug-cam-panel');
  const scoreSide = document.querySelector('.prac-score-side');
  const toggle = document.getElementById('toggle-debug');
  _debugCamsActive = !!(toggle && toggle.checked);
  panel.style.display = _debugCamsActive ? '' : 'none';
  if (scoreSide) scoreSide.classList.toggle('debug-cam-open', _debugCamsActive);
  if (_debugCamsActive) {
    switchDebugCam(_debugActiveCam);
  } else {
    document.getElementById('debug-cam-view').src = '';
  }
}

function switchDebugCam(camId) {
  _debugActiveCam = camId;
  const img = document.getElementById('debug-cam-view');
  img.src = '/api/stream/warped/' + camId;
  // Update switcher buttons
  document.querySelectorAll('.debug-cam-sw').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.cam) === camId);
  });
  // Show matching info, hide others
  for (let i = 0; i < 3; i++) {
    const el = document.getElementById('debug-info-' + i);
    if (el) el.style.display = (i === camId) ? '' : 'none';
  }
}

function updateDebugCamInfo(details) {
  if (!_debugCamsActive) return;
  details.forEach(d => {
    const el = document.getElementById('debug-info-' + d.cam);
    if (!el) return;
    el.className = 'debug-cam-info';
    if (d.label === null) {
      el.innerHTML = '<div class="dci-row"><span class="dci-label" style="color:#555">—</span><span style="color:#444;font-style:italic;font-size:10px">No detection</span></div>';
      el.classList.add('no-detect');
    } else if (d.used) {
      el.innerHTML = [
        '<div class="dci-row">',
          `<span class="dci-label">${d.label}</span>`,
          `<span class="dci-tag">${d.score} pts</span>`,
          `<span class="dci-tag">${d.method}</span>`,
        '</div>',
        '<div class="dci-row dci-coords">',
          `<span>(${d.x_mm}, ${d.y_mm})mm</span>`,
          `<span class="dci-tag">r=${d.r_mm}</span>`,
          `<span class="dci-tag">area=${d.area}</span>`,
        '</div>',
      ].join('');
      el.classList.add('used');
    } else {
      el.innerHTML = [
        '<div class="dci-row">',
          `<span class="dci-label">${d.label}</span>`,
          `<span class="dci-tag">${d.score} pts</span>`,
          `<span class="dci-tag">${d.method}</span>`,
          '<span class="dci-tag" style="color:#f87171;background:rgba(248,113,113,0.12)">rejected</span>',
        '</div>',
        '<div class="dci-row dci-coords">',
          `<span>(${d.x_mm}, ${d.y_mm})mm</span>`,
          `<span class="dci-tag">r=${d.r_mm}</span>`,
          `<span class="dci-tag">area=${d.area}</span>`,
        '</div>',
      ].join('');
      el.classList.add('rejected');
    }
  });
}


// Maps resolution key → human label
const _RES_LABELS = {
  '1920x1080': '1920 \u00d7 1080 (Full HD)',
  '1280x720':  '1280 \u00d7 720 (HD)',
  '848x480':   '848 \u00d7 480 \u2605 Recommended',
  '640x480':   '640 \u00d7 480',
  '640x360':   '640 \u00d7 360',
  '424x240':   '424 \u00d7 240',
  '320x240':   '320 \u00d7 240',
};

/**
 * Fetch the list of camera-supported resolutions from the server and rebuild
 * the #set-resolution dropdown.  Selects `preferValue` if provided, otherwise
 * keeps whatever value was previously selected (or the first entry).
 */
async function populateResolutionDropdown(preferValue) {
  const sel = document.getElementById('set-resolution');
  if (!sel) return;

  // Remember the currently selected value so we can restore it
  const prevValue = preferValue || sel.value || '';

  // Show a transient loading state
  sel.innerHTML = '<option value="">Detecting resolutions\u2026</option>';
  sel.disabled = true;

  try {
    const res = await fetch('/api/cameras/resolutions');
    if (!res.ok) throw new Error('probe failed');
    const data = await res.json();
    const list = data.resolutions || [];

    if (list.length === 0) throw new Error('empty list');

    sel.innerHTML = '';
    for (const { width, height } of list) {
      const key = `${width}x${height}`;
      const label = _RES_LABELS[key] || `${width} \u00d7 ${height}`;
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = label;
      sel.appendChild(opt);
    }

    // Restore previous selection if it exists; otherwise pick the first
    if (prevValue && [...sel.options].some(o => o.value === prevValue)) {
      sel.value = prevValue;
    } else {
      sel.selectedIndex = 0;
    }
  } catch (e) {
    // Fallback: show a minimal static list so the UI isn't broken
    console.warn('[RES] Resolution probe failed, using fallback list:', e);
    sel.innerHTML = [
      '1920x1080', '1280x720', '848x480', '640x480', '640x360', '424x240'
    ].map(k => `<option value="${k}">${_RES_LABELS[k] || k}</option>`).join('');
    if (prevValue && [...sel.options].some(o => o.value === prevValue)) {
      sel.value = prevValue;
    }
  } finally {
    sel.disabled = false;
  }
}

// Load settings from server and sync UI inputs
async function loadServerSettings() {
  try {
    const res = await fetch('/api/settings');
    if (!res.ok) return;
    const s = await res.json();

    // Populate resolution dropdown first (probe camera), then select saved value
    await populateResolutionDropdown(s.resolution || '');

    // Sync cal-resolution dropdown too
    if (s.resolution) {
      const calRes = document.getElementById('cal-resolution');
      if (calRes && [...calRes.options].some(o => o.value === s.resolution)) calRes.value = s.resolution;
    }

    // FPS
    if (s.fps != null) {
      const el = document.getElementById('set-fps');
      if (el && [...el.options].some(o => parseInt(o.value) === s.fps)) el.value = s.fps;
    }

    // Number of cameras
    if (s.num_cameras != null) {
      const el = document.getElementById('set-num-cams');
      if (el && [...el.options].some(o => parseInt(o.value) === s.num_cameras)) el.value = s.num_cameras;
    }

    // Standby timeout
    if (s.standby_time) {
      const el = document.getElementById('set-standby');
      // Map server format ("15m") to option values ("15min")
      const standbyMap = { '5m': '5min', '10m': '10min', '15m': '15min', '30m': '30min', '1h': 'never' };
      const mapped = standbyMap[s.standby_time] || s.standby_time;
      if (el && [...el.options].some(o => o.value === mapped)) el.value = mapped;
    }

    // Detection speed
    if (s.detection_speed) {
      const el = document.getElementById('set-speed');
      const speedMap = { 'high': 'fast', 'very_high': 'fast', 'default': 'default', 'low': 'careful', 'very_low': 'careful' };
      const mapped = speedMap[s.detection_speed] || s.detection_speed;
      if (el && [...el.options].some(o => o.value === mapped)) el.value = mapped;
    }

    // Triangle K factor
    if (s.triangle_k_factor != null) {
      const el = document.getElementById('set-triangle-k');
      if (el) {
        el.value = s.triangle_k_factor;
        const disp = el.closest('.setting-row')?.querySelector('.range-val');
        if (disp) disp.textContent = s.triangle_k_factor;
      }
    }

    // Approximate distortion toggle
    if (s.approximate_distortion != null) {
      const el = document.getElementById('set-approx-dist');
      if (el) el.checked = !!s.approximate_distortion;
    }
    if (s.calibrate_on_startup != null) {
      const el = document.getElementById('set-auto-cal-startup');
      if (el) el.checked = !!s.calibrate_on_startup;
    }

    // Blur kernel
    if (s.blur_kernel != null) {
      const el = document.getElementById('set-blur');
      if (el && [...el.options].some(o => parseInt(o.value) === s.blur_kernel)) el.value = s.blur_kernel;
    }

    // Detection inputs (sliders / numbers)
    const map = {
      'set-tip-offset': s.tip_offset_px,
      'set-dart-min': s.min_dart_area,
      'set-dart-max': s.max_dart_area,
      'set-stable': s.stable_frames,
      'set-bin-thresh': s.binary_thresh,
    };
    for (const [id, val] of Object.entries(map)) {
      if (val == null) continue;
      const el = document.getElementById(id);
      if (el) {
        el.value = val;
        const disp = el.closest('.setting-row')?.querySelector('.range-val');
        if (disp) disp.textContent = val;
      }
    }
    // TF-Luna oche sensor
    if (s.tfluna_enabled != null) {
      const el = document.getElementById('set-tfluna-enabled');
      if (el) el.checked = !!s.tfluna_enabled;
    }
    if (s.tfluna_port != null) {
      const el = document.getElementById('set-tfluna-port');
      if (el) el.textContent = s.tfluna_port;
    }
    if (s.tfluna_foul_distance_cm != null) {
      const el = document.getElementById('set-tfluna-foul');
      if (el) el.value = s.tfluna_foul_distance_cm;
    }
    if (s.tfluna_tolerance_cm != null) {
      const el = document.getElementById('set-tfluna-tolerance');
      if (el) el.value = s.tfluna_tolerance_cm;
    }
    // Auto-scan for TF-Luna port and fill the field
    _autoScanTFLuna();
  } catch (e) { console.warn('Failed to load server settings', e); }
}

// ── TF-Luna probe ──────────────────────────────────────────────────

function _setTFLunaStatus(dotClass, text) {
  const dot = document.getElementById('tfluna-dot');
  const label = document.getElementById('tfluna-status-text');
  if (dot) dot.className = 'tfluna-dot ' + dotClass;
  if (label) label.textContent = text;
}

// Auto-save just the TF-Luna settings to the server (no Save button needed)
async function _saveTFLunaSettings() {
  const portEl = document.getElementById('set-tfluna-port');
  const enabledEl = document.getElementById('set-tfluna-enabled');
  const foulEl = document.getElementById('set-tfluna-foul');
  const tolEl = document.getElementById('set-tfluna-tolerance');
  const settings = {
    tfluna_enabled: enabledEl ? enabledEl.checked : true,
    tfluna_port: portEl ? (portEl.textContent || '').trim() : '',
    tfluna_foul_distance_cm: foulEl ? parseInt(foulEl.value) || 237 : 237,
    tfluna_tolerance_cm: tolEl ? parseInt(tolEl.value) || 5 : 5,
  };
  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  } catch { /* silent */ }
}

// Auto-scan for TF-Luna port, fill the UI, and auto-save
async function _autoScanTFLuna() {
  try {
    const res = await fetch('/api/tfluna/scan');
    const d = await res.json();
    if (d.found && d.port) {
      const portEl = document.getElementById('set-tfluna-port');
      const enabledEl = document.getElementById('set-tfluna-enabled');
      // Auto-fill the port and enable it
      if (portEl) portEl.textContent = d.port;
      if (enabledEl) enabledEl.checked = true;
      // Auto-save these settings
      await _saveTFLunaSettings();
      // Now probe the connection
      probeTFLuna();
    } else {
      // No port found by auto-scan, but try probing with whatever is in the field
      const portEl = document.getElementById('set-tfluna-port');
      if (portEl && (portEl.textContent || '').trim()) {
        probeTFLuna();
      }
    }
  } catch { /* silent */ }
}

async function probeTFLuna() {
  const btn = document.getElementById('btn-tfluna-probe');
  if (btn) { btn.disabled = true; btn.textContent = 'Testing…'; }
  _setTFLunaStatus('tfluna-checking', 'Checking…');

  // Send the port from the UI so it works even before saving settings
  const portEl = document.getElementById('set-tfluna-port');
  const port = portEl ? (portEl.textContent || '').trim() : '';

  try {
    const res = await fetch('/api/tfluna/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port }),
    });
    const d = await res.json();
    if (d.ok) {
      _setTFLunaStatus('tfluna-ok', `${d.port} Connected`);
      // Auto-fill port from probe result and auto-save
      if (d.port && portEl) portEl.value = d.port;
      const enabledEl = document.getElementById('set-tfluna-enabled');
      if (enabledEl) enabledEl.checked = true;
      _saveTFLunaSettings();
    } else {
      const msgs = {
        no_port: 'No COM port configured',
        no_config: 'Config not loaded',
        failed: d.msg || 'Cannot open port',
        no_data: d.msg || 'Port open but no data',
      };
      _setTFLunaStatus('tfluna-fail', msgs[d.status] || d.msg || 'Not detected');
    }
  } catch (e) {
    _setTFLunaStatus('tfluna-fail', 'Probe error: ' + e.message);
  }

  if (btn) {
    btn.disabled = false;
    btn.textContent = 'Test Connection';
    const badge = document.getElementById('tfluna-badge');
    if (badge) { badge.classList.add('tfluna-flash'); setTimeout(() => badge.classList.remove('tfluna-flash'), 800); }
  }
}

// ── TF-Luna live distance display (settings page) ──────────────────
function _updateTFLunaLive(data) {
  const valueEl = document.getElementById('tfluna-live-value');
  const statusEl = document.getElementById('tfluna-live-status');
  const marker = document.getElementById('tfluna-live-marker');
  const arrow = document.getElementById('tfluna-marker-arrow');
  const foulZone = document.getElementById('tfluna-sim-foul-zone');
  const okZone = document.getElementById('tfluna-sim-ok-zone');

  if (!data.connected) {
    if (valueEl) valueEl.textContent = '—';
    if (statusEl) { statusEl.textContent = 'Disconnected'; statusEl.className = 'tfluna-live-status tfluna-live-off'; }
    if (marker) marker.style.display = 'none';
    return;
  }

  const dist = data.distance_cm;
  const threshold = data.foul_threshold_cm || 237;
  const isFoul = data.is_trespassing;

  if (valueEl) {
    valueEl.textContent = dist;
    valueEl.className = 'tfluna-live-value' + (isFoul ? ' tfluna-live-foul' : ' tfluna-live-safe');
  }
  if (statusEl) {
    statusEl.textContent = isFoul ? 'FOUL' : 'OK';
    statusEl.className = 'tfluna-live-status' + (isFoul ? ' tfluna-live-foul' : ' tfluna-live-safe');
  }

  // Update foul/ok bar proportions
  const barMin = 100, barMax = 350;
  const foulPct = Math.max(0, Math.min(100, ((threshold - barMin) / (barMax - barMin)) * 100));
  if (foulZone) foulZone.style.flex = `0 0 ${foulPct}%`;
  if (okZone) okZone.style.flex = `0 0 ${100 - foulPct}%`;

  // Position marker arrow
  if (marker && arrow) {
    marker.style.display = '';
    const pct = Math.max(0, Math.min(100, ((dist - barMin) / (barMax - barMin)) * 100));
    arrow.style.left = pct + '%';
    arrow.className = 'tfluna-marker-arrow' + (isFoul ? ' tfluna-marker-foul' : ' tfluna-marker-ok');
  }
}

// ══════════════════════════════════════════════════════════════════════
// Game Mode System
// ══════════════════════════════════════════════════════════════════════

let _selectedGameMode = null;
let _gameMode = null;        // current active mode
let _gameOpts = {};           // options for current game
let _gameFirstPlayer = 1;

const _gameModeCopy = {
  x01: {
    kicker: 'X01 Selected',
    title: 'X01 Match Setup',
    description: 'Based on 01-game countdown play: reduce your score to exactly zero, with optional double-out finishing for a stricter tournament feel.',
  },
  cricket: {
    kicker: 'Cricket Selected',
    title: 'Cricket Match Setup',
    description: 'Standard Cricket closes scoring targets with marks; Random Cricket keeps the same mark logic but changes the live target numbers for each match.',
  },
  countup: {
    kicker: 'Count Up Selected',
    title: 'Count Up Session Setup',
    description: 'Count Up totals every round, and Multiple Count-Up boosts later darts in the round for a faster-scoring practice challenge.',
  },
};

function setGameFirstPlayer(player) {
  _gameFirstPlayer = Number(player) === 2 ? 2 : 1;
  document.getElementById('game-first-player-1')?.classList.toggle('active', _gameFirstPlayer === 1);
  document.getElementById('game-first-player-2')?.classList.toggle('active', _gameFirstPlayer === 2);
  updateGameConfigView();
}

function _isBullOffEnabled() {
  const input = document.getElementById('match-use-bulloff');
  return input ? input.checked : true;
}

function _isBotEnabled() {
  return !!document.getElementById('match-use-bot')?.checked;
}

function _resolveBotConfig() {
  // Returns null when bot is disabled.
  if (!_isBotEnabled()) return null;
  const modeEl = document.getElementById('bot-mode');
  const skillEl = document.getElementById('bot-skill');
  const mode = modeEl?.value === 'auto_advance' ? 'auto_advance' : 'simulated';
  const skillRaw = (skillEl?.value || 'medium').toLowerCase();
  const skill = ['easy', 'medium', 'hard'].includes(skillRaw) ? skillRaw : 'medium';
  return { enabled: true, mode, skill, seat: 2, name: 'Bot' };
}

function onBotToggleChanged() {
  const on = _isBotEnabled();
  const panel = document.getElementById('bot-config-grid');
  if (panel) panel.style.display = on ? '' : 'none';
  const p2Input = document.getElementById('game-p2-name');
  if (p2Input) {
    if (on) {
      p2Input.value = 'Bot';
      p2Input.readOnly = true;
      p2Input.classList.add('input-locked');
    } else {
      if (p2Input.value === 'Bot') p2Input.value = '';
      p2Input.readOnly = false;
      p2Input.classList.remove('input-locked');
    }
  }
  // Reflect bot skill field visibility based on bot mode.
  _syncBotSkillVisibility();
  onPlayerNameInput();
}

function _syncBotSkillVisibility() {
  const modeEl = document.getElementById('bot-mode');
  const skillField = document.getElementById('bot-skill-field');
  if (!modeEl || !skillField) return;
  skillField.style.display = modeEl.value === 'simulated' ? '' : 'none';
}

function _resolvePlayerNames() {
  // Read inputs and return [p1, p2] with "Player N" fallback when empty.
  const raw1 = (document.getElementById('game-p1-name')?.value || '').trim();
  const raw2 = (document.getElementById('game-p2-name')?.value || '').trim();
  const p1 = raw1.slice(0, 24) || 'Player 1';
  let p2 = raw2.slice(0, 24) || 'Player 2';
  if (_isBotEnabled()) p2 = 'Bot';
  return [p1, p2];
}

function onPlayerNameInput() {
  // Live-update the starting-player segment labels as the user types.
  const [n1, n2] = _resolvePlayerNames();
  const b1 = document.getElementById('game-first-player-1');
  const b2 = document.getElementById('game-first-player-2');
  if (b1) b1.textContent = n1;
  if (b2) b2.textContent = n2;
  updateGameConfigView?.();
}

// Module-level cache of the current match's player names so pre-game
// UI (bullseye throw-off) and server events that arrive before a full
// re-render can still show the custom names the user typed.
let _currentPlayerNames = ['Player 1', 'Player 2'];

function applyScoreboardPlayerNames(names) {
  if (!Array.isArray(names) || names.length < 2) return;
  _currentPlayerNames = [String(names[0] || 'Player 1'),
                        String(names[1] || 'Player 2')];
  document.querySelectorAll('#player-panel-1 .pp-name').forEach(el => {
    el.textContent = _currentPlayerNames[0];
  });
  document.querySelectorAll('#player-panel-2 .pp-name').forEach(el => {
    el.textContent = _currentPlayerNames[1];
  });
  // Propagate to the bullseye (pre-game) screen so P1/P2 cards + prompts
  // reflect the typed names and the bot's configured name.
  applyBullseyePlayerNames(_currentPlayerNames);
  // Re-apply flight colors — panels may have just been re-shown.
  if (typeof _applyPlayerFlights === 'function') _applyPlayerFlights();
}

function applyBullseyePlayerNames(names) {
  if (!Array.isArray(names) || names.length < 2) return;
  const bp1Label = document.querySelector('#bullseye-p1 .bp-label');
  const bp2Label = document.querySelector('#bullseye-p2 .bp-label');
  if (bp1Label) bp1Label.textContent = names[0];
  if (bp2Label) bp2Label.textContent = names[1];
}

function _selectedGameConfig() {
  if (!_selectedGameMode) return null;

  const config = {
    mode: _selectedGameMode,
    bullOff: _isBullOffEnabled(),
    firstPlayer: _gameFirstPlayer,
    playerNames: _resolvePlayerNames(),
    botConfig: _resolveBotConfig(),
    options: {},
  };

  if (_selectedGameMode === 'x01') {
    config.options.starting_score = parseInt(document.getElementById('x01-starting-score')?.value || '501', 10);
    config.options.finish_rule = String(document.getElementById('x01-finish-rule')?.value || 'straight_out');
  } else if (_selectedGameMode === 'cricket') {
    config.options.variant = String(document.getElementById('cricket-variant')?.value || 'standard');
  } else if (_selectedGameMode === 'countup') {
    config.options.total_rounds = parseInt(document.getElementById('countup-rounds')?.value || '8', 10);
    config.options.variant = String(document.getElementById('countup-variant')?.value || 'standard');
  }

  return config;
}

function _gameConfigNotes(config) {
  if (!config) {
    return ['Choose a mode to see the rules and launch summary.'];
  }

  const notes = [];
  notes.push(config.bullOff
    ? 'Bull-off enabled. The match begins with a bullseye throw to decide who starts.'
    : `Bull-off disabled. Player ${config.firstPlayer} will throw first immediately.`);

  if (config.mode === 'x01') {
    const finishText = config.options.finish_rule === 'double_out' ? 'Double Out' : 'Straight Out';
    notes.push(`${config.options.starting_score} start. Reach exactly zero to win under ${finishText} rules.`);
    notes.push(finishText === 'Double Out'
      ? 'A non-double checkout will bust the turn, matching common tournament 501 formats.'
      : 'Any segment can finish the leg, which keeps the match flow closer to casual straight-out play.');
  } else if (config.mode === 'cricket') {
    notes.push(config.options.variant === 'random'
      ? 'Random Cricket chooses six live scoring numbers plus Bull for this match.'
      : 'Standard Cricket uses 20 through 15 plus Bull as the live scoring targets.');
    notes.push('Triples count as three marks, doubles count as two, and scoring only happens on targets your opponent has not closed.');
  } else if (config.mode === 'countup') {
    notes.push(`${config.options.total_rounds} rounds total. Each player throws three darts per round.`);
    notes.push(config.options.variant === 'multiple'
      ? 'Multiple Count-Up applies x1, x2, and x3 scoring to the first, second, and third dart of every round.'
      : 'Standard Count Up totals the raw score of every dart across the selected number of rounds.');
  }

  return notes;
}

function _gameLaunchSummary(config) {
  if (!config) return 'Choose a mode to see the launch summary.';

  const summaryBits = [config.bullOff ? 'Bull-off' : `Player ${config.firstPlayer} starts`];

  if (config.mode === 'x01') {
    summaryBits.push(`${config.options.starting_score}`);
    summaryBits.push(config.options.finish_rule === 'double_out' ? 'Double Out' : 'Straight Out');
  } else if (config.mode === 'cricket') {
    summaryBits.push(config.options.variant === 'random' ? 'Random Cricket' : 'Standard Cricket');
  } else if (config.mode === 'countup') {
    summaryBits.push(`${config.options.total_rounds} Rounds`);
    summaryBits.push(config.options.variant === 'multiple' ? 'Multiple Count-Up' : 'Standard Count Up');
  }

  return summaryBits.join(' • ');
}

function updateGameConfigView() {
  document.querySelectorAll('.game-card').forEach((card) => {
    card.classList.toggle('selected', card.id === `gc-${_selectedGameMode}`);
  });

  const config = _selectedGameConfig();
  const copy = _gameModeCopy[_selectedGameMode] || {
    kicker: 'Choose a mode',
    title: 'Select a game from the left',
    description: 'Each mode exposes real match options supported by the current Throw Vision rules engine.',
  };

  const kicker = document.getElementById('game-config-kicker');
  const title = document.getElementById('game-config-title');
  const desc = document.getElementById('game-config-description');
  if (kicker) kicker.textContent = copy.kicker;
  if (title) title.textContent = copy.title;
  if (desc) desc.textContent = copy.description;

  document.getElementById('config-x01')?.classList.toggle('active', _selectedGameMode === 'x01');
  document.getElementById('config-cricket')?.classList.toggle('active', _selectedGameMode === 'cricket');
  document.getElementById('config-countup')?.classList.toggle('active', _selectedGameMode === 'countup');

  const firstPlayerControls = document.getElementById('game-first-player-controls');
  if (firstPlayerControls) {
    const show = !_isBullOffEnabled();
    firstPlayerControls.hidden = !show;
    firstPlayerControls.style.display = show ? '' : 'none';
  }

  const notesEl = document.getElementById('game-notes-list');
  if (notesEl) {
    notesEl.innerHTML = _gameConfigNotes(config).map((note) =>
      `<div class="game-note-item">${note}</div>`
    ).join('');
  }

  const summaryEl = document.getElementById('game-launch-summary');
  if (summaryEl) summaryEl.textContent = _gameLaunchSummary(config);

  const startBtn = document.getElementById('btn-start-game');
  if (startBtn) startBtn.disabled = !_selectedGameMode;
}

// ── Game Selection ──────────────────────────────────────────────────

function selectGameMode(mode) {
  _selectedGameMode = mode;
  updateGameConfigView();
}

function startGame() {
  const config = _selectedGameConfig();
  if (!config) return;
  if (!socket || !socket.connected) {
    alert('System is offline');
    return;
  }

  _resetPracticeState();   // clear any lingering practice state
  _gameTurnReviewReady = false;
  _setGameReviewButtonVisible(false);
  closePracticeReviewModal();
  _gameMode = config.mode;
  _gameOpts = {
    ...config.options,
    bull_off: config.bullOff,
    first_player: config.firstPlayer,
    player_names: config.playerNames,
    bot_config: config.botConfig,
  };
  _prevGamePlayer = null;  // Reset turn tracking for new game
  applyScoreboardPlayerNames(config.playerNames);

  // Clear any residual dots from previous sessions
  clearBoardDots();

  // Show game page and launch the selected match flow
  showPage('game');
  document.getElementById('bullseye-phase').style.display = config.bullOff ? '' : 'none';
  document.getElementById('game-active').style.display = 'none';
  document.getElementById('game-result-overlay').style.display = 'none';

  // Show opening cameras status immediately before server starts the process
  setStatus('waiting', 'Opening cameras…');

  if (config.bullOff) {
    socket.emit('start_bullseye', {
      mode: _gameMode,
      options: config.options,
      player_names: config.playerNames,
      bot_config: config.botConfig,
    });
  } else {
    socket.emit('start_game', {
      mode: _gameMode,
      options: config.options,
      first_player: config.firstPlayer,
      player_names: config.playerNames,
      bot_config: config.botConfig,
    });
  }
}

function restartGame() {
  _resetPracticeState();
  _gameTurnReviewReady = false;
  _setGameReviewButtonVisible(false);
  closePracticeReviewModal();
  // Clear any residual dots
  clearBoardDots();
  document.getElementById('game-result-overlay').style.display = 'none';
  showPage('game');
  const bullOff = !!_gameOpts.bull_off;
  const firstPlayer = Number(_gameOpts.first_player || 1);
  const playerNames = Array.isArray(_gameOpts.player_names)
    ? _gameOpts.player_names
    : ['Player 1', 'Player 2'];
  const botConfig = _gameOpts.bot_config || null;
  const options = { ..._gameOpts };
  delete options.bull_off;
  delete options.first_player;
  delete options.player_names;
  delete options.bot_config;
  document.getElementById('bullseye-phase').style.display = bullOff ? '' : 'none';
  document.getElementById('game-active').style.display = 'none';
  applyScoreboardPlayerNames(playerNames);
  if (bullOff) {
    socket.emit('start_bullseye', {
      mode: _gameMode,
      options,
      player_names: playerNames,
      bot_config: botConfig,
    });
  } else {
    socket.emit('start_game', {
      mode: _gameMode,
      options,
      first_player: firstPlayer,
      player_names: playerNames,
      bot_config: botConfig,
    });
  }
}

function endGame() {
  if (socket) socket.emit('end_game');
  if (socket) socket.emit('stop_detection');
  if (socket) socket.emit('close_cameras');
  _gameTurnReviewReady = false;
  _setGameReviewButtonVisible(false);
  closePracticeReviewModal();
  _resetPracticeAccuracyState({ closeModal: true });
  _gameMode = null;
  // Clear all dart dots
  clearBoardDots();
  // ── Reset bullseye pre-game UI so next session starts clean ──────────
  const prompt   = document.getElementById('bullseye-prompt');
  const p1dist   = document.getElementById('bp1-distance');
  const p2dist   = document.getElementById('bp2-distance');
  const p1label  = document.getElementById('bp1-label');
  const p2label  = document.getElementById('bp2-label');
  const tiebreak = document.getElementById('bullseye-tiebreak');
  const bp1      = document.getElementById('bullseye-p1');
  const bp2      = document.getElementById('bullseye-p2');
  const turnInfo = document.getElementById('game-turn-info');
  // Use innerHTML (not textContent) so ▶ Continue button markup is fully removed
  if (prompt)   { prompt.innerHTML = ''; prompt.className = 'bullseye-prompt'; }
  if (p1dist)   p1dist.textContent  = '—';
  if (p2dist)   p2dist.textContent  = '—';
  if (p1label)  p1label.textContent = '';
  if (p2label)  p2label.textContent = '';
  if (tiebreak) tiebreak.style.display = 'none';
  if (bp1)      bp1.classList.remove('active', 'winner');
  if (bp2)      bp2.classList.remove('active', 'winner');
  if (turnInfo) turnInfo.innerHTML  = '';
  window._awaitingTakeoutGame = false;
  // Reset phase divs so bullseye appears fresh on next entry
  const bphase  = document.getElementById('bullseye-phase');
  const gactive = document.getElementById('game-active');
  if (bphase)  bphase.style.display  = '';
  if (gactive) gactive.style.display = 'none';
  // Reset status bar to neutral
  setStatus('ready', 'Ready');
  showPage('home');
}

function skipTakeout() {
  window._awaitingTakeoutGame = false;
  // Immediately clear the "Darts removed! ▶ Continue" banner so it doesn't linger
  const turnInfo = document.getElementById('game-turn-info');
  if (turnInfo) turnInfo.innerHTML = '';
  const prompt = document.getElementById('bullseye-prompt');
  if (prompt) { prompt.textContent = 'Next player up…'; prompt.className = 'bullseye-prompt'; }
  setStatus('detecting', 'Waiting for Throw');
  if (socket) socket.emit('skip_takeout');
}

function undoGameDart() {
  if (socket) socket.emit('undo_dart');
}

// ── Bullseye Throw Phase ────────────────────────────────────────────

function onBullseyeState(state) {
  const prompt = document.getElementById('bullseye-prompt');
  const p1dist = document.getElementById('bp1-distance');
  const p2dist = document.getElementById('bp2-distance');
  const p1label = document.getElementById('bp1-label');
  const p2label = document.getElementById('bp2-label');
  const tiebreak = document.getElementById('bullseye-tiebreak');

  // Update distances
  p1dist.textContent = state.p1_distance !== null ? state.p1_distance.toFixed(1) + ' mm' : '—';
  p2dist.textContent = state.p2_distance !== null ? state.p2_distance.toFixed(1) + ' mm' : '—';
  p1label.textContent = state.p1_label || '';
  p2label.textContent = state.p2_label || '';
  p1label.classList.toggle('label-bounce', state.p1_label === 'BOUNCE');
  p2label.classList.toggle('label-bounce', state.p2_label === 'BOUNCE');

  // Place dart dots on the bullseye board
  const dotsG = document.getElementById('bullseye-board-dots');
  if (dotsG) {
    dotsG.innerHTML = '';  // Clear previous dots
    const scale = TOTAL_R / 170;
    const playerDots = [
      { coord: state.p1_coord, color: _flightHexFor(1) },  // P1 = chosen flight
      { coord: state.p2_coord, color: _flightHexFor(2) },  // P2 = chosen flight
    ];
    playerDots.forEach(({ coord, color }) => {
      if (coord) {
        const sx = BOARD_CX + coord[0] * scale;
        const sy = BOARD_CY - coord[1] * scale;
        appendPrecisionDot(dotsG, sx, sy, color, {
          outerR: 4.1,
          innerR: 1.35,
          strokeWidth: 1.05,
        });
      }
    });
  }

  // Highlight active player
  const bp1 = document.getElementById('bullseye-p1');
  const bp2 = document.getElementById('bullseye-p2');
  bp1.classList.remove('active', 'winner');
  bp2.classList.remove('active', 'winner');

  if (state.tiebreak_count > 0) {
    tiebreak.textContent = 'Tiebreak #' + state.tiebreak_count;
    tiebreak.style.display = '';
  } else {
    tiebreak.style.display = 'none';
  }

  // Pull names from the server state when provided, else fall back to
  // the module-level cache set at game-start. This keeps the bullseye
  // UI in sync with the custom names the user typed (or "Bot" when a
  // bot is seated).
  const names = (Array.isArray(state.player_names) && state.player_names.length >= 2)
    ? state.player_names
    : _currentPlayerNames;
  const n1 = names[0] || 'Player 1';
  const n2 = names[1] || 'Player 2';
  // Keep the bullseye cards' labels in sync too (belt-and-suspenders
  // in case start_bullseye arrived before applyScoreboardPlayerNames).
  applyBullseyePlayerNames([n1, n2]);

  switch (state.phase) {
    case 'player1_throw':
    case 'tiebreak_p1':
      prompt.textContent = `${n1}: Throw at the bullseye!`;
      prompt.className = 'bullseye-prompt p1-active';
      bp1.classList.add('active');
      setStatus('detecting', `${n1} Throwing`);
      break;
    case 'player2_throw':
    case 'tiebreak_p2':
      prompt.textContent = `${n2}: Throw at the bullseye!`;
      prompt.className = 'bullseye-prompt p2-active';
      bp2.classList.add('active');
      setStatus('detecting', `${n2} Throwing`);
      break;
    case 'result':
      if (state.winner === 1) {
        prompt.textContent = `${n1} goes first!`;
        prompt.className = 'bullseye-prompt p1-winner';
        bp1.classList.add('winner');
      } else {
        prompt.textContent = `${n2} goes first!`;
        prompt.className = 'bullseye-prompt p2-winner';
        bp2.classList.add('winner');
      }
      setStatus('ready', 'Result');
      break;
  }
}

function onBullseyeResult(state) {
  // Don't auto-switch — wait for user to remove darts and click Continue
  // The awaiting_takeout / takeout_ready events handle the prompt
  console.log('Bullseye finished, winner:', state.winner);
}

// ── Game State Handler ──────────────────────────────────────────────

let _prevGamePlayer = null;

function onGameState(state) {
  if (state.error) { console.error('Game error:', state.error); return; }
  _lastGameState = state;
  if (state.type === 'idle') return;
  if (state.type !== 'x01') {
    _gameTurnReviewReady = false;
  }
  // Only clear the takeout guard when this is a REAL game state (not a held-back
  // awaiting_takeout state). Held-back states keep the guard so onState can't
  // overwrite the Remove Darts status.
  if (!state.awaiting_takeout) {
    window._awaitingTakeoutGame = false;
  }

  const $active = document.getElementById('game-active');
  if ($active.style.display === 'none') {
    document.getElementById('bullseye-phase').style.display = 'none';
    $active.style.display = '';
    _prevGamePlayer = state.current_player;
    // Clear any leftover "Press Continue" status from the bullseye takeout
    setStatus('ready', 'Waiting for Throw');
  }

  // ── Sync board dots to match darts_this_turn exactly ──────────────
  // This handles undo (removes extra dot), new dart (adds dot), and
  // cross-turn undo (clears previous player's dots and restores prior turn).
  const dotsG = document.getElementById('game-board-dots');
  if (dotsG && state.darts_this_turn !== undefined) {
    dotsG.innerHTML = '';
    for (const dart of state.darts_this_turn) {
      if (dart.coord && dart.coord[0] !== null && dart.coord[1] !== null) {
        const scale = TOTAL_R / 170;
        const sx = BOARD_CX + dart.coord[0] * scale;
        const sy = BOARD_CY - dart.coord[1] * scale;
        appendPrecisionDot(dotsG, sx, sy, '#ff4444', {
          outerR: 3.4,
          innerR: 1.15,
          strokeWidth: 1.0,
        });
      }
    }
  }
  _prevGamePlayer = state.current_player;

  switch (state.type) {
    case 'x01': renderX01State(state); break;
    case 'cricket': renderCricketState(state); break;
    case 'countup': renderCountUpState(state); break;
  }
  _syncAccuracyReviewActionButtons();
}

function onGameOver(state) {
  const overlay = document.getElementById('game-result-overlay');
  const title = document.getElementById('gr-title');
  const sub = document.getElementById('gr-sub');

  if (state.winner === 0) {
    title.textContent = "It's a Tie!";
  } else {
    title.textContent = 'Player ' + state.winner + ' Wins!';
  }

  if (state.type === 'x01') {
    const darts = state.total_darts || [];
    sub.textContent = darts[state.winner - 1] ? darts[state.winner - 1] + ' darts' : '';
  } else if (state.type === 'countup') {
    sub.textContent = 'Score: ' + (state.scores[state.winner - 1] || 0);
  } else {
    sub.textContent = '';
  }

  overlay.style.display = 'flex';
}

function _setGameModeHeader(tag, title, rules) {
  const tagEl = document.getElementById('game-mode-tag');
  const titleEl = document.getElementById('game-mode-title');
  const rulesEl = document.getElementById('game-mode-rules');
  if (tagEl) tagEl.textContent = tag;
  if (titleEl) titleEl.textContent = title;
  if (rulesEl) rulesEl.textContent = rules;
}

function _setGameVisualMode(mode) {
  const gameActive = document.getElementById('game-active');
  const stage = gameActive?.querySelector('.game-stage');
  const body = gameActive?.querySelector('.game-body');
  if (gameActive) gameActive.dataset.mode = mode || '';
  if (stage) stage.dataset.mode = mode || '';
  if (body) body.dataset.mode = mode || '';
}

function _renderGameTurnInfo(state, chips) {
  const el = document.getElementById('game-turn-info');
  if (!el) return;

  if (state?.awaiting_takeout) {
    el.innerHTML = `<span class="game-turn-chip is-alert">${state.turn_info || 'Remove darts from the board!'}</span>`;
    return;
  }

  el.innerHTML = chips.join('');
}

function _renderPlayerMetrics(targetId, metrics) {
  const el = document.getElementById(targetId);
  if (!el) return;
  el.innerHTML = metrics.map((metric) => `
    <div class="pp-metric-card">
      <span>${metric.label}</span>
      <strong>${metric.value}</strong>
    </div>
  `).join('');
}

function _renderPlayerTurn(targetId, darts, mode) {
  const el = document.getElementById(targetId);
  if (!el) return;
  if (!darts || darts.length === 0) {
    el.innerHTML = '<div class="pp-empty-turn">No darts recorded in the current turn.</div>';
    return;
  }

  const index = darts.length - 1;
  const dart = darts[index];
  const scoreText = mode === 'countup' && Number(dart.multiplier || 1) > 1
    ? `${dart.score} pts`
    : `${dart.score}`;
  const meta = mode === 'countup' && Number(dart.multiplier || 1) > 1
    ? `x${dart.multiplier} multiplier`
    : (dart.points_added ? `+${dart.points_added} pts` : (dart.marks_added ? `${dart.marks_added} marks` : 'Live result'));

  el.innerHTML = `
    <div class="pp-dart-pill ${dart.bust ? 'is-bust' : ''}">
      <span class="pp-dart-index">Dart ${index + 1}</span>
      <strong>${dart.label}</strong>
      <small>${meta}</small>
      <em>${dart.bust ? 'BUST' : scoreText}</em>
    </div>
  `;
}

// ── Per-dart aim suggestions ────────────────────────────────────────
//
// Populates the empty dart cards with "Aim here next" hints:
//   * X01    : standard double-out checkout path (table generated at load).
//              Setup shot is T20 when the remaining score isn't on a
//              3-dart finish.
//   * Cricket: highest-value target the current player hasn't closed
//              (prefers triple form for 15-20, "BULL" for 25).
//   * Count Up: always T20 (max scoring shot).

const X01_CHECKOUTS = (() => {
  const doubles = {};
  for (let n = 1; n <= 20; n++) doubles[n * 2] = `D${n}`;
  doubles[50] = 'BULL';

  // Setup shots (first dart in a 2- or 3-dart finish): triples high->low,
  // BULL, outer bull, singles high->low.
  const setupShots = [];
  for (let n = 20; n >= 1; n--) setupShots.push({ score: n * 3, label: `T${n}` });
  setupShots.push({ score: 50, label: 'BULL' });
  setupShots.push({ score: 25, label: '25' });
  for (let n = 20; n >= 1; n--) setupShots.push({ score: n, label: `S${n}` });

  // Preferred finishing doubles (classic tournament order): D20 is safest
  // (miss lands in D5/D1 lanes), then D16, D18, D17... Bull is last because
  // it's a small target.
  const preferredDoubleOrder = [40, 32, 36, 34, 28, 24, 20, 16, 12, 8, 4,
                                 38, 30, 26, 22, 18, 14, 10, 6, 2, 50];

  const one = {};
  for (const s of Object.keys(doubles)) one[s] = [doubles[s]];

  const two = {};
  for (let r = 2; r <= 170; r++) {
    if (one[r]) { two[r] = one[r]; continue; }
    outer: for (const dScore of preferredDoubleOrder) {
      const setupNeed = r - dScore;
      if (setupNeed <= 0) continue;
      for (const s of setupShots) {
        if (s.score === setupNeed) {
          two[r] = [s.label, doubles[dScore]];
          break outer;
        }
      }
    }
  }

  const three = {};
  for (let r = 2; r <= 170; r++) {
    if (two[r] && two[r].length <= 2) { three[r] = two[r].slice(); continue; }
    for (const fc of setupShots) {
      const rem = r - fc.score;
      if (rem >= 2 && two[rem] && two[rem].length === 2) {
        three[r] = [fc.label, ...two[rem]];
        break;
      }
    }
  }
  return { one, two, three };
})();

function _x01LabelScore(label) {
  if (!label) return 0;
  if (label === 'BULL') return 50;
  if (label === '25') return 25;
  const m = String(label).match(/^([STD])(\d+)$/);
  if (!m) return 0;
  const n = Number(m[2]);
  const mult = m[1] === 'T' ? 3 : m[1] === 'D' ? 2 : 1;
  return n * mult;
}

function _x01ThrownSoFar(darts) {
  return (darts || []).reduce(
    (sum, d) => sum + (d && !d.bust ? Number(d.score || 0) : 0),
    0,
  );
}

function _suggestX01(state) {
  const out = [null, null, null];
  const playerIdx = Math.max(0, Number(state.current_player || 1) - 1);
  const scores = Array.isArray(state.scores) ? state.scores : [];
  const remainingAtTurnStart = Number(scores[playerIdx] || 0);
  const darts = Array.isArray(state.darts_this_turn) ? state.darts_this_turn : [];
  const thrown = _x01ThrownSoFar(darts);
  const remaining = remainingAtTurnStart - thrown;
  if (remaining <= 0) return out;
  if (remaining === 1) return out;

  const dartsLeft = 3 - darts.length;

  if (dartsLeft >= 1 && X01_CHECKOUTS.one[remaining]) {
    out[darts.length] = {
      label: X01_CHECKOUTS.one[remaining][0],
      hint: `Finish on ${remaining}`,
    };
    return out;
  }
  if (dartsLeft >= 2 && X01_CHECKOUTS.two[remaining]
      && X01_CHECKOUTS.two[remaining].length === 2) {
    const path = X01_CHECKOUTS.two[remaining];
    const leave = remaining - _x01LabelScore(path[0]);
    out[darts.length]     = { label: path[0], hint: `Checkout ${remaining}` };
    out[darts.length + 1] = { label: path[1], hint: `Finish on ${leave}` };
    return out;
  }
  if (dartsLeft >= 3 && X01_CHECKOUTS.three[remaining]
      && X01_CHECKOUTS.three[remaining].length === 3) {
    const path = X01_CHECKOUTS.three[remaining];
    const s1 = _x01LabelScore(path[0]);
    const s2 = _x01LabelScore(path[1]);
    out[0] = { label: path[0], hint: `Checkout ${remaining}` };
    out[1] = { label: path[1], hint: `Leave ${remaining - s1 - s2}` };
    out[2] = { label: path[2], hint: `Finish on ${remaining - s1 - s2}` };
    return out;
  }

  for (let i = darts.length; i < 3; i++) {
    out[i] = { label: 'T20', hint: 'Max scoring shot' };
  }
  return out;
}

const CRICKET_TARGET_LABEL = {
  15: 'T15', 16: 'T16', 17: 'T17', 18: 'T18', 19: 'T19', 20: 'T20', 25: 'BULL',
};

function _suggestCricket(state) {
  const out = [null, null, null];
  const playerIdx = Math.max(0, Number(state.current_player || 1) - 1);
  const numbers = Array.isArray(state.numbers) ? state.numbers : [20, 19, 18, 17, 16, 15, 25];
  const marks = Array.isArray(state.marks) ? state.marks : [];
  const me = marks[playerIdx] || {};
  const opp = marks[1 - playerIdx] || {};
  const darts = Array.isArray(state.darts_this_turn) ? state.darts_this_turn : [];

  const priority = [20, 19, 18, 17, 16, 15, 25].filter((n) => numbers.includes(n));
  let target = null;
  for (const n of priority) {
    if (Number(me[String(n)] || 0) < 3) { target = n; break; }
  }
  if (target == null) {
    for (const n of priority) {
      if (Number(me[String(n)] || 0) >= 3 && Number(opp[String(n)] || 0) < 3) {
        target = n; break;
      }
    }
  }
  if (target == null) return out;

  const label = CRICKET_TARGET_LABEL[target] || `S${target}`;
  const hint = Number(me[String(target)] || 0) < 3
    ? `Close ${target === 25 ? 'bull' : target}`
    : `Score on ${target === 25 ? 'bull' : target}`;
  for (let i = darts.length; i < 3; i++) {
    out[i] = { label, hint };
  }
  return out;
}

function _suggestCountUp(state) {
  const out = [null, null, null];
  const darts = Array.isArray(state.darts_this_turn) ? state.darts_this_turn : [];
  for (let i = darts.length; i < 3; i++) {
    out[i] = { label: 'T20', hint: 'Max scoring shot' };
  }
  return out;
}

function _computeAimSuggestions(state, mode) {
  if (!state) return [null, null, null];
  if (mode === 'x01') return _suggestX01(state);
  if (mode === 'cricket') return _suggestCricket(state);
  if (mode === 'countup') return _suggestCountUp(state);
  return [null, null, null];
}

function _buildThrowCard(index, dart, mode, aim) {
  const card = document.createElement('div');
  card.className = 'game-throw-card';
  if (!dart) card.classList.add('is-empty');
  if (dart && dart.bust) card.classList.add('is-bust');

  const kicker = document.createElement('span');
  kicker.className = 'game-throw-kicker';
  kicker.textContent = `Dart ${index + 1}`;
  card.appendChild(kicker);

  const strong = document.createElement('strong');
  const small = document.createElement('small');
  const em = document.createElement('em');

  if (!dart) {
    if (aim && aim.label) {
      card.classList.add('is-suggest');
      strong.textContent = aim.label;
      small.textContent = aim.hint || 'Suggested aim';
      em.className = 'game-throw-aim';
      em.textContent = 'AIM';
    } else {
      strong.textContent = 'No dart';
      small.textContent = 'Waiting for detection';
      em.textContent = '';
    }
  } else {
    const baseScore = Number(dart.score || 0);
    let meta;
    if (mode === 'countup' && Number(dart.multiplier || 1) > 1) {
      meta = `Multiplier x${dart.multiplier}`;
    } else if (dart.points_added) {
      meta = `Points +${dart.points_added}`;
    } else if (dart.marks_added) {
      meta = `Marks ${dart.marks_added}`;
    } else {
      meta = 'Recorded';
    }
    strong.textContent = String(dart.label || '');
    small.textContent = meta;
    em.textContent = dart.bust ? 'BUST' : String(baseScore);
  }

  card.appendChild(strong);
  card.appendChild(small);
  card.appendChild(em);
  return card;
}

function _renderGameThrowStrip(darts, mode, suggestions) {
  const el = document.getElementById('game-throw-strip');
  if (!el) return;
  const aims = Array.isArray(suggestions) ? suggestions : [null, null, null];
  while (el.firstChild) el.removeChild(el.firstChild);
  for (let i = 0; i < 3; i++) {
    el.appendChild(_buildThrowCard(i, darts[i], mode, aims[i]));
  }
}

function _renderModePanel(kicker, title, bodyHtml) {
  const el = document.getElementById('game-mode-panel');
  if (!el) return;
  el.innerHTML = `
    <div class="game-mode-panel-card">
      <div class="game-mode-panel-head">
        <span>${kicker}</span>
        <strong>${title}</strong>
      </div>
      <div class="game-mode-panel-body">${bodyHtml}</div>
    </div>
  `;
}

function _sumTurnDarts(darts) {
  return (darts || []).reduce((sum, dart) => sum + (dart && dart.bust ? 0 : Number(dart?.score || 0)), 0);
}

function _playerTurnHistory(history, player) {
  return (history || []).filter((turn) => Number(turn.player) === Number(player));
}

function _x01PlayerStats(state, player) {
  const turns = _playerTurnHistory(state.turn_history, player);
  const historyTotal = turns.reduce((sum, turn) => sum + Number(turn.total || 0), 0);
  const historyDarts = turns.reduce((sum, turn) => sum + ((turn.darts || []).length), 0);
  const liveTotal = state.current_player === player ? _sumTurnDarts(state.darts_this_turn) : 0;
  const liveDarts = state.current_player === player ? (state.darts_this_turn || []).length : 0;
  const scored = historyTotal + liveTotal;
  const darts = historyDarts + liveDarts;
  const bestTurn = turns.reduce((best, turn) => Math.max(best, Number(turn.total || 0)), 0);
  return {
    avg: darts > 0 ? (scored / darts) : 0,
    turns: turns.length,
    darts,
    bestTurn,
  };
}

function _renderX01Chalkboard(history) {
  if (!history || history.length === 0) {
    return '<div class="game-panel-empty">No completed turns yet. The chalkboard will fill as the match progresses.</div>';
  }

  const rows = history.slice(-5).reverse().map((turn) => {
    const left = turn.busted ? Number(turn.score_before || 0) : Math.max(0, Number(turn.score_before || 0) - Number(turn.total || 0));
    return `
      <div class="chalk-row ${turn.busted ? 'is-bust' : ''}">
        <span>T${turn.turn_index}</span>
        <span>P${turn.player}</span>
        <strong>${turn.busted ? 'BUST' : turn.total}</strong>
        <em>${left}</em>
      </div>
    `;
  }).join('');

  return `
    <div class="chalk-table">
      <div class="chalk-head">
        <span>Turn</span>
        <span>Player</span>
        <span>Scored</span>
        <span>Left</span>
      </div>
      ${rows}
    </div>
  `;
}

function _renderCricketMarks(count) {
  const safeCount = Math.max(0, Math.min(3, Number(count || 0)));
  return `
    <span class="cricket-mark ${safeCount >= 1 ? 'is-hit' : ''}"></span>
    <span class="cricket-mark ${safeCount >= 2 ? 'is-hit' : ''}"></span>
    <span class="cricket-mark ${safeCount >= 3 ? 'is-hit is-closed' : ''}"></span>
  `;
}

function _renderCricketBoard(state) {
  const numbers = state.numbers || [];
  if (numbers.length === 0) {
    return '<div class="game-panel-empty">No Cricket targets are active for this match.</div>';
  }

  return `
    <div class="cricket-board">
      <div class="cricket-board-head">
        <span>Player 1</span>
        <span>Target</span>
        <span>Player 2</span>
      </div>
      ${numbers.map((number) => {
        const key = String(number);
        const p1 = Number(state.marks?.[0]?.[key] || 0);
        const p2 = Number(state.marks?.[1]?.[key] || 0);
        return `
          <div class="cricket-board-row">
            <div class="cricket-board-marks">${_renderCricketMarks(p1)}</div>
            <strong>${number === 25 ? 'Bull' : number}</strong>
            <div class="cricket-board-marks">${_renderCricketMarks(p2)}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function _renderCountupBoard(state) {
  const totalRounds = Number(state.total_rounds || 0);
  const rows = Array.from({ length: totalRounds }, (_, index) => {
    const p1Round = state.round_scores?.[0]?.[index];
    const p2Round = state.round_scores?.[1]?.[index];
    const currentRound = index + 1 === Number(state.current_round || 1);
    return `
      <div class="countup-board-row ${currentRound ? 'is-current' : ''}">
        <span>R${index + 1}</span>
        <strong>${p1Round ? p1Round.total : '—'}</strong>
        <strong>${p2Round ? p2Round.total : '—'}</strong>
      </div>
    `;
  }).join('');

  return `
    <div class="countup-board">
      <div class="countup-board-head">
        <span>Round</span>
        <span>P1</span>
        <span>P2</span>
      </div>
      ${rows}
    </div>
  `;
}

// ── X01 Renderer ────────────────────────────────────────────────────

function renderX01State(s) {
  const finishRule = s.finish_rule === 'double_out' ? 'Double Out' : 'Straight Out';
  const turnTotal = _sumTurnDarts(s.darts_this_turn);
  const p1Stats = _x01PlayerStats(s, 1);
  const p2Stats = _x01PlayerStats(s, 2);

  _setGameVisualMode('x01');
  document.getElementById('game-title').textContent = `${s.starting_score} Match`;
  _setGameModeHeader('X01 MATCH', `${s.starting_score} Countdown`, `${s.starting_score} start • ${finishRule}`);

  document.getElementById('pp1-score-label').textContent = 'Remaining';
  document.getElementById('pp2-score-label').textContent = 'Remaining';
  document.getElementById('pp1-score').textContent = s.scores[0];
  document.getElementById('pp2-score').textContent = s.scores[1];
  document.getElementById('pp1-subline').textContent = `Match Avg ${p1Stats.avg.toFixed(2)}`;
  document.getElementById('pp2-subline').textContent = `Match Avg ${p2Stats.avg.toFixed(2)}`;

  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? 'Throwing' : 'Waiting';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing' : 'Waiting';
  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  _renderPlayerMetrics('pp1-metrics', [
    { label: 'Turns', value: p1Stats.turns },
    { label: 'Darts', value: p1Stats.darts },
    { label: 'Best Turn', value: p1Stats.bestTurn },
  ]);
  _renderPlayerMetrics('pp2-metrics', [
    { label: 'Turns', value: p2Stats.turns },
    { label: 'Darts', value: p2Stats.darts },
    { label: 'Best Turn', value: p2Stats.bestTurn },
  ]);

  _renderPlayerTurn('pp1-darts', s.current_player === 1 ? s.darts_this_turn : [], 'x01');
  _renderPlayerTurn('pp2-darts', s.current_player === 2 ? s.darts_this_turn : [], 'x01');
  _renderGameThrowStrip(s.darts_this_turn || [], 'x01', _computeAimSuggestions(s, 'x01'));

  _renderGameTurnInfo(s, [
    `<span class="game-turn-chip">Player ${s.current_player} Throwing</span>`,
    `<span class="game-turn-chip">Turn ${turnTotal}</span>`,
    `<span class="game-turn-chip">${(s.darts_this_turn || []).length}/3 darts</span>`,
  ]);

  _renderModePanel('Chalkboard', 'Last 5 turns', _renderX01Chalkboard(s.turn_history || []));
  renderTurnHistory(s.turn_history, 'x01');
}

// ── Cricket Renderer ────────────────────────────────────────────────

const CRICKET_DISPLAY = { 15: '15', 16: '16', 17: '17', 18: '18', 19: '19', 20: '20', 25: 'Bull' };

function renderCricketState(s) {
  const numbers = s.numbers || [15, 16, 17, 18, 19, 20, 25];
  const currentDarts = s.darts_this_turn || [];
  const p1Closed = numbers.filter((number) => Number(s.marks?.[0]?.[String(number)] || 0) >= 3).length;
  const p2Closed = numbers.filter((number) => Number(s.marks?.[1]?.[String(number)] || 0) >= 3).length;
  const p1Marks = numbers.reduce((sum, number) => sum + Number(s.marks?.[0]?.[String(number)] || 0), 0);
  const p2Marks = numbers.reduce((sum, number) => sum + Number(s.marks?.[1]?.[String(number)] || 0), 0);

  _setGameVisualMode('cricket');
  document.getElementById('game-title').textContent = 'Cricket Match';
  _setGameModeHeader(
    'CRICKET MATCH',
    s.variant === 'random' ? 'Random Cricket' : 'Standard Cricket',
    numbers.map((number) => CRICKET_DISPLAY[number] || number).join(' • ')
  );

  document.getElementById('pp1-score-label').textContent = 'Points';
  document.getElementById('pp2-score-label').textContent = 'Points';
  document.getElementById('pp1-score').textContent = s.points[0];
  document.getElementById('pp2-score').textContent = s.points[1];
  document.getElementById('pp1-subline').textContent = `${p1Closed}/${numbers.length} targets closed`;
  document.getElementById('pp2-subline').textContent = `${p2Closed}/${numbers.length} targets closed`;

  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? 'Throwing' : 'Waiting';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing' : 'Waiting';
  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  _renderPlayerMetrics('pp1-metrics', [
    { label: 'Marks', value: p1Marks },
    { label: 'Closed', value: p1Closed },
    { label: 'Turns', value: _playerTurnHistory(s.turn_history, 1).length },
  ]);
  _renderPlayerMetrics('pp2-metrics', [
    { label: 'Marks', value: p2Marks },
    { label: 'Closed', value: p2Closed },
    { label: 'Turns', value: _playerTurnHistory(s.turn_history, 2).length },
  ]);

  _renderPlayerTurn('pp1-darts', s.current_player === 1 ? currentDarts : [], 'cricket');
  _renderPlayerTurn('pp2-darts', s.current_player === 2 ? currentDarts : [], 'cricket');
  _renderGameThrowStrip(currentDarts, 'cricket', _computeAimSuggestions(s, 'cricket'));

  const turnPoints = currentDarts.reduce((sum, dart) => sum + Number(dart.points_added || 0), 0);
  _renderGameTurnInfo(s, [
    `<span class="game-turn-chip">Player ${s.current_player} Throwing</span>`,
    `<span class="game-turn-chip">${currentDarts.length}/3 darts</span>`,
    `<span class="game-turn-chip">${turnPoints > 0 ? `+${turnPoints} points` : 'Marking targets'}</span>`,
  ]);

  _renderModePanel('Target Board', s.variant === 'random' ? 'Random target set' : 'Standard targets', _renderCricketBoard(s));
  renderTurnHistory(s.turn_history, 'cricket');
}


function cricketMarksDisplay(count) {
  if (count === 0) return '<span class="cm-empty"></span>';
  if (count === 1) return '<span class="cm-mark">/</span>';
  if (count === 2) return '<span class="cm-mark">✕</span>';
  return '<span class="cm-mark cm-closed">⊗</span>';
}

// ── Count Up Renderer ───────────────────────────────────────────────

function renderCountUpState(s) {
  const turnDarts = s.darts_this_turn || [];
  const turnTotal = _sumTurnDarts(turnDarts);
  const p1Rounds = s.round_scores?.[0] || [];
  const p2Rounds = s.round_scores?.[1] || [];
  const p1Best = p1Rounds.reduce((best, round) => Math.max(best, Number(round.total || 0)), 0);
  const p2Best = p2Rounds.reduce((best, round) => Math.max(best, Number(round.total || 0)), 0);
  const p1Avg = p1Rounds.length ? (s.scores[0] / p1Rounds.length) : 0;
  const p2Avg = p2Rounds.length ? (s.scores[1] / p2Rounds.length) : 0;

  _setGameVisualMode('countup');
  document.getElementById('game-title').textContent = 'Count Up Match';
  _setGameModeHeader(
    'COUNT UP',
    s.variant === 'multiple' ? 'Multiple Count-Up' : 'Standard Count Up',
    `${s.total_rounds} rounds • ${s.variant === 'multiple' ? 'x1 / x2 / x3 scoring' : 'Raw scoring'}`
  );

  document.getElementById('pp1-score-label').textContent = 'Total';
  document.getElementById('pp2-score-label').textContent = 'Total';
  document.getElementById('pp1-score').textContent = s.scores[0];
  document.getElementById('pp2-score').textContent = s.scores[1];
  document.getElementById('pp1-subline').textContent = `Avg / Round ${p1Avg.toFixed(1)}`;
  document.getElementById('pp2-subline').textContent = `Avg / Round ${p2Avg.toFixed(1)}`;

  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? 'Throwing' : 'Waiting';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing' : 'Waiting';
  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  _renderPlayerMetrics('pp1-metrics', [
    { label: 'Rounds', value: p1Rounds.length },
    { label: 'Best Round', value: p1Best },
    { label: 'Darts', value: _playerTurnHistory(s.turn_history, 1).reduce((sum, turn) => sum + ((turn.darts || []).length), 0) },
  ]);
  _renderPlayerMetrics('pp2-metrics', [
    { label: 'Rounds', value: p2Rounds.length },
    { label: 'Best Round', value: p2Best },
    { label: 'Darts', value: _playerTurnHistory(s.turn_history, 2).reduce((sum, turn) => sum + ((turn.darts || []).length), 0) },
  ]);

  _renderPlayerTurn('pp1-darts', s.current_player === 1 ? turnDarts : [], 'countup');
  _renderPlayerTurn('pp2-darts', s.current_player === 2 ? turnDarts : [], 'countup');
  _renderGameThrowStrip(turnDarts, 'countup', _computeAimSuggestions(s, 'countup'));

  _renderGameTurnInfo(s, [
    `<span class="game-turn-chip">Round ${s.current_round} / ${s.total_rounds}</span>`,
    `<span class="game-turn-chip">Player ${s.current_player} Throwing</span>`,
    `<span class="game-turn-chip">Turn ${turnTotal}</span>`,
  ]);

  _renderModePanel('Round Board', 'Score by round', _renderCountupBoard(s));
  renderTurnHistory(s.turn_history, 'countup');
}

// ── Shared: Turn History ────────────────────────────────────────────

function renderTurnHistory(history, mode = 'x01') {
  const el = document.getElementById('game-history');
  const caption = document.getElementById('game-history-caption');
  if (!el) return;

  if (caption) {
    caption.textContent = mode === 'countup'
      ? 'Latest completed rounds'
      : (mode === 'cricket' ? 'Latest completed turns' : 'Latest scoring turns');
  }

  if (!history || history.length === 0) {
    el.innerHTML = '<div class="game-history-empty">No completed turns yet. Match history will appear here.</div>';
    return;
  }

  el.innerHTML = history.slice().reverse().map((turn) => {
    const darts = (turn.darts || []).map((dart) => dart.label).join(' • ');
    const total = turn.busted ? 'BUST' : (turn.total || 0);
    const meta = mode === 'countup'
      ? `Round ${turn.round || turn.turn_index || '—'}`
      : `Turn ${turn.turn_index || '—'}`;
    return `
      <div class="gh-card ${turn.busted ? 'is-busted' : ''}">
        <div class="gh-topline">
          <span class="gh-player">Player ${turn.player}</span>
          <span class="gh-meta">${meta}</span>
        </div>
        <div class="gh-darts">${darts || 'No darts'}</div>
        <div class="gh-total">${total}</div>
      </div>
    `;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════
// Statistics
// ══════════════════════════════════════════════════════════════════════

function loadStats(mode) {
  _statsMode = mode || null;
  // Update tabs
  document.querySelectorAll('.stats-tab').forEach(t => {
    t.classList.toggle('active', (t.dataset.mode || '') === (_statsMode || ''));
  });
  const dash = document.getElementById('stats-dashboard');
  if (dash) {
    dash.innerHTML = '<div class="stats-loading">Loading statistics...</div>';
  }
  // Fetch from server
  const url = _statsMode ? '/api/stats?mode=' + _statsMode : '/api/stats';
  fetch(url)
    .then(r => r.json())
    .then(data => renderStats(data, _statsMode))
    .catch(() => {
      if (dash) {
        dash.innerHTML = '<div class="stats-loading">Failed to load statistics.</div>';
      }
    });
}

function setStatsSection(section) {
  _statsSection = section === 'accuracy' ? 'accuracy' : 'games';
  document.querySelectorAll('.stats-section-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.section === _statsSection);
  });

  const gameSection = document.getElementById('stats-games-section');
  const accuracySection = document.getElementById('stats-accuracy-section');
  if (gameSection) {
    const active = _statsSection === 'games';
    gameSection.hidden = !active;
    gameSection.classList.toggle('active', active);
  }
  if (accuracySection) {
    const active = _statsSection === 'accuracy';
    accuracySection.hidden = !active;
    accuracySection.classList.toggle('active', active);
  }

  if (_statsSection === 'accuracy') {
    loadAccuracyStats();
  } else {
    loadStats(_statsMode);
  }
}

function loadAccuracyStats() {
  const dash = document.getElementById('accuracy-dashboard');
  if (dash) {
    dash.innerHTML = '<div class="stats-loading">Loading accuracy statistics...</div>';
  }

  fetch('/api/accuracy/summary')
    .then(r => r.json())
    .then(data => renderAccuracyStats(data))
    .catch(() => {
      if (dash) {
        dash.innerHTML = '<div class="stats-loading">Failed to load accuracy statistics.</div>';
      }
    });
}

function buildStatsRow(game, modeLabel, winnerText, date, summary) {
    const row = document.createElement('div');
    row.className = 'sr-row';
    if (game.has_review) {
        row.classList.add('sr-row--clickable');
        row.addEventListener('click', (e) => {
            if (e.target.closest('.sr-delete')) return;
            openMatchReview(Number(game.id));
        });
    }

    const main = document.createElement('div');
    main.className = 'sr-main';

    const topline = document.createElement('div');
    topline.className = 'sr-topline';
    const modeSpan = document.createElement('span');
    modeSpan.className = 'sr-mode';
    modeSpan.textContent = modeLabel;
    topline.appendChild(modeSpan);
    const summarySpan = document.createElement('span');
    summarySpan.className = 'sr-summary';
    summarySpan.textContent = summary;
    topline.appendChild(summarySpan);
    if (game.status === 'abandoned') {
        const badge = document.createElement('span');
        badge.className = 'mr-abandoned-badge';
        badge.textContent = 'Abandoned';
        topline.appendChild(badge);
    }
    main.appendChild(topline);

    const meta = document.createElement('div');
    meta.className = 'sr-meta';
    const winSpan = document.createElement('span');
    winSpan.textContent = winnerText;
    meta.appendChild(winSpan);
    const dateSpan = document.createElement('span');
    dateSpan.textContent = date;
    meta.appendChild(dateSpan);
    main.appendChild(meta);

    row.appendChild(main);

    if (game.id) {
        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-sm btn-secondary sr-delete';
        delBtn.textContent = 'Delete';
        delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteGameStat(Number(game.id));
        });
        row.appendChild(delBtn);
    }

    return row;
}

function renderStats(data, mode) {
  const dash = document.getElementById('stats-dashboard');
  const recentList = document.getElementById('stats-recent-list');
  if (!dash || !recentList) return;

  if (!data || data.games_played === 0) {
    dash.innerHTML = '<div class="stats-empty">No games played yet. Start a game to see your stats!</div>';
    recentList.innerHTML = '<div class="stats-empty">No saved matches yet.</div>';
    return;
  }

  let cards = '';
  cards += statCard('Games Played', data.games_played);
  cards += statCard('P1 Wins', data.p1_wins || 0);
  cards += statCard('P2 Wins', data.p2_wins || 0);

  if (mode === 'x01' || (!mode && data.by_mode && data.by_mode.x01)) {
    const s = mode ? data : (data.by_mode.x01 || {});
    cards += statCard('Avg / Dart', s.avg_per_dart || 0);
    cards += statCard('Avg / Round', s.avg_per_round || 0);
    cards += statCard('First 9 Avg', s.first9_avg || 0);
    cards += statCard('Checkout %', (s.checkout_pct || 0) + '%');
    cards += statCard('Highest Round', s.highest_round || 0);
    cards += statCard('180s', s.count_180 || 0);
    cards += statCard('140+', s.count_140_plus || 0);
    cards += statCard('100+', s.count_100_plus || 0);
    if (s.best_game_darts) cards += statCard('Best Game', s.best_game_darts + ' darts');
  }

  if (mode === 'cricket' || (!mode && data.by_mode && data.by_mode.cricket)) {
    const s = mode ? data : (data.by_mode.cricket || {});
    cards += statCard('Marks / Round', s.avg_marks_per_round || 0);
  }

  if (mode === 'countup' || (!mode && data.by_mode && data.by_mode.countup)) {
    const s = mode ? data : (data.by_mode.countup || {});
    cards += statCard('Avg / Dart', s.avg_per_dart || 0);
    cards += statCard('Avg / Round', s.avg_per_round || 0);
    cards += statCard('Best Score', s.best_game_score || 0);
    cards += statCard('Highest Round', s.highest_round || 0);
  }

  dash.innerHTML = '<div class="stats-cards">' + cards + '</div>';

  // Recent games
  const recent = data.recent || [];
  if (recent.length === 0) {
    recentList.innerHTML = '<div class="stats-empty">No saved matches yet.</div>';
  } else {
    recentList.replaceChildren();
    recent.forEach(g => {
      const date = formatStatsDate(g.started_at);
      const modeLabel = (g.mode || '').toUpperCase();
      const winnerText = g.winner ? 'Player ' + g.winner + ' won'
                       : (g.status === 'abandoned' ? 'Abandoned' : 'Tie');
      const summary = gameSummaryText(g);
      recentList.appendChild(buildStatsRow(g, modeLabel, winnerText, date, summary));
    });
  }
}

function statCard(label, value, className = '') {
  return `<div class="stat-card${className ? ' ' + className : ''}">
    <div class="sc-value">${value}</div>
    <div class="sc-label">${label}</div>
  </div>`;
}

function formatStatsDate(value) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}

function formatStatsPercent(value, digits = 2) {
  return value == null ? '—' : Number(value).toFixed(digits) + '%';
}

function formatStatsMs(value, digits = 2) {
  return value == null ? '—' : Number(value).toFixed(digits) + ' ms';
}

function gameSummaryText(game) {
  const mode = String(game.mode || '').toLowerCase();
  const players = game.players || [];
  if (mode === 'x01' || mode === 'countup') {
    const left = players[0]?.score ?? '—';
    const right = players[1]?.score ?? '—';
    return `${left} • ${right}`;
  }
  if (mode === 'cricket') {
    const left = players[0]?.points ?? '—';
    const right = players[1]?.points ?? '—';
    return `${left} pts • ${right} pts`;
  }
  return 'Saved match';
}

function renderAccuracyStats(data) {
  const dash = document.getElementById('accuracy-dashboard');
  if (!dash) return;
  const sessions = (data && data.sessions) || [];
  const hasData = data && (data.sessions_count > 0 || data.actual_darts > 0 || data.predicted_darts > 0);

  if (!hasData) {
    dash.innerHTML = '<div class="stats-empty">No reviewed accuracy sessions yet. Start practice or X01 to build review data.</div>';
    return;
  }

  const agreement = (data && data.agreement_counts) || {};
  let cards = '';
  cards += statCard('Active Model', data.active_model || '—', 'stat-card-wide');
  cards += statCard('Actual Darts', data.actual_darts || 0);
  cards += statCard('Predicted Darts', data.predicted_darts || 0);
  cards += statCard('Corrections', data.corrected_darts || 0);
  cards += statCard('Missed Darts', data.missed_darts || 0);
  cards += statCard('False Positives', data.false_positives || 0);
  cards += statCard('System Accuracy', formatStatsPercent(data.accuracy_pct));
  cards += statCard('Avg Detect→Score', formatStatsMs(data.avg_detect_to_score_ms));
  cards += statCard('Avg Pose Infer', formatStatsMs(data.avg_pose_infer_ms));

  const agreementCards = `
    <div class="accuracy-agreement-grid">
      <div class="accuracy-agreement-card">
        <div class="accuracy-agreement-title">3-Cam Agreement</div>
        <div class="accuracy-agreement-line">all match: <strong>${agreement['3cam_all_match'] || 0}</strong></div>
        <div class="accuracy-agreement-line">two match: <strong>${agreement['3cam_two_match'] || 0}</strong></div>
        <div class="accuracy-agreement-line">all diff: <strong>${agreement['3cam_all_diff'] || 0}</strong></div>
      </div>
      <div class="accuracy-agreement-card">
        <div class="accuracy-agreement-title">2-Cam Agreement</div>
        <div class="accuracy-agreement-line">match: <strong>${agreement['2cam_match'] || 0}</strong></div>
        <div class="accuracy-agreement-line">disagree: <strong>${agreement['2cam_disagree'] || 0}</strong></div>
      </div>
      <div class="accuracy-agreement-card">
        <div class="accuracy-agreement-title">Low Visibility</div>
        <div class="accuracy-agreement-line">single cam: <strong>${agreement['1cam_only'] || 0}</strong></div>
        <div class="accuracy-agreement-line">no detection: <strong>${agreement['no_detection'] || 0}</strong></div>
      </div>
    </div>
  `;

  const tableRows = sessions.map((session) => {
    const sessionAgreement = session.agreement_counts || {};
    const sessionLabel = session.context === 'game'
      ? `${String(session.session_mode || 'game').toUpperCase()} Review`
      : 'Practice Review';
    return `
      <tr>
        <td>${formatStatsDate(session.started_at)}</td>
        <td><span class="accuracy-session-badge">${sessionLabel}</span></td>
        <td>${session.model_name || '—'}</td>
        <td>${session.actual_darts || 0}</td>
        <td>${session.predicted_darts || 0}</td>
        <td>${session.corrected_darts || 0}</td>
        <td>${session.missed_darts || 0}</td>
        <td>${session.false_positives || 0}</td>
        <td>${sessionAgreement['3cam_all_match'] || 0}/${sessionAgreement['3cam_two_match'] || 0}/${sessionAgreement['3cam_all_diff'] || 0}</td>
        <td>${sessionAgreement['2cam_match'] || 0}/${sessionAgreement['2cam_disagree'] || 0}</td>
        <td>${sessionAgreement['1cam_only'] || 0}/${sessionAgreement['no_detection'] || 0}</td>
        <td>${formatStatsPercent(session.accuracy_pct)}</td>
        <td>${formatStatsMs(session.avg_detect_to_score_ms)}</td>
        <td>${formatStatsMs(session.avg_pose_infer_ms)}</td>
      </tr>
    `;
  }).join('');

  dash.innerHTML = `
    <div class="stats-cards accuracy-cards">${cards}</div>
    ${agreementCards}
    <div class="accuracy-table-wrap">
      <div class="accuracy-table-title">Accuracy Sessions</div>
      <div class="accuracy-table-scroll">
        <table class="accuracy-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>Type</th>
              <th>Model</th>
              <th>Actual</th>
              <th>Detected</th>
              <th>Corrections</th>
              <th>Missed</th>
              <th>False+</th>
              <th>3C A/T/D</th>
              <th>2C M/D</th>
              <th>1C / ND</th>
              <th>Accuracy</th>
              <th>Avg D→S</th>
              <th>Avg Infer</th>
            </tr>
          </thead>
          <tbody>${tableRows || '<tr><td colspan="14">No accuracy sessions yet.</td></tr>'}</tbody>
        </table>
      </div>
    </div>
  `;
}

async function resetGameStats() {
  const confirmed = await showConfirmModal({
    kicker: 'Game Statistics',
    icon: '⚠️',
    title: 'Reset Game Stats',
    message: 'Delete all saved game statistics and match history? This action cannot be undone.',
    confirmText: 'Reset Stats',
  });
  if (!confirmed) return;

  try {
    const res = await fetch('/api/stats/reset', { method: 'POST' });
    if (!res.ok) throw new Error('reset failed');
    showToast('Game statistics reset.', 'ok', 2600);
    loadStats(_statsMode);
  } catch {
    showToast('Failed to reset game statistics.', 'warn', 2600);
  }
}

async function deleteGameStat(gameId) {
  if (!gameId) return;
  const confirmed = await showConfirmModal({
    kicker: 'Match History',
    icon: '🗑️',
    title: 'Delete Saved Match',
    message: 'Delete this saved match from history? The stored result for this entry will be permanently removed.',
    confirmText: 'Delete Match',
  });
  if (!confirmed) return;

  try {
    const res = await fetch(`/api/stats/game/${gameId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('delete failed');
    showToast('Match removed from history.', 'ok', 2400);
    loadStats(_statsMode);
  } catch {
    showToast('Failed to delete saved match.', 'warn', 2600);
  }
}

async function openMatchReview(matchId) {
    try {
        const resp = await fetch(`/api/matches/${matchId}/review`);
        if (!resp.ok) {
            alert('Review not available');
            return;
        }
        const data = await resp.json();
        window._currentMatchReview = data;
        showPage('match-review');
        renderMatchReview(data);
    } catch (err) {
        console.error('openMatchReview failed', err);
    }
}
function fmtMm(v) {
    if (v === null || v === undefined) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${Number(v).toFixed(1)}mm`;
}


function _mrThumb(matchId, kind, player, round, dart, cam) {
    const img = document.createElement('img');
    img.className = 'mr-thumb';
    img.loading = 'lazy';
    img.src = `/api/matches/${matchId}/frame/${kind}/${player}/${round}/${dart}/${cam}`;
    img.alt = `cam${cam}`;
    img.addEventListener('error', () => img.classList.add('mr-thumb--missing'));
    img.addEventListener('click',
        () => openLightbox(matchId, kind, player, round, dart, cam));
    return img;
}


function _mrThumbRow(labelText, thumbs) {
    const row = document.createElement('div');
    row.className = 'mr-thumbrow';
    const label = document.createElement('span');
    label.className = 'mr-thumb-label';
    label.textContent = labelText;
    row.appendChild(label);
    thumbs.forEach(t => row.appendChild(t));
    return row;
}


function renderMatchReview(data) {
    // Title
    const titleEl = document.querySelector('.mr-title');
    if (titleEl) titleEl.textContent = `Match #${data.match_id} Review`;

    // Abandonment banner
    const banner = document.getElementById('mr-abandoned-banner');
    if (data.status === 'abandoned') {
        const reasonMap = {
            user_quit: 'quit by user',
            server_restart: 'server restart',
            cameras_lost: 'cameras lost',
        };
        banner.style.display = '';
        banner.textContent =
            `⚠  This match was not completed — reason: ${reasonMap[data.abandoned_reason] || data.abandoned_reason || 'unknown'}`;
    } else {
        banner.style.display = 'none';
        banner.textContent = '';
    }

    // Meta
    const meta = document.getElementById('mr-meta');
    const winnerTxt = data.winner
        ? `Player ${data.winner} won`
        : (data.status === 'abandoned' ? '—' : 'Draw');
    const startedTs = data.started_at
        ? new Date(data.started_at * 1000).toLocaleString() : '';
    const totalDarts = (data.players || []).reduce(
        (n, p) => n + (p.turns || []).reduce((m, t) => m + (t.darts || []).length, 0), 0);
    meta.textContent =
        `${(data.mode || '').toUpperCase()} • ${winnerTxt} • ${startedTs} • ${totalDarts} darts`;

    // Delete button
    const delBtn = document.getElementById('mr-delete-btn');
    delBtn.onclick = async () => {
        if (!confirm('Delete this match permanently?')) return;
        await fetch(`/api/stats/game/${data.match_id}`, { method: 'DELETE' });
        showPage('stats');
        if (typeof refreshStats === 'function') refreshStats();
    };

    // Tabs
    const tabs = document.getElementById('mr-tabs');
    tabs.replaceChildren();
    (data.players || []).forEach((p, idx) => {
        const btn = document.createElement('button');
        btn.className = 'mr-tab' + (idx === 0 ? ' mr-tab--active' : '');
        btn.textContent = p.name || `Player ${p.player}`;
        btn.dataset.seat = String(p.player);
        // Tint the tab with the player's flight color when available.
        if (typeof _playerFlights !== 'undefined'
            && (p.player === 1 || p.player === 2)) {
            btn.setAttribute('data-flight', _playerFlights[p.player - 1]);
        }
        btn.addEventListener('click', () => {
            tabs.querySelectorAll('.mr-tab').forEach(b => b.classList.remove('mr-tab--active'));
            btn.classList.add('mr-tab--active');
            renderMatchReviewPlayer(data, p.player);
        });
        tabs.appendChild(btn);
    });

    if (data.players && data.players.length) {
        renderMatchReviewPlayer(data, data.players[0].player);
    }
}


function renderMatchReviewPlayer(data, playerNum) {
    const turnsEl = document.getElementById('mr-turns');
    turnsEl.replaceChildren();
    const p = (data.players || []).find(x => x.player === playerNum);
    if (!p) return;

    (p.turns || []).forEach(turn => {
        const card = document.createElement('div');
        card.className = 'mr-turn-card';

        // Head
        const head = document.createElement('div');
        head.className = 'mr-turn-card__head';
        const headLabel = document.createElement('span');
        headLabel.textContent = `Round ${turn.round} · Turn ${turn.index + 1}`;
        head.appendChild(headLabel);

        const total = (turn.darts || []).reduce((s, d) => s + (d.score || 0), 0);
        const totalEl = document.createElement('span');
        totalEl.className = 'mr-turn-card__total';
        totalEl.textContent = `Total: ${total}`;
        head.appendChild(totalEl);
        card.appendChild(head);

        // Dart rows
        const dartsEl = document.createElement('div');
        dartsEl.className = 'mr-turn-card__darts';
        (turn.darts || []).forEach(d => {
            const row = document.createElement('div');
            row.className = 'mr-dart-row';

            const l1 = document.createElement('span');
            l1.className = 'mr-dart-label';
            l1.textContent = `Dart ${d.dart_index + 1}:`;
            row.appendChild(l1);

            const l2 = document.createElement('span');
            l2.className = 'mr-dart-call';
            l2.textContent = `${d.label} (${d.score})`;
            row.appendChild(l2);

            const l3 = document.createElement('span');
            l3.className = 'mr-dart-mm';
            l3.textContent = `${fmtMm(d.x_mm)}, ${fmtMm(d.y_mm)}`;
            row.appendChild(l3);

            const l4 = document.createElement('span');
            l4.className = 'mr-dart-agree';
            l4.textContent = d.agreement_bucket || '';
            row.appendChild(l4);

            dartsEl.appendChild(row);
        });
        card.appendChild(dartsEl);

        // Captures
        const caps = document.createElement('div');
        caps.className = 'mr-turn-card__captures';

        const capLabel = document.createElement('div');
        capLabel.className = 'mr-captures-label';
        capLabel.textContent = 'Per-dart captures:';
        caps.appendChild(capLabel);

        (turn.darts || []).forEach(d => {
            const thumbs = [0, 1, 2].map(cam =>
                _mrThumb(data.match_id, 'per_dart', playerNum, turn.round, d.dart_index, cam));
            caps.appendChild(_mrThumbRow(`Dart ${d.dart_index + 1}:`, thumbs));
        });

        if (turn.end_of_turn) {
            const eotLabel = document.createElement('div');
            eotLabel.className = 'mr-captures-label';
            eotLabel.textContent = 'End-of-turn:';
            caps.appendChild(eotLabel);
            const thumbs = [0, 1, 2].map(cam =>
                _mrThumb(data.match_id, 'eot', playerNum, turn.round, 0, cam));
            caps.appendChild(_mrThumbRow('EOT:', thumbs));
        }

        card.appendChild(caps);
        turnsEl.appendChild(card);
    });
}
window.renderMatchReview = renderMatchReview;
window.openMatchReview = openMatchReview;

async function resetAccuracyStats() {
  const confirmed = await showConfirmModal({
    kicker: 'Accuracy Review',
    icon: '⚠️',
    title: 'Reset Accuracy Stats',
    message: 'Delete all saved accuracy review sessions? This action cannot be undone.',
    confirmText: 'Reset Accuracy',
  });
  if (!confirmed) return;

  try {
    const res = await fetch('/api/accuracy/reset', { method: 'POST' });
    if (!res.ok) throw new Error('reset failed');
    showToast('Accuracy statistics reset.', 'ok', 2600);
    loadAccuracyStats();
  } catch {
    showToast('Failed to reset accuracy statistics.', 'warn', 2600);
  }
}

function onStatsData(data) {
  if ((data && data.mode) === 'accuracy') {
    if (_statsSection === 'accuracy') renderAccuracyStats(data);
    return;
  }
  if (_statsSection === 'games' || currentPage !== 'stats') {
    renderStats(data, data.mode || _statsMode);
  }
}

const _lightbox = {
    matchId: null,
    sequence: [],  // list of {kind, player, round, dart, cam}
    position: 0,
    warped: false,
};


function buildLightboxSequence(data) {
    const seq = [];
    (data.players || []).forEach(p => {
        const playerName = p.name || `Player ${p.player}`;
        (p.turns || []).forEach(t => {
            const dartsInTurn = t.darts || [];
            const turnTotal = dartsInTurn.reduce(
                (sum, x) => sum + Number(x.score || 0), 0,
            );
            const turnSequenceLabels = dartsInTurn
                .map(x => x.label || '—')
                .join(' · ');
            dartsInTurn.forEach(d => {
                for (let cam = 0; cam < 3; cam++) {
                    seq.push({
                        kind: 'per_dart',
                        player: p.player,
                        playerName,
                        round: t.round,
                        dart: d.dart_index,
                        cam,
                        dartLabel: d.label || '',
                        dartScore: Number(d.score || 0),
                        xMm: (d.x_mm == null) ? null : Number(d.x_mm),
                        yMm: (d.y_mm == null) ? null : Number(d.y_mm),
                        agreement: d.agreement_bucket || '',
                        bot: !!d.bot,
                        turnTotal,
                        turnSequenceLabels,
                    });
                }
            });
            if (t.end_of_turn) {
                for (let cam = 0; cam < 3; cam++) {
                    seq.push({
                        kind: 'eot',
                        player: p.player,
                        playerName,
                        round: t.round,
                        dart: 0,
                        cam,
                        turnTotal,
                        turnSequenceLabels,
                    });
                }
            }
        });
    });
    return seq;
}


function openLightbox(matchId, kind, player, round, dart, cam) {
    const data = window._currentMatchReview;
    if (!data) return;

    // Build the full sequence, then narrow to the 3 camera captures for the
    // clicked dart/EOT so the lightbox isn't paging through the whole match.
    const fullSequence = buildLightboxSequence(data);
    const filtered = fullSequence.filter(s =>
        s.kind === kind && s.player === player && s.round === round
        && s.dart === dart);
    _lightbox.sequence = filtered.length ? filtered : fullSequence;
    _lightbox.position = _lightbox.sequence.findIndex(s =>
        s.kind === kind && s.player === player && s.round === round
        && s.dart === dart && s.cam === cam);
    if (_lightbox.position < 0) _lightbox.position = 0;
    _lightbox.warped = false;
    _lightbox.matchId = matchId;
    _applyLightbox();
    document.getElementById('mr-lightbox').style.display = '';
    document.addEventListener('keydown', _lightboxKey);
}


function closeLightbox() {
    document.getElementById('mr-lightbox').style.display = 'none';
    document.removeEventListener('keydown', _lightboxKey);
}


function lightboxPrev() {
    if (_lightbox.position > 0) {
        _lightbox.position -= 1;
        _applyLightbox();
    }
}


function lightboxNext() {
    if (_lightbox.position < _lightbox.sequence.length - 1) {
        _lightbox.position += 1;
        _applyLightbox();
    }
}


function _lightboxKey(ev) {
    if (ev.key === 'Escape') closeLightbox();
    else if (ev.key === 'ArrowLeft') lightboxPrev();
    else if (ev.key === 'ArrowRight') lightboxNext();
}


function _applyLightbox() {
    const item = _lightbox.sequence[_lightbox.position];
    if (!item) return;
    const suffix = _lightbox.warped ? '/warped' : '';
    const url = `/api/matches/${_lightbox.matchId}/frame/${item.kind}/${item.player}/${item.round}/${item.dart}/${item.cam}${suffix}`;
    document.getElementById('mr-lightbox-img').src = url;
    const kindLabel = item.kind === 'per_dart' ? `Dart ${item.dart + 1}` : 'End-of-turn';
    const viewLabel = _lightbox.warped ? ' · Warped' : '';
    document.getElementById('mr-lightbox-caption').textContent =
        `${item.playerName || ('Player ' + item.player)} · Round ${item.round} · ${kindLabel} · Cam ${item.cam}${viewLabel}`;
    document.getElementById('mr-lightbox-pos').textContent =
        `${_lightbox.position + 1} / ${_lightbox.sequence.length}`;
    document.getElementById('mr-lightbox-raw').classList.toggle('mr-btn--active', !_lightbox.warped);
    const warpBtn = document.getElementById('mr-lightbox-warp');
    if (warpBtn) warpBtn.classList.toggle('mr-btn--active', _lightbox.warped);

    _renderLightboxScorecard(item);
}

function _renderLightboxScorecard(item) {
    const host = document.getElementById('mr-lightbox-scorecard');
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);

    const card = document.createElement('div');
    card.className = 'mr-sc';

    // Kicker row: what we're looking at + player
    const kicker = document.createElement('div');
    kicker.className = 'mr-sc__kicker';
    const kickerText = item.kind === 'per_dart'
        ? `Dart ${item.dart + 1} · Round ${item.round}`
        : `End of Turn · Round ${item.round}`;
    kicker.textContent = kickerText;
    card.appendChild(kicker);

    const who = document.createElement('div');
    who.className = 'mr-sc__who';
    who.textContent = (item.playerName || ('Player ' + item.player))
        + (item.bot ? ' · Bot' : '');
    card.appendChild(who);

    if (item.kind === 'per_dart') {
        // Hero label + score for this dart
        const hero = document.createElement('div');
        hero.className = 'mr-sc__hero';
        const heroLabel = document.createElement('strong');
        heroLabel.textContent = item.dartLabel || '—';
        const heroScore = document.createElement('em');
        heroScore.textContent = String(item.dartScore || 0);
        hero.appendChild(heroLabel);
        hero.appendChild(heroScore);
        card.appendChild(hero);

        // Coord + agreement chips
        const chips = document.createElement('div');
        chips.className = 'mr-sc__chips';
        if (item.xMm != null && item.yMm != null) {
            const coord = document.createElement('span');
            coord.className = 'mr-sc__chip';
            coord.textContent = `${item.xMm.toFixed(1)}mm, ${item.yMm.toFixed(1)}mm`;
            chips.appendChild(coord);
        }
        if (item.agreement) {
            const agr = document.createElement('span');
            agr.className = 'mr-sc__chip mr-sc__chip--agr';
            agr.textContent = String(item.agreement);
            chips.appendChild(agr);
        }
        if (chips.childElementCount > 0) card.appendChild(chips);
    } else {
        // End-of-turn: show the 3-dart sequence + total
        const hero = document.createElement('div');
        hero.className = 'mr-sc__hero';
        const heroLabel = document.createElement('strong');
        heroLabel.textContent = 'Turn';
        const heroScore = document.createElement('em');
        heroScore.textContent = String(item.turnTotal || 0);
        hero.appendChild(heroLabel);
        hero.appendChild(heroScore);
        card.appendChild(hero);

        if (item.turnSequenceLabels) {
            const seq = document.createElement('div');
            seq.className = 'mr-sc__chips';
            const chip = document.createElement('span');
            chip.className = 'mr-sc__chip';
            chip.textContent = item.turnSequenceLabels;
            seq.appendChild(chip);
            card.appendChild(seq);
        }
    }

    host.appendChild(card);
}


document.addEventListener('DOMContentLoaded', () => {
    const raw = document.getElementById('mr-lightbox-raw');
    const warp = document.getElementById('mr-lightbox-warp');
    if (raw) raw.addEventListener('click', () => { _lightbox.warped = false; _applyLightbox(); });
    if (warp) warp.addEventListener('click', () => { _lightbox.warped = true; _applyLightbox(); });
    _initPlayerFlights();
});

window.openLightbox = openLightbox;
window.closeLightbox = closeLightbox;
window.lightboxPrev = lightboxPrev;
window.lightboxNext = lightboxNext;

/* ─────────────────────────────────────────────────────────────
 * Player flight colors (cosmetic personalization)
 *
 * Each player picks a flight color on the Game Setup page; the
 * chosen color themes their scoreboard panel, turn cards, and
 * match-review tab via a `data-flight` attribute. Persisted in
 * localStorage so a player's color sticks between sessions.
 * ───────────────────────────────────────────────────────────── */
const FLIGHT_PALETTE = [
    { id: 'blue',   label: 'Blue'   },
    { id: 'red',    label: 'Red'    },
    { id: 'green',  label: 'Green'  },
    { id: 'gold',   label: 'Gold'   },
    { id: 'purple', label: 'Purple' },
    { id: 'orange', label: 'Orange' },
    { id: 'pink',   label: 'Pink'   },
    { id: 'teal',   label: 'Teal'   },
];
const _FLIGHT_DEFAULTS = ['blue', 'red'];
let _playerFlights = _FLIGHT_DEFAULTS.slice();

function _loadStoredFlights() {
    try {
        const raw = localStorage.getItem('throwvision.playerFlights');
        if (!raw) return _FLIGHT_DEFAULTS.slice();
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return _FLIGHT_DEFAULTS.slice();
        const ids = FLIGHT_PALETTE.map(f => f.id);
        return [0, 1].map(i => (ids.indexOf(parsed[i]) >= 0
            ? parsed[i] : _FLIGHT_DEFAULTS[i]));
    } catch (_e) {
        return _FLIGHT_DEFAULTS.slice();
    }
}

function _saveStoredFlights() {
    try {
        localStorage.setItem('throwvision.playerFlights',
            JSON.stringify(_playerFlights));
    } catch (_e) { /* quota / private mode – ignore */ }
}

function _buildFlightPicker(containerId, seat) {
    const root = document.getElementById(containerId);
    if (!root) return;
    root.textContent = '';
    FLIGHT_PALETTE.forEach(flight => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'flight-swatch';
        btn.dataset.flight = flight.id;
        btn.title = flight.label + ' flight';
        btn.setAttribute('aria-label', flight.label + ' flight');
        btn.addEventListener('click', (ev) => {
            ev.preventDefault();
            setPlayerFlight(seat, flight.id);
        });
        root.appendChild(btn);
    });
    _syncFlightPickerSelection(seat);
}

function _syncFlightPickerSelection(seat) {
    const picker = document.getElementById('flight-picker-' + seat);
    if (!picker) return;
    const current = _playerFlights[seat - 1];
    picker.querySelectorAll('.flight-swatch').forEach(sw => {
        sw.classList.toggle('is-selected', sw.dataset.flight === current);
    });
}

function setPlayerFlight(seat, flightId) {
    const idx = seat - 1;
    if (idx < 0 || idx > 1) return;
    if (!FLIGHT_PALETTE.some(f => f.id === flightId)) return;
    _playerFlights[idx] = flightId;
    _saveStoredFlights();
    _syncFlightPickerSelection(seat);
    _applyPlayerFlights();
}

function _applyPlayerFlights() {
    const p1 = document.getElementById('player-panel-1');
    const p2 = document.getElementById('player-panel-2');
    if (p1) p1.setAttribute('data-flight', _playerFlights[0]);
    if (p2) p2.setAttribute('data-flight', _playerFlights[1]);
    // Expose as CSS custom properties on the page root so match review
    // turn cards can pick the right tint even after the scoreboard is gone.
    const root = document.documentElement;
    root.style.setProperty('--player1-flight', 'var(--flight-' + _playerFlights[0] + ')');
    root.style.setProperty('--player2-flight', 'var(--flight-' + _playerFlights[1] + ')');
    // Also tag the bullseye pre-game cards so their color dots + accent
    // ring reflect the chosen flight color instead of the hard-coded
    // red/blue defaults.
    const bp1 = document.getElementById('bullseye-p1');
    const bp2 = document.getElementById('bullseye-p2');
    if (bp1) bp1.setAttribute('data-flight', _playerFlights[0]);
    if (bp2) bp2.setAttribute('data-flight', _playerFlights[1]);
    // Tag any existing match-review tabs so their accents match.
    document.querySelectorAll('.mr-tab').forEach(tab => {
        const seat = parseInt(tab.dataset.seat || '0', 10);
        if (seat === 1 || seat === 2) {
            tab.setAttribute('data-flight', _playerFlights[seat - 1]);
        }
    });
}

// Resolve a flight id (e.g. "blue") into its concrete CSS hex so canvas
// / SVG dots drawn from JS can match the CSS theming. Falls back to the
// :root default if the var is unset.
function _flightHexFor(seat) {
    const id = _playerFlights[seat - 1] || (seat === 1 ? 'blue' : 'red');
    const cssVar = '--flight-' + id;
    const val = getComputedStyle(document.documentElement)
        .getPropertyValue(cssVar).trim();
    return val || (seat === 1 ? '#4ea7ff' : '#ff5260');
}

function _initPlayerFlights() {
    _playerFlights = _loadStoredFlights();
    _buildFlightPicker('flight-picker-1', 1);
    _buildFlightPicker('flight-picker-2', 2);
    _applyPlayerFlights();
}

window.setPlayerFlight = setPlayerFlight;

