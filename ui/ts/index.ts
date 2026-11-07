/* ============================================================================
   Lab overview page — KPIs, model registry (load/unload/configure), and the
   wiring between the live /ws feed and the 3D scene + ECG pulse strip.
   Loaded after app.js (helpers) and scene.js (scene feed).
   ========================================================================== */

/** Registry row: UI copy + measured numbers + scene-tower metadata. */
interface RegRow {
  id: string;
  name: string;
  short: string;        // tower label in the scene
  role: string;
  why: string;
  ctx: string;
  tps: string;
  vram: string;
  vramN: number;        // GB, scene tower height
  pinned?: boolean;
}

const REGISTRY: RegRow[] = [
  { id: "loco:auto", name: "Loco Auto", short: "auto", role: "router decides",
    why: "delegates to the deterministic map; you never pick wrong by hand",
    ctx: "—", tps: "—", vram: "—", vramN: 0, pinned: true },
  { id: "hermes-4-14b-iq4_xs", name: "Hermes 4 14B — Chain Leader", short: "Hermes4",
    role: "agentic tools leader · measured",
    why: "PROMOTED 2026-11-07 (measured): agentic tools 8/10 (best on rig), role 6/8 (ties Nemotron). Leads auto chains. Blast-radius tradeoff vs prior leader Bolt: 9.0GB vs 4.3GB, ~37 t/s vs 71 t/s, 16k vs 32k ctx; unguided multi-step math miss (14 vs 15) and safe-002 forbidden-tool miss observed. Text-only (no vision).",
    ctx: "16384", tps: "37", vram: "9.0GB", vramN: 9.0 },
  { id: "nemotron-3.5-bolt-e4b", name: "Bolt — Fast Fallback Lane", short: "Bolt",
    role: "fallback · fast · 32k",
    why: "prior chain leader; 6/8 role, 6/10 tool — now primary fallback: 71 t/s, 4.3GB, 32k ctx (faster/lighter/longer than Hermes).",
    ctx: "32768", tps: "71", vram: "4.3GB", vramN: 4.3 },
  { id: "ornith-1.5-9b", name: "Orbit — Reasoning/Compact Fallback", short: "Orbit",
    role: "mid reasoning · 65k fallback",
    why: "5/8 role, 6/10 tool — fallback in chains; strong for compaction/65k. Thinks out loud (reasoning_content).",
    ctx: "65536", tps: "54", vram: "6.6GB", vramN: 6.6 },
  { id: "lfm3-1.7b", name: "Sparrow — Fastest Fallback", short: "Sparrow",
    role: "routing · extraction · fast fallback",
    why: "3/8 role, 5/10 tool — last fallback and fastest (155 t/s), 131k ctx, 1.8GB.",
    ctx: "131072", tps: "155", vram: "1.8GB", vramN: 1.8 },
  { id: "phi4-mini-3.8b", name: "Scout — Explicit-only Role Specialist", short: "Scout",
    role: "general small · role prompts",
    why: "6/8 role but 1/10 tool — strong prose roles, weak tool grammar; kept out of auto chains, select it explicitly",
    ctx: "65536", tps: "94", vram: "6.9GB", vramN: 6.9 },
  { id: "huihui_ai/gemma-4-abliterated:e4b", name: "Flagship — Vision Lane", short: "Flagship",
    role: "flagship chat · vision (embedded, only)",
    why: "4/8 role, 6/10 tool; the ONLY model on this rig with real image vision (embedded). PDF-as-image not supported. Explicit selection / managed backend.",
    ctx: "131072", tps: "77", vram: "9.7GB", vramN: 9.7 },
  { id: "deepseek-r1-14b", name: "Axiom — Heavy Reasoner", short: "Axiom",
    role: "reasoning · math · code · explicit",
    why: "1/8, 1/10 at default budget (needs max_tokens ≥ 1000), heaviest footprint (9.9GB), heavy reasoning_content — explicit only",
    ctx: "16384", tps: "33", vram: "9.9GB", vramN: 9.9 },
  { id: "Underdog-Saluki-27B-1.0-IQ2-mix", name: "Candidate — Benchmark Lane", short: "Candidate",
    role: "heavy role specialist · explicit only",
    why: "7/8 role (best), 6/10 tool — slowest (23 t/s) and 8.7GB, explicit only",
    ctx: "16384", tps: "23", vram: "8.7GB", vramN: 8.7 },
  { id: "bonsai-2-9b", name: "Bonsai — Benchmarked Low", short: "Bonsai",
    role: "reasoning (text) · no vision · explicit",
    why: "0/8 role, 4/10 tool. qwen35 text-only (no vision/mmproj) — cannot process image/PDF inputs; emits reasoning_content, needs token budget.",
    ctx: "65536", tps: "92", vram: "4.0GB", vramN: 4.0 },
  { id: "bge-small-en-v1.5", name: "Index — Embedder", short: "Index",
    role: "embeddings 384d",
    why: "always-on semantic key for memory + cache",
    ctx: "512", tps: "—", vram: "0.06GB", vramN: 1.2, pinned: true },
];

