#!/usr/bin/env python3
"""Loco bench - per-model llama.cpp/ollama benchmark with VRAM tracking.

Sequential, one model at a time. Results appended to results/bench.csv.
Logs per test in logs/<model>-ctx<ctx>[-q8kv].log
"""
import json, os, signal, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path('/home/jahanzaib/Desktop/Loco')
LOGS = ROOT / 'logs'
RESULTS = ROOT / 'results' / 'bench.csv'
MODELS_DIR = Path('/home/jahanzaib/models')
SERVER = str(Path.home() / 'Desktop/Inference Engine/llama.cpp/build/bin/llama-server')

PROMPT = ("You are a concise, precise assistant. Summarize the following text in one short paragraph: " +
          "The quick brown fox jumps over the lazy dog. " * 60)  # ~450-500 tokens

# (file, list of ctx sizes)
LLAMA_TESTS = [
    ('lfm3-1.7b.gguf',            [8192, 32768, 65536, 131072]),
    ('phi4-mini-3.8b.gguf',       [8192, 32768, 65536]),
    ('bonsai-2-9b.gguf',          [8192, 32768, 65536]),
    ('ornith-1.5-9b.gguf',        [8192, 32768, 65536]),
    ('nemotron-3.5-bolt-e4b.gguf',[8192, 32768]),
    ('deepseek-r1-14b.gguf',      [8192, 16384, 32768]),
]
OLLAMA_TESTS = [
    ('huihui_ai/gemma-4-abliterated:e4b', [8192, 32768, 65536, 131072]),
]

def nvidia_used_mib():
    out = subprocess.check_output(
        ['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
        text=True).strip()
    return int(out.splitlines()[0])

def wait_gpu_free(max_used_mib=1500, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if nvidia_used_mib() <= max_used_mib:
            return True
        time.sleep(3)
    return False

def wait_health(port, proc, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1.5)
    return False

def post(port, path, payload, timeout=180):
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body), time.time() - t0

def chat_probe(port, model):
    try:
        resp, wall = post(port, '/v1/chat/completions',
                          {'model': model,
                           'messages': [{'role': 'user', 'content': PROMPT}],
                           'max_tokens': 32, 'temperature': 0})
        t = resp.get('timings', {})
        u = resp.get('usage', {})
        pp = t.get('prompt_per_second') or t.get('prompt_per_token_ms')
        tg = t.get('predicted_per_second') or t.get('predicted_per_token_ms')
        return {'wall_s': round(wall, 2),
                'prompt_toks': u.get('prompt_tokens'),
                'gen_toks': u.get('completion_tokens'),
                'prompt_tps': round(t.get('prompt_per_second') or 0, 1),
                'gen_tps': round(t.get('predicted_per_second') or 0, 1),
                'timings_raw': json.dumps(t),
                'finish': resp['choices'][0].get('finish_reason')}
    except Exception as e:
        return {'error': str(e)[:200]}

def embed_probe(port, model):
    try:
        resp, _ = post(port, '/v1/embeddings', {'model': model, 'input': 'hello world'})
        data = resp.get('data', [])
        return {'embed_ok': 'yes', 'embed_dims': len(data[0]['embedding']) if data else None}
    except Exception as e:
        return {'embed_ok': 'no', 'embed_dims': '', 'embed_err': str(e)[:120]}

def log(fh, line):
    print(line, flush=True)
    fh.write(line + '\n'); fh.flush()

def run_llama(model_file, ctx, port, kv8=False):
    name = model_file.replace('.gguf', '')
    logf = open(LOGS / f'{name}-ctx{ctx}{"-q8kv" if kv8 else ""}.log', 'w')
    used_before = nvidia_used_mib()
    wait_gpu_free()
    used_before = nvidia_used_mib()
    args = [SERVER, '--model', str(MODELS_DIR / model_file), '--host', '127.0.0.1',
            '--port', str(port), '-ngl', '99', '--ctx-size', str(ctx), '--embedding']
    if kv8:
        args += ['--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0']
    t0 = time.time()
    proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)
    ok = wait_health(port, proc)
    load_s = round(time.time() - t0, 1)
    used_after = nvidia_used_mib()
    row = {'model': name, 'backend': 'llama-cpp', 'ctx': ctx, 'kv_cache': 'q8_0' if kv8 else 'f16',
           'load_ok': 'yes' if ok else 'no', 'gpu_before_mib': used_before,
           'gpu_after_mib': used_after, 'gpu_delta_mib': used_after - used_before, 'load_s': load_s}
    if ok:
        row.update(chat_probe(port, name))
        row.update(embed_probe(port, name))
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(3)
    row['gpu_after_unload_mib'] = nvidia_used_mib()
    logf.close()
    return row

