# LoRA fine-tuning & rating

Fine-tune a Code-LLM towards **secure-coding recommendations** using
**LoRA** (Low-Rank Adaptation, via HuggingFace PEFT), then **rate** the base
model against the fine-tuned adapter.

This is the "train a dedicated model" track from the problem statement, offered
*alongside* (not instead of) the runtime RAG grounding.

## Pipeline

```
prepare_data.py  →  train_lora.py  →  evaluate.py
   (dataset)         (LoRA adapter)     (rating & comparison)
```

## 1. Build the dataset

```bash
python finetune/prepare_data.py
```
Creates `finetune/data/train.jsonl` and `eval.jsonl` from the RAG knowledge base
(`rag/secure_coding_docs.json`) plus hand-written vulnerable→secure code pairs.

## 2. Fine-tune with LoRA

```bash
# tiny CPU smoke test (proves the pipeline end-to-end in a few steps)
python finetune/train_lora.py --max-steps 5

# a real run (GPU strongly recommended)
python finetune/train_lora.py --epochs 3 \
    --base-model Qwen/Qwen2.5-Coder-1.5B-Instruct
```
Only the low-rank adapter weights are trained (a few % of the model), so the
adapter saved to `finetune/adapter/` is small.

## 3. Rate / compare

```bash
# rate the base model
python finetune/evaluate.py

# rate base vs LoRA fine-tuned
python finetune/evaluate.py --adapter finetune/adapter
```
Prints a 0–100 rating (token-overlap F1 vs reference answers) for each model and
the LoRA improvement.

## Hardware notes

- **GPU recommended.** This machine is CPU-only (`torch.cuda.is_available() == False`),
  so use `--max-steps 5` for a demonstration; a full multi-epoch run on the
  larger base models needs a GPU.
- Base models are downloaded from HuggingFace on first run (the 0.5B default is
  ~1 GB). Set `HF_TOKEN` to avoid rate limits.

## How this relates to the running app

The **app uses Ollama** (`qwen2.5-coder`) at inference time. This folder is the
**offline training/evaluation track**: once you have a fine-tuned adapter you
can merge it and serve it through Ollama (`ollama create`) or a local HF/vLLM
endpoint (`LLM_PROVIDER=local_hf`) to plug it back into the pipeline.