/* ------------------------------------------------------- control config -- */
let cfgCache: { presets: Record<string, any>; docs: Record<string, any> } | null = null;

async function getCfg(): Promise<{ presets: Record<string, any>; docs: Record<string, any> }> {
  if (cfgCache) return cfgCache;
  const r = await api("/v1/control/config");
  cfgCache = { presets: r.data?.presets || {}, docs: r.data?.flag_docs || {} };
  return cfgCache;
}

/* ----------------------------------------------------------- registry UI - */
async function loadRegistry(status: any): Promise<void> {
  const byId: Record<string, any> = Object.fromEntries(
    ((status.llama_cpp || []) as any[]).map((m) => [m.id, m])
  );
  const managedLoaded = /gemma/.test(String(status.ollama_ps || ""));
  const banner = $("#presetBanner");
  if (banner) {
    banner.innerHTML = status.presets_active
      ? '<span class="badge">per-model flags active</span>'
      : `<span class="badge warn">flags not live yet — <code>sh scripts/install-preset-unit.sh</code> (sudo, once)</span>`;
  }

  const towerList: TowerSpec[] = [];
  const reg = $("#registry");
  if (!reg) return;

  reg.innerHTML = REGISTRY.map((m) => {
    const llama = byId[m.id];
    const loaded = m.pinned
      ? true
      : llama
        ? !!llama.loaded
        : m.id.startsWith("huihui") ? managedLoaded : false;

    if (m.id !== "loco:auto") {
      towerList.push({ id: m.id, short: m.short, vram: m.vramN, loaded: !!loaded || !!m.pinned });
    }

    const pill = m.pinned
      ? '<span class="pill">resident</span>'
      : loaded
        ? '<span class="pill"><span class="dot"></span>loaded</span>'
        : '<span class="pill"><span class="dot warn"></span>idle</span>';
    const btn = m.pinned
      ? ""
      : `<button class="${loaded ? "danger" : "primary"}" data-act="${loaded ? "unload" : "load"}" data-model="${esc(m.id)}">${loaded ? "Unload" : "Load"}</button>`;
    const editable = m.id !== "loco:auto" && m.id !== "bge-small-en-v1.5";

    return `<div class="card">
      <div class="row"><h3 style="margin:0">${esc(m.name)}</h3><span class="spacer"></span>${pill}</div>
      <div class="muted" style="font-size:12px;margin:6px 0">${esc(m.role)}</div>
      <p style="font-size:12px;color:#b8c2d4">${esc(m.why)}</p>
      <div class="row" style="margin:10px 0"><span class="tag">ctx ${esc(m.ctx)}</span><span class="tag">${esc(m.tps)} t/s</span><span class="tag">${esc(m.vram)}</span></div>
      <div class="row">${btn}<button class="cfgToggle" data-toggle="${esc(m.id)}">Configure</button><a class="btn" href="/ui/identities/${encodeURIComponent(m.id.split("/").pop()!.split(":")[0])}.md">why / what / style</a></div>
      ${editable ? `<div class="cfgPanel" data-panel="${esc(m.id)}" hidden>
        <div class="cfgGrid">
          <label>-ngl (gpu layers)<input type="number" min="0" max="99" data-f="n-gpu-layers"></label>
          <label>ctx-size<input type="number" min="512" step="512" data-f="ctx-size"></label>
          <label>cache-type-k<select data-f="cache-type-k"><option value="">(unset)</option><option>f16</option><option>q8_0</option><option>q4_0</option></select></label>
          <label>cache-type-v<select data-f="cache-type-v"><option value="">(unset)</option><option>f16</option><option>q8_0</option><option>q4_0</option></select></label>
          <label>flash-attn<select data-f="flash-attn"><option value="">(unset)</option><option>on</option><option>off</option></select></label>
          <label>keep (sink)<input type="number" min="0" data-f="keep" placeholder="1"></label>
        </div>
        <label>System prompt (md)</label>
        <textarea class="cfgPrompt" data-p="${esc(m.id)}" style="min-height:70px"></textarea>
        <div class="row" style="margin-top:6px"><button class="saveFlags" data-model="${esc(m.id)}">Save flags</button><button class="savePrompt" data-model="${esc(m.id)}">Save prompt</button><span class="spacer"></span><span class="cfgHint" data-hint="${esc(m.id)}"></span></div>
      </div>` : ""}
    </div>`;
  }).join("");

  sceneRegistry(towerList);

  /* lifecycle buttons */
  $$("#registry [data-act]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      const r = await api("/v1/control/" + (b as HTMLElement).dataset.act, {
        method: "POST",
        body: { model: (b as HTMLElement).dataset.model },
      });
      toast(r.ok ? String(r.data?.detail || "done") : "failed: " + String(r.data?.error?.message || r.status), 3200, r.ok ? "ok" : "err");
      refresh();
    })
  );

  /* configure panels: lazy-load prompt + flags once */
  $$("#registry .cfgToggle").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const el = btn as HTMLElement;
      const model = el.dataset.toggle!;
      const panel = document.querySelector(`[data-panel="${CSS.escape(model)}"]`) as HTMLElement | null;
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (panel.hidden) return;
      const cfg = await getCfg();
      const kv = cfg.presets[model] || {};
      panel.querySelectorAll<HTMLElement>("[data-f]").forEach((i) => { i.value = kv[(i.dataset as any).f] ?? ""; });
      const ta = panel.querySelector(".cfgPrompt") as HTMLTextAreaElement | null;
      if (ta && !ta.dataset.loaded) {
        const pr = await api("/v1/control/prompt?model=" + encodeURIComponent(model));
        ta.value = pr.data?.content || "";
        ta.dataset.loaded = "1";
      }
      const hint = panel.querySelector(".cfgHint");
      if (hint) hint.textContent = "flags apply on next load · restart: sudo systemctl restart llama-server";
    })
  );

  $$("#registry .saveFlags").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const el = btn as HTMLElement;
      const model = el.dataset.model!;
      const panel = document.querySelector(`[data-panel="${CSS.escape(model)}"]`) as HTMLElement | null;
      if (!panel) return;
      const flags: Record<string, string> = {};
      panel.querySelectorAll<HTMLElement>("[data-f]").forEach((i) => {
        const v = i.value.trim();
        if (v) flags[(i.dataset as any).f] = v;
      });
      const r = await api("/v1/control/config", { method: "POST", body: { model, flags } });
      toast(r.ok ? "flags saved" : "failed: " + String(r.data?.error?.message || r.status), 3200, r.ok ? "ok" : "err");
      if (r.ok) cfgCache = null;
    })
  );

  $$("#registry .savePrompt").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const el = btn as HTMLElement;
      const model = el.dataset.model!;
      const panel = document.querySelector(`[data-panel="${CSS.escape(model)}"]`) as HTMLElement | null;
      const ta = panel?.querySelector(".cfgPrompt") as HTMLTextAreaElement | null;
      if (!ta) return;
      const r = await api("/v1/control/prompt", { method: "POST", body: { model, content: ta.value } });
      toast(r.ok ? "prompt saved (injected when request has no system message)" : "failed", 3200, r.ok ? "ok" : "err");
    })
  );
}

