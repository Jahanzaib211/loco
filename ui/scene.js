/* ============================================================================
   Loco live scene — hand-rolled 3D canvas + ECG route-pulse strip.
   Zero deps: no three.js, no CDN, no web fonts. Pure2D-canvas projection.

   The scene renders the request pipeline as a physical place:
     apps → gateway → classify → VRAM gate → model towers → respond → memory db
   Packets are spawned from live /ws events (route/memory/control/gpu), the
   database cylinder beats with a heartbeat pulse, and the ECG strip charts
   per-route latency like a heart monitor.

   Public surface (called by page scripts):
     sceneBoot() · sceneRegistry(list) · sceneRoute(ev) · sceneGpu(ev)
     sceneMemory(ev) · sceneControl(ev) · ecgPush(ev) · tickerPush(kind, txt)
   ========================================================================== */

/** Design-space resolution: everything is drawn in this virtual box and
 *  uniformly fit into the real canvas (keeps math resolution-independent). */
const DW = 1280;
const DH = 480;

                                                
                                                                       
                                                                                      

/** Registry row handed over by the page: builds one tower per brain. */
                     
             
                
                          
                  
 

                                   
                                  
                                                     
 

                  
                                
                                            
                                                    
                                     
                                
                
            
                                                                           
               
 

                                                                                                  
                                                                                        

const TASK_RGB                                           = {
  chat: [110, 168, 255],
  math: [246, 193, 119],
  code: [61, 220, 151],
  vision: [255, 107, 138],
  compact: [167, 139, 250],
  longdoc: [87, 230, 255],
  cache: [87, 230, 255],
  route: [169, 198, 255],
};
const RGB_HEARTBEAT                           = [70, 110, 170];
const RGB_DB                           = [61, 220, 151];
const RGB_GATE                           = [87, 230, 255];

/* ---------------------------------------------------------------- state -- */
let cvScene                           = null;
let cvEcg                           = null;
let ctxScene                                  = null;
let ctxEcg                                  = null;

const cam      = { yaw: -0.16, pitch: 0.37, dist: 920, fov: 920 };
let mouseYaw = 0;
let mousePitch = 0;
let towers          = [];
let packets           = [];
let rings         = [];
let heartT = 0;                 // last heartbeat beat
let gpuFrac = 0.35;
let gpuRgb                           = [87, 230, 255];
let ecgHist          = [];
let ecgCount = 0;
let ecgCache = 0;
let rafId = 0;
let lastTs = 0;

/* page-scoped scene canvas + actor hook (per-page 3D canvases) */
let sceneCanvasId                = null;
let sceneActor                                                       = null;

/* pipeline waypoints (design space; −z is toward the viewer) */
const WP = {
  apps:   { x: -470, y: 16, z: 0 }      ,
  gate:   { x: -300, y: 34, z: 0 }      ,
  class:  { x: -168, y: 18, z: 0 }      ,
  vram:   { x: -44,  y: 40, z: 0 }      ,
  resp:   { x: 560,  y: 44, z: 0 }      ,
  db:     { x: 706,  y: 62, z: 0 }      ,
};

/* ------------------------------------------------------------- helpers -- */
function hexOf(rgb                          , b = 1, a = 1)         {
  const c = (v        ) => Math.max(0, Math.min(255, Math.round(v * b)));
  return a >= 1
    ? `rgb(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])})`
    : `rgba(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])},${a})`;
}

