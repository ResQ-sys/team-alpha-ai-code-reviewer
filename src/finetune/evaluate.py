"""
Rate a Code-LLM's secure-coding recommendations, and compare the base model
against the LoRA fine-tuned adapter.

The "rating" is a simple, transparent 0-100 score: the token-overlap F1 between
the model's generated recommendation and the reference secure-coding answer,
averaged over the eval set. It is a lightweight proxy (not a substitute for a
full DeepEval/Ragas harness) but is enough to *rate* and compare models.

Examples:
    # rate the base model only
    python finetune/evaluate.py

    # rate base vs LoRA-fine-tuned adapter
    python finetune/evaluate.py --adapter finetune/adapter
"""
from __future__ import annotations

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _tokens(text: str):
    return re.findall(r"[a-z0-9_]+", text.lower())


def overlap_f1(pred: str, ref: str) -> float:
    """Token-overlap F1 in [0, 1]."""
    p, r = _tokens(pred), _tokens(ref)
    if not p or not r:
        return 0.0
    ps, rs = set(p), set(r)
    common = ps & rs
    if not common:
        return 0.0
    precision = len(common) / len(ps)
    recall = len(common) / len(rs)
    return 2 * precision * recall / (precision + recall)


def build_model(base_model: str, adapter: str | None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(adapter or base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(base_model, torch_dtype=torch.float32)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return model, tokenizer


def generate(model, tokenizer, row, max_new_tokens=200) -> str:
    messages = [{"role": "system", "content": row.get("system", "")}]
    user = row["instruction"] + (("\n\n" + row["input"]) if row.get("input") else "")
    messages.append({"role": "user", "content": user})
    prompt = tokenizer.apply_chat_template(messages, tokenize=False,
                                           add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt")
    import torch
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens,
                             do_sample=False, pad_token_id=tokenizer.pad_token_id)
    text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:],
                            skip_special_tokens=True)
    return text.strip()


def rate(base_model: str, adapter: str | None, eval_file: str, label: str):
    model, tokenizer = build_model(base_model, adapter)
    rows = load_jsonl(eval_file)
    scores = []
    for r in rows:
        pred = generate(model, tokenizer, r)
        scores.append(overlap_f1(pred, r["output"]))
    rating = round(100 * sum(scores) / len(scores), 1)
    print(f"  {label:<22} rating: {rating}/100  ({len(rows)} examples)")
    return rating


def main():
    ap = argparse.ArgumentParser(description="Rate secure-coding recommendations")
    ap.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-0.5B-Instruct")
    ap.add_argument("--adapter", default=None,
                    help="Path to a LoRA adapter to compare against the base model")
    ap.add_argument("--eval-file", default=os.path.join(DATA_DIR, "eval.jsonl"))
    args = ap.parse_args()

    if not os.path.exists(args.eval_file):
        raise SystemExit(f"Missing {args.eval_file}. Run: python finetune/prepare_data.py")

    print("Rating secure-coding recommendation quality (token-overlap F1):")
    base = rate(args.base_model, None, args.eval_file, "base model")

    if args.adapter and os.path.isdir(args.adapter):
        tuned = rate(args.base_model, args.adapter, args.eval_file, "LoRA fine-tuned")
        delta = round(tuned - base, 1)
        arrow = "▲" if delta >= 0 else "▼"
        print(f"\n  LoRA improvement: {arrow} {abs(delta)} points")
    else:
        print("\n  (Pass --adapter finetune/adapter to compare a fine-tuned model.)")


if __name__ == "__main__":
    main()