/* -------------------------------------------------------------- refresh -- */
function setKpi(sel: string, val: string): void {
  const el = $(sel);
  if (!el || el.textContent === val) return;
  el.textContent = val;
  flash(el);
}

async function refresh(): Promise<void> {
  const [h, stats, status] = await Promise.all([
    api("/health"),
    api("/v1/memory/stats"),
    api("/v1/control/status"),
  ]);

  const liveDot = $("#liveDot");
  if (liveDot) liveDot.classList.toggle("bad", !h.ok);
  const liveTxt = $("#liveTxt");
  if (liveTxt && h.ok) liveTxt.textContent = "loco :8200 live";

  setKpi("#up", Math.floor((h.data?.uptime_s || 0) / 60) + "m");

  const g = h.data?.gpu;
  if (g) {
    setKpi("#gpuUsed", fmtMiB(g.used_mib));
    setKpi("#gpuFree", fmtMiB(g.free_mib));
    setKpi("#gpuUtil", fmtPct(g.util_pct));
    const bar = $("#gpuBar") as HTMLElement | null;
    if (bar && g.total_mib) {
      const pct = ((g.used_mib / g.total_mib) * 100).toFixed(1);
      if (bar.style.width !== pct + "%") bar.style.width = pct + "%";
    }
    sceneGpu({ used_mib: g.used_mib, total_mib: g.total_mib, util_pct: g.util_pct });
  }

  setKpi("#routes", fmtNum(stats.data?.route_log_rows));
  setKpi("#cacheHits", fmtNum(stats.data?.cache_hits));
  setKpi("#notes", fmtNum(stats.data?.notes));
  setKpi("#pinned", fmtNum(stats.data?.pinned));

  if (status.ok) await loadRegistry(status.data);
}

