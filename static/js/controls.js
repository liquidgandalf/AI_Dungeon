// SkeletonGame/static/js/controls.js
(function(){
  const socket = io();

  const joinDiv = document.getElementById('join');
  const padDiv = document.getElementById('pad');
  const titleEl = document.getElementById('title');
  const nameInput = document.getElementById('name');
  const joinBtn = document.getElementById('joinBtn');
  const charSelect = document.getElementById('character');
  const charPreview = document.getElementById('charPreview');
  const charCanvas = document.getElementById('charPreviewCanvas');
  const charDataEl = document.getElementById('char-data');
  const hairColorInput = document.getElementById('hairColor');
  const clothesColorInput = document.getElementById('clothesColor');
  const skinColorInput = document.getElementById('skinColor');
  const brightnessInput = document.getElementById('brightness');
  const contrastInput = document.getElementById('contrast');
  let characters = [];
  try {
    if (charDataEl && charDataEl.textContent) {
      const parsed = JSON.parse(charDataEl.textContent);
      if (Array.isArray(parsed)) characters = parsed;
    }

  // Initialize ITEM_ICONS from embedded JSON if not set
  (function initItemIcons(){
    try {
      if (!window.ITEM_ICONS) {
        const tag = document.getElementById('item-icons');
        if (tag && tag.textContent) {
          window.ITEM_ICONS = JSON.parse(tag.textContent);
        }
      }
    } catch(_){}
  })();
  } catch(_) {}
  const leaveTopBtn = document.getElementById('leaveTop');
  const view3d = document.getElementById('view3d');
  const ctx = view3d ? view3d.getContext('2d') : null;
  const leftActionBtn = document.getElementById('leftAction');
  const rightActionBtn = document.getElementById('rightAction');
  const openInvBtn = document.getElementById('openInventory');
  const invOverlay = document.getElementById('inventoryOverlay');
  const closeInvBtn = document.getElementById('closeInventory');
  const backpackGrid = document.getElementById('backpackGrid');
  const equipSummary = document.getElementById('equipSummary');

  // Live recolor preview helpers
  const idToChar = () => new Map(characters.map(c => [c.id, c]));
  const getSelectedCharImgPath = () => {
    const map = idToChar();
    const c = map.get(charSelect?.value) || characters[0];
    return c && c.img ? ('/static/img/' + c.img.replace(/^\/+/, '')) : null;
  };

  function hexToRgb(hex){
    if (!hex) return [0,0,0];
    const h = hex.replace('#','');
    const v = h.length === 3 ? h.split('').map(ch=>ch+ch).join('') : h;
    const n = parseInt(v,16);
    return [(n>>16)&255, (n>>8)&255, n&255];
  }

  function recolorImageToCanvas(img, canvas, hairHex, clothesHex, skinHex){
    if (!img || !canvas) return;
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    canvas.width = w; canvas.height = h;
    const ctx2 = canvas.getContext('2d', { willReadFrequently: true });
    ctx2.imageSmoothingEnabled = false;
    ctx2.drawImage(img, 0, 0);
    const imgData = ctx2.getImageData(0, 0, w, h);
    const d = imgData.data;
    const [hr,hg,hb] = hexToRgb(hairHex);
    const [cr,cg,cb] = hexToRgb(clothesHex);
    const [sr,sg,sb] = hexToRgb(skinHex);
    for (let i=0; i<d.length; i+=4){
      const r=d[i], g=d[i+1], b=d[i+2], a=d[i+3];
      if (a === 0) continue;
      // Soft mask weights from channel dominance
      let wr = Math.max(0, r - Math.max(g, b));
      let wg = Math.max(0, g - Math.max(r, b));
      let wb = Math.max(0, b - Math.max(r, g));
      const sum = wr + wg + wb;
      if (sum <= 0){
        // Keep original for neutrals/greys
        continue;
      }
      wr /= sum; wg /= sum; wb /= sum;
      // Preserve shading using luminance
      const lum = (0.2126*r + 0.7152*g + 0.0722*b) / 255;
      let nr = 0, ng = 0, nb = 0;
      if (wg>0){ nr += hr * wg * lum; ng += hg * wg * lum; nb += hb * wg * lum; }
      if (wr>0){ nr += cr * wr * lum; ng += cg * wr * lum; nb += cb * wr * lum; }
      if (wb>0){ nr += sr * wb * lum; ng += sg * wb * lum; nb += sb * wb * lum; }
      d[i] = Math.max(0, Math.min(255, nr|0));
      d[i+1] = Math.max(0, Math.min(255, ng|0));
      d[i+2] = Math.max(0, Math.min(255, nb|0));
    }
    // Apply brightness/contrast adjustments post-recolor
    const bVal = brightnessInput ? parseInt(brightnessInput.value || '0', 10) : 0; // [-100..100]
    const cVal = contrastInput ? parseInt(contrastInput.value || '0', 10) : 0; // [-100..100]
    if (bVal !== 0 || cVal !== 0){
      const brightness = bVal; // simple additive
      const factor = (259 * (cVal + 255)) / (255 * (259 - cVal));
      for (let i=0; i<d.length; i+=4){
        // skip alpha 0
        if (d[i+3] === 0) continue;
        let r = d[i], g = d[i+1], b = d[i+2];
        r = factor * (r - 128) + 128 + brightness;
        g = factor * (g - 128) + 128 + brightness;
        b = factor * (b - 128) + 128 + brightness;
        d[i] = Math.max(0, Math.min(255, r|0));
        d[i+1] = Math.max(0, Math.min(255, g|0));
        d[i+2] = Math.max(0, Math.min(255, b|0));
      }
    }
    ctx2.putImageData(imgData, 0, 0);
  }

  let currentImg = null;
  function loadAndRenderPreview(){
    const src = getSelectedCharImgPath();
    if (!src || !charCanvas) return;
    const hair = hairColorInput ? hairColorInput.value : '#00ff00';
    const clothes = clothesColorInput ? clothesColorInput.value : '#ff0000';
    const skin = skinColorInput ? skinColorInput.value : '#3399ff';
    const img = new Image();
    img.onload = () => {
      currentImg = img;
      recolorImageToCanvas(img, charCanvas, hair, clothes, skin);
    };
    img.src = src + (src.includes('?') ? '&' : '?') + 't=' + Date.now(); // bust cache on changes
  }
  const cooldownBar = document.getElementById('cooldownBar');
  const tabBackpack = document.getElementById('tabBackpack');
  const tabStats = document.getElementById('tabStats');
  const tabLoadout = document.getElementById('tabLoadout');
  const statsView = document.getElementById('statsView');
  const loadoutView = document.getElementById('loadoutView');
  let lastState = null;
  // HUD equip cache (ids)
  let hudEquip = { left: null, right: null };

  function showPad(show){
    padDiv.style.display = show ? 'block' : 'none';
    joinDiv.style.display = show ? 'none' : 'block';
  }

  // FX queue: transient overlay effects sent by server
  const fxQueue = [];
  socket.on('fx', (ev) => {
    if (!ev || !ctx) return;
    const now = performance.now();
    const type = ev.type || 'spark';
    const level = Math.max(0, Math.min(1, +ev.level || 0));
    // lifetime per type
    const life = type === 'crack' ? 650 : 160; // ms
    fxQueue.push({ type, level, t0: now, t1: now + life, data: ev });
  });

  function showTab(which){
    if (!invOverlay) return;
    if (which === 'backpack'){
      backpackGrid.style.display = 'grid';
      statsView.style.display = 'none';
      loadoutView.style.display = 'none';
    } else if (which === 'stats'){
      backpackGrid.style.display = 'none';
      statsView.style.display = 'block';
      loadoutView.style.display = 'none';
      renderStats(lastState);
    } else if (which === 'loadout'){
      backpackGrid.style.display = 'none';
      statsView.style.display = 'none';
      loadoutView.style.display = 'block';
      renderLoadout(lastState);
    }
  }

  function renderStats(state){
    if (!statsView || !state) return;
    const s = state.stats || {};
    const pairs = [
      ['Attack', s.attack|0],
      ['Defense', s.defense|0],
      ['Water Dmg', s.water_damage|0],
      ['Water Def', s.water_defense|0],
      ['Fire Dmg', s.fire_damage|0],
      ['Fire Def', s.fire_defense|0],
      ['Earth Dmg', s.earth_damage|0],
      ['Earth Def', s.earth_defense|0],
      ['Backpack Size', s.backpack_size|0],
      ['Strength', s.strength|0],
      ['Speed', s.speed|0],
    ];
    statsView.innerHTML = pairs.map(([k,v])=>`<div style="display:flex; justify-content:space-between; padding:4px 0;"><span>${k}</span><strong>${v}</strong></div>`).join('');
  }

  function renderLoadout(state){
    if (!loadoutView || !state) return;
    const eq = state.equipment || {};
    const lines = [
      ['Head', eq.head && eq.head.name],
      ['Body', eq.body && eq.body.name],
      ['Backpack', eq.backpack && eq.backpack.name],
      ['Left Hand', eq.left_hand && eq.left_hand.name],
      ['Right Hand', eq.right_hand && eq.right_hand.name],
      ['Legs', eq.legs && eq.legs.name],
      ['Feet', eq.feet && eq.feet.name],
    ];
    loadoutView.innerHTML = lines.map(([k,v])=>`<div style="display:flex; justify-content:space-between; padding:4px 0;"><span>${k}</span><strong>${v||'Empty'}</strong></div>`).join('');
  }

  // Cooldown state
  let cdNow = 0; // server time in seconds
  let cdReadyAt = 0;
  let cdDuration = 1;
  let cdTimer = null;
  let pending = null; // {kind: 'control'|'action', payload: {...}}

  function isReady(){
    const t = Date.now() / 1000;
    return t >= cdReadyAt - 0.02; // small grace
  }

  function drawCooldown(){
    if (!cooldownBar) return;
    const t = Date.now() / 1000;
    const total = Math.max(0.001, cdDuration);
    const remain = Math.max(0, cdReadyAt - t);
    const done = Math.max(0, Math.min(1, 1 - (remain / total)));
    cooldownBar.style.width = (done * 100).toFixed(1) + '%';
    cooldownBar.style.background = done >= 1 ? '#4caf50' : '#f39c12';
  }

  function startCooldownLoop(){
    if (cdTimer) return;
    cdTimer = setInterval(() => {
      drawCooldown();
      if (pending && isReady()){
        const p = pending; pending = null;
        if (p.kind === 'control'){
          socket.emit('control', p.payload);
        } else if (p.kind === 'action'){
          socket.emit('action', p.payload);
        }
      }
    }, 100);
  }

  function updateCooldown(ev){
    if (!ev) return;
    cdDuration = ev.duration || cdDuration;
    cdReadyAt = ev.ready_at || cdReadyAt;
    cdNow = ev.now || (Date.now()/1000);
    drawCooldown();
    startCooldownLoop();
  }

  function normalizeCmd(cmd){
    if (cmd === 'turn_left') return 'left';
    if (cmd === 'turn_right') return 'right';
    return cmd;
  }

  function trySendControl(cmd){
    // Movement is smooth: send immediately, no cooldown gating
    socket.emit('control', { command: normalizeCmd(cmd) });
  }

  if (charSelect && charPreview && Array.isArray(characters)){
    // ensure preview matches current selection
    const byId = new Map(characters.map(c => [c.id, c]));
    const updatePreview = () => {
      const cid = charSelect.value;
      const c = byId.get(cid) || characters[0];
      if (c && c.img) charPreview.src = '/static/img/' + c.img.replace(/^\/+/, '');
    };
    charSelect.addEventListener('change', () => {
      updatePreview();
      loadAndRenderPreview();
    });
    updatePreview();
  }

  // Update recolor when colors change
  [hairColorInput, clothesColorInput, skinColorInput, brightnessInput, contrastInput].forEach(inp => {
    if (!inp) return;
    inp.addEventListener('input', () => {
      if (currentImg && charCanvas){
        recolorImageToCanvas(currentImg, charCanvas, hairColorInput.value, clothesColorInput.value, skinColorInput.value);
      } else {
        loadAndRenderPreview();
      }
    });
  });

  // Initial preview render
  loadAndRenderPreview();

  joinBtn.addEventListener('click', () => {
    const name = (nameInput.value || '').trim() || (window.defaultName || 'Player');
    const character = charSelect ? charSelect.value : undefined;
    const colors = {
      hair: hairColorInput ? hairColorInput.value : undefined,
      clothes: clothesColorInput ? clothesColorInput.value : undefined,
      skin: skinColorInput ? skinColorInput.value : undefined,
    };
    const spriteData = (charCanvas && typeof charCanvas.toDataURL === 'function') ? charCanvas.toDataURL('image/png') : undefined;
    socket.emit('join', { name, character, colors, spriteData });
  });

  (padDiv.querySelectorAll('.btn') || []).forEach(btn => {
    const cmd = btn.getAttribute('data-cmd');
    if (!cmd) return;
    btn.addEventListener('click', () => trySendControl(cmd));
  });

  if (leaveTopBtn) {
    leaveTopBtn.addEventListener('click', () => {
      socket.emit('leave');
      // Immediately return to login UI for responsiveness
      showPad(false);
      if (titleEl) titleEl.textContent = 'Join';
      leaveTopBtn.style.display = 'none';
      // Hide inventory overlay if open
      if (invOverlay) invOverlay.style.display = 'none';
    });
  }

  // When the server confirms join, refresh the inventory portrait to the saved recolored sprite
  socket.on('joined', () => {
    const portrait = document.getElementById('openInventory');
    if (portrait && window.spriteUrlGuess){
      const bust = (window.spriteUrlGuess.includes('?') ? '&' : '?') + 't=' + Date.now();
      portrait.src = window.spriteUrlGuess + bust;
      portrait.style.display = '';
    }
    showPad(true);
    // Update header: switch from "Join" to a top Leave button
    if (titleEl) titleEl.textContent = 'Controller';
    if (leaveTopBtn) leaveTopBtn.style.display = 'inline-block';
  });

  // Server acknowledges leave; ensure UI is back on login
  socket.on('left', () => {
    showPad(false);
    if (titleEl) titleEl.textContent = 'Join';
    if (leaveTopBtn) leaveTopBtn.style.display = 'none';
    if (invOverlay) invOverlay.style.display = 'none';
  });

  if (leftActionBtn) {
    leftActionBtn.addEventListener('click', () => {
      if (isReady()) socket.emit('action', { button: 'left' });
      else pending = { kind: 'action', payload: { button: 'left' } };
    });
  }
  if (rightActionBtn) {
    rightActionBtn.addEventListener('click', () => {
      if (isReady()) socket.emit('action', { button: 'right' });
      else pending = { kind: 'action', payload: { button: 'right' } };
    });
  }
  if (openInvBtn) {
    openInvBtn.addEventListener('click', () => {
      if (invOverlay) invOverlay.style.display = 'block';
      if (isReady()) socket.emit('action', { button: 'inventory' });
      else pending = { kind: 'action', payload: { button: 'inventory' } };
    });
  }

  if (closeInvBtn) {
    closeInvBtn.addEventListener('click', () => {
      if (invOverlay) invOverlay.style.display = 'none';
    });
  }

  function renderInventory(state){
    if (!backpackGrid || !state) return;
    lastState = state;
    const stats = state.stats || {};
    const eq = state.equipment || {};
    const inv = state.inventory || [];
    const rows = Math.max(0, Math.min(4, stats.backpack_size|0));
    const totalSlots = 12;
    backpackGrid.innerHTML = '';
    // Equip summary
    if (equipSummary) {
      const lh = eq.left_hand ? eq.left_hand.name : 'Empty';
      const rh = eq.right_hand ? eq.right_hand.name : 'Empty';
      const bp = eq.backpack ? eq.backpack.name : `Backpack: ${rows} row(s)`;
      equipSummary.textContent = `Left: ${lh} | Right: ${rh} | ${bp}`;
    }
    // Build 12 cells, fill first rows*3 as unlocked
    for (let idx = 0; idx < totalSlots; idx++){
      const cell = document.createElement('div');
      const row = Math.floor(idx / 3);
      const unlocked = row < rows;
      cell.style.border = '1px solid #444';
      cell.style.minHeight = '48px';
      cell.style.display = 'flex';
      cell.style.alignItems = 'center';
      cell.style.justifyContent = 'center';
      cell.style.fontSize = '12px';
      cell.style.borderRadius = '6px';
      if (!unlocked){
        cell.style.background = '#2a2a2a';
        cell.style.color = '#666';
        cell.textContent = 'Locked';
      } else {
        const item = inv[idx];
        if (item){
          cell.textContent = item.name;
          cell.style.background = '#333';
        } else {
          cell.textContent = '';
          cell.style.background = '#1f1f1f';
        }
      }
      backpackGrid.appendChild(cell);
    }
  }

  socket.on('joined', () => {
    showPad(true);
    // Update header: switch from "Join" to a top Leave button
    if (titleEl) titleEl.textContent = 'Controller';
    if (leaveTopBtn) leaveTopBtn.style.display = 'inline-block';
  });

  // Simple image cache for spritesheets and item images
  const imgCache = new Map();
  function getImage(path){
    // path like 'items/chest.png' or 'enemies/goblin.png'
    const url = '/static/img/' + path.replace(/^\/+/, '');
    if (imgCache.has(url)) return imgCache.get(url);
    const img = new Image();
    img.src = url;
    imgCache.set(url, img);
    return img;
  }

  // Map item id to HUD icon path
  function itemIconPath(itemId){
    if (!itemId) return null;
    try {
      if (window.ITEM_ICONS && typeof window.ITEM_ICONS === 'object'){
        const p = window.ITEM_ICONS[itemId];
        if (typeof p === 'string' && p) return p;
      }
    } catch(_){}
    return null;
  }

  // Small checker pattern generator (cached by cell size)
  const patternCache = new Map(); // key: size (int) -> CanvasPattern
  function getCheckerPattern(size){
    size = Math.max(2, size|0);
    if (patternCache.has(size)) return patternCache.get(size);
    const cnv = document.createElement('canvas');
    cnv.width = size; cnv.height = size;
    const c2 = cnv.getContext('2d');
    // base light (brighter for contrast)
    c2.fillStyle = 'rgb(240,240,240)';
    c2.fillRect(0,0,size,size);
    // darker quads (much darker for visibility)
    c2.fillStyle = 'rgb(120,120,120)';
    c2.fillRect(0,0,size>>1,size>>1);
    c2.fillRect(size>>1,size>>1,size>>1,size>>1);
    // stronger noise dots
    const dots = Math.max(2, (size*size)>>4);
    c2.fillStyle = 'rgba(0,0,0,0.15)';
    for (let i=0;i<dots;i++){
      c2.fillRect((Math.random()*size)|0,(Math.random()*size)|0,1,1);
    }
    const p = c2.createPattern(cnv,'repeat');
    patternCache.set(size, p);
    return p;
  }

  // Additional lightweight patterns for wall styling
  const brickCache = new Map(); // key: `${w}x${h}` -> CanvasPattern
  function getBrickPattern(w, h){
    w = Math.max(4, w|0); h = Math.max(3, h|0);
    const key = w + 'x' + h;
    if (brickCache.has(key)) return brickCache.get(key);
    const cnv = document.createElement('canvas');
    cnv.width = w; cnv.height = h;
    const c2 = cnv.getContext('2d');
    c2.fillStyle = 'rgb(200,200,200)';
    c2.fillRect(0,0,w,h);
    c2.strokeStyle = 'rgba(40,40,40,0.9)';
    c2.lineWidth = 1;
    // mortar lines: horizontal
    c2.beginPath(); c2.moveTo(0, h-0.5); c2.lineTo(w, h-0.5); c2.stroke();
    // vertical stagger: left half
    c2.beginPath(); c2.moveTo(0.5, 0); c2.lineTo(0.5, h); c2.stroke();
    // right half vertical line offset to mimic staggered bricks
    c2.beginPath(); c2.moveTo((w>>1)+0.5, 0); c2.lineTo((w>>1)+0.5, h); c2.stroke();
    const pat = c2.createPattern(cnv, 'repeat');
    brickCache.set(key, pat);
    return pat;
  }

  const noiseCache = new Map(); // key: size -> CanvasPattern
  function getNoisePattern(size){
    size = Math.max(4, size|0);
    if (noiseCache.has(size)) return noiseCache.get(size);
    const cnv = document.createElement('canvas');
    cnv.width = size; cnv.height = size;
    const c2 = cnv.getContext('2d');
    c2.fillStyle = 'rgb(220,220,220)';
    c2.fillRect(0,0,size,size);
    c2.fillStyle = 'rgba(0,0,0,0.20)';
    const dots = Math.max(4, (size*size)>>3);
    for (let i=0;i<dots;i++) c2.fillRect((Math.random()*size)|0,(Math.random()*size)|0,1,1);
    const pat = c2.createPattern(cnv,'repeat');
    noiseCache.set(size, pat);
    return pat;
  }

  const streakCache = new Map(); // key: `${w}x${h}` -> CanvasPattern
  function getStreakPattern(w, h){
    w = Math.max(3, w|0); h = Math.max(6, h|0);
    const key = w + 'x' + h;
    if (streakCache.has(key)) return streakCache.get(key);
    const cnv = document.createElement('canvas');
    cnv.width = w; cnv.height = h;
    const c2 = cnv.getContext('2d');
    c2.fillStyle = 'rgb(210,210,210)';
    c2.fillRect(0,0,w,h);
    c2.strokeStyle = 'rgba(60,40,20,0.25)';
    c2.lineWidth = 1;
    // a few vertical streaks
    for (let i=0;i<w; i+= Math.max(2, (w/3)|0)){
      c2.beginPath(); c2.moveTo(i+0.5, 0); c2.lineTo(i+0.5, h); c2.stroke();
    }
    const pat = c2.createPattern(cnv,'repeat');
    streakCache.set(key, pat);
    return pat;
  }

  function mix(a, b, k){ return (a*(1-k) + b*k) | 0; }
  function tintFromBiome(bid){
    switch(bid|0){
      case 4: return [60, 140, 80];     // hedge green
      case 1: return [170, 70, 60];     // warm brick
      case 5: return [80, 120, 170];    // cool brick
      case 2: return [160, 110, 60];    // mud (orange)
      case 3: return [150, 130, 60];    // mud (yellow)
      case 6: return [40, 90, 70];      // dark hedge (was purple brick)
      default: return [140, 140, 140];  // neutral
    }
  }
  function overlayPatternForBiome(bid, colH){
    // choose sizes scaled a bit by column height
    if (bid === 4 || bid === 6) return { pat: getNoisePattern(Math.max(6, (colH/6)|0)), alpha: 0.20 }; // dark hedge feel
    if (bid === 1 || bid === 5) return { pat: getBrickPattern(Math.max(6, (colH/5)|0), Math.max(4, (colH/10)|0)), alpha: 0.28 };
    if (bid === 2 || bid === 3) return { pat: getStreakPattern(Math.max(4, (colH/8)|0), Math.max(6, (colH/6)|0)), alpha: 0.22 };
    return null;
  }

  // Draw per-column frame data (heights and shades) onto canvas, then sprites
  let _lastSpriteLogTs = 0;
  socket.on('frame', (data) => {
    if (!ctx || !data) return;
    const w = data.w|0, h = data.h|0;
    const heights = data.heights || [];
    const shades = data.shades || [];
    const dists = data.dists || [];
    const sprites = data.sprites || [];
    // Occasional debug: how many player sprites arrived and sample path
    try {
      const nowMs = performance.now();
      if (nowMs - _lastSpriteLogTs > 1000) {
        const ps = sprites.filter(s => s && s.kind === 'player');
        if (ps.length) {
          console.debug('[frame] player sprites:', ps.length, 'example img:', ps[0].img);
          _lastSpriteLogTs = nowMs;
        }
      }
    } catch(_){}
    if (view3d.width !== w || view3d.height !== h) {
      view3d.width = w;
      view3d.height = h;
    }
    // clear
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
    // sky (ceiling) color from server biome, fallback to sky blue
    const sky = Array.isArray(data.sky) && data.sky.length === 3 ? data.sky : null;
    const skyH = (h/2)|0;
    if (sky) ctx.fillStyle = `rgb(${sky[0]|0},${sky[1]|0},${sky[2]|0})`; else ctx.fillStyle = '#62aefc';
    ctx.fillRect(0, 0, w, skyH);
    // Subtle vertical gradient to brighten zenith
    if (sky) {
      const sg = ctx.createLinearGradient(0, 0, 0, skyH);
      sg.addColorStop(0.0, 'rgba(255,255,255,0.12)');
      sg.addColorStop(0.7, 'rgba(255,255,255,0.02)');
      sg.addColorStop(1.0, 'rgba(255,255,255,0.00)');
      ctx.fillStyle = sg;
      ctx.fillRect(0, 0, w, skyH);
      // Cheap moving cloud puffs (ellipses), very low alpha
      const t = Date.now() * 0.00005; // slow time
      // Motion guided by viewing angle if provided
      const ang = (data && typeof data.angle === 'number') ? data.angle : null;
      let vx = 1.0, vy = 0.0; // default drift to the right
      if (ang !== null){
        // We want: facing north (up) -> clouds move downwards (towards player)
        // Canvas y increases downward, so downwards is +vy.
        // Use a small speed vector opposite of look dir for parallax hint.
        vx = Math.cos(ang) * 0.6;
        vy = Math.sin(ang) * 0.6;
        // invert to move towards camera when looking north (up ~ -pi/2) -> vy positive
        vx = -vx; vy = -vy;
      }
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      ctx.globalAlpha = 0.10;
      const rows = 3; // few rows of clouds
      const perRow = 3; // puffs per row
      for (let r=0; r<rows; r++){
        const y = Math.floor(skyH * (0.15 + r * 0.18));
        const ry = Math.max(6, Math.floor(skyH * 0.10));
        const rx = Math.max(12, Math.floor(w * (0.20 + r*0.05)));
        for (let i=0; i<perRow; i++){
          const speed = 10 + r * 8;
          // Horizontal drift with wrap and direction sign
          const dirX = (vx >= 0 ? 1 : -1);
          const spanX = (w + rx*2);
          const baseX = t * speed * w * (0.2 + Math.abs(vx)) * dirX;
          let cx = (((baseX + i * (w / perRow)) % spanX) + spanX) % spanX - rx;
          // Vertical drift: bounded sinusoid influenced by vy sign/magnitude
          const amp = skyH * 0.10 * (0.5 + Math.min(1.0, Math.abs(vy)));
          const freq = 0.8 + Math.abs(vy) * 1.2;
          const s = Math.sin(t * speed * freq + i * 0.7 + r * 0.4);
          const cy = y + s * amp * (vy >= 0 ? 1 : -1);
          ctx.beginPath();
          ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI*2);
          ctx.fillStyle = 'rgba(255,255,255,1)';
          ctx.fill();
        }
      }
      ctx.restore();
    }
    // Base floor tint from sky (darker). If no sky (outside biome => black), fall back to dark grey
    if (sky) {
      const fr = Math.max(0, Math.min(255, (sky[0]|0) * 0.45))|0;
      const fg = Math.max(0, Math.min(255, (sky[1]|0) * 0.45))|0;
      const fb = Math.max(0, Math.min(255, (sky[2]|0) * 0.45))|0;
      ctx.fillStyle = `rgb(${fr},${fg},${fb})`;
    } else {
      ctx.fillStyle = '#2f2f2f';
    }
    const floorTop = (h/2)|0;
    ctx.fillRect(0, floorTop, w, h - floorTop);
    // Faux texture overlay: banded perspective checker with multiply blending
    const bands = 9;
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    ctx.globalAlpha = 0.55;
    for (let i=0;i<bands;i++){
      const t0 = i / bands;
      const t1 = (i+1) / bands;
      const y0 = (floorTop + Math.floor((h - floorTop) * t0))|0;
      const y1 = (floorTop + Math.floor((h - floorTop) * t1))|0;
      const bandH = Math.max(1, y1 - y0);
      // Smaller pattern near horizon, larger near bottom to fake perspective
      const scale = 4 + Math.floor(t1 * 28); // ~4..32 px (bigger cells -> more visible)
      const pat = getCheckerPattern(scale);
      ctx.fillStyle = pat;
      ctx.fillRect(0, y0, w, bandH);
    }
    // Add a vertical darkening gradient for depth
    const g = ctx.createLinearGradient(0, floorTop, 0, h);
    g.addColorStop(0.0, 'rgba(0,0,0,0.00)');
    g.addColorStop(1.0, 'rgba(0,0,0,0.25)');
    ctx.fillStyle = g;
    ctx.fillRect(0, floorTop, w, h - floorTop);
    ctx.restore();
    for (let x = 0; x < w; x++) {
      const colH = heights[x] | 0;
      const shade = shades[x] | 0;
      const y0 = ((h - colH) / 2) | 0;
      // Biome-tinted wall color
      const bid = (data.biome|0) || 0;
      const [tr,tg,tb] = tintFromBiome(bid);
      const k = 0.42; // blend amount
      const r = mix(shade, tr, k), g = mix(shade, tg, k), b = mix(shade, tb, k);
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(x, y0, 1, colH);
      // Overlay simple pattern for material feel
      const ov = overlayPatternForBiome(bid, colH);
      if (ov && colH > 0){
        ctx.save();
        ctx.globalCompositeOperation = 'multiply';
        ctx.globalAlpha = ov.alpha;
        ctx.fillStyle = ov.pat;
        ctx.fillRect(x, y0, 1, colH);
        ctx.restore();
      }
    }

    // Draw sprites (already depth-sorted far->near from server)
    for (let s of sprites){
      const img = getImage(s.img);
      const sx = s.sx|0, sy = s.sy|0, sw = s.sw|0, sh = s.sh|0;
      const dx0 = s.x|0, dy0 = s.y|0, dw = s.w|0, dh = s.h|0;
      if (!dw || !dh) continue;
      // If image not ready yet, draw a temporary placeholder block for visibility
      if (!img || !img.complete || !img.naturalWidth) {
        try {
          ctx.save();
          ctx.globalAlpha = 0.65;
          ctx.fillStyle = s.kind === 'player' ? '#ff00aa' : '#888';
          ctx.fillRect(dx0, dy0, dw, dh);
          ctx.strokeStyle = '#000';
          ctx.lineWidth = 1;
          ctx.strokeRect(dx0, dy0, dw, dh);
          ctx.restore();
        } catch(_){}
        continue;
      }
      // Column-wise occlusion using dists buffer
      for (let i = 0; i < dw; i++){
        const x = dx0 + i;
        if (x < 0 || x >= w) continue;
        // occluded by wall closer than sprite depth?
        if (Array.isArray(dists) && dists.length === w){
          const wall = dists[x] || 1e9;
          if (wall < (s.depth || 0)) continue;
        }
        const srcX = sx + Math.floor((i / dw) * sw);
        try {
          ctx.drawImage(img, srcX, sy, 1, sh, x, dy0, 1, dh);
        } catch(e) {
          // ignore draw errors if image not ready yet
        }
      }
    }

    // HUD: draw right-hand item icon (bottom-right)
    if (hudEquip && hudEquip.right){
      const iconPath = itemIconPath(hudEquip.right);
      const size = Math.max(16, Math.floor(Math.min(w, h) * 0.16));
      const pad = Math.max(3, Math.floor(size * 0.12));
      const x = w - size - pad;
      const y = h - size - pad;
      // backdrop
      ctx.save();
      ctx.globalAlpha = 0.6;
      ctx.fillStyle = '#000';
      ctx.fillRect(x-2, y-2, size+4, size+4);
      ctx.restore();
      let drew = false;
      if (iconPath){
        try {
          const img = getImage(iconPath);
          if (img && img.complete && img.naturalWidth){
            ctx.drawImage(img, x, y, size, size);
            drew = true;
          }
        } catch(e) {}
      }
      // Fallback: draw a simple pickaxe glyph if no image
      if (!drew){
        ctx.save();
        ctx.translate(x + size/2, y + size/2);
        ctx.scale(size/32, size/32);
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#ddd';
        ctx.fillStyle = '#a67c52';
        // head
        ctx.beginPath();
        ctx.moveTo(-10, -4); ctx.lineTo(10, -4); ctx.lineTo(6, 2); ctx.lineTo(-6, 2); ctx.closePath();
        ctx.stroke();
        // handle
        ctx.beginPath();
        ctx.moveTo(0, 0); ctx.lineTo(0, 12);
        ctx.strokeStyle = '#a67c52';
        ctx.stroke();
        ctx.restore();
      }
    }

    // Render FX overlays (post layer)
    if (fxQueue.length){
      const now = performance.now();
      for (let i = fxQueue.length - 1; i >= 0; i--){
        const fx = fxQueue[i];
        const t = now;
        if (t >= fx.t1){ fxQueue.splice(i,1); continue; }
        const k = (t - fx.t0) / Math.max(1, fx.t1 - fx.t0); // 0..1
        const fade = 1 - k;
        if (fx.type === 'hit_spark'){
          // small yellow-white star at screen center
          const cx = (w/2)|0, cy = (h/2)|0;
          const r = Math.max(4, (Math.min(w,h) * 0.02) | 0);
          ctx.save();
          ctx.globalCompositeOperation = 'screen';
          ctx.globalAlpha = 0.6 * fade;
          ctx.strokeStyle = '#ffd54a';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy);
          ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r);
          ctx.stroke();
          ctx.restore();
        } else if (fx.type === 'crack'){
          // simple crack overlay: multiple jagged lines emanating from center
          const cx = (w/2)|0, cy = (h/2)|0;
          const strength = Math.max(0.2, fx.level || 0);
          const len = Math.max(12, Math.floor(Math.min(w,h) * (0.10 + 0.25*strength)));
          const branches = 5;
          ctx.save();
          ctx.globalCompositeOperation = 'multiply';
          ctx.globalAlpha = 0.35 * strength * fade;
          ctx.strokeStyle = 'rgba(0,0,0,1)';
          ctx.lineWidth = 1;
          for (let b = 0; b < branches; b++){
            const ang = (b / branches) * Math.PI * 2 + (k * 0.5);
            const segs = 6;
            let x0 = cx, y0 = cy;
            for (let sIdx = 0; sIdx < segs; sIdx++){
              const segLen = len / segs;
              const jitter = (Math.random() - 0.5) * (len * 0.08);
              const a = ang + (Math.random()-0.5)*0.3;
              const x1 = x0 + Math.cos(a) * (segLen + jitter);
              const y1 = y0 + Math.sin(a) * (segLen + jitter);
              ctx.beginPath();
              ctx.moveTo(x0, y0);
              ctx.lineTo(x1, y1);
              ctx.stroke();
              x0 = x1; y0 = y1;
            }
          }
          ctx.restore();
        }
      }
    }
  });

  // Receive state for inventory overlay and render
  socket.on('state', (data) => {
    if (invOverlay) invOverlay.style.display = 'block';
    renderInventory(data);
    showTab('backpack');
    // Update HUD equip from state payload if present
    try {
      const eq = (data && data.equipment) || {};
      hudEquip.left = eq.left_hand && eq.left_hand.id ? eq.left_hand.id : null;
      hudEquip.right = eq.right_hand && eq.right_hand.id ? eq.right_hand.id : null;
    } catch(_){}
  });

  // Lightweight equip snapshot for HUD (sent on join)
  socket.on('equip', (data) => {
    try {
      const eq = (data && data.equipment) || {};
      hudEquip.left = eq.left_hand || null;
      hudEquip.right = eq.right_hand || null;
    } catch(_){}
  });

  // Receive cooldown updates from server
  socket.on('cooldown', (ev) => {
    updateCooldown(ev);
  });

  if (tabBackpack) tabBackpack.addEventListener('click', () => showTab('backpack'));
  if (tabStats) tabStats.addEventListener('click', () => showTab('stats'));
  if (tabLoadout) tabLoadout.addEventListener('click', () => showTab('loadout'));
})();
