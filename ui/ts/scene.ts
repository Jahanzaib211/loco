/* ============================================================================
   Loco live scenes — a zero-dependency blueprint diorama renderer.

   One shared visual language for the hero pipeline and the per-page scenes:
   oblique 3D boxes/cylinders on a dark drafting grid, thin luminous edges,
   restrained accent glows, monospace labels, and live packets.

   The scene renders the request pipeline as a physical place:
     apps → loco gateway → classify → VRAM gate → model rack → respond → db
   Packets are spawned from live /ws events (route/memory/control/gpu), the
   database drum beats with the memory pulse, and the ECG strip charts
   per-route latency like a heart monitor.

   Public surface (called by page scripts):
     sceneBoot() · sceneRegistry(list) · sceneRoute(ev) · sceneGpu(ev)
     sceneMemory(ev) · sceneControl(ev) · ecgPush(ev) · tickerPush(kind, txt)
     sceneSetCanvas(id) · sceneSetActor(fn) · sceneBurst(rgb, from, to)
     window.SceneKit — shared projection + drawing primitives for page scenes
   ========================================================================== */

/** Design-space resolution: everything is drawn in this virtual box and
 *  uniformly fit into the real canvas (keeps math resolution-independent). */
const DW = 1280;
const DH = 480;

interface P3 { x: number; y: number; z: number }
interface TowerSpec {
  id: string;
  short: string;
  vram: number;      // GB
  loaded: boolean;
}
interface Tower extends TowerSpec {
  x: number; z: number; h: number;
  flashT: number; flashRgb: [number, number, number];
}
interface Packet {
  pts: P3[];
  seg: number;
  segT: number;
  speed: number;
  rgb: [number, number, number];
  alpha: number;
  r: number;
  trail: { x: number; y: number }[];
  born: number;
}
interface Ring { t0: number; dur: number; rgb: [number, number, number]; y: number; maxR: number }
interface EcgEv { t: number; ms: number; rgb: [number, number, number]; cache: boolean }
type RGB = [number, number, number];

const TASK_RGB: Record<string, RGB> = {
  chat: [110, 168, 255],
  math: [246, 193, 119],
  code: [61, 220, 151],
  vision: [255, 107, 138],
  compact: [167, 139, 250],
  longdoc: [87, 230, 255],
  cache: [87, 230, 255],
  route: [169, 198, 255],
};
const RGB_HEARTBEAT: RGB = [70, 110, 170];
const RGB_DB: RGB = [61, 220, 151];
const RGB_GATE: RGB = [87, 230, 255];

/* ---------------------------------------------------------------- state -- */
let cvScene: HTMLCanvasElement | null = null;
let cvEcg: HTMLCanvasElement | null = null;
let ctxScene: CanvasRenderingContext2D | null = null;
let ctxEcg: CanvasRenderingContext2D | null = null;

let mouseX = 0;
let mouseY = 0;
let towers: Tower[] = [];
let packets: Packet[] = [];
let rings: Ring[] = [];
let heartT = 0;
let gpuFrac = 0.35;
let gpuRgb: RGB = [87, 230, 255];
let ecgHist: EcgEv[] = [];
let ecgCount = 0;
let ecgCache = 0;
let rafId = 0;
let lastTs = 0;
let booted = false;

/* page-scoped scene canvas + actor hook (per-page 3D canvases) */
let sceneCanvasId: string | null = null;
let sceneActor: ((ctx: any, now: number, dt: number) => void) | null = null;

/* pipeline waypoints (world space) */
const WP = {
  apps:   { x: -320, y: 34,  z: -110 } as P3,
  gate:   { x: -195, y: 58,  z: -70 } as P3,
  class:  { x: -75,  y: 44,  z: 0 } as P3,
  vram:   { x: 40,   y: 68,  z: 55 } as P3,
  resp:   { x: 260,  y: 112, z: -20 } as P3,
  db:     { x: 450,  y: 110, z: -150 } as P3,
};

/* Stage anchors for the hero diorama. The path climbs into depth toward the
   rack, then returns to the front-right for the answer + memory. */
const IDX = {
  apps: { x: -300, z: -110 },
  gate: { x: -195, z: -70 },
  cls:  { x: -75,  z: 0 },
  vram: { x: 40,   z: 55 },
  rack: { x: 240,  z: 140 },
  resp: { x: 260,  z: -20 },
  mem:  { x: 450,  z: -150 },
};

const IDX_BOUNDS = { x0: -500, x1: 540, y0: 0, y1: 330, z0: -220, z1: 210 };

/* ------------------------------------------------------------- helpers -- */
function cssRGB(rgb: RGB, a = 1): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return a >= 1
    ? `rgb(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])})`
    : `rgba(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])},${a})`;
}

/** Brightness multiplier, with a small ambient lift so dark blues stay alive. */
function shadeRgb(rgb: RGB, k: number, a = 1): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * k + 7 * k)));
  return a >= 1
    ? `rgb(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])})`
    : `rgba(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])},${a})`;
}

function hexOf(rgb: RGB, b = 1, a = 1): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * b)));
  return a >= 1
    ? `rgb(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])})`
    : `rgba(${c(rgb[0])},${c(rgb[1])},${c(rgb[2])},${a})`;
}

function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}

