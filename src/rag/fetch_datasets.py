"""
Fetch real vulnerability datasets from HuggingFace and cache them into the
RAG corpus.

Streams rows from the named datasets (so it does not download multi-GB files
in full), keeps the vulnerable-labeled functions, and writes them as raw rows
to `rag/datasets/hf_cache.jsonl`. `dataset_loader.load_sample_docs()` then
picks that file up automatically, so retrieval becomes backed by the *real*
datasets after one fetch — not just the bundled distilled samples.

Run:
    python rag/fetch_datasets.py                       # default: Devign (CodeXGLUE)
    python rag/fetch_datasets.py --limit 200
    python rag/fetch_datasets.py --dataset google/code_x_glue_cc_defect_detection

Needs the `datasets` package and network. Set HF_TOKEN to avoid rate limits.
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "datasets", "hf_cache.jsonl")

# Datasets whose rows expose a function + a vulnerable/defect label.
_DEFAULTS = [
    # CodeXGLUE defect detection == Devign (C functions; target==1 is vulnerable)
    {"name": "google/code_x_glue_cc_defect_detection", "dataset": "Devign", "language": "c"},
]


def _row_is_vulnerable(row: dict) -> bool:
    return int(row.get("target", row.get("label", 0)) or 0) == 1


def _func_of(row: dict) -> str:
    return row.get("func") or row.get("function") or row.get("code") or ""


def fetch(specs, limit: int, out_path: str = CACHE_PATH) -> int:
    from datasets import load_dataset

    written = 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out:
        for spec in specs:
            name = spec["name"]
            print(f"Streaming {name} …")
            try:
                ds = load_dataset(name, split="train", streaming=True)
            except Exception as e:  # noqa: BLE001
                print(f"  skipped ({type(e).__name__}: {e})")
                continue
            kept = 0
            for row in ds:
                if kept >= limit:
                    break
                if not _row_is_vulnerable(row):
                    continue
                func = _func_of(row)
                if not func:
                    continue
                raw = {
                    "dataset": spec.get("dataset", name.split("/")[-1]),
                    "cwe": row.get("cwe", ""),
                    "language": spec.get("language", row.get("language", "")),
                    "title": "Vulnerable function (real dataset sample)",
                    "vulnerable": func[:400],
                    "guidance": "Labeled vulnerable in the source dataset. Review "
                                "against the matching CWE remediation before shipping.",
                    "tags": [t for t in [row.get("cwe", "")] if t],
                }
                out.write(json.dumps(raw) + "\n")
                kept += 1
                written += 1
            print(f"  cached {kept} vulnerable samples")
    print(f"\nWrote {written} rows to {out_path}")
    print("They are now included in RAG automatically via dataset_loader.load_sample_docs().")
    return written


def main():
    ap = argparse.ArgumentParser(description="Fetch & cache real vuln datasets for RAG")
    ap.add_argument("--dataset", help="HuggingFace dataset id (defaults to Devign/CodeXGLUE)")
    ap.add_argument("--limit", type=int, default=100, help="Max vulnerable rows per dataset")
    args = ap.parse_args()

    specs = ([{"name": args.dataset, "dataset": args.dataset.split("/")[-1]}]
             if args.dataset else _DEFAULTS)
    fetch(specs, args.limit)


if __name__ == "__main__":
    main()
