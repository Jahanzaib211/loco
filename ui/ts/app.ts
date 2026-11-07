/* ============================================================================
   Loco UI core — shared helpers. Authored in TypeScript, compiled to app.js
   as a classic script by scripts/build-ui (zero deps, no bundler).
   Classic-script rules: top-level declarations become globals for the
   per-page inline scripts. No import/export in this file.
   ========================================================================== */

/** Shape of a JSON API reply from the Loco server. */
interface ApiResult {
  ok: boolean;
  status: number;
  data: any;
}

/** One selectable brain in the playground dropdown. */
interface ModelDef {
  id: string;
  name: string;
  role: string;
  backend: string;
}

type WsHandler = (msg: any) => void;

/** JSON helper: fetch + parse, never throws. */
async function api(path: string, opts: { method?: string; body?: any } = {}): Promise<ApiResult> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {
    method,
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { ok: r.ok, status: r.status, data };
}

/** Transient corner notification. `kind` switches to the error palette. */
function toast(msg: string, ms = 3200, kind: "ok" | "err" = "ok"): void {
  let el = document.getElementById("toast") as HTMLDivElement | null;
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle("err", kind === "err");
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), ms);
}

const $ = (sel: string): HTMLElement | null => document.querySelector(sel);
const $$ = (sel: string): HTMLElement[] => Array.from(document.querySelectorAll(sel));

/** Bytes-in-MiB → "4.3 GB" (or "—" when unknown). */
function fmtMiB(mib: number | null | undefined): string {
  if (mib == null) return "—";
  return (mib / 1024).toFixed(1) + " GB";
}

/** Locale-grouped integer, "—" for null. */
function fmtNum(n: number | null | undefined): string {
  if (n == null) return "—";
  return Number(n).toLocaleString();
}

/** Integer percent. */
function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return Math.round(n) + "%";
}

/** HTML-escape untrusted strings before template interpolation. */
function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

/** Seconds-since → compact "42s" / "7m". */
function since(ts: number | null | undefined): string {
  if (!ts) return "—";
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  return s < 60 ? s + "s" : Math.floor(s / 60) + "m";
}

/** HH:MM:SS wall clock for tickers. */
function clockOf(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Flash a KPI briefly when its value changes. */
function flash(el: HTMLElement | null): void {
  if (!el) return;
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 650);
}

/** Every brain the lab can serve. Roles are measured, not guessed. */
const MODELS: ModelDef[] = [
  { id: "loco:auto", name: "Loco Auto", role: "router decides", backend: "auto" },
  { id: "huihui_ai/gemma-4-abliterated:e4b", name: "Flagship — Local Main Candidate A", role: "flagship chat · vision", backend: "managed backend :11434" },
  { id: "deepseek-r1-14b", name: "Axiom — Large Reasoner", role: "reasoning · math · code", backend: "local GGUF stack :8080" },
  { id: "phi4-mini-3.8b", name: "Scout — Balanced Small", role: "general small · 65k ctx", backend: "local GGUF stack :8080" },
  { id: "lfm3-1.7b", name: "Sparrow — Fastest Lightweight", role: "routing · extract · fast", backend: "local GGUF stack :8080" },
  { id: "ornith-1.5-9b", name: "Orbit — Main General Lane", role: "mid reasoning · 65k", backend: "local GGUF stack :8080" },
  { id: "nemotron-3.5-bolt-e4b", name: "Bolt — Primary General Lane", role: "chat · math · code · longdoc first", backend: "local GGUF stack :8080" },
  { id: "bonsai-2-9b", name: "Bonsai — Benchmarked Low", role: "mid general", backend: "local GGUF stack :8080" },
  { id: "Underdog-Saluki-27B-1.0-IQ2-mix", name: "Candidate — Benchmark Lane", role: "heavy role specialist · explicit only", backend: "local GGUF stack :8080" },
];

/** <option> markup for a model <select>, pre-selecting `sel`. */
function modelOptions(sel: string): string {
  return MODELS.map(
    (m) => `<option value="${esc(m.id)}"${m.id === sel ? " selected" : ""}>${esc(m.name)} (${esc(m.id)})</option>`
  ).join("");
}

/** Sidebar navigation. href ends with the page's data-active value. */
const NAV: string[][] = [
  ["/ui/index.html", "Lab overview"],
  ["/ui/chat.html", "Chat playground"],
  ["/ui/api.html", "API catalog"],
  ["/ui/memory.html", "Memory bank"],
  ["/ui/compact.html", "Context compactor"],
  ["/ui/embed.html", "Embeddings"],
  ["/ui/tools.html", "Agentic tools"],
  ["/ui/pipeline.html", "Pipeline & identity"],
];

function renderNav(active: string): void {
  const el = document.getElementById("nav");
  if (!el) return;
  el.innerHTML = NAV.map(
    ([href, label]) =>
      `<a href="${href}" class="${href.endsWith(active) ? "active" : ""}">${label}</a>`
  ).join("");
}
document.addEventListener("DOMContentLoaded", () => {
  renderNav(document.body.dataset.active || "");
  /* stamp the running version into every page footer */
  api("/health").then((r) => {
    const v = r.data?.version;
    if (!v) return;
    document.querySelectorAll(".footer").forEach((f) => {
      const el = f as HTMLElement;
      if (el.dataset.ver) return;
      el.dataset.ver = v;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.style.marginLeft = "8px";
      tag.textContent = "v" + v;
      el.appendChild(tag);
    });
  }).catch(() => { /* offline: leave the footer clean */ });
});

/* ------- live feed (Loco /ws) --------------------------------------------
   One socket per page. Handlers registered with onWs receive every event:
   gpu (1s snapshot), route, memory, control. Reconnects with backoff.   */
let _ws: WebSocket | null = null;
let _wsRetry = 0;
const _handlers: WsHandler[] = [];

function onWs(handler: WsHandler): void {
  _handlers.push(handler);
}

function wsConnect(): void {
  try {
    _ws = new WebSocket(`ws://${location.host}/ws`);
  } catch {
    _scheduleReconnect();
    return;
  }
  _ws.onopen = () => { _wsRetry = 0; _setLive(true); };
  _ws.onclose = () => { _setLive(false); _scheduleReconnect(); };
  _ws.onerror = () => { try { _ws?.close(); } catch { /* already closing */ } };
  _ws.onmessage = (ev: MessageEvent) => {
    let msg: any;
    try { msg = JSON.parse(String(ev.data)); } catch { return; }
    wsEventCount++;
    for (const h of _handlers) {
      try { h(msg); } catch (e) { console.error(e); }
    }
    /* bridge into the 3D component bus so page scenes react to live events */
    try { fireSceneEvent(msg.type, msg); } catch (e) { console.error(e); }
  };
}

function _scheduleReconnect(): void {
  const delay = Math.min(8000, 500 * Math.pow(2, _wsRetry++));
  setTimeout(wsConnect, delay);
}

function _setLive(on: boolean): void {
  document.querySelectorAll(".live-dot").forEach((el) => el.classList.toggle("bad", !on));
  const t = document.getElementById("liveTxt");
  if (t) t.textContent = on ? "live · ws" : "reconnecting…";
}

/** WS events seen since page load — the scene HUD reads this. */
let wsEventCount = 0;

document.addEventListener("DOMContentLoaded", wsConnect);