function project(p    , cx        , cy        )                                                 {
  /* orbit camera: elevation = pitch, azimuth = yaw, always aimed at origin.
     -z faces the viewer (front), +x runs left→right on screen. */
  const yaw = cam.yaw + mouseYaw;
  const pitch = cam.pitch + mousePitch;
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const C     = {
    x: Math.sin(yaw) * cp * cam.dist,
    y: sp * cam.dist,
    z: -Math.cos(yaw) * cp * cam.dist,
  };
  const fl = Math.hypot(C.x, C.y, C.z) || 1;
  const fw     = { x: -C.x / fl, y: -C.y / fl, z: -C.z / fl };   // forward
  const rtLen = Math.hypot(fw.z, 0, -fw.x) || 1;                 // right = up0 × fw
  const rt     = { x: fw.z / rtLen, y: 0, z: -fw.x / rtLen };
  const up     = {                                        // up = fw × right
    x: fw.y * rt.z - fw.z * rt.y,
    y: fw.z * rt.x - fw.x * rt.z,
    z: fw.x * rt.y - fw.y * rt.x,
  };
  const vx = p.x - C.x, vy = p.y - C.y, vz = p.z - C.z;
  const xc = vx * rt.x + vy * rt.y + vz * rt.z;
  const yc = vx * up.x + vy * up.y + vz * up.z;
  const zc = vx * fw.x + vy * fw.y + vz * fw.z;
  const f = cam.fov / Math.max(160, zc);
  return { x: cx + xc * f, y: cy - yc * f, f, d: zc };
}

/** World box → 6 shaded faces (painter's algorithm sorts by depth). */
function boxFaces(
  x        , y        , z        ,
  w        , h        , dpt        ,
  rgb                          , emis = 0, key = ""
)         {
  const x0 = x - w / 2, x1 = x + w / 2;
  const y0 = y, y1 = y + h;
  const z0 = z - dpt / 2, z1 = z + dpt / 2;
  const c       = [
    { x: x0, y: y0, z: z0 }, { x: x1, y: y0, z: z0 }, { x: x1, y: y1, z: z0 }, { x: x0, y: y1, z: z0 },
    { x: x0, y: y0, z: z1 }, { x: x1, y: y0, z: z1 }, { x: x1, y: y1, z: z1 }, { x: x0, y: y1, z: z1 },
  ];
  const quads             = [
    [0, 1, 2, 3], [5, 4, 7, 6], [4, 0, 3, 7], [1, 5, 6, 2], [3, 2, 6, 7], [4, 5, 1, 0],
  ];
  return quads.map((q, i) => ({ pts: q.map((k) => c[k]), rgb, emis, key: key + i }));
}

/* ------------------------------------------------------------ building --- */
function buildSceneModel()         {
  const faces         = [];
  const slate                           = [34, 44, 66];
  const steel                           = [46, 60, 92];

  /* apps: three client slabs stacked in depth */
  [-52, 0, 52].forEach((z, i) => {
    faces.push(...boxFaces(WP.apps.x, 0, z, 54, 26 + i * 5, 30, i === 1 ? steel : slate));
  });

  /* gateway: loco — two pillars + glowing lintel */
  faces.push(...boxFaces(WP.gate.x, 0, -46, 16, 74, 16, steel));
  faces.push(...boxFaces(WP.gate.x, 0, 46, 16, 74, 16, steel));
  faces.push(...boxFaces(WP.gate.x, 74, -46, 16, 14, 108, RGB_GATE, 0.35, "lintel"));
  faces.push(...boxFaces(WP.gate.x, 0, 0, 10, 46, 40, [26, 36, 58]));

  /* classify: compact prism block */
  faces.push(...boxFaces(WP.class.x, 0, 0, 40, 44, 40, [40, 70, 110], 0.12, "cls"));
  faces.push(...boxFaces(WP.class.x, 44, 0, 22, 14, 22, RGB_GATE, 0.3, "clsTop"));

  /* vram gate: arch the packets fly over */
  faces.push(...boxFaces(WP.vram.x, 0, -58, 14, 92, 14, steel));
  faces.push(...boxFaces(WP.vram.x, 0, 58, 14, 92, 14, steel));
  faces.push(...boxFaces(WP.vram.x, 92, -58, 14, 14, 130, [70, 96, 140], 0.18, "arch"));
  faces.push(...boxFaces(WP.vram.x - 26, 30, 0, 6, 44, 100, [24, 34, 54], 0, "beam"));

  /* respond: antenna mast + beacon */
  faces.push(...boxFaces(WP.resp.x, 0, 0, 20, 24, 20, slate));
  faces.push(...boxFaces(WP.resp.x, 24, 0, 8, 78, 8, steel));
  faces.push(...boxFaces(WP.resp.x, 102, 0, 16, 12, 16, [255, 107, 138], 0.4, "beacon"));

  /* database drum: cylinder made of side quads + caps */
  const DBX = WP.db.x, R = 62, H = 104, SEG = 16;
  for (let i = 0; i < SEG; i++) {
    const a0 = (i / SEG) * Math.PI * 2, a1 = ((i + 1) / SEG) * Math.PI * 2;
    const ring = (a        , y        )     =>
      ({ x: DBX + Math.cos(a) * R, y, z: Math.sin(a) * R });
    faces.push({ pts: [ring(a0, 0), ring(a1, 0), ring(a1, H), ring(a0, H)], rgb: [28, 62, 52], emis: 0.05, key: "db" + i });
  }
  const top       = [];
  for (let i = 0; i < SEG; i++) {
    const a = (i / SEG) * Math.PI * 2;
    top.push({ x: DBX + Math.cos(a) * R, y: H, z: Math.sin(a) * R });
  }
  faces.push({ pts: top, rgb: [40, 96, 78], emis: 0.22, key: "dbTop" });

  /* model towers are added by sceneRegistry() */
  return faces;
}