function hash01(i: number): number {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/* ============================================================== SceneKit = */
/* Oblique axonometric projection: x runs right, y runs up, z runs up-right
   (into the scene). Everything auto-fits into the 1280×480 design box, so a
   page can describe a world in plain coordinates and never clip. */

interface ObliqueProj {
  p: (x: number, y: number, z: number) => { x: number; y: number; d: number };
  s: number; ox: number; oy: number; kx: number; ky: number;
  dw: number; dh: number;
}

function makeProj(b: any, opts: any = {}): ObliqueProj {
  const dw = opts.dw || DW;
  const dh = opts.dh || DH;
  const pad = opts.pad == null ? 30 : opts.pad;
  const padX = opts.padX == null ? pad : opts.padX;
  const padT = opts.padTop == null ? pad : opts.padTop;
  const padB = opts.padBottom == null ? pad : opts.padBottom;
  const kx = opts.kx == null ? 0.62 : opts.kx;
  const ky = opts.ky == null ? 0.34 : opts.ky;
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  const consider = (x: number, y: number, z: number) => {
    const rx = x + z * kx;
    const ry = -y - z * ky;
    if (rx < minx) minx = rx;
    if (rx > maxx) maxx = rx;
    if (ry < miny) miny = ry;
    if (ry > maxy) maxy = ry;
  };
  if (opts.points && opts.points.length) {
    /* Fit the real content, not the empty corners of its bounding box. This
       lets the ground plate bleed off-canvas while the diorama stays large. */
    for (const q of opts.points) consider(q.x, q.y, q.z);
  } else {
    for (const x of [b.x0, b.x1]) {
      for (const y of [b.y0, b.y1]) {
        for (const z of [b.z0, b.z1]) consider(x, y, z);
      }
    }
  }
  const availW = Math.max(1, dw - padX * 2);
  const availH = Math.max(1, dh - padT - padB);
  const s = Math.min(
    availW / Math.max(1, maxx - minx),
    availH / Math.max(1, maxy - miny)
  );
  const ox = padX + (availW - (maxx - minx) * s) / 2 - minx * s;
  const oy = padT + (availH - (maxy - miny) * s) / 2 - miny * s;
  return {
    s, ox, oy, kx, ky, dw, dh,
    p(x: number, y: number, z: number) {
      const rx = x + z * kx;
      const ry = -y - z * ky;
      return { x: ox + rx * s, y: oy + ry * s, d: z };
    },
  };
}

function kitBegin(ctx: CanvasRenderingContext2D, bounds: any, opts: any = {}): any {
  const P = makeProj(bounds, opts);

  const poly = (pts: { x: number; y: number }[], fill: string): void => {
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) i ? ctx.lineTo(pts[i].x, pts[i].y) : ctx.moveTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  };
  const outline = (pts: { x: number; y: number }[], color: string, width = 0.8): void => {
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) i ? ctx.lineTo(pts[i].x, pts[i].y) : ctx.moveTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
  };

  const S: any = {
    ctx, P, s: P.s, p: P.p,

    /** Colour helpers for page actors that live outside this module. */
    rgb(rgb: RGB, a = 1): string { return cssRGB(rgb, a); },
    shade(rgb: RGB, k: number, a = 1): string { return shadeRgb(rgb, k, a); },
    hex(rgb: RGB, b = 1, a = 1): string { return hexOf(rgb, b, a); },

    bg(o: any = {}): void {
      const bw = P.dw || DW, bh = P.dh || DH;
      const g = ctx.createRadialGradient(bw * 0.5, bh * 0.62, Math.min(bw, bh) * 0.12, bw * 0.5, bh * 0.62, Math.max(bw, bh) * 0.75);
      g.addColorStop(0, o.center || "rgba(21,34,54,.86)");
      g.addColorStop(0.55, o.mid || "rgba(11,18,30,.6)");
      g.addColorStop(1, o.edge || "rgba(5,7,11,.96)");
      ctx.fillStyle = g;
      ctx.fillRect(-4, -4, bw + 8, bh + 8);
    },

    grid(o: any = {}): void {
      const x0 = o.x0 == null ? bounds.x0 : o.x0;
      const x1 = o.x1 == null ? bounds.x1 : o.x1;
      const z0 = o.z0 == null ? bounds.z0 : o.z0;
      const z1 = o.z1 == null ? bounds.z1 : o.z1;
      const step = o.step || 40;
      const y = o.y == null ? 0 : o.y;
      const plate = o.plate !== false;
      if (plate) {
        const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
        S.box(cx, -8, cz, x1 - x0, 8, z1 - z0, o.plateRgb || [13, 20, 31], {
          edgeAlpha: 0.08, front: shadeRgb([13, 20, 31], 0.7),
        });
      }
      ctx.save();
      let n = 0;
      for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step, n++) {
        const major = Math.round(x / step) % 5 === 0;
        S.line([{ x, y, z: z0 }, { x, y, z: z1 }], [120, 165, 215], {
          alpha: major ? 0.075 : 0.032, width: major ? 0.8 : 0.5,
        });
      }
      n = 0;
      for (let z = Math.ceil(z0 / step) * step; z <= z1; z += step, n++) {
        const major = Math.round(z / step) % 5 === 0;
        S.line([{ x: x0, y, z }, { x: x1, y, z }], [120, 165, 215], {
          alpha: major ? 0.075 : 0.032, width: major ? 0.8 : 0.5,
        });
      }
      ctx.restore();
    },

    box(cx: number, y: number, cz: number, w: number, h: number, d: number, rgb: RGB, o: any = {}): void {
      const x0 = cx - w / 2, x1 = cx + w / 2;
      const z0 = cz - d / 2, z1 = cz + d / 2;
      const y1 = y + h;
      const f0 = P.p(x0, y, z0), f1 = P.p(x1, y, z0), f2 = P.p(x1, y1, z0), f3 = P.p(x0, y1, z0);
      const b0 = P.p(x0, y, z1), b1 = P.p(x1, y, z1), b2 = P.p(x1, y1, z1), b3 = P.p(x0, y1, z1);
      const a = o.alpha == null ? 1 : o.alpha;
      const emis = o.emis || 0;
      const top = o.top || shadeRgb(rgb, 1.22, a);
      const front = o.front || shadeRgb(rgb, 0.92, a);
      const right = o.right || shadeRgb(rgb, 0.64, a);
      const edgeA = o.edgeAlpha == null ? 0.1 + emis * 0.34 : o.edgeAlpha;
      const edge = `rgba(176,204,238,${edgeA})`;

      poly([f1, f2, b2, b1], right);
      outline([f1, f2, b2, b1], edge, 0.7);
      poly([f3, f2, b2, b3], top);
      if (emis > 0.12) {
        ctx.save();
        ctx.shadowColor = cssRGB(rgb, 0.85);
        ctx.shadowBlur = 10 + 22 * emis;
        poly([f3, f2, b2, b3], top);
        ctx.restore();
      }
      outline([f3, f2, b2, b3], edge, 0.8);
      poly([f0, f1, f2, f3], front);
      outline([f0, f1, f2, f3], edge, 0.85);
      if (o.glass) {
        poly([f0, f1, f2, f3], cssRGB(rgb, 0.07));
        S.line([{ x: x0, y: y1, z: z0 }, { x: x1, y: y1, z: z0 }], rgb, { alpha: 0.55, width: 1.2 });
      }
    },

    /** Truncated pyramid — a hopper/compressor with a wide top and narrow foot. */
    frustum(cx: number, y: number, cz: number, w0: number, d0: number, w1: number, d1: number, h: number, rgb: RGB, o: any = {}): void {
      const a = o.alpha == null ? 1 : o.alpha;
      const emis = o.emis || 0;
      const y1 = y + h;
      const b = [
        P.p(cx - w0 / 2, y, cz - d0 / 2), P.p(cx + w0 / 2, y, cz - d0 / 2),
        P.p(cx + w0 / 2, y, cz + d0 / 2), P.p(cx - w0 / 2, y, cz + d0 / 2),
      ];
      const t = [
        P.p(cx - w1 / 2, y1, cz - d1 / 2), P.p(cx + w1 / 2, y1, cz - d1 / 2),
        P.p(cx + w1 / 2, y1, cz + d1 / 2), P.p(cx - w1 / 2, y1, cz + d1 / 2),
      ];
      const front = o.front || shadeRgb(rgb, 0.92, a);
      const right = o.right || shadeRgb(rgb, 0.64, a);
      const top = o.top || shadeRgb(rgb, 1.22, a);
      const edge = `rgba(176,204,238,${0.1 + emis * 0.3})`;
      poly([b[1], b[2], t[2], t[1]], right);
      outline([b[1], b[2], t[2], t[1]], edge, 0.7);
      poly(t, top);
      if (emis > 0.12) {
        ctx.save();
        ctx.shadowColor = cssRGB(rgb, 0.85);
        ctx.shadowBlur = 10 + 22 * emis;
        poly(t, top);
        ctx.restore();
      }
      outline(t, edge, 0.8);
      poly([b[0], b[1], t[1], t[0]], front);
      outline([b[0], b[1], t[1], t[0]], edge, 0.85);
      outline([b[0], b[3], t[3], t[0]], edge, 0.7);
    },

    cyl(cx: number, y: number, cz: number, r: number, h: number, rgb: RGB, o: any = {}): void {
      const a = o.alpha == null ? 1 : o.alpha;
      const emis = o.emis || 0;
      const bot = P.p(cx, y, cz);
      const top = P.p(cx, y + h, cz);
      const rx = r * P.s;
      const ry = r * P.ky * P.s;
      /* bottom rim */
      ctx.beginPath();
      ctx.ellipse(bot.x, bot.y, rx, ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = shadeRgb(rgb, 0.45, a);
      ctx.fill();
      /* side shell */
      const g = ctx.createLinearGradient(top.x - rx, 0, top.x + rx, 0);
      g.addColorStop(0, shadeRgb(rgb, 0.5, a));
      g.addColorStop(0.45, shadeRgb(rgb, 0.9, a));
      g.addColorStop(1, shadeRgb(rgb, 0.62, a));
      ctx.fillStyle = g;
      ctx.fillRect(top.x - rx, top.y, rx * 2, bot.y - top.y);
      /* top disc */
      ctx.beginPath();
      ctx.ellipse(top.x, top.y, rx, ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = shadeRgb(rgb, 1.28, a);
      if (emis > 0.1) {
        ctx.save();
        ctx.shadowColor = cssRGB(rgb, 0.9);
        ctx.shadowBlur = 12 + 24 * emis;
        ctx.fill();
        ctx.restore();
        ctx.beginPath();
        ctx.ellipse(top.x, top.y, rx, ry, 0, 0, Math.PI * 2);
        ctx.fillStyle = shadeRgb(rgb, 1.28, a);
      }
      ctx.fill();
      ctx.strokeStyle = `rgba(176,204,238,${0.12 + emis * 0.34})`;
      ctx.lineWidth = 0.9;
      ctx.stroke();
      /* vertical seams */
      ctx.strokeStyle = `rgba(176,204,238,${0.08 + emis * 0.18})`;
      ctx.beginPath();
      ctx.moveTo(top.x - rx, top.y); ctx.lineTo(bot.x - rx, bot.y);
      ctx.moveTo(top.x + rx, top.y); ctx.lineTo(bot.x + rx, bot.y);
      ctx.stroke();
    },

    line(pts: P3[], rgb: RGB, o: any = {}): void {
      if (!pts || pts.length < 2) return;
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        const q = P.p(pts[i].x, pts[i].y, pts[i].z);
        i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y);
      }
      ctx.strokeStyle = o.color || cssRGB(rgb, o.alpha == null ? 0.5 : o.alpha);
      ctx.lineWidth = o.width == null ? 1.4 : o.width;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      if (o.dash) {
        ctx.setLineDash(o.dash);
        ctx.lineDashOffset = o.offset || 0;
      }
      if (o.glow) {
        ctx.save();
        ctx.shadowColor = cssRGB(rgb, 0.8);
        ctx.shadowBlur = o.glow;
      }
      ctx.stroke();
      if (o.glow) ctx.restore();
      if (o.dash) ctx.setLineDash([]);
    },

    beam(a: P3, b: P3, rgb: RGB, o: any = {}): void {
      const pa = P.p(a.x, a.y, a.z), pb = P.p(b.x, b.y, b.z);
      const g = ctx.createLinearGradient(pa.x, pa.y, pb.x, pb.y);
      const al = o.alpha == null ? 0.55 : o.alpha;
      g.addColorStop(0, cssRGB(rgb, al));
      g.addColorStop(1, cssRGB(rgb, al * 0.12));
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.strokeStyle = g;
      ctx.lineWidth = o.width == null ? 1.6 : o.width;
      ctx.lineCap = "round";
      if (o.dash) ctx.setLineDash(o.dash);
      ctx.stroke();
      if (o.dash) ctx.setLineDash([]);
    },

    dot(x: number, y: number, z: number, rgb: RGB, r: number, o: any = {}): void {
      const q = P.p(x, y, z);
      const a = o.alpha == null ? 1 : o.alpha;
      const rr = Math.max(1, r);
      const g = ctx.createRadialGradient(q.x, q.y, 0, q.x, q.y, rr * 3.6);
      g.addColorStop(0, cssRGB(rgb, 0.95 * a));
      g.addColorStop(0.32, cssRGB(rgb, 0.36 * a));
      g.addColorStop(1, cssRGB(rgb, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(q.x, q.y, rr * 3.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = cssRGB([245, 250, 255], 0.92 * a);
      ctx.beginPath();
      ctx.arc(q.x, q.y, Math.max(0.9, rr * 0.42), 0, Math.PI * 2);
      ctx.fill();
    },

    /** A comet travelling along a world-space polyline. `t` is a 0..1 loop. */
    flow(pts: P3[], t: number, rgb: RGB, o: any = {}): void {
      if (!pts || pts.length < 2) return;
      const segs: number[] = [];
      let total = 0;
      for (let i = 0; i < pts.length - 1; i++) {
        const L = segLen(pts[i], pts[i + 1]);
        segs.push(L);
        total += L;
      }
      if (total <= 0) return;
      const posAt = (tt: number) => {
        let d = Math.max(0, Math.min(1, tt)) * total;
        let i = 0;
        while (i < segs.length - 1 && d > segs[i]) { d -= segs[i]; i++; }
        const f = Math.max(0, Math.min(1, d / (segs[i] || 1)));
        const a = pts[i], b = pts[i + 1];
        return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z + (b.z - a.z) * f };
      };
      const loop = o.loop !== false;
      const tn = loop ? ((t % 1) + 1) % 1 : Math.max(0, Math.min(1, t));
      if (!loop && (t < 0 || t > 1)) return;
      const n = o.trail == null ? 6 : o.trail;
      const spread = o.spread == null ? 0.055 : o.spread;
      const r = o.r == null ? 3.2 : o.r;
      const alpha = o.alpha == null ? 0.9 : o.alpha;
      for (let i = n; i >= 0; i--) {
        let tt = tn - (i / n) * spread;
        if (loop) tt = ((tt % 1) + 1) % 1;
        else tt = Math.max(0, Math.min(1, tt));
        const q = posAt(tt);
        const k = 1 - i / n;
        if (i > 0) {
          ctx.fillStyle = cssRGB(rgb, alpha * 0.22 * k);
          const sp = P.p(q.x, q.y, q.z);
          ctx.beginPath();
          ctx.arc(sp.x, sp.y, r * (0.4 + 0.55 * k), 0, Math.PI * 2);
          ctx.fill();
        } else {
          S.dot(q.x, q.y, q.z, rgb, r, { alpha });
        }
      }
    },

    ring(x: number, y: number, z: number, r: number, rgb: RGB, o: any = {}): void {
      const a = o.alpha == null ? 0.5 : o.alpha;
      ctx.beginPath();
      for (let i = 0; i <= 48; i++) {
        const t = (i / 48) * Math.PI * 2;
        const q = P.p(x + Math.cos(t) * r, y, z + Math.sin(t) * r);
        i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y);
      }
      ctx.closePath();
      if (o.fill) {
        ctx.fillStyle = cssRGB(rgb, a * 0.12);
        ctx.fill();
      }
      ctx.strokeStyle = cssRGB(rgb, a);
      ctx.lineWidth = o.width == null ? 1.6 : o.width;
      ctx.stroke();
    },

    arc(cx: number, y: number, cz: number, r: number, a0: number, a1: number, rgb: RGB, o: any = {}): void {
      ctx.beginPath();
      const steps = Math.max(8, Math.round(Math.abs(a1 - a0) * 18));
      for (let i = 0; i <= steps; i++) {
        const t = a0 + (a1 - a0) * (i / steps);
        const q = P.p(cx + Math.cos(t) * r, y, cz + Math.sin(t) * r);
        i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y);
      }
      ctx.strokeStyle = cssRGB(rgb, o.alpha == null ? 0.7 : o.alpha);
      ctx.lineWidth = o.width == null ? 1.5 : o.width;
      if (o.dash) ctx.setLineDash(o.dash);
      ctx.stroke();
      if (o.dash) ctx.setLineDash([]);
    },

    label(text: string, x: number, y: number, z: number, o: any = {}): void {
      /* narrow canvases (phones): headline only, slightly smaller, so stage
         tags stop stacking on top of each other */
      if (P.dw < 560 && o.sub) o = Object.assign({}, o, { sub: undefined, size: (o.size || 15) * 0.86 });
      const q = P.p(x, y, z);
      const size = o.size || 15;
      const align = o.align || "center";
      const color = o.color || "rgba(206,220,238,.92)";
      ctx.font = `${o.weight || 700} ${size}px ui-monospace, Menlo, Consolas, monospace`;
      ctx.textAlign = align;
      ctx.textBaseline = "middle";
      if (o.bg) {
        let tw = ctx.measureText(text.toUpperCase()).width;
        const subSize = o.sub ? (o.subSize || Math.max(8, size * 0.68)) : 0;
        if (subSize) {
          ctx.font = `${o.subWeight || 600} ${subSize}px ui-monospace, Menlo, Consolas, monospace`;
          tw = Math.max(tw, ctx.measureText(String(o.sub).toUpperCase()).width);
          ctx.font = `${o.weight || 700} ${size}px ui-monospace, Menlo, Consolas, monospace`;
        }
        const pw = tw + 18;
        const ph = size + 10 + (subSize ? subSize + 2 : 0);
        let bx = align === "left" ? q.x - 9 : align === "right" ? q.x - pw + 9 : q.x - pw / 2;
        let by = q.y - size * 0.5 - 5;
        /* Keep tags inside the viewport: on narrow canvases the projection
           can put an anchor near the edge and the plate would be clipped. */
        const loX = 4, hiX = P.dw - pw - 4, loY = 4, hiY = P.dh - ph - 4;
        if (hiX > loX) {
          if (bx < loX) { q.x += loX - bx; bx = loX; }
          else if (bx > hiX) { q.x -= bx - hiX; bx = hiX; }
        }
        if (hiY > loY) {
          if (by < loY) { q.y += loY - by; by = loY; }
          else if (by > hiY) { q.y -= by - hiY; by = hiY; }
        }
        roundRectPath(ctx, bx, by, pw, ph, 5);
        ctx.fillStyle = o.bgFill || "rgba(6,10,17,.72)";
        ctx.fill();
        ctx.strokeStyle = o.bgEdge || "rgba(150,180,220,.18)";
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }
      ctx.fillStyle = color;
      ctx.fillText(text.toUpperCase(), q.x, q.y + 0.5);
      if (o.sub) {
        ctx.font = `${o.subWeight || 600} ${o.subSize || Math.max(8, size * 0.68)}px ui-monospace, Menlo, Consolas, monospace`;
        ctx.fillStyle = o.subColor || "rgba(126,145,170,.75)";
        ctx.fillText(String(o.sub).toUpperCase(), q.x, q.y + size * 0.82 + (o.subSize || size * 0.68) * 0.52);
      }
      if (o.leader) {
        const ly = q.y + size * 0.62 + (o.sub ? size * 0.55 : 0) + 2;
        ctx.strokeStyle = o.leaderColor || "rgba(140,170,205,.16)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(q.x, ly);
        ctx.lineTo(q.x, ly + o.leader);
        ctx.stroke();
      }
    },

    tag(text: string, x: number, y: number, z: number, o: any = {}): void {
      S.label(text, x, y, z, Object.assign({ bg: true, size: 11.5 }, o));
    },

    /** Drafting-style vertical label: reads bottom-to-top from the anchor. */
    vlabel(text: string, x: number, y: number, z: number, o: any = {}): void {
      const q = P.p(x, y, z);
      const size = o.size || 9.5;
      ctx.save();
      ctx.translate(q.x, q.y);
      ctx.rotate(o.down ? Math.PI / 2 : -Math.PI / 2);
      ctx.font = `${o.weight || 700} ${size}px ui-monospace, Menlo, Consolas, monospace`;
      ctx.textAlign = o.align || "left";
      ctx.textBaseline = "middle";
      ctx.fillStyle = o.color || "rgba(150,175,205,.82)";
      ctx.fillText(String(text).toUpperCase(), 0, 0);
      ctx.restore();
    },

    number(n: string, x: number, y: number, z: number, rgb: RGB, o: any = {}): void {
      const q = P.p(x, y, z);
      const r = o.r || 11;
      ctx.beginPath();
      ctx.arc(q.x, q.y, r, 0, Math.PI * 2);
      ctx.fillStyle = o.fill || cssRGB(rgb, 0.16);
      ctx.fill();
      ctx.strokeStyle = cssRGB(rgb, 0.75);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.font = `800 ${o.size || 10}px ui-monospace, Menlo, Consolas, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = o.color || cssRGB(rgb, 0.95);
      ctx.fillText(n, q.x, q.y + 0.5);
    },

    arrow(a: P3, b: P3, rgb: RGB, o: any = {}): void {
      const pa = P.p(a.x, a.y, a.z), pb = P.p(b.x, b.y, b.z);
      const ang = Math.atan2(pb.y - pa.y, pb.x - pa.x);
      const size = o.size || 8;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.strokeStyle = cssRGB(rgb, o.alpha == null ? 0.65 : o.alpha);
      ctx.lineWidth = o.width == null ? 1.4 : o.width;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(pb.x, pb.y);
      ctx.lineTo(pb.x - Math.cos(ang - 0.42) * size, pb.y - Math.sin(ang - 0.42) * size);
      ctx.lineTo(pb.x - Math.cos(ang + 0.42) * size, pb.y - Math.sin(ang + 0.42) * size);
      ctx.closePath();
      ctx.fillStyle = cssRGB(rgb, o.alpha == null ? 0.75 : o.alpha);
      ctx.fill();
    },

    particles(now: number, o: any = {}): void {
      const n = o.n || 28;
      const x0 = o.x0 == null ? bounds.x0 : o.x0;
      const x1 = o.x1 == null ? bounds.x1 : o.x1;
      const z0 = o.z0 == null ? bounds.z0 : o.z0;
      const z1 = o.z1 == null ? bounds.z1 : o.z1;
      const top = o.top || 200;
      const a = o.alpha == null ? 0.16 : o.alpha;
      ctx.save();
      for (let i = 0; i < n; i++) {
        const x = x0 + hash01(i) * (x1 - x0);
        const z = z0 + hash01(i + 41) * (z1 - z0);
        const speed = 6 + hash01(i + 7) * 14;
        const y = ((hash01(i + 91) * top) + (now / 1000) * speed) % top;
        const q = P.p(x, y, z);
        const r = 0.7 + hash01(i + 13) * 1.1;
        ctx.fillStyle = cssRGB([126, 178, 235], a * (0.35 + hash01(i + 31) * 0.65));
        ctx.beginPath();
        ctx.arc(q.x, q.y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    },
  };
  return S;
}

(window as any).SceneKit = {
  begin: kitBegin,
  proj: makeProj,
  rgb: cssRGB,
  shade: shadeRgb,
  hash: hash01,
};

/* ------------------------------------------------------------- registry -- */
function sceneRegistry(list: TowerSpec[]): void {
  const cols = 5, dx = 68;
  towers = list.map((spec, i) => {
    const row = i < cols ? 0 : 1;
    const col = i % cols;
    return {
      ...spec,
      /* staggered city block: the back row sits high in the frame, the front
         row steps half a slot right so every cap stays visible. */
      x: IDX.rack.x + (col - 2) * dx + (row === 1 ? 34 : 0),
      z: IDX.rack.z + (row === 0 ? 35 : -25),
      h: Math.max(38, Math.min(280, 40 + spec.vram * 25)),
      flashT: 0,
      flashRgb: [61, 220, 151],
    };
  });
}

function towerOf(modelId: string | undefined): Tower | null {
  if (!modelId) return null;
  const m = modelId.toLowerCase();
  return towers.find((t) => t.id.toLowerCase() === m) ?? null;
}

function pathFor(tower: Tower | null, cacheHit: boolean): P3[] {
  const pts: P3[] = [WP.apps, WP.gate, WP.class, WP.vram];
  if (cacheHit) {
    pts.push(WP.resp, WP.db);
    return pts;
  }
  if (tower) pts.push({ x: tower.x, y: tower.h + 18, z: tower.z });
  pts.push(WP.resp, WP.db);
  return pts;
}

function spawnPacket(pts: P3[], rgb: RGB, opts: { speed?: number; alpha?: number; r?: number } = {}): void {
  if (packets.length > 70) packets.shift();
  packets.push({
    pts, seg: 0, segT: 0,
    speed: opts.speed ?? 760,
    rgb, alpha: opts.alpha ?? 1, r: opts.r ?? 5,
    trail: [], born: performance.now(),
  });
}

function segLen(a: P3, b: P3): number {
  return Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
}

/* ---------------------------------------------------------- public feed -- */
function sceneRoute(ev: any): void {
  const hit = !!ev.cache_hit || ev.action === "cache-hit" || ev.action === "cache";
  const rgb: RGB = hit
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
    rings.push({ t0: performance.now(), dur: 900, rgb: [255, 107, 138], y: 8, maxR: 150 });
  }
}

function sceneGpu(ev: any): void {
  const total = ev.total_mib || 12288;
  gpuFrac = Math.max(0, Math.min(1, (ev.used_mib || 0) / total));
  gpuRgb = gpuFrac > 0.88 ? [255, 107, 138] : gpuFrac > 0.72 ? [246, 193, 119] : [87, 230, 255];
}

function sceneMemory(_ev: any): void {
  rings.push({ t0: performance.now(), dur: 1500, rgb: RGB_DB, y: 112, maxR: 180 });
}

function sceneControl(ev: any): void {
  const t = towerOf(ev.model);
  if (!t) return;
  if (ev.action === "load") { t.loaded = true; t.flashT = performance.now(); t.flashRgb = [61, 220, 151]; }
  if (ev.action === "unload") { t.loaded = false; t.flashT = performance.now(); t.flashRgb = [246, 193, 119]; }
  rings.push({
    t0: performance.now(), dur: 1200,
    rgb: ev.action === "load" ? [61, 220, 151] : [246, 193, 119],
    y: 4, maxR: 130,
  });
}

/* ----------------------------------------------------------------- ECG --- */
function ecgPush(ev: any): void {
  const now = performance.now();
  const ms = Math.max(40, Math.min(4000, Number(ev.latency_ms) || 260));
  const rgb: RGB = ev.cache_hit
    ? TASK_RGB.cache
    : TASK_RGB[String(ev.task || "route").toLowerCase()] ?? TASK_RGB.route;
  ecgHist.push({ t: now, ms, rgb, cache: !!ev.cache_hit });
  if (ecgHist.length > 90) ecgHist.shift();
  ecgCount++;
  if (ev.cache_hit) ecgCache++;
  paintEcgStats();
}

function paintEcgStats(): void {
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
function tickerPush(kind: string, detail: string): void {
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
function fitTransform(ctx: CanvasRenderingContext2D, w: number, h: number, dpr: number): { sc: number; ox: number; oy: number } {
  const s = Math.min(w / DW, h / DH);
  const ox = (w - DW * s) / 2;
  const oy = (h - DH * s) / 2;
  ctx.setTransform(s * dpr, 0, 0, s * dpr, ox * dpr, oy * dpr);
  return { sc: s, ox, oy };
}

/* ---------------------------------------------------------- index scene -- */
function drawRail(S: any, now: number): void {
  const rail: P3[] = [
    { x: IDX.apps.x, y: 12, z: IDX.apps.z },
    { x: IDX.gate.x, y: 12, z: IDX.gate.z },
    { x: IDX.cls.x, y: 12, z: IDX.cls.z },
    { x: IDX.vram.x, y: 12, z: IDX.vram.z },
    { x: IDX.rack.x - 150, y: 12, z: IDX.rack.z },
    { x: IDX.rack.x, y: 12, z: IDX.rack.z },
    { x: IDX.resp.x, y: 12, z: IDX.resp.z },
    { x: IDX.mem.x, y: 12, z: IDX.mem.z },
  ];
  /* soft underglow */
  S.line(rail, [87, 230, 255], { alpha: 0.07, width: 8 });
  S.line(rail, [87, 230, 255], {
    alpha: 0.32, width: 2, dash: [9, 8], offset: -(now / 26) % 17, glow: 9,
  });
  /* branches to every brain — the rack is a fan-out, then back to respond */
  for (const t of towers) {
    const rgb: RGB = t.loaded ? [87, 230, 255] : [90, 120, 160];
    S.line([{ x: IDX.vram.x, y: 9, z: IDX.vram.z }, { x: t.x, y: 9, z: t.z }], rgb, { alpha: t.loaded ? 0.11 : 0.06, width: 1 });
    S.line([{ x: t.x, y: 9, z: t.z }, { x: IDX.resp.x, y: 9, z: IDX.resp.z }], rgb, { alpha: t.loaded ? 0.09 : 0.05, width: 1 });
  }
}

function drawTower(S: any, t: Tower, now: number): void {
  const base: RGB = t.loaded ? [50, 88, 146] : [29, 39, 55];
  const capRgb: RGB = t.loaded ? [87, 230, 255] : [54, 66, 94];
  S.box(t.x, 0, t.z, 46, t.h, 46, base, {
    emis: t.loaded ? 0.05 : 0,
    edgeAlpha: t.loaded ? 0.24 : 0.1,
    front: shadeRgb(base, 0.9),
  });
  /* vertical status ribbon on the front face */
  S.box(t.x, 4, t.z - 24, 5, Math.max(6, t.h - 12), 1.6, capRgb, {
    emis: t.loaded ? 0.5 : 0.06, edgeAlpha: 0.2,
  });
  /* floor seams: the rack reads as stacked compute, not a blank slab */
  for (let i = 1; i <= 3; i++) {
    const y = 6 + (t.h - 16) * (i / 4);
    S.box(t.x, y, t.z - 23.3, 38, 1.3, 0.6, capRgb, {
      alpha: t.loaded ? 0.55 : 0.2, emis: t.loaded ? 0.18 : 0.02, edgeAlpha: 0,
    });
  }
  /* cap slab */
  S.box(t.x, t.h, t.z, 50, 8, 50, capRgb, {
    emis: t.loaded ? 0.55 : 0.08, edgeAlpha: t.loaded ? 0.5 : 0.14,
  });
  /* control flash */
  const age = now - t.flashT;
  if (age < 700) {
    const k = 1 - age / 700;
    S.box(t.x, 0, t.z, 54, t.h + 8, 54, t.flashRgb, {
      alpha: 0.16 * k, emis: 0.7 * k, edgeAlpha: 0.55 * k,
    });
  }
  /* tiny LED */
  S.dot(t.x, t.h + 18, t.z, capRgb, t.loaded ? 2.4 : 1.6, { alpha: t.loaded ? 0.9 : 0.35 });
}

function drawApps(S: any): void {
  const steel: RGB = [38, 52, 78];
  const blue: RGB = [110, 168, 255];
  const { x, z } = IDX.apps;
  /* base */
  S.box(x, -4, z, 120, 8, 100, [17, 25, 38], { edgeAlpha: 0.1 });
  [-32, 0, 32].forEach((dz, i) => {
    const h = 30 + i * 5;
    S.box(x, 0, z + dz, 54, h, 30, i === 1 ? [44, 60, 88] : steel, { edgeAlpha: 0.22 });
    /* screen */
    S.box(x, h * 0.34, z + dz - 15.4, 40, h * 0.46, 1.2, blue, {
      emis: 0.45, edgeAlpha: 0.35, top: shadeRgb(blue, 0.85),
    });
    /* stand */
    S.box(x, 0, z + dz, 12, 8, 12, [24, 32, 46], { edgeAlpha: 0.12 });
  });
  S.tag("01 · APPS", x, 96, z, {
    size: 12, color: cssRGB(blue, 0.98), sub: "curl · any client", subSize: 8.6,
    bgFill: "rgba(7,12,20,.78)",
  });
}

function drawGateway(S: any): void {
  const steel: RGB = [44, 58, 86];
  const { x, z } = IDX.gate;
  S.box(x, -4, z, 100, 8, 96, [16, 24, 36], { edgeAlpha: 0.12 });
  S.box(x, 0, z - 40, 16, 88, 16, steel, { edgeAlpha: 0.26 });
  S.box(x, 0, z + 40, 16, 88, 16, steel, { edgeAlpha: 0.26 });
  S.box(x, 88, z, 16, 15, 100, RGB_GATE, { emis: 0.42, edgeAlpha: 0.55 });
  /* translucent gate curtain */
  S.box(x, 0, z, 5, 84, 80, RGB_GATE, { glass: true, alpha: 0.7, edgeAlpha: 0.12 });
  /* parse ticks on the floor */
  for (let i = 0; i < 3; i++) {
    S.box(x - 32 + i * 19, 1, z, 6, 2, 38, RGB_GATE, { emis: 0.2, alpha: 0.6, edgeAlpha: 0.12 });
  }
  S.tag("02 · LOCO", x, 132, z, {
    size: 12, color: cssRGB(RGB_GATE, 0.98), sub: "parse · :8200", subSize: 8.6,
    bgFill: "rgba(7,12,20,.78)",
  });
}

function drawClassify(S: any): void {
  const blue: RGB = [135, 197, 255];
  const { x, z } = IDX.cls;
  S.box(x, -4, z, 104, 8, 96, [16, 24, 36], { edgeAlpha: 0.1 });
  S.box(x, 0, z, 64, 54, 64, [35, 54, 86], { emis: 0.04, edgeAlpha: 0.24 });
  S.box(x, 54, z, 30, 10, 30, RGB_GATE, { emis: 0.5, edgeAlpha: 0.5 });
  /* floating task cubes */
  const tasks: [string, RGB, number, number][] = [
    ["vision", [255, 107, 138], -34, 18],
    ["math", [246, 193, 119], 0, -14],
    ["code", [61, 220, 151], 34, 18],
  ];
  tasks.forEach(([name, col, dx, dz], i) => {
    const bob = Math.sin(i * 1.7 + performance.now() / 900) * 3;
    S.box(x + dx, 72 + bob, z + dz, 13, 13, 13, col, { emis: 0.35, edgeAlpha: 0.4 });
  });
  S.tag("03 · CLASSIFY", x, 116, z, {
    size: 12, color: cssRGB(blue, 0.98), sub: "regex → task", subSize: 8.6,
    bgFill: "rgba(7,12,20,.78)",
  });
}

function drawVramGate(S: any): void {
  const amber: RGB = [246, 193, 119];
  const { x, z } = IDX.vram;
  S.box(x, -4, z, 118, 8, 100, [16, 24, 36], { edgeAlpha: 0.1 });
  S.box(x, 0, z - 46, 14, 100, 14, [48, 62, 90], { edgeAlpha: 0.26 });
  S.box(x, 0, z + 46, 14, 100, 14, [48, 62, 90], { edgeAlpha: 0.26 });
  S.box(x, 100, z, 14, 13, 108, amber, { emis: 0.34, edgeAlpha: 0.48 });
  /* gauge track + fill */
  const trackW = 96;
  S.box(x, 120, z, trackW, 7, 11, [22, 31, 46], { edgeAlpha: 0.16 });
  const fw = Math.max(4, trackW * gpuFrac);
  S.box(x - trackW / 2 + fw / 2, 121, z, fw, 5, 9, gpuRgb, {
    emis: 0.55, edgeAlpha: 0.45, top: shadeRgb(gpuRgb, 1.05),
  });
  S.tag("04 · VRAM GATE", x, 152, z, {
    size: 12, color: cssRGB(amber, 0.98),
    sub: `${(gpuFrac * 100).toFixed(0)}% used · evict before load`, subSize: 8.4,
    bgFill: "rgba(7,12,20,.8)",
  });
}

function drawRespond(S: any): void {
  const pink: RGB = [255, 107, 138];
  const { x, z } = IDX.resp;
  S.box(x, -4, z, 90, 8, 90, [16, 24, 36], { edgeAlpha: 0.1 });
  S.box(x, 0, z, 38, 24, 38, [42, 56, 82], { edgeAlpha: 0.22 });
  S.box(x, 24, z, 9, 82, 9, [54, 70, 98], { edgeAlpha: 0.24 });
  S.box(x, 106, z, 16, 13, 16, pink, { emis: 0.52, edgeAlpha: 0.55 });
  /* signal rings */
  const t = (performance.now() % 1900) / 1900;
  S.ring(x, 112, z, 20 + t * 34, pink, { alpha: (1 - t) * 0.28, width: 1.2 });
  S.ring(x, 112, z, 20 + ((t + 0.5) % 1) * 34, pink, { alpha: (1 - ((t + 0.5) % 1)) * 0.22, width: 1 });
  S.tag("06 · RESPOND", x, 154, z, {
    size: 12, color: cssRGB(pink, 0.98), sub: "sse · buffered", subSize: 8.6,
    bgFill: "rgba(7,12,20,.8)",
  });
}

function drawMemory(S: any): void {
  const green: RGB = [61, 220, 151];
  const { x, z } = IDX.mem;
  S.box(x, -4, z, 110, 8, 110, [16, 24, 36], { edgeAlpha: 0.1 });
  S.cyl(x, 0, z, 55, 104, [30, 84, 66], { emis: 0.12, edgeAlpha: 0.28 });
  /* sqlite strata */
  [26, 52, 78].forEach((h) => S.ring(x, h, z, 55, [61, 220, 151], { alpha: 0.16, width: 1 }));
  S.ring(x, 60, z, 76, [61, 220, 151], { alpha: 0.13, width: 1.2 });
  const a = performance.now() / 1900;
  for (let i = 0; i < 8; i++) {
    const t = a + (i / 8) * Math.PI * 2;
    S.dot(x + Math.cos(t) * 76, 62 + Math.sin(a * 1.4 + i) * 8, z + Math.sin(t) * 76, green, 1.7, { alpha: 0.6 });
  }
  S.dot(x, 104, z, green, 3, { alpha: 0.8 });
  S.tag("07 · MEMORY", x, 134, z, {
    size: 12, color: cssRGB(green, 0.98), sub: "sqlite · fts5 · 384d", subSize: 8.4,
    bgFill: "rgba(7,12,20,.8)",
  });
}

function drawPackets(S: any, dt: number): void {
  const ctx = S.ctx;
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
    const pos: P3 = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t };
    const sp = S.p(pos.x, pos.y, pos.z);

    pk.trail.unshift({ x: sp.x, y: sp.y });
    if (pk.trail.length > 10) pk.trail.pop();
    for (let i = pk.trail.length - 1; i > 0; i--) {
      const al = (1 - i / pk.trail.length) * 0.55 * pk.alpha;
      ctx.beginPath();
      ctx.moveTo(pk.trail[i].x, pk.trail[i].y);
      ctx.lineTo(pk.trail[i - 1].x, pk.trail[i - 1].y);
      ctx.strokeStyle = hexOf(pk.rgb, 1, al);
      ctx.lineWidth = Math.max(1, pk.r * 0.66 * (1 - i / pk.trail.length));
      ctx.lineCap = "round";
      ctx.stroke();
    }

    const r = Math.max(2.2, pk.r * 1.15);
    const glow = ctx.createRadialGradient(sp.x, sp.y, 0, sp.x, sp.y, r * 3.6);
    glow.addColorStop(0, hexOf(pk.rgb, 1, 0.95 * pk.alpha));
    glow.addColorStop(0.34, hexOf(pk.rgb, 1, 0.34 * pk.alpha));
    glow.addColorStop(1, hexOf(pk.rgb, 1, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(sp.x, sp.y, r * 3.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = hexOf([245, 250, 255], 1, 0.9 * pk.alpha);
    ctx.beginPath();
    ctx.arc(sp.x, sp.y, r * 0.45, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawSceneRings(S: any, now: number): void {
  rings = rings.filter((r) => now - r.t0 < r.dur);
  for (const r of rings) {
    const k = (now - r.t0) / r.dur;
    const rad = 16 + k * r.maxR;
    S.ring(WP.db.x, r.y, WP.db.z, rad, r.rgb, { alpha: (1 - k) * 0.65, width: 2.2 });
  }
}

function drawIndexScene(ctx: CanvasRenderingContext2D, cv: HTMLCanvasElement, w: number, h: number, now: number, dt: number): void {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  /* Fit the actual diorama, not an empty bounding box: the drafting grid is
     allowed to bleed off-canvas so the stages and rack stay large. */
  const fit: P3[] = [
    { x: IDX.apps.x - 60, y: 0, z: IDX.apps.z - 50 },
    { x: IDX.apps.x - 60, y: 104, z: IDX.apps.z },
    { x: IDX.gate.x, y: 140, z: IDX.gate.z },
    { x: IDX.cls.x, y: 124, z: IDX.cls.z },
    { x: IDX.vram.x, y: 164, z: IDX.vram.z },
    { x: IDX.resp.x, y: 178, z: IDX.resp.z },
    { x: IDX.mem.x, y: 144, z: IDX.mem.z },
    { x: IDX.rack.x - 190, y: 0, z: IDX.rack.z - 45 },
    { x: IDX.rack.x + 220, y: 0, z: IDX.rack.z + 45 },
    { x: IDX.rack.x, y: 314, z: IDX.rack.z + 35 },
  ];
  for (const t of towers) fit.push({ x: t.x, y: t.h + 18, z: t.z });

  ctx.save();
  ctx.translate(mouseX * 16, mouseY * 9);
  const S = kitBegin(ctx, IDX_BOUNDS, {
    dw: w, dh: h, ky: 0.42,
    padX: 16, padTop: 48, padBottom: 40,
    points: fit,
  });
  S.bg();
  S.grid({ x0: -480, x1: 520, z0: -240, z1: 220, step: 40 });

  drawRail(S, now);

  /* back-to-front: rack rows first, then the stages, then the near islands.
     Each tower's name is painted with the tower itself, on its front face, so
     a nearer tower correctly occludes the name of the one behind it. */
  const back = towers.filter((t) => t.z > 150).sort((a, b) => a.x - b.x);
  const front = towers.filter((t) => t.z <= 150).sort((a, b) => a.x - b.x);
  for (const t of [...back, ...front]) {
    drawTower(S, t, now);
    const col = t.loaded ? [150, 240, 255] as RGB : [128, 144, 168] as RGB;
    const lenW = (t.short.length * 5.0) / Math.max(0.1, S.s);
    const ly = Math.max(10, t.h - lenW - 8);
    S.vlabel(t.short, t.x - 12, ly, t.z - 24, {
      size: 8.4, color: cssRGB(col, t.loaded ? 0.92 : 0.68),
    });
  }
  drawVramGate(S);
  drawClassify(S);
  drawRespond(S);
  drawGateway(S);
  drawMemory(S);
  drawApps(S);

  /* rack title above the skyline */
  S.tag("05 · MODEL RACK", IDX.rack.x, 314, IDX.rack.z + 35, {
    size: 12.5, color: "rgba(190,214,248,.98)",
    sub: `${towers.length} brains · height = vram`, subSize: 8.8,
    bgFill: "rgba(7,12,20,.8)",
  });

  drawSceneRings(S, now);
  drawPackets(S, dt);
  S.particles(now, { n: 30, top: 200, alpha: 0.13, x0: -420, x1: 460, z0: -200, z1: 200 });

  /* idle heartbeat: the lab is alive even with no traffic */
  if (now - heartT > 2600) {
    heartT = now;
    spawnPacket([WP.gate, WP.class, WP.vram, WP.resp, WP.db], RGB_HEARTBEAT, { speed: 640, alpha: 0.5, r: 4 });
    rings.push({ t0: now, dur: 1800, rgb: [40, 120, 96], y: 104, maxR: 130 });
  }
  ctx.restore();
}

/* ----------------------------------------------------------------- ECG --- */
function drawEcg(now: number): void {
  const ctx = ctxEcg;
  if (!ctx || !cvEcg) return;
  const w = cvEcg.clientWidth, h = cvEcg.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const baseY = h * 0.76;
  const WIN = 26000;

  /* monitor grid */
  ctx.strokeStyle = "rgba(61,220,151,.07)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = w; x >= 0; x -= 46) { ctx.moveTo(x, 0); ctx.lineTo(x, h); }
  for (let y = h; y >= 0; y -= 24) { ctx.moveTo(0, y); ctx.lineTo(w, y); }
  ctx.stroke();

  /* baseline */
  ctx.strokeStyle = "rgba(61,220,151,.34)";
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

  /* flatline note */
  const live = ecgHist.some((e) => now - e.t < WIN);
  if (!live) {
    ctx.font = "700 9.5px ui-monospace, Menlo, monospace";
    ctx.fillStyle = "rgba(126,145,170,.5)";
    ctx.textAlign = "center";
    ctx.fillText("FLATLINE · WAITING FOR ROUTE EVENTS", w * 0.5, h * 0.46);
    ctx.textAlign = "left";
  }

  /* sweep head */
  const hx = w - 2;
  const breathe = 0.7 + 0.3 * Math.sin(now / 900);
  ctx.fillStyle = `rgba(61,220,151,${0.55 + 0.35 * breathe})`;
  ctx.beginPath();
  ctx.arc(hx, baseY, 3, 0, Math.PI * 2);
  ctx.fill();
  const sweep = ctx.createLinearGradient(hx - 70, 0, hx, 0);
  sweep.addColorStop(0, "rgba(61,220,151,0)");
  sweep.addColorStop(1, "rgba(61,220,151,.28)");
  ctx.fillStyle = sweep;
  ctx.fillRect(hx - 70, 0, 70, h);

  /* label */
  ctx.font = "700 10px ui-monospace, Menlo, monospace";
  ctx.fillStyle = "rgba(138,149,173,.78)";
  /* the card header already names the strip; only repeat it when there is
     room beside the stats readout pinned top-right */
  if (w >= 560) ctx.fillText("ROUTE LATENCY · 26s WINDOW · ms → AMP", 12, 16);
}

/* ------------------------------------------------------------ main loop -- */
function frame(ts: number): void {
  rafId = requestAnimationFrame(frame);
  if (document.hidden) { lastTs = ts; return; }
  const dt = Math.min(0.05, (ts - lastTs) / 1000 || 0.016);
  lastTs = ts;
  drawScene(ts, dt);
  drawEcg(ts);
}

function drawScene(now: number, dt: number): void {
  const cv = sceneCanvasId
    ? (document.getElementById(sceneCanvasId) as HTMLCanvasElement | null)
    : cvScene;
  if (sceneActor) {
    if (!cv) return;
    const c2 = cv.getContext("2d");
    if (c2) {
      c2.setTransform(1, 0, 0, 1, 0, 0);
      c2.clearRect(0, 0, cv.width, cv.height);
    }
    sceneActor({
      ctx: c2 || ctxScene, cv, w: cv.clientWidth, h: cv.clientHeight,
      now, dt, fitTransform,
    }, now, dt);
    return;
  }
  if (!ctxScene || !cvScene) return;
  const w = cvScene.clientWidth, h = cvScene.clientHeight;
  if (!w || !h) return;
  drawIndexScene(ctxScene, cvScene, w, h, now, dt);
}

function resizeAll(): void {
  const dpr = window.devicePixelRatio || 1;
  for (const cv of [cvScene, cvEcg]) {
    if (!cv) continue;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) continue;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
}

function sceneBoot(): void {
  if (booted) return;
  booted = true;
  const sceneId = sceneCanvasId || "scene";
  cvScene = document.getElementById(sceneId) as HTMLCanvasElement | null;
  cvEcg = document.getElementById("ecg") as HTMLCanvasElement | null;
  if (!cvScene && !cvEcg) return;
  ctxScene = cvScene?.getContext("2d") ?? null;
  ctxEcg = cvEcg?.getContext("2d") ?? null;
  resizeAll();
  window.addEventListener("resize", resizeAll);
  /* Layout can change without a window resize (fonts settle, a sibling card
     grows, the sidebar collapses on mobile). Keep the backing store matched
     to the element so the scene never renders stretched or soft. */
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => resizeAll());
    if (cvScene) ro.observe(cvScene);
    if (cvEcg) ro.observe(cvEcg);
  }

  cvScene?.addEventListener("mousemove", (ev) => {
    const r = cvScene!.getBoundingClientRect();
    mouseX = (ev.clientX - r.left) / r.width - 0.5;
    mouseY = (ev.clientY - r.top) / r.height - 0.5;
  });
  cvScene?.addEventListener("mouseleave", () => { mouseX = 0; mouseY = 0; });

  paintEcgStats();
  if (!rafId) { lastTs = performance.now(); rafId = requestAnimationFrame(frame); }
}

/* ---- generic page scene wiring (per-page 3D canvas) ---- */
function sceneSetCanvas(id: string): void { sceneCanvasId = id; }
function sceneSetActor(fn: ((ctx: any, now: number, dt: number) => void) | null): void {
  sceneActor = fn;
  if (!booted) sceneBoot();
}
function sceneBurst(color: RGB, wpFrom: P3, wpTo: P3): void {
  packets.push({ pts: [wpFrom, wpTo], seg: 0, segT: 0, speed: 640, rgb: color, alpha: 1, r: 5, trail: [], born: performance.now() });
}
