"""Stage 4 (part 2) — offline QLoRA training run.

This module is intentionally outside the serving path: the running Loco
process never imports it, and it never talks to the endpoint.  It consumes the
deterministic split produced by `train/dataset.py` and writes an adapter
directory plus a provenance manifest that `train/promote.py` later requires.

The GPU stack is imported lazily; without it this module exits with an honest
message instead of a traceback (see `train/deps.py`).  Nothing here downloads
weights or packages — bring your own base model path.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import deps
from .dataset import dataset_hash

DEFAULT_BASE = None  # required: a local HF-format base model, never fetched
DEFAULT_DATA = "data/train.jsonl"
DEFAULT_OUT = "adapters"


def _load_rows(path: str) -> list[dict]:
    rows = []
    source = Path(path)
    if not source.exists():
        raise SystemExit(f"dataset not found: {source} — run `loco-train dataset` first")
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise SystemExit(f"dataset is empty: {source}")
    return rows


def train(base: str, data: str, out: str, epochs: float = 2.0,
          lr: float = 2e-4, batch_size: int = 1, lora_r: int = 16,
          lora_alpha: int = 32, max_seq_len: int = 2048,
          gradient_accumulation: int = 4) -> dict:
    status = deps.status()
    if not status["ready"]:
        raise SystemExit(deps.install_hint(status["missing_required"]))

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              DataCollatorForLanguageModeling, Trainer,
                              TrainingArguments)

    rows = _load_rows(data)
    tokenizer = AutoTokenizer.from_pretrained(base, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def encode(row):
        text = tokenizer.apply_chat_template(
            row["messages"], tokenize=False, add_generation_prompt=False)
        encoded = tokenizer(text, truncation=True, max_length=max_seq_len)
        encoded["labels"] = encoded["input_ids"].copy()
        return encoded

    dataset = Dataset.from_list(rows).map(encode, remove_columns=["messages"])

    quantized = bool(status["present"].get("bitsandbytes"))
    model = AutoModelForCausalLM.from_pretrained(
        base,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        device_map="auto",
        **({"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": torch.bfloat16} if quantized else {}),
    )
    if quantized:
        model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=lora_r, lora_alpha=lora_alpha, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    ))

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    destination = Path(out) / f"adapter-{stamp}"
    arguments = TrainingArguments(
        output_dir=str(destination / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation,
        learning_rate=lr,
        warmup_ratio=0.03,
        logging_steps=10,
        save_strategy="epoch",
        bf16=torch.cuda.is_available(),
        report_to=[],
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=arguments,
        train_dataset=dataset,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )
    result = trainer.train()

    destination.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(destination))
    tokenizer.save_pretrained(str(destination))
    manifest = {
        "base": base,
        "data": str(data),
        "rows": len(rows),
        "dataset_hash": dataset_hash(data),
        "epochs": epochs,
        "lr": lr,
        "batch_size": batch_size,
        "gradient_accumulation": gradient_accumulation,
        "lora_r": lora_r,
        "lora_alpha": lora_alpha,
        "max_seq_len": max_seq_len,
        "quantized_4bit": quantized,
        "train_loss": getattr(result, "training_loss", None),
        "created_at": time.time(),
        "deps": status["present"],
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({"adapter": str(destination), **manifest}, indent=2))
    return {"adapter": str(destination), **manifest}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help="local base model (HF format) — never fetched")
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--max-seq-len", type=int, default=2048)
    args = parser.parse_args(argv)
    if not args.base:
        print("pass --base /path/to/local/hf-format-model "
              "(Loco never downloads weights)")
        return 2
    status = deps.status()
    if not status["ready"]:
        print(deps.install_hint(status["missing_required"]))
        return 2
    train(args.base, args.data, args.out, epochs=args.epochs, lr=args.lr,
          batch_size=args.batch_size, lora_r=args.lora_r,
          lora_alpha=args.lora_alpha, max_seq_len=args.max_seq_len,
          gradient_accumulation=args.grad_accum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
