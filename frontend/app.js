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
  tripleDark: '#1a6b1a', tripleLight: '#6b1a1a',
  doubleDark: '#1a6b1a', doubleLight: '#6b1a1a',
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
    const nr = R.doubleOuter + 16;
    const [nx, ny] = polarToCart(cx, cy, nr, angle);
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('x', nx); t.setAttribute('y', ny);
    t.setAttribute('text-anchor', 'middle'); t.setAttribute('dominant-baseline', 'central');
    t.setAttribute('font-size', '12'); t.setAttribute('font-weight', '700');
    t.setAttribute('font-family', "'Inter', sans-serif"); t.setAttribute('fill', '#9999aa');
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
  if (name === 'home') {
    $homeBoard.classList.remove('launch-practice');
    if (socket && socket.connected) $homeBoard.classList.add('spinning');
  }
  if (name === 'practice' && !practiceActive) {
    setStatus('idle', 'Idle');
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
    loadStats(null);
  }
}

// ── Camera Preview Streams ─────────────────────────────────────────────
let _camViewMode = 'raw';
let _hasActiveProfile = false;

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
    if (overlay) overlay.style.display = showOverlay ? 'flex' : 'none';
    if (img && showOverlay) { img.onerror = null; img.src = ''; img.style.display = 'none'; }
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
  'D20/D1', 'D6/D10', 'D3/D19', 'D11/D14',      // outer double ring
  'T20/T1', 'T6/T10', 'T3/T19', 'T11/T14',      // inner triple ring
];
let calActivePoint = 0;  // which point zoom follows

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
        } else {
          // Default: 8-point layout
          const cx = img.width / 2, cy = img.height / 2;
          const ro = Math.min(img.width, img.height) * 0.40;  // outer double ring radius
          const ri = ro * (107.0 / 170.0);                     // triple ring radius
          // Same 4 angles as server: D20/D1, D6/D10, D3/D19, D11/D14
          const angs = [81, 351, 261, 171].map(a => a * Math.PI / 180);
          calPoints = [
            // 4 outer double ring
            ...angs.map(a => ({ x: cx + ro * Math.cos(a), y: cy - ro * Math.sin(a) })),
            // 4 triple ring (same angles, inner radius)
            ...angs.map(a => ({ x: cx + ri * Math.cos(a), y: cy - ri * Math.sin(a) })),
          ];
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

// Auto-refresh calibration frame when user tabs back to the page
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentPage === 'calibration' && calImage) {
    calCaptureFrame();
  }
});

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
  const angs = [81, 351, 261, 171].map(a => a * Math.PI / 180);
  calPoints = [
    ...angs.map(a => ({ x: cx + ro * Math.cos(a), y: cy - ro * Math.sin(a) })),
    ...angs.map(a => ({ x: cx + ri * Math.cos(a), y: cy - ri * Math.sin(a) })),
  ];
  calDraw();
}

async function acceptCalibration() {
  if (calPoints.length !== 4 && calPoints.length !== 8) {
    alert('Place all calibration points (4 or 8) before accepting.');
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
    } else { alert('Calibration failed: ' + (data.error || 'Unknown')); }
  } catch (err) { alert('Failed: ' + err.message); }
}

function showProfileModal() {
  const modal = document.getElementById('profile-modal');
  const input = document.getElementById('profile-modal-name');
  if (modal) { modal.style.display = 'flex'; }
  if (input) { input.value = ''; setTimeout(() => input.focus(), 100); }
}

function closeProfileModal(save) {
  const modal = document.getElementById('profile-modal');
  if (modal) modal.style.display = 'none';
  if (save) {
    const input = document.getElementById('profile-modal-name');
    const name = (input && input.value.trim()) || 'default';
    const nameInput = document.getElementById('profile-name-input');
    if (nameInput) nameInput.value = name;
    registerBoard();
  }
  if (socket) socket.emit('close_cameras');
}

function requestRecalibrate() { launchCalibration(); }

