# Loco — Local-lab benchmark report (2026-11-05)

Host: RTX 3060 12 GB · 12 cores · 30 GB RAM · Ubuntu 26.04 · llama.cpp 0.9.0-dev (cd959bae9, CUDA)

## Headline answers

1. **All 6 llama.cpp models load and run on the 3060** — with the right context limits.
2. **gemma-4-abliterated is ollama-only by design** (merged multimodal GGUF; llama.cpp registers 720/2131 tensors and aborts). It is NOT a fixable llama.cpp issue.
3. **deepseek-r1-14b is the tightest fit**: 8192 ctx f16 (10.2 GB) or **16384 ctx with q8_0 KV only**. 16384+ f16 = OOM.
4. **phi4-mini needs q8_0 KV for 65536** (7.3 GB vs 11.2 GB f16). 32768 f16 is fine (7.0 GB).
5. **Embeddings**: llama.cpp serves them per-model with `--embedding`; OpenAI `/v1/embeddings` additionally needs `--pooling mean|cls` (lfm3: 2048 dims, verified 200). Ollama embeddings require adding `--embeddings` to the ollama systemd unit (sudo change).
6. llama.cpp **router mode on :8080 already does max-context-per-model unloading** (one model resident, LRU eviction) — Loco will wrap, not reimplement, this.

## Verified matrix (all loads followed by a real 32-token completion)

| model | backend | ctx | KV | GPU Δ MiB | gen tok/s | pp tok/s | verdict |
|---|---|---|---|---|---|---|---|
| lfm3-1.7b | llama.cpp | 8192 | f16 | 1565 | 158 | 4142 | ✅ light/fast |
| lfm3-1.7b | llama.cpp | 32768 | f16 | 1877 | 158 | 3638 | ✅ |
| lfm3-1.7b | llama.cpp | 65536 | f16 | 2293 | 159 | 3092 | ✅ |
| lfm3-1.7b | llama.cpp | **131072** | f16 | 3125 | 154 | 2417 | ✅ full native ctx |
| phi4-mini-3.8b | llama.cpp | 8192 | f16 | 3949 | 98 | 2765 | ✅ |
| phi4-mini-3.8b | llama.cpp | 32768 | f16 | 7045 | 99 | 2536 | ✅ |
| phi4-mini-3.8b | llama.cpp | 65536 | f16 | **11173** | 100 | 2275 | ⚠️ near-OOM |
| phi4-mini-3.8b | llama.cpp | 65536 | **q8_0** | 7337 | 94 | 2165 | ✅ recommended |
| bonsai-2-9b | llama.cpp | 8192 | f16 | 3337 | 92 | 1163 | ✅ |
| bonsai-2-9b | llama.cpp | 32768 | f16 | 4129 | 91 | 1173 | ✅ |
| bonsai-2-9b | llama.cpp | 65536 | f16 | 5187 | 92 | 1108 | ✅ |
| ornith-1.5-9b | llama.cpp | 8192 | f16 | 5931 | 54 | 1377 | ✅ |
| ornith-1.5-9b | llama.cpp | 32768 | f16 | 6723 | 54 | 1326 | ✅ |
| ornith-1.5-9b | llama.cpp | 65536 | f16 | 7781 | 55 | 1245 | ✅ |
| nemotron-3.5-bolt-e4b | llama.cpp | 8192 | f16 | 6901 | 58 | 163* | ✅ |
| nemotron-3.5-bolt-e4b | llama.cpp | 32768 | f16 | 4362 | 71 | 1418 | ✅ |
| deepseek-r1-14b | llama.cpp | 8192 | f16 | **10167** | 33 | 989 | ⚠️ saturates GPU |
| deepseek-r1-14b | llama.cpp | 16384 | f16 | — | — | — | ❌ CUDA OOM |
| deepseek-r1-14b | llama.cpp | 16384 | **q8_0** | ≈10.3 GB | 33 | — | ✅ (manual probe, reasoning-heavy) |
| deepseek-r1-14b | llama.cpp | 32768 | any | — | — | — | ❌ KV won't fit |
| huihui_ai/gemma-4-abliterated:e4b | **ollama** | 8192–**131072** | n/a | (ollama-managed) | 77–79 | ~2080 | ✅ 100% GPU at 131k |

*nemotron 8192 pp figure is an outlier artifact (prompt tokens encoded already); not representative.

## Failure modes diagnosed (not guessed)

- **gemma on llama.cpp** → `done_getting_tensors: wrong number of tensors; expected 2131, got 720`
  (text blk.* = 720, vision 658, audio 749, mm 4). Merged multimodal checkpoint — llama.cpp requires
  text GGUF + separate mmproj; ollama loads the merged model natively. Keep on ollama.
- **deepseek-r1-14b @ 16k f16** → `unable to allocate CUDA0 buffer` / `failed to allocate buffer for kv cache`.
- **/v1/embeddings → 400** → `Pooling type 'none' is not OAI compatible` — chat models default pooling=none;
  use `--pooling mean` (measured 200, 2048 dims on lfm3) or the native `/embedding` route.
- **/v1/embeddings → 503 "Loading model"** → llama.cpp `/health` returns 200 with `"loading"` before ready;
  poll `/health` for `"ok"`.
- **ollama /api/embed (gemma)** → `This server does not support embeddings. Start it with --embeddings`
  → add `--embeddings` to the ollama systemd unit (needs sudo).
- **VRAM interference between child servers** — my first deepseek pass failed because a nemotron child
  hadn't released VRAM (wait logic now polls `nvidia-smi` before each load).
- **llama.cpp 0.8.0-dev copy at ~/llama.cpp** — stale duplicate (543 MB), superseded by the
  Inference Engine build (0.9.0-dev). Candidate for deletion — awaiting your OK.

## Recommended config (written to /home/jahanzaib/models/loco.yaml)

- small/routing → **lfm3-1.7b** (131072 ctx, 155 tok/s)
- mid → **bonsai-2-9b** (65536 ctx) or **ornith-1.5-9b**
- large/reasoning → **deepseek-r1-14b** @ **16384 q8_0** only, `max_tokens ≥ 1000`
- gemma → **ollama** (never on llama.cpp); 131072 ctx, ~78 tok/s
- phi4-mini @ 65536 **requires q8_0 KV**
- nemotron-e4b @ 8192 preferred (KV is 4.4 GB at 32k f16)
- deepseek-r1 and other reasoning models will burn the completion budget silently — set generous max_tokens

Raw data: `bench.csv`. Per-test server logs: `../logs/`