def ollama_generate(model, ctx, num_ctx):
    payload = {'model': model, 'prompt': PROMPT, 'stream': False,
               'options': {'num_ctx': num_ctx, 'temperature': 0, 'num_predict': 32}}
    req = urllib.request.Request('http://127.0.0.1:11434/api/generate',
                                 data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode())
    wall = time.time() - t0
    out = {'model': model, 'backend': 'ollama', 'ctx': num_ctx, 'kv_cache': 'ollama-default',
           'load_ok': 'yes', 'gpu_before_mib': '', 'gpu_after_mib': '', 'gpu_delta_mib': '',
           'load_s': wall,
           'prompt_toks': resp.get('prompt_eval_count'),
           'gen_toks': resp.get('eval_count'),
           'prompt_tps': round(resp.get('prompt_eval_count', 0) / (resp.get('prompt_eval_duration', 1) / 1e9), 1) if resp.get('prompt_eval_duration') else '',
           'gen_tps': round(resp.get('eval_count', 0) / (resp.get('eval_duration', 1) / 1e9), 1) if resp.get('eval_duration') else '',
           'finish': resp.get('done_reason')}
    return out

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--kv8':
        KV8_TESTS = [('deepseek-r1-14b.gguf', [16384, 32768]), ('phi4-mini-3.8b.gguf', [65536])]
        import csv as _csv
        fieldnames = ['model','backend','ctx','kv_cache','load_ok','gpu_before_mib','gpu_after_mib',
                      'gpu_delta_mib','load_s','prompt_toks','gen_toks','prompt_tps','gen_tps',
                      'wall_s','finish','embed_ok','embed_dims','embed_err','timings_raw','error',
                      'gpu_after_unload_mib']
        with open(RESULTS, 'a', newline='') as csvfile:
            w = _csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            port = 8430
            for model_file, ctxs in KV8_TESTS:
                for ctx in ctxs:
                    print(f'--- llama.cpp {model_file} ctx={ctx} q8_0 KV port={port}', flush=True)
                    row = run_llama(model_file, ctx, port, kv8=True)
                    w.writerow(row); csvfile.flush()
                    port += 1
        print('KV8 TESTS DONE')
        return
    only = sys.argv[1] if len(sys.argv) > 1 else None
    LOGS.mkdir(parents=True, exist_ok=True)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ['model','backend','ctx','kv_cache','load_ok','gpu_before_mib','gpu_after_mib',
                  'gpu_delta_mib','load_s','prompt_toks','gen_toks','prompt_tps','gen_tps',
                  'wall_s','finish','embed_ok','embed_dims','embed_err','timings_raw','error',
                  'gpu_after_unload_mib']
    new_file = not RESULTS.exists()
    import csv
    with open(RESULTS, 'a', newline='') as csvfile:
        w = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        if new_file:
            w.writeheader()
        port = 8410
        for model_file, ctxs in LLAMA_TESTS:
            if only and only not in model_file:
                continue
            for ctx in ctxs:
                print(f'--- llama.cpp {model_file} ctx={ctx} port={port}', flush=True)
                row = run_llama(model_file, ctx, port)
                w.writerow(row); csvfile.flush()
                port += 1
        for model, ctxs in OLLAMA_TESTS:
            if only and only not in model:
                continue
            for ctx in ctxs:
                print(f'--- ollama {model} ctx={ctx}', flush=True)
                try:
                    row = ollama_generate(model, ctx, ctx)
                except Exception as e:
                    row = {'model': model, 'backend': 'ollama', 'ctx': ctx, 'load_ok': 'no', 'error': str(e)[:200]}
                w.writerow(row); csvfile.flush()
    print('BENCH DONE')

if __name__ == '__main__':
    main()