async function autoCalibrate() {
  const btn = document.getElementById('btn-auto-cal');
  const orig = btn.textContent;
  btn.textContent = '⏳ Detecting…'; btn.disabled = true;
  try {
    const res = await fetch(`/api/cal/auto/${calCamId}`);
    const data = await res.json();
    const np = data.n_points || (data.points && data.points.length);
    if (data.ok && data.points && (np === 4 || np === 8)) {
      calPoints = data.points.map(p => ({ x: p[0], y: p[1] }));
      calActivePoint = 0;
      calDraw();
      addLog(`Auto-calibrate Cam ${calCamId + 1}: ${np} points detected (${data.method})`);
      btn.textContent = `✓ ${np} pts`;
    } else {
      alert(data.error || 'Auto-detection failed');
      btn.textContent = '✗ Failed';
    }
  } catch (err) {
    alert('Auto-calibrate error: ' + err.message);
    btn.textContent = '✗ Failed';
  }
  btn.disabled = false;
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

/**
 * Rotate all 4 calibration points by one dartboard segment (18°).
 * Uses the homography to rotate in BOARD SPACE (perspective-correct)
 * then transforms back to camera space.
 * direction: -1 = counter-clockwise (left), +1 = clockwise (right)
 */
function calRotatePoints(direction) {
  if (calPoints.length !== 4) return;

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

  // Homography: board → camera
  const camPts = calPoints.map(p => ({ x: p.x, y: p.y }));
  const H = computeHomography(boardPts, camPts);
  if (!H) return;

  // Compute NEW board-space positions (shifted by one 18° segment)
  const shift = direction * BOARD_SECTOR_ANGLE;
  const newBoardPts = BOARD_DST_WIRE_ANGLES.map(a => {
    const rad = (a + shift) * Math.PI / 180;
    return {
      x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad),
      y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad),
    };
  });

  // Transform new board positions to camera space using existing H
  calPoints = newBoardPts.map(bp => {
    const cp = applyH(H, bp.x, bp.y);
    return { x: cp.x, y: cp.y };
  });

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
const BOARD_DST_WIRE_ANGLES = [81, 351, 261, 171]; // D20/D1, D6/D10, D3/D19, D11/D14

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

  const boardPts = BOARD_DST_WIRE_ANGLES.map(a => {
    const rad = a * Math.PI / 180;
    return {
      x: bCx + BOARD_RADII_MM.double_outer * sc * Math.cos(rad),
      y: bCy - BOARD_RADII_MM.double_outer * sc * Math.sin(rad),
    };
  });

  const H = computeHomography(boardPts, calPoints.map(p => ({ x: p.x, y: p.y })));
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

  try {
    // 1. Connection
    setStep('gs-connection', 'active', '🔄');
    statusEl.textContent = 'Checking server connection…';
    await new Promise(r => setTimeout(r, 300));
    if (socket && socket.connected) {
      setStep('gs-connection', 'done', '✓');
    } else {
      setStep('gs-connection', 'warn', '✗');
      statusEl.textContent = 'Connection failed';
      await new Promise(r => setTimeout(r, 2000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    // 2. Cameras
    setStep('gs-cameras', 'active', '🔄');
    statusEl.textContent = 'Checking cameras…';
    let camCount = 0;
    try {
      const camRes = await fetch('/api/status');
      const camData = await camRes.json();
      camCount = camData.num_cameras || 0;
      const states = camData.cam_states || {};
      const activeCams = Object.values(states).filter(c => c.active).length;
      if (activeCams > 0) camCount = activeCams;
    } catch (e) {}
    setStep('gs-cameras', camCount > 0 ? 'done' : 'warn', camCount > 0 ? '✓' : '⚠');

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
    setStep('gs-calibration', calCount === 3 ? 'done' : 'warn', calCount === 3 ? '✓' : '⚠');

    // 4. Board profile
    setStep('gs-profile', 'active', '🔄');
    statusEl.textContent = 'Loading board profile…';
    await new Promise(r => setTimeout(r, 300));
    try {
      const pRes = await fetch('/api/board/status');
      const pData = await pRes.json();
      setStep('gs-profile', pData.registered ? 'done' : 'warn', pData.registered ? '✓' : '⚠');
    } catch (e) { setStep('gs-profile', 'warn', '⚠'); }

    // 5. Detection engine
    setStep('gs-detection', 'active', '🔄');
    statusEl.textContent = 'Starting detection engine…';
    await new Promise(r => setTimeout(r, 500));
    setStep('gs-detection', 'done', '✓');

    statusEl.textContent = 'Ready!';
    _systemChecked = true;
    await new Promise(r => setTimeout(r, 600));

  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    await new Promise(r => setTimeout(r, 2000));
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

  try {
    // 1. Connection check
    setStep('ls-connection', 'active', '🔄');
    statusEl.textContent = 'Checking server connection…';
    await new Promise(r => setTimeout(r, 300));
    if (socket && socket.connected) {
      setStep('ls-connection', 'done', '✓');
    } else {
      setStep('ls-connection', 'warn', '✗');
      statusEl.textContent = 'Connection failed';
      await new Promise(r => setTimeout(r, 2000));
      overlay.classList.add('fade-out');
      setTimeout(() => { overlay.style.display = 'none'; showPage('home'); }, 500);
      return;
    }

    // 2. Camera check — just verify cameras are configured (don't open them)
    setStep('ls-cameras', 'active', '🔄');
    statusEl.textContent = 'Checking cameras…';
    let camCount = 0;
    try {
      const camRes = await fetch('/api/status');
      const camData = await camRes.json();
      camCount = camData.num_cameras || 0;
      // Also check if already open
      const states = camData.cam_states || {};
      const activeCams = Object.values(states).filter(c => c.active).length;
      if (activeCams > 0) camCount = activeCams;
    } catch (e) { }
    if (camCount > 0) {
      setStep('ls-cameras', 'done', '✓');
    } else {
      setStep('ls-cameras', 'warn', '⚠');
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
      } catch (e) { }
    }
    if (calCount === 3) {
      setStep('ls-calibration', 'done', '✓');
    } else {
      setStep('ls-calibration', 'warn', '⚠');
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
        setStep('ls-profile', 'warn', '⚠');
      }
    } catch (e) { setStep('ls-profile', 'warn', '⚠'); }

    // 5. Detection engine
    setStep('ls-detection', 'active', '🔄');
    statusEl.textContent = 'Starting detection engine…';
    await new Promise(r => setTimeout(r, 500));
    setStep('ls-detection', 'done', '✓');

    // All done
    statusEl.textContent = 'Ready!';
    _systemChecked = true;
    await new Promise(r => setTimeout(r, 600));

  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    await new Promise(r => setTimeout(r, 2000));
  }

  // Fade out loading screen and go straight to practice (no board animation on first load)
  overlay.classList.add('fade-out');

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
let throwData = [], totalScore = 0, throwCount = 0, activeCam = 0;

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
    // Only update if not in an active practice/game detecting state
    if (!practiceActive && currentPage !== 'game') {
      const type = data.type || 'idle';
      const msg  = data.message || '';
      if (type === 'loading') setStatus('waiting', msg);
      else if (type === 'ready') setStatus('ready', msg);
      else setStatus('idle', msg);
    }
  });

  socket.on('state', onState);
  socket.on('takeout', onTakeout);
  socket.on('server_log', (data) => appendDebugLog(data.msg, data.ts));
  socket.on('cameras_state', (data) => {
    if (data.open && practiceActive) {
      setStatus('ready', 'Waiting for Throw');
    }
  });

  // ── Game mode events
  socket.on('bullseye_state', onBullseyeState);
  socket.on('bullseye_result', onBullseyeResult);
  socket.on('game_state', onGameState);
  socket.on('game_over', onGameOver);
  socket.on('stats_data', onStatsData);

  // Clear dart dots from board when turn takeout is confirmed
  socket.on('clear_board_dots', () => {
    ['practice-board-dots', 'bullseye-board-dots', 'game-board-dots'].forEach(id => {
      const g = document.getElementById(id);
      if (g) g.innerHTML = '';
    });
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

  socket.on('takeout_ready', () => {
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
    const turnInfo = document.getElementById('game-turn-info');
    if (turnInfo) {
      turnInfo.innerHTML = '<span style="color:var(--yellow);font-size:18px;">🎯 Remove darts from the board!</span>';
    }
    setStatus('takeout', 'Remove Darts');
  });

  // Practice 3-throw takeout events
  socket.on('practice_awaiting_takeout', (data) => {
    const banner = document.getElementById('practice-takeout-banner');
    if (banner) banner.style.display = 'flex';
    setStatus('takeout', 'Remove Darts — 3 Thrown');
    addLog('[PRACTICE] 3 darts thrown — remove from board');
  });

  socket.on('practice_reset', () => {
    const banner = document.getElementById('practice-takeout-banner');
    if (banner) banner.style.display = 'none';
    resetTurn();
    setStatus('ready', 'Board Reset — Throw Again');
    addLog('[PRACTICE] Board auto-reset after takeout');
    const side = document.querySelector('.prac-score-side');
    if (side) {
      side.classList.remove('reset-flash');
      void side.offsetWidth;
      side.classList.add('reset-flash');
      setTimeout(() => side.classList.remove('reset-flash'), 700);
    }
  });
}

// ══════════════════════════════════════════════════════════════════════
// Event Handlers
// ══════════════════════════════════════════════════════════════════════

function onDartScored(data) {
  const { label, score, x_mm, y_mm } = data;
  throwCount++; totalScore += score;
  updateDartCount();

  document.getElementById('score-current').textContent = label;
  document.getElementById('score-ring').textContent = getRingName(label);
  document.getElementById('stat-throws').textContent = throwCount;
  document.getElementById('stat-total').textContent = totalScore;
  document.getElementById('stat-avg').textContent = throwCount > 0 ? (totalScore / throwCount).toFixed(1) : '–';

  addHistoryRow(label, score);
  placeDot(x_mm, y_mm);
  setStatus('scored', label + ' = ' + score);
  setTimeout(() => {
    // Don't override status if in game takeout wait OR practice banner visible
    if (window._awaitingTakeoutGame) return;
    const banner = document.getElementById('practice-takeout-banner');
    if (!banner || banner.style.display === 'none' || banner.style.display === '') {
      setStatus('ready', 'Waiting for Throw');
    }
  }, 2000);
  addLog(`[SCORE] ${label} = ${score} pts (${x_mm.toFixed(1)}, ${y_mm.toFixed(1)})mm`);

  // Update debug panel per-camera info
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
  setStatus('takeout', 'Takeout — Darts Removed');
  addLog('[DET] Takeout detected');
  // Clear board dots on all SVGs
  ['practice-board-dots', 'bullseye-board-dots', 'game-board-dots'].forEach(id => {
    const g = document.getElementById(id);
    if (g) g.innerHTML = '';
  });
  setTimeout(() => { setStatus('ready', 'Waiting for Throw'); }, 2000);
}

// ══════════════════════════════════════════════════════════════════════

function leavePractice() {
  // Stop detection and close cameras when leaving practice
  if (practiceActive) {
    practiceActive = false;
    const btn = document.getElementById('btn-practice-toggle');
    if (btn) { btn.textContent = 'Start'; btn.classList.remove('btn-reset'); btn.classList.add('btn-primary'); }
  }
  if (socket) socket.emit('stop_detection');
  if (socket) socket.emit('close_cameras');
  // Clear board state so next session starts fresh
  resetTurn();
  // Disable debug toggle
  const dbg = document.getElementById('toggle-debug');
  if (dbg) { dbg.checked = false; dbg.disabled = true; toggleDebugCams(); }
  showPage('home');
}

// ══════════════════════════════════════════════════════════════════════
// Practice Mode
// ══════════════════════════════════════════════════════════════════════

function togglePractice() {
  practiceActive = !practiceActive;
  const btn = document.getElementById('btn-practice-toggle');
  if (practiceActive) {
    btn.textContent = 'Stop'; btn.classList.remove('btn-primary'); btn.classList.add('btn-reset');
    setStatus('loading', 'Opening cameras…'); addLog('Practice started');
    // Enable debug toggle
    const dbg = document.getElementById('toggle-debug');
    if (dbg) dbg.disabled = false;
    if (socket) socket.emit('start_detection');
  } else {
    btn.textContent = 'Start'; btn.classList.remove('btn-reset'); btn.classList.add('btn-primary');
    setStatus('stopped', 'Stopped'); addLog('Practice stopped');
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
    const dotsG = document.getElementById('practice-board-dots');
    if (dotsG) dotsG.innerHTML = '';
    throwData = []; throwCount = 0; totalScore = 0;
  }
}

function resetTurn() {
  throwData = []; throwCount = 0; totalScore = 0;
  document.getElementById('score-current').textContent = '–';
  document.getElementById('score-ring').innerHTML = '&nbsp;';
  document.getElementById('stat-throws').textContent = '0';
  document.getElementById('stat-avg').textContent = '–';
  document.getElementById('stat-total').textContent = '0';
  document.getElementById('history-list').innerHTML = '';
  document.getElementById('practice-log').innerHTML = '';
  updateDartCount();
  const dotsG = document.getElementById('practice-board-dots');
  if (dotsG) dotsG.innerHTML = '';
  // Tell backend to clear scored tips (green dots on warped stream)
  if (socket && socket.connected) socket.emit('clear_tips');
  addLog('Session reset');
}

function selectCam(n) {
  activeCam = n;
  document.querySelectorAll('.p-cam-btn').forEach((b, i) => b.classList.toggle('active', i === n));
}

function updateDartCount() {
  const el = document.getElementById('p-dart-n');
  if (el) el.textContent = Math.min(throwCount, 3);
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
  if (!label || label === 'OFF') return 'Miss';
  if (label === 'BULL') return 'Bullseye';
  if (label.startsWith('D')) return 'Double ' + label.substring(1);
  if (label.startsWith('T')) return 'Triple ' + label.substring(1);
  if (label.startsWith('S')) return 'Single ' + label.substring(1);
  return label;
}

function addHistoryRow(label, score) {
  const list = document.getElementById('history-list');
  const row = document.createElement('div');
  row.className = 'history-item';
  const scoreClass = score === 0 ? 'miss' : (label === 'BULL' || label === 'D25') ? 'bull' : '';
  row.innerHTML = `
    <span class="h-num">#${throwCount}</span>
    <span class="h-ring">${getRingName(label)}</span>
    <span class="h-score ${scoreClass}">${score}</span>
  `;
  list.insertBefore(row, list.firstChild);
}

function placeDot(x_mm, y_mm) {
  const scale = TOTAL_R / 170;
  const sx = BOARD_CX + x_mm * scale, sy = BOARD_CY - y_mm * scale;

  function _addDot(groupId) {
    const g = document.getElementById(groupId);
    if (!g) return;
    const dot = document.createElementNS(SVG_NS, 'circle');
    dot.setAttribute('cx', sx); dot.setAttribute('cy', sy); dot.setAttribute('r', '4');
    dot.setAttribute('fill', '#ff4444'); dot.setAttribute('stroke', '#fff');
    dot.setAttribute('stroke-width', '1.2'); dot.setAttribute('opacity', '0.9');
    dot.classList.add('dart-dot');
    g.appendChild(dot);
  }

  // Place on practice board
  _addDot('practice-board-dots');
  // Place on active game board (whichever is visible)
  _addDot('bullseye-board-dots');
  _addDot('game-board-dots');
}

function addLog(msg) {
  const log = document.getElementById('practice-log');
  if (!log) return;
  const ts = new Date().toLocaleTimeString();
  log.innerHTML += ts + ' ' + msg + '\n';
  log.scrollTop = log.scrollHeight;
}

// ══════════════════════════════════════════════════════════════════════
// Settings
// ══════════════════════════════════════════════════════════════════════

function saveSettings() {
  const settings = {
    detection_speed: document.getElementById('set-speed').value,
    tip_offset_px: parseFloat(document.getElementById('set-tip-offset').value),
    dart_size_min: parseInt(document.getElementById('set-dart-min').value),
    dart_size_max: parseInt(document.getElementById('set-dart-max').value),
    stable_frames: parseInt(document.getElementById('set-stable').value),
    resolution: document.getElementById('set-resolution').value,
    fps: parseInt(document.getElementById('set-fps').value),
    num_cameras: parseInt(document.getElementById('set-num-cams').value),
    standby_time: document.getElementById('set-standby').value,
    triangle_k_factor: parseFloat(document.getElementById('set-triangle-k').value),
    approximate_distortion: document.getElementById('set-approx-dist').checked,
    blur_kernel: parseInt(document.getElementById('set-blur').value),
    binary_thresh: parseInt(document.getElementById('set-bin-thresh').value),
  };
  if (socket && socket.connected) { socket.emit('update_settings', settings); addLog('Settings saved'); }
  // Sync calibration resolution dropdown
  const calRes = document.getElementById('cal-resolution');
  if (calRes && settings.resolution) {
    if ([...calRes.options].some(o => o.value === settings.resolution)) {
      calRes.value = settings.resolution;
    }
  }
  const btn = event ? event.target : document.querySelector('#page-settings .page-header .btn-primary');
  if (!btn) return;
  const orig = btn.textContent;
  btn.textContent = '✓ Saved'; btn.style.background = 'var(--green)';
  setTimeout(() => { btn.textContent = orig; btn.style.background = ''; }, 1500);
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
    // Update warped-mode overlays in case user is currently on warped view
    updateNoProfileOverlays();
    if (_camViewMode === 'warped' && !_hasActiveProfile) stopCamPreview();
  } catch (e) { /* ignore */ }
}

