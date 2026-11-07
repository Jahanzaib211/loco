#!/bin/sh
# bge-small embedding sidecar for Loco — 67MB model, ~450MiB VRAM, ctx 512
exec /home/jahanzaib/Desktop/'Inference Engine'/llama.cpp/build/bin/llama-server \
  --model /home/jahanzaib/Desktop/Loco/models/bge-small-en-v1.5-f16.gguf \
  --host 127.0.0.1 --port 8210 -c 512 --embedding --pooling mean -ngl 99 --no-warmup