let baseFaces         = [];

/* ------------------------------------------------------------- registry -- */
function sceneRegistry(list             )       {
  const cols = 5, dx = 96, x0 = 66;
  towers = list.map((spec, i) => {
    const row = i < cols ? 0 : 1;
    const col = i % cols;
    return {
      ...spec,
      x: x0 + col * dx,
      z: row === 0 ? -64 : 64,
      h: Math.max(30, Math.min(160, spec.vram * 15)),
      flashT: 0,
      flashRgb: [61, 220, 151],
    };
  });
}

function towerOf(modelId                    )               {
  if (!modelId) return null;
  const m = modelId.toLowerCase();
  return towers.find((t) => t.id.toLowerCase() === m) ?? null;
}

/* --------------------------------------------------------------- packets - */
function pathFor(tower              , cacheHit         )       {
  const pts       = [WP.apps, WP.gate];
  if (cacheHit) {
    pts.push(WP.resp, WP.db);
    return pts;
  }
  pts.push(WP.class, WP.vram);
  if (tower) pts.push({ x: tower.x, y: tower.h + 14, z: tower.z });
  pts.push(WP.resp, WP.db);
  return pts;
}

function spawnPacket(pts      , rgb                          , opts                                                 = {})       {
  if (packets.length > 70) packets.shift();
  packets.push({
    pts, seg: 0, segT: 0,
    speed: opts.speed ?? 760,
    rgb, alpha: opts.alpha ?? 1, r: opts.r ?? 5,
    trail: [], born: performance.now(),
  });
}

function segLen(a    , b    )         {
  return Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
}

/* ---------------------------------------------------------- public feed -- */
function sceneRoute(ev     )       {
  const hit = !!ev.cache_hit || ev.action === "cache-hit" || ev.action === "cache";
  const rgb                           = hit
    ? TASK_RGB.cache
    : TASK_RGB[String(ev.task || "route").toLowerCase()] ?? TASK_RGB.route;
  spawnPacket(pathFor(towerOf(ev.model), hit), rgb, {
    speed: hit ? 1180 : 760,
    r: hit ? 4.4 : 5.2,
  });
  if (!hit && ev.model) {
    const t = towerOf(ev.model);
    if (t) { t.flashT = performance.now(); t.flashRgb = rgb; }
  }
  const a = String(ev.action || "");
  if (a.includes("evict") || a.includes("overload")) {
    rings.push({ t0: performance.now(), dur: 900, rgb: [255, 107, 138], y: 6, maxR: 150 });
  }
}

function sceneGpu(ev     )       {
  const total = ev.total_mib || 12288;
  gpuFrac = Math.max(0, Math.min(1, (ev.used_mib || 0) / total));
  gpuRgb = gpuFrac > 0.88 ? [255, 107, 138] : gpuFrac > 0.72 ? [246, 193, 119] : [87, 230, 255];
}