async function registerBoard() {
  const nameInput = document.getElementById('profile-name-input');
  const name = (nameInput && nameInput.value.trim()) || 'default';
  const btn = document.getElementById('btn-register-board');
  const orig = btn.textContent;
  btn.textContent = '⏳ Saving…'; btn.disabled = true;
  try {
    const res = await fetch('/api/board/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cam_id: 0, name }),
    });
    const data = await res.json();
    if (data.ok) {
      btn.textContent = `✓ Saved "${data.name}"`;
      addLog(`Board profile "${data.name}" saved with ${data.features} features`);
      if (nameInput) nameInput.value = '';
      checkBoardProfile();
    } else {
      alert(data.error || 'Save failed');
      btn.textContent = '✗ Failed';
    }
  } catch (err) {
    alert('Save error: ' + err.message);
    btn.textContent = '✗ Failed';
  }
  btn.disabled = false;
  setTimeout(() => { btn.textContent = orig; }, 2500);
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

let _confirmDeleteName = null;

function deleteProfile(name) {
  _confirmDeleteName = name;
  const modal = document.getElementById('confirm-modal');
  const label = document.getElementById('confirm-delete-name');
  if (label) label.textContent = `"${name}"`;
  if (modal) modal.style.display = 'flex';
}

async function closeConfirmModal(confirmed) {
  const modal = document.getElementById('confirm-modal');
  if (modal) modal.style.display = 'none';
  if (!confirmed || !_confirmDeleteName) return;
  const name = _confirmDeleteName;
  _confirmDeleteName = null;
  try {
    const res = await fetch('/api/board/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (data.ok) {
      addLog(`Board profile "${name}" deleted`);
      checkBoardProfile();
    } else { alert(data.error || 'Delete failed'); }
  } catch (err) { alert('Delete error: ' + err.message); }
}

