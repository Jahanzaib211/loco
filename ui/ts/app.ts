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

/** Every brain the lab can serve. Roles are measured, not guessed.
 *  Bootstrapped from /v1/models at runtime so newly registered models
 *  appear without a UI rebuild; this list is the offline fallback. */
let MODELS: ModelDef[] = [
  { id: "loco:auto", name: "Loco Auto", role: "router decides", backend: "auto" },
  { id: "hermes-4-14b-iq4_xs", name: "Hermes 4 14B — Chain Leader", role: "agentic tools 8/10 · role 6/8 · 16k · ~37 t/s (measured)", backend: "local GGUF stack :8080" },
  { id: "nemotron-3.5-bolt-e4b", name: "Bolt — Fast Fallback Lane", role: "fallback · 71 t/s · 32k · 4.3GB", backend: "local GGUF stack :8080" },
  { id: "huihui_ai/gemma-4-abliterated:e4b", name: "Flagship — Vision Lane", role: "flagship chat · vision (embedded, only)", backend: "managed backend :11434" },
  { id: "ornith-1.5-9b", name: "Orbit — Reasoning/Compact Fallback", role: "mid reasoning · 65k · fallback", backend: "local GGUF stack :8080" },
  { id: "lfm3-1.7b", name: "Sparrow — Fastest Fallback", role: "routing · extract · 155 t/s · fallback", backend: "local GGUF stack :8080" },
  { id: "deepseek-r1-14b", name: "Axiom — Large Reasoner", role: "reasoning · math · code · explicit", backend: "local GGUF stack :8080" },
  { id: "phi4-mini-3.8b", name: "Scout — Balanced Small", role: "general small · 65k ctx · explicit", backend: "local GGUF stack :8080" },
  { id: "bonsai-2-9b", name: "Bonsai — Benchmarked Low", role: "reasoning (text) · no vision · explicit", backend: "local GGUF stack :8080" },
  { id: "Underdog-Saluki-27B-1.0-IQ2-mix", name: "Candidate — Benchmark Lane", role: "heavy role specialist · explicit only", backend: "local GGUF stack :8080" },
];

/** <option> markup for a model <select>, pre-selecting `sel`. */
function modelOptions(sel: string): string {
  return MODELS.map(
    (m) => `<option value="${esc(m.id)}"${m.id === sel ? " selected" : ""}>${esc(m.name)} (${esc(m.id)})</option>`
  ).join("");
}

/** Pull the live registry so new models appear without a rebuild. */
async function refreshModelsFromServer(): Promise<void> {
  try {
    const r = await api("/v1/models");
    const data: any[] = r.data?.data || [];
    if (!data.length) return;
    const known = new Map(MODELS.map((m) => [m.id, m]));
    const merged: ModelDef[] = [known.get("loco:auto") || { id: "loco:auto", name: "Loco Auto", role: "router decides", backend: "auto" }];
    for (const m of data) {
      if (m.id === "bge-small-en-v1.5") continue; // embedding, not a chat target
      const k = known.get(m.id);
      merged.push({
        id: m.id,
        name: k?.name || m.id,
        role: (m.role as string) || k?.role || "registered",
        backend: (m.backend as string) || k?.backend || "local",
      });
    }
    MODELS = merged;
    // re-render any model selects that were populated from modelOptions()
    document.querySelectorAll("select").forEach((sel) => {
      const cur = sel.value;
      if (!Array.from(sel.options).some((o) => o.value === "loco:auto")) return;
      sel.innerHTML = modelOptions(cur || "loco:auto");
    });
  } catch { /* offline fallback stays */ }
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

/* ------- theme (Modern Minimalist · light default / dark) ----------------
   The <head> snippet applies the stored choice before first paint; this
   module only syncs the sidebar toggle and broadcasts the change to any
   canvas scene listening on the "loco:theme" event. */
const THEME_KEY = "loco-theme";

function currentTheme(): "light" | "dark" {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(mode: "light" | "dark", persist = true): void {
  const root = document.documentElement;
  if (mode === "dark") root.dataset.theme = "dark";
  else delete root.dataset.theme;
  if (persist) {
    try { localStorage.setItem(THEME_KEY, mode); } catch { /* private mode */ }
  }
  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.innerHTML =
      `<span class="tt-ico" aria-hidden="true">${mode === "dark" ? "☾" : "☀"}</span>` +
      `<span>${mode === "dark" ? "Dark" : "Light"}</span>`;
    btn.setAttribute("aria-label", `Switch to ${mode === "dark" ? "light" : "dark"} theme`);
    btn.setAttribute("aria-pressed", String(mode === "dark"));
  }
  window.dispatchEvent(new CustomEvent("loco:theme", { detail: { mode } }));
}

function toggleTheme(): void {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
}

function mountThemeToggle(): void {
  if (document.getElementById("themeToggle")) return;
  const side = document.querySelector(".side");
  if (!side) return;
  const row = document.createElement("div");
  row.className = "theme-row";
  row.innerHTML = '<button id="themeToggle" class="theme-toggle" type="button"></button>';
  side.insertBefore(row, side.querySelector(".footer"));
  row.querySelector("button")?.addEventListener("click", toggleTheme);
  applyTheme(currentTheme(), false);   // sync the label with the pre-paint choice
}

document.addEventListener("DOMContentLoaded", () => {
  renderNav(document.body.dataset.active || "");
  mountThemeToggle();
  refreshModelsFromServer();
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