function sceneMemory(_ev     )       {
  rings.push({ t0: performance.now(), dur: 1500, rgb: RGB_DB, y: 104, maxR: 170 });
}

function sceneControl(ev     )       {
  const t = towerOf(ev.model);
  if (!t) return;
  if (ev.action === "load") { t.loaded = true; t.flashT = performance.now(); t.flashRgb = [61, 220, 151]; }
  if (ev.action === "unload") { t.loaded = false; t.flashT = performance.now(); t.flashRgb = [246, 193, 119]; }
  rings.push({ t0: performance.now(), dur: 1200, rgb: ev.action === "load" ? [61, 220, 151] : [246, 193, 119], y: 4, maxR: 130 });
}

/* ----------------------------------------------------------------- ECG --- */
function ecgPush(ev     )       {
  const now = performance.now();
  const ms = Math.max(40, Math.min(4000, Number(ev.latency_ms) || 260));
  const rgb                           = ev.cache_hit
    ? TASK_RGB.cache
    : TASK_RGB[String(ev.task || "route").toLowerCase()] ?? TASK_RGB.route;
  ecgHist.push({ t: now, ms, rgb, cache: !!ev.cache_hit });
  if (ecgHist.length > 90) ecgHist.shift();
  ecgCount++;
  if (ev.cache_hit) ecgCache++;
  paintEcgStats();
}

function paintEcgStats()       {
  const el = document.getElementById("ecgStats");
  if (!el) return;
  const win = ecgHist.filter((e) => performance.now() - e.t < 60000);
  const sorted = win.map((e) => e.ms).sort((a, b) => a - b);
  const p50 = sorted.length ? Math.round(sorted[Math.floor(sorted.length / 2)]) : 0;
  const hitPct = ecgCount ? Math.round((ecgCache / ecgCount) * 100) : 0;
  el.innerHTML =
    `routes <b>${ecgCount}</b> · cache <b>${hitPct}%</b><br>` +
    `p50 <b>${p50} ms</b> · last <b>${Math.round(win.length ? win[win.length - 1].ms : 0)} ms</b>`;
}

/* -------------------------------------------------------------- ticker --- */
function tickerPush(kind        , detail        )       {
  const el = document.getElementById("ticker");
  if (!el) return;
  const empty = el.querySelector(".empty");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = `ev ${kind}`;
  const time = document.createElement("span");
  time.className = "t";
  time.textContent = clockOf(Date.now() / 1000);
  const k = document.createElement("span");
  k.className = "k";
  k.textContent = kind.toUpperCase();
  const d = document.createElement("span");
  d.className = "d";
  d.textContent = detail;
  d.title = detail;
  row.append(time, k, d);
  el.prepend(row);
  while (el.children.length > 10) el.lastElementChild?.remove();
}

/* --------------------------------------------------------------- render -- */
function fitTransform(ctx                          , w        , h        , dpr        )                                         {
  const s = Math.min(w / DW, h / DH);
  const ox = (w - DW * s) / 2;
  const oy = (h - DH * s) / 2;
  ctx.setTransform(s * dpr, 0, 0, s * dpr, ox * dpr, oy * dpr);
  return { sc: s, ox: ox, oy: oy };
}