// ── Warped Homography Preview (on calibration page) ─────────────────
let _warpPreviewTimer = null;

function calUpdateWarpPreview() {
  if (!calImage || calPoints.length !== 4) return;
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
    // Sync settings resolution dropdown
    const setRes = document.getElementById('set-resolution');
    if (setRes) {
      const key = w + 'x' + h;
      if ([...setRes.options].some(o => o.value === key)) setRes.value = key;
    }
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
  _debugCamsActive = document.getElementById('toggle-debug').checked;
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
      el.textContent = 'No detection';
      el.classList.add('no-detect');
    } else if (d.used) {
      el.innerHTML = `<b>${d.label}</b> (${d.score}pts) · ${d.method}<br>(${d.x_mm}, ${d.y_mm})mm · r=${d.r_mm} · area=${d.area}`;
      el.classList.add('used');
    } else {
      el.innerHTML = `<b>${d.label}</b> (${d.score}pts) · ${d.method}<br>(${d.x_mm}, ${d.y_mm})mm · r=${d.r_mm} · area=${d.area} · <i>rejected</i>`;
      el.classList.add('rejected');
    }
  });
}


// Load settings from server and sync UI inputs
async function loadServerSettings() {
  try {
    const res = await fetch('/api/settings');
    if (!res.ok) return;
    const s = res.json ? await res.json() : {};
    // Resolution
    if (s.resolution) {
      const setRes = document.getElementById('set-resolution');
      if (setRes && [...setRes.options].some(o => o.value === s.resolution)) setRes.value = s.resolution;
      const calRes = document.getElementById('cal-resolution');
      if (calRes && [...calRes.options].some(o => o.value === s.resolution)) calRes.value = s.resolution;
    }
    // Detection inputs
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
  } catch (e) { console.warn('Failed to load server settings', e); }
}

