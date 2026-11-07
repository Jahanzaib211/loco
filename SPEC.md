# Loco — the brief (improved)

Original (verbatim, fragmentary):
> "the ui of it every py endpoint with its on html page with full control what is the piline speed which model is doing what what roles they have give them a little why and what style md file for consistent identiy plus tool and mcp wire sperately which the whole purpose of this piline is to support agentic workflow first improve my prompt"
> "how about use websockets for streaming real data for all to make the ui alive"

## Upgraded brief

1. **UI — one page per Python endpoint, full control.** Every Loco route (`/v1/chat/completions`, `/v1/embeddings`, `/v1/compact`, `/v1/memory/*`, `/v1/control/*`, `/health`, `/metrics`) gets its own HTML page in the lab UI, built flat (no frameworks), served by Loco itself at `/ui`. Control means real lifecycle: load · unload · status · roles — never stub buttons.
2. **Live, not polled.** One WebSocket (`/ws`) pushes GPU telemetry (1s cadence), route events, and memory events to every page. Pages paint from WS first; 30s poll is only a safety net. Chat stays SSE (already real-time; WS would add nothing for token streams).
3. **The pipeline, visible.** A doctrine page shows request lifecycle with measured costs, which model does what and why, the honest build-vs-exists status of the design, and the eval loop (shadow logs → tune → never vibes).
4. **Identity library.** Every model gets a why/what/style card — one markdown per model — so agents stay consistent. Linked from the registry and the doctrine page.
5. **Tools & MCP — separate plane, by design.** Loco routes models only and never executes tools. Agents (Eliza, Nova, you) wire tools/MCP on top; that separation is the product: any agent gets any brain, deterministically, without bargaining with an inference stack.
6. **Built for agentic workflows first.** Everything above exists so an agent can plan, act, remember — without a human gluing GPUs together.
7. **Honesty rule.** If a claim isn't in `results/`, it didn't happen.