function drawFloor(ctx                          , cx        , cy        )       {
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(110,168,255,.07)";
  ctx.beginPath();
  for (let x = -540; x <= 780; x += 86) {
    const a = project({ x, y: 0, z: -190 }, cx, cy);
    const b = project({ x, y: 0, z: 190 }, cx, cy);
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
  }
  for (let z = -190; z <= 190; z += 63) {
    const a = project({ x: -540, y: 0, z }, cx, cy);
    const b = project({ x: 780, y: 0, z }, cx, cy);
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
  }
  ctx.stroke();

  /* main artery rail */
  ctx.strokeStyle = "rgba(87,230,255,.20)";
  ctx.lineWidth = 2;
  ctx.setLineDash([10, 8]);
  ctx.lineDashOffset = -(performance.now() / 34) % 18;
  ctx.beginPath();
  [WP.apps, WP.gate, WP.class, WP.vram, WP.resp, WP.db].forEach((p, i) => {
    const q = project({ x: p.x, y: 3, z: 0 }, cx, cy);
    i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y);
  });
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawFaces(ctx                          , faces        , cx        , cy        )       {
  const light     = { x: -0.35, y: 0.86, z: -0.37 };
  const lit = faces.map((f) => {
    const pts = f.pts.map((p) => project(p, cx, cy));
    const d = pts.reduce((s, p) => s + p.d, 0) / pts.length;
    /* face normal from first three corners (world space) */
    const [a, b, c] = f.pts;
    const u = { x: b.x - a.x, y: b.y - a.y, z: b.z - a.z };
    const v = { x: c.x - a.x, y: c.y - a.y, z: c.z - a.z };
    const n = { x: u.y * v.z - u.z * v.y, y: u.z * v.x - u.x * v.z, z: u.x * v.y - u.y * v.x };
    const len = Math.hypot(n.x, n.y, n.z) || 1;
    const dot = Math.abs((n.x * light.x + n.y * light.y + n.z * light.z) / len);
    return { f, pts, d, b: 0.52 + 0.62 * dot };
  });
  lit.sort((p, q) => q.d - p.d);   // far → near
  for (const it of lit) {
    ctx.beginPath();
    it.pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.closePath();
    ctx.fillStyle = hexOf(it.f.rgb, it.f.emis ? 1 : it.b);
    ctx.fill();
    ctx.strokeStyle = "rgba(8,12,20,.55)";
    ctx.lineWidth = 0.8;
    ctx.stroke();
    if (it.f.emis) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = hexOf(it.f.rgb, 1, 0.16 * it.f.emis);
      ctx.fill();
      ctx.restore();
    }
  }
}

function drawTowers(ctx                          , cx        , cy        , now        )       {
  const faces         = [];
  for (const t of towers) {
    const base                           = t.loaded ? [58, 104, 176] : [38, 48, 72];
    faces.push(...boxFaces(t.x, 0, t.z, 46, t.h, 46, base, t.loaded ? 0.14 : 0, "tw"));
    /* cap slab */
    faces.push(...boxFaces(t.x, t.h, t.z, 50, 8, 50,
      t.loaded ? [87, 230, 255] : [54, 66, 94], t.loaded ? 0.5 : 0.1, "cap"));
    /* load flash */
    const age = now - t.flashT;
    if (age < 700) {
      const k = 1 - age / 700;
      faces.push(...boxFaces(t.x - 3, 2, t.z - 3, 52, t.h + 6, 52, t.flashRgb, 0.5 * k, "flash"));
    }
  }
  drawFaces(ctx, faces, cx, cy);

  /* labels above towers */
  ctx.textAlign = "center";
  for (const t of towers) {
    const top = project({ x: t.x, y: t.h + 34, z: t.z }, cx, cy);
    ctx.font = "700 11px ui-monospace, Menlo, monospace";
    ctx.fillStyle = t.loaded ? "#8ff0ff" : "#7e8aa3";
    ctx.fillText(t.short, top.x, top.y);
    ctx.font = "600 9.5px ui-monospace, Menlo, monospace";
    ctx.fillStyle = t.loaded ? "rgba(143,240,255,.72)" : "rgba(126,138,163,.6)";
    ctx.fillText(`${t.vram.toFixed(1)} GB${t.loaded ? " · up" : ""}`, top.x, top.y + 12);
  }
  ctx.textAlign = "left";
}

function drawStageLabels(ctx                          , cx        , cy        )       {
  const labels                 = [
    ["apps", { x: WP.apps.x, y: 74, z: 0 }],
    ["loco ·8200", { x: WP.gate.x, y: 128, z: 0 }],
    ["classify", { x: WP.class.x, y: 84, z: 0 }],
    ["vram gate", { x: WP.vram.x, y: 138, z: 0 }],
    ["respond", { x: WP.resp.x, y: 132, z: 0 }],
    ["memory db", { x: WP.db.x, y: 150, z: 0 }],
  ];
  ctx.font = "700 10px ui-monospace, Menlo, monospace";
  ctx.textAlign = "center";
  for (const [txt, p] of labels) {
    const q = project(p, cx, cy);
    ctx.fillStyle = "rgba(138,149,173,.9)";
    ctx.fillText(txt.toUpperCase(), q.x, q.y);
  }
  ctx.textAlign = "left";
}