// ══════════════════════════════════════════════════════════════════════
// Game Mode System
// ══════════════════════════════════════════════════════════════════════

let _selectedGameMode = null;
let _gameMode = null;        // current active mode
let _gameOpts = {};           // options for current game

// ── Game Selection ──────────────────────────────────────────────────

function selectGameMode(mode) {
  _selectedGameMode = mode;
  document.querySelectorAll('.game-card').forEach(c => c.classList.remove('selected'));
  const card = document.getElementById('gc-' + mode);
  if (card) card.classList.add('selected');
  document.getElementById('btn-start-game').disabled = false;
}

function startGame() {
  if (!_selectedGameMode) return;
  if (!socket || !socket.connected) {
    alert('System is offline');
    return;
  }

  _gameMode = _selectedGameMode;
  _gameOpts = {};
  _prevGamePlayer = null;  // Reset turn tracking for new game

  if (_gameMode === 'x01') {
    _gameOpts.starting_score = parseInt(document.getElementById('x01-starting-score').value);
  } else if (_gameMode === 'countup') {
    _gameOpts.total_rounds = parseInt(document.getElementById('countup-rounds').value);
  }

  // Clear any residual dots from previous sessions
  ['practice-board-dots', 'bullseye-board-dots', 'game-board-dots'].forEach(id => {
    const g = document.getElementById(id);
    if (g) g.innerHTML = '';
  });

  // Show game page with bullseye phase
  showPage('game');
  document.getElementById('bullseye-phase').style.display = '';
  document.getElementById('game-active').style.display = 'none';
  document.getElementById('game-result-overlay').style.display = 'none';

  // Start bullseye throw on server
  socket.emit('start_bullseye', { mode: _gameMode, options: _gameOpts });
}