/* ----------------------------------------------------------- WS wiring --- */
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleRefresh(delay = 450): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { refreshTimer = null; refresh(); }, delay);
}

function shortOf(modelId: string | undefined): string {
  if (!modelId) return "—";
  const hit = REGISTRY.find((m) => m.id === modelId);
  return hit ? hit.short : modelId;
}

onWs((msg) => {
  const hudR = $("#hudRoutes");
  const hudE = $("#hudEvents");
  if (hudR) hudR.textContent = fmtNum(Number((hudR.textContent || "0").replace(/,/g, "")) + 1);
  if (hudE) hudE.textContent = fmtNum(wsEventCount);

  if (msg.type === "gpu") {
    setKpi("#gpuUsed", fmtMiB(msg.used_mib));
    setKpi("#gpuFree", fmtMiB(msg.free_mib));
    setKpi("#gpuUtil", fmtPct(msg.util_pct));
    const bar = $("#gpuBar") as HTMLElement | null;
    if (bar && msg.total_mib) bar.style.width = ((msg.used_mib / msg.total_mib) * 100).toFixed(1) + "%";
    sceneGpu(msg);
  } else if (msg.type === "route") {
    const cache = !!msg.cache_hit;
    sceneRoute(msg);
    ecgPush(msg);
    tickerPush(cache ? "route" : "route",
      `${msg.task || "?"} → ${shortOf(msg.model)}${cache ? " · cache hit" : ` · ${msg.latency_ms ?? "?"} ms`}`);
  } else if (msg.type === "memory") {
    sceneMemory(msg);
    tickerPush("memory", `${msg.action || "?"}${msg.id ? " · " + String(msg.id).slice(0, 8) : ""}${msg.count ? " · ×" + msg.count : ""}`);
    scheduleRefresh();
  } else if (msg.type === "control") {
    sceneControl(msg);
    tickerPush("control", `${msg.action} · ${shortOf(msg.model)}`);
    toast(`${msg.action} · ${shortOf(msg.model)}`);
    scheduleRefresh(200);
  }
});

/* ----------------------------------------------------------------- boot -- */
document.addEventListener("DOMContentLoaded", () => {
  sceneBoot();
  refresh();
  setInterval(refresh, 30000);   // safety net; WS is the primary feed
});