function drawRings(ctx                          , cx        , cy        , now        )       {
  rings = rings.filter((r) => now - r.t0 < r.dur);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const r of rings) {
    const k = (now - r.t0) / r.dur;
    const rad = 14 + k * r.maxR;
    const alpha = (1 - k) * 0.75;
    ctx.beginPath();
    for (let i = 0; i <= 28; i++) {
      const a = (i / 28) * Math.PI * 2;
      const p = project({ x: WP.db.x + Math.cos(a) * rad, y: r.y, z: Math.sin(a) * rad }, cx, cy);
      i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
    }
    ctx.closePath();
    ctx.strokeStyle = hexOf(r.rgb, 1, alpha);
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }
  ctx.restore();
}

function drawVramSlab(ctx                          , cx        , cy        )       {
  const x0 = -240, w = 480, z = -158, h = 16;
  /* track */
  drawFaces(ctx, boxFaces(x0, 0, z, w, h, 26, [20, 27, 42]), cx, cy);
  /* fill */
  const fw = Math.max(6, w * gpuFrac);
  drawFaces(ctx, boxFaces(x0, 1, z - 2, fw, h - 2, 22, gpuRgb, 0.42, "fill"), cx, cy);

  const a = project({ x: x0, y: h + 34, z }, cx, cy);
  const b = project({ x: x0 + w, y: h + 34, z }, cx, cy);
  ctx.font = "700 10px ui-monospace, Menlo, monospace";
  ctx.fillStyle = hexOf(gpuRgb, 1, 0.95);
  ctx.textAlign = "left";
  ctx.fillText(`VRAM ${(gpuFrac * 100).toFixed(0)}%`, a.x, a.y);
  ctx.textAlign = "right";
  ctx.fillStyle = "rgba(138,149,173,.85)";
  ctx.fillText("live · 1s poll", b.x, b.y);
  ctx.textAlign = "left";
}