function restartGame() {
  // Clear any residual dots
  ['practice-board-dots', 'bullseye-board-dots', 'game-board-dots'].forEach(id => {
    const g = document.getElementById(id);
    if (g) g.innerHTML = '';
  });
  document.getElementById('game-result-overlay').style.display = 'none';
  showPage('game');
  document.getElementById('bullseye-phase').style.display = '';
  document.getElementById('game-active').style.display = 'none';
  socket.emit('start_bullseye', { mode: _gameMode, options: _gameOpts });
}

function endGame() {
  if (socket) socket.emit('end_game');
  if (socket) socket.emit('stop_detection');
  if (socket) socket.emit('close_cameras');
  _gameMode = null;
  // Clear all dart dots
  ['practice-board-dots', 'bullseye-board-dots', 'game-board-dots'].forEach(id => {
    const g = document.getElementById(id);
    if (g) g.innerHTML = '';
  });
  showPage('home');
}

function skipTakeout() {
  window._awaitingTakeoutGame = false;
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

  // Place dart dots on the bullseye board
  const dotsG = document.getElementById('bullseye-board-dots');
  if (dotsG) {
    dotsG.innerHTML = '';  // Clear previous dots
    const scale = TOTAL_R / 170;
    const playerDots = [
      { coord: state.p1_coord, color: '#ff4444' },  // P1 = red
      { coord: state.p2_coord, color: '#4488ff' },  // P2 = blue
    ];
    playerDots.forEach(({ coord, color }) => {
      if (coord) {
        const sx = BOARD_CX + coord[0] * scale;
        const sy = BOARD_CY - coord[1] * scale;
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('cx', sx);
        dot.setAttribute('cy', sy);
        dot.setAttribute('r', '6');
        dot.setAttribute('fill', color);
        dot.setAttribute('stroke', '#fff');
        dot.setAttribute('stroke-width', '1.5');
        dot.setAttribute('opacity', '0.9');
        dotsG.appendChild(dot);
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

  switch (state.phase) {
    case 'player1_throw':
    case 'tiebreak_p1':
      prompt.textContent = 'Player 1: Throw at the bullseye!';
      prompt.className = 'bullseye-prompt p1-active';
      bp1.classList.add('active');
      setStatus('detecting', 'Player 1 Throwing');
      break;
    case 'player2_throw':
    case 'tiebreak_p2':
      prompt.textContent = 'Player 2: Throw at the bullseye!';
      prompt.className = 'bullseye-prompt p2-active';
      bp2.classList.add('active');
      setStatus('detecting', 'Player 2 Throwing');
      break;
    case 'result':
      if (state.winner === 1) {
        prompt.textContent = 'Player 1 goes first!';
        prompt.className = 'bullseye-prompt p1-winner';
        bp1.classList.add('winner');
      } else {
        prompt.textContent = 'Player 2 goes first!';
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
  if (state.type === 'idle') return;
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

  // Clear dart dots only when a genuine new player turn starts (NOT for held-back
  // awaiting_takeout states — those still have the old player's darts on the board).
  // clear_board_dots event handles dot removal after physical takeout.
  if (!state.awaiting_takeout && state.darts_this_turn && state.darts_this_turn.length === 0) {
    const dotsG = document.getElementById('game-board-dots');
    if (dotsG) dotsG.innerHTML = '';
  }
  _prevGamePlayer = state.current_player;

  switch (state.type) {
    case 'x01': renderX01State(state); break;
    case 'cricket': renderCricketState(state); break;
    case 'countup': renderCountUpState(state); break;
  }
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

// ── X01 Renderer ────────────────────────────────────────────────────

function renderX01State(s) {
  document.getElementById('game-title').textContent = s.starting_score + ' Game';
  document.getElementById('cricket-grid').style.display = 'none';
  document.getElementById('game-countup-rounds').style.display = 'none';


  // Scores
  document.getElementById('pp1-score').textContent = s.scores[0];
  document.getElementById('pp2-score').textContent = s.scores[1];

  // Current player indicators
  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? '◀ Throwing' : '';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing ▶' : '';

  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  // Darts this turn
  const dartsEl = s.current_player === 1 ?
    document.getElementById('pp1-darts') :
    document.getElementById('pp2-darts');
  const otherDartsEl = s.current_player === 1 ?
    document.getElementById('pp2-darts') :
    document.getElementById('pp1-darts');

  dartsEl.innerHTML = s.darts_this_turn.map((d, i) =>
    `<span class="dart-chip ${d.bust ? 'bust' : ''}">${d.label} (${d.score})</span>`
  ).join('');
  otherDartsEl.innerHTML = '';

  // Turn info
  const turnInfo = document.getElementById('game-turn-info');
  const turnTotal = s.darts_this_turn.reduce((sum, d) => sum + (d.bust ? 0 : d.score), 0);
  turnInfo.innerHTML = `<span>Turn: ${turnTotal}</span> <span>Darts: ${s.darts_this_turn.length}/3</span>`;

  // History
  renderTurnHistory(s.turn_history);
}

// ── Cricket Renderer ────────────────────────────────────────────────

const CRICKET_DISPLAY = { 15: '15', 16: '16', 17: '17', 18: '18', 19: '19', 20: '20', 25: 'Bull' };

function renderCricketState(s) {
  document.getElementById('game-title').textContent = 'Cricket';
  document.getElementById('cricket-grid').style.display = '';
  document.getElementById('game-countup-rounds').style.display = 'none';

  // Points
  document.getElementById('pp1-score').textContent = s.points[0];
  document.getElementById('pp2-score').textContent = s.points[1];

  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? '◀ Throwing' : '';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing ▶' : '';

  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  // Cricket marks grid
  const body = document.getElementById('cg-body');
  body.innerHTML = '';
  (s.numbers || [15, 16, 17, 18, 19, 20, 25]).forEach(n => {
    const key = String(n);
    const p1m = (s.marks[0] || {})[key] || 0;
    const p2m = (s.marks[1] || {})[key] || 0;
    const row = document.createElement('div');
    row.className = 'cg-row';
    row.innerHTML = `
      <span class="cg-marks">${cricketMarksDisplay(p1m)}</span>
      <span class="cg-target">${CRICKET_DISPLAY[n] || n}</span>
      <span class="cg-marks">${cricketMarksDisplay(p2m)}</span>
    `;
    body.appendChild(row);
  });

  // Darts this turn
  const dartsEl = s.current_player === 1 ?
    document.getElementById('pp1-darts') :
    document.getElementById('pp2-darts');
  dartsEl.innerHTML = (s.darts_this_turn || []).map(d =>
    `<span class="dart-chip">${d.label}</span>`
  ).join('');

  renderTurnHistory(s.turn_history);
}

function cricketMarksDisplay(count) {
  if (count === 0) return '<span class="cm-empty"></span>';
  if (count === 1) return '<span class="cm-mark">/</span>';
  if (count === 2) return '<span class="cm-mark">✕</span>';
  return '<span class="cm-mark cm-closed">⊗</span>';
}

// ── Count Up Renderer ───────────────────────────────────────────────

function renderCountUpState(s) {
  document.getElementById('game-title').textContent = 'Count Up (' + s.total_rounds + ' rounds)';
  document.getElementById('cricket-grid').style.display = 'none';
  document.getElementById('game-countup-rounds').style.display = '';

  // Scores
  document.getElementById('pp1-score').textContent = s.scores[0];
  document.getElementById('pp2-score').textContent = s.scores[1];

  document.getElementById('pp1-indicator').textContent = s.current_player === 1 ? '◀ Throwing' : '';
  document.getElementById('pp2-indicator').textContent = s.current_player === 2 ? 'Throwing ▶' : '';

  document.getElementById('player-panel-1').classList.toggle('active-turn', s.current_player === 1);
  document.getElementById('player-panel-2').classList.toggle('active-turn', s.current_player === 2);

  // Round grid
  const roundsEl = document.getElementById('game-countup-rounds');
  let html = '<div class="cu-header"><span>Rd</span><span>P1</span><span>P2</span></div>';
  for (let i = 0; i < s.total_rounds; i++) {
    const p1r = s.round_scores[0][i];
    const p2r = s.round_scores[1][i];
    html += `<div class="cu-row">
      <span class="cu-rd">${i + 1}</span>
      <span class="cu-score">${p1r ? p1r.total : '—'}</span>
      <span class="cu-score">${p2r ? p2r.total : '—'}</span>
    </div>`;
  }
  roundsEl.innerHTML = html;

  // Darts this turn
  const dartsEl = s.current_player === 1 ?
    document.getElementById('pp1-darts') :
    document.getElementById('pp2-darts');
  dartsEl.innerHTML = (s.darts_this_turn || []).map(d =>
    `<span class="dart-chip">${d.label} (${d.score})</span>`
  ).join('');

  const turnInfo = document.getElementById('game-turn-info');
  const rc = s.rounds_completed || [0, 0];
  turnInfo.innerHTML = `<span>Round ${s.current_round || 1} / ${s.total_rounds}</span>`;

  renderTurnHistory(s.turn_history);
}

// ── Shared: Turn History ────────────────────────────────────────────

function renderTurnHistory(history) {
  const el = document.getElementById('game-history');
  if (!el || !history) return;
  el.innerHTML = history.map(t => {
    const cls = t.busted ? 'gh-busted' : '';
    const darts = (t.darts || []).map(d => d.label).join(', ');
    return `<div class="gh-row ${cls}">
      <span class="gh-player">P${t.player}</span>
      <span class="gh-darts">${darts}</span>
      <span class="gh-total">${t.busted ? 'BUST' : (t.total || 0)}</span>
    </div>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════
// Statistics
// ══════════════════════════════════════════════════════════════════════

function loadStats(mode) {
  // Update tabs
  document.querySelectorAll('.stats-tab').forEach(t => {
    t.classList.toggle('active', (t.dataset.mode || '') === (mode || ''));
  });
  // Fetch from server
  const url = mode ? '/api/stats?mode=' + mode : '/api/stats';
  fetch(url)
    .then(r => r.json())
    .then(data => renderStats(data, mode))
    .catch(e => {
      document.getElementById('stats-dashboard').innerHTML =
        '<div class="stats-loading">Failed to load statistics.</div>';
    });
}

function renderStats(data, mode) {
  const dash = document.getElementById('stats-dashboard');
  if (!data || data.games_played === 0) {
    dash.innerHTML = '<div class="stats-empty">No games played yet. Start a game to see your stats!</div>';
    document.getElementById('stats-recent-list').innerHTML = '';
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
  const recentList = document.getElementById('stats-recent-list');
  const recent = data.recent || [];
  if (recent.length === 0) {
    recentList.innerHTML = '<div class="stats-empty">No recent games.</div>';
  } else {
    recentList.innerHTML = recent.map(g => {
      const date = g.started_at ? new Date(g.started_at * 1000).toLocaleDateString() : '';
      const modeLabel = (g.mode || '').toUpperCase();
      const winner = g.winner ? 'P' + g.winner + ' won' : 'Tie';
      return `<div class="sr-row">
        <span class="sr-mode">${modeLabel}</span>
        <span class="sr-winner">${winner}</span>
        <span class="sr-date">${date}</span>
      </div>`;
    }).join('');
  }
}

function statCard(label, value) {
  return `<div class="stat-card">
    <div class="sc-value">${value}</div>
    <div class="sc-label">${label}</div>
  </div>`;
}

function onStatsData(data) {
  renderStats(data, data.mode);
}
