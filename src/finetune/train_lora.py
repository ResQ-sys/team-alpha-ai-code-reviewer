"""
LoRA fine-tuning of a Code-LLM for secure-coding recommendations.

Uses HuggingFace Transformers + PEFT (Low-Rank Adaptation). Only a small set
of adapter weights is trained, so it runs on modest hardware; a GPU is strongly
recommended for anything beyond a smoke test.

Base model defaults to a small Qwen2.5-Coder so it aligns with the runtime
model (Ollama `qwen2.5-coder`) while staying trainable on CPU for a demo.

Examples:
    # tiny CPU smoke run (a few steps, proves the pipeline end-to-end)
    python finetune/train_lora.py --max-steps 5

    # a real run on GPU
    python finetune/train_lora.py --epochs 3 \
        --base-model Qwen/Qwen2.5-Coder-1.5B-Instruct

Output: a LoRA adapter under finetune/adapter/
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DEFAULT_OUT = os.path.join(HERE, "adapter")


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser(description="LoRA fine-tune a Code-LLM for secure coding")
    ap.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-0.5B-Instruct")
    ap.add_argument("--train-file", default=os.path.join(DATA_DIR, "train.jsonl"))
    ap.add_argument("--output", default=DEFAULT_OUT)
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--max-steps", type=int, default=-1,
                    help="Cap total steps (use a small value like 5 for a CPU smoke test)")
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    if not os.path.exists(args.train_file):
        raise SystemExit(f"Missing {args.train_file}. Run: python finetune/prepare_data.py")

    print(f"Loading base model: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, torch_dtype=torch.float32
    )

    # LoRA: train only small low-rank adapters on the attention/MLP projections.
    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    rows = load_jsonl(args.train_file)

    def format_row(r):
        messages = [{"role": "system", "content": r.get("system", "")}]
        user = r["instruction"] + (("\n\n" + r["input"]) if r.get("input") else "")
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": r["output"]})
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        return {"text": text}

    ds = Dataset.from_list([format_row(r) for r in rows])

    def tokenize(batch):
        out = tokenizer(batch["text"], truncation=True, max_length=args.max_len,
                        padding="max_length")
        return out

    ds = ds.map(tokenize, batched=True, remove_columns=["text"])
    collator = DataCollatorForLanguageModeling(tokenizer, mlm=False)

    targs = TrainingArguments(
        output_dir=os.path.join(args.output, "_checkpoints"),
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=2,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        logging_steps=1,
        save_strategy="no",
        report_to=[],
    )

    trainer = Trainer(model=model, args=targs, train_dataset=ds, data_collator=collator)
    print("Starting LoRA training...")
    trainer.train()

    os.makedirs(args.output, exist_ok=True)
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"\nSaved LoRA adapter to: {args.output}")
    print("Evaluate/compare with:  python finetune/evaluate.py --adapter finetune/adapter")


if __name__ == "__main__":
    main()