function drawPackets(ctx                          , cx        , cy        , dt        )       {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  packets = packets.filter((pk) => pk.seg < pk.pts.length - 1);
  for (const pk of packets) {
    let remain = pk.speed * dt;
    while (remain > 0 && pk.seg < pk.pts.length - 1) {
      const a = pk.pts[pk.seg], b = pk.pts[pk.seg + 1];
      const L = segLen(a, b) || 1;
      const need = (1 - pk.segT) * L;
      if (remain < need) { pk.segT += remain / L; remain = 0; }
      else { remain -= need; pk.seg++; pk.segT = 0; }
    }
    if (pk.seg >= pk.pts.length - 1) continue;

    const a = pk.pts[pk.seg], b = pk.pts[pk.seg + 1];
    const t = pk.segT;
    const pos     = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t };
    const sp = project(pos, cx, cy);

    /* trail */
    pk.trail.unshift({ x: sp.x, y: sp.y });
    if (pk.trail.length > 9) pk.trail.pop();
    for (let i = pk.trail.length - 1; i > 0; i--) {
      const al = (1 - i / pk.trail.length) * 0.5 * pk.alpha;
      ctx.beginPath();
      ctx.moveTo(pk.trail[i].x, pk.trail[i].y);
      ctx.lineTo(pk.trail[i - 1].x, pk.trail[i - 1].y);
      ctx.strokeStyle = hexOf(pk.rgb, 1, al);
      ctx.lineWidth = Math.max(1, pk.r * 0.7 * (1 - i / pk.trail.length));
      ctx.stroke();
    }

    /* head */
    const r = Math.max(2.2, pk.r * sp.f * 1.5);
    const glow = ctx.createRadialGradient(sp.x, sp.y, 0, sp.x, sp.y, r * 3.4);
    glow.addColorStop(0, hexOf(pk.rgb, 1, 0.95 * pk.alpha));
    glow.addColorStop(0.35, hexOf(pk.rgb, 1, 0.34 * pk.alpha));
    glow.addColorStop(1, hexOf(pk.rgb, 1, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(sp.x, sp.y, r * 3.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = hexOf([255, 255, 255], 1, 0.85 * pk.alpha);
    ctx.beginPath();
    ctx.arc(sp.x, sp.y, r * 0.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawScene(now        , dt        )       {
  if (sceneActor) { sceneActor({ ctx: ctxScene, cv: sceneCanvasId ? (document.getElementById(sceneCanvasId)                     ) : cvScene, w: sceneCanvasId ? (document.getElementById(sceneCanvasId)                     )?.clientWidth : cvScene?.clientWidth, h: sceneCanvasId ? (document.getElementById(sceneCanvasId)                     )?.clientHeight : cvScene?.clientHeight, now: now, dt: dt, fitTransform: fitTransform, drawFloor: drawFloor, drawFaces: drawFaces, drawTowers: drawTowers, drawStageLabels: drawStageLabels, drawVramSlab: drawVramSlab, drawRings: drawRings, drawPackets: drawPackets }, now, dt); return; }
  const ctx = ctxScene;
  if (!ctx || !cvScene) return;
  const w = cvScene.clientWidth, h = cvScene.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cvScene.width, cvScene.height);
  fitTransform(ctx, w, h, dpr);

  /* design-space center: world x spans −470…+706 → center at 522 */
  const ccx = 522, ccy = 330;

  /* slow yaw drift + eased mouse parallax */
  cam.yaw = -0.16 + Math.sin(now / 6400) * 0.075;

  /* backdrop: soft radial glow only — the card's own gradient shows through,
     so the canvas has no hard rectangular edge */
  ctx.save();
  const vg = ctx.createRadialGradient(DW / 2, DH * 0.6, 60, DW / 2, DH * 0.6, DW * 0.62);
  vg.addColorStop(0, "rgba(26,42,72,.6)");
  vg.addColorStop(0.55, "rgba(15,24,44,.3)");
  vg.addColorStop(1, "rgba(5,7,13,0)");
  ctx.fillStyle = vg;
  ctx.fillRect(-40, -40, DW + 80, DH + 80);
  ctx.restore();

  drawFloor(ctx, ccx, ccy);
  drawFaces(ctx, baseFaces, ccx, ccy);
  drawTowers(ctx, ccx, ccy, now);
  drawStageLabels(ctx, ccx, ccy);
  drawVramSlab(ctx, ccx, ccy);
  drawRings(ctx, ccx, ccy, now);

  /* heartbeat: the pipeline is alive even when no traffic */
  if (now - heartT > 2600) {
    heartT = now;
    spawnPacket([WP.gate, WP.class, WP.vram, WP.resp, WP.db], RGB_HEARTBEAT, { speed: 640, alpha: 0.5, r: 4 });
    rings.push({ t0: now, dur: 1800, rgb: [40, 120, 96], y: 104, maxR: 120 });
  }
  drawPackets(ctx, ccx, ccy, dt);
}

/* ----------------------------------------------------------------- ECG --- */
function drawEcg(now        )       {
  const ctx = ctxEcg;
  if (!ctx || !cvEcg) return;
  const w = cvEcg.clientWidth, h = cvEcg.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const baseY = h * 0.76;
  const WIN = 26000;   // visible window, ms

  /* monitor grid */
  ctx.strokeStyle = "rgba(61,220,151,.06)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = w; x >= 0; x -= 46) { ctx.moveTo(x, 0); ctx.lineTo(x, h); }
  for (let y = h; y >= 0; y -= 24) { ctx.moveTo(0, y); ctx.lineTo(w, y); }
  ctx.stroke();

  /* baseline */
  ctx.strokeStyle = "rgba(61,220,151,.32)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(0, baseY);
  ctx.lineTo(w, baseY);
  ctx.stroke();

  /* event spikes */
  for (const e of ecgHist) {
    const age = now - e.t;
    if (age > WIN) continue;
    const x = w - (age / WIN) * w;
    const amp = Math.min(h * 0.62, (e.ms / 2400) * h * 0.62 + 8);
    const fresh = Math.max(0, 1 - age / 900);
    ctx.strokeStyle = hexOf(e.rgb, 1, e.cache ? 0.55 : 0.5 + 0.5 * fresh);
    ctx.lineWidth = e.cache ? 1.4 : 2.1;
    ctx.beginPath();
    ctx.moveTo(x, baseY);
    ctx.lineTo(x, baseY - amp * 0.62);
    ctx.lineTo(x + 3.5, baseY - amp);
    ctx.lineTo(x + 7, baseY - amp * 0.62);
    ctx.lineTo(x + 7, baseY);
    ctx.stroke();
    if (fresh > 0.05) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      const g = ctx.createRadialGradient(x + 3.5, baseY - amp, 0, x + 3.5, baseY - amp, 16 * fresh + 4);
      g.addColorStop(0, hexOf(e.rgb, 1, 0.5 * fresh));
      g.addColorStop(1, hexOf(e.rgb, 1, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x + 3.5, baseY - amp, 16 * fresh + 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  /* sweep head */
  const hx = w - 2;
  ctx.fillStyle = "rgba(61,220,151,.95)";
  ctx.beginPath();
  ctx.arc(hx, baseY, 3, 0, Math.PI * 2);
  ctx.fill();
  const sweep = ctx.createLinearGradient(hx - 70, 0, hx, 0);
  sweep.addColorStop(0, "rgba(61,220,151,0)");
  sweep.addColorStop(1, "rgba(61,220,151,.28)");
  ctx.fillStyle = sweep;
  ctx.fillRect(hx - 70, 0, 70, h);

  /* label */
  ctx.font = "700 9.5px ui-monospace, Menlo, monospace";
  ctx.fillStyle = "rgba(138,149,173,.75)";
  ctx.fillText("ROUTE LATENCY · 26s WINDOW · ms→AMP", 12, 16);
}

/* ------------------------------------------------------------ main loop -- */
function frame(ts        )       {
  rafId = requestAnimationFrame(frame);
  if (document.hidden) { lastTs = ts; return; }
  const dt = Math.min(0.05, (ts - lastTs) / 1000 || 0.016);
  lastTs = ts;
  drawScene(ts, dt);
  drawEcg(ts);
}

function resizeAll()       {
  const dpr = window.devicePixelRatio || 1;
  for (const cv of [cvScene, cvEcg]) {
    if (!cv) continue;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) continue;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
}

function sceneBoot()       {
  cvScene = document.getElementById("scene")                            ;
  cvEcg = document.getElementById("ecg")                            ;
  if (!cvScene && !cvEcg) return;
  ctxScene = cvScene?.getContext("2d") ?? null;
  ctxEcg = cvEcg?.getContext("2d") ?? null;
  baseFaces = buildSceneModel();
  resizeAll();
  window.addEventListener("resize", resizeAll);

  cvScene?.addEventListener("mousemove", (ev) => {
    const r = cvScene .getBoundingClientRect();
    const nx = (ev.clientX - r.left) / r.width - 0.5;
    const ny = (ev.clientY - r.top) / r.height - 0.5;
    mouseYaw = nx * 0.22;
    mousePitch = -ny * 0.10;
  });
  cvScene?.addEventListener("mouseleave", () => { mouseYaw = 0; mousePitch = 0; });

  paintEcgStats();
  if (!rafId) { lastTs = performance.now(); rafId = requestAnimationFrame(frame); }
}

/* ---- generic page scene wiring (per-page 3D canvas) ---- */
function sceneSetCanvas(id        )       { sceneCanvasId = id; }
function sceneSetActor(fn                                                      )       { sceneActor = fn; }
function sceneBurst(color                          , wpFrom    , wpTo    )       {
  packets.push({ pts: [wpFrom, wpTo], seg: 0, segT: 0, speed: 640, rgb: color, alpha: 1, r: 5, trail: [], born: performance.now() });
}


//# sourceURL=scene.ts