"""
Dataset loader for the secure-coding RAG corpus.

Converts entries from the named vulnerability datasets (Devign, Big-Vul,
CodeXGLUE, Juliet, DiverseVul, ManySStuBs4J, SAP Project-KB) into RAG documents
so retrieval is genuinely *dataset-backed*, not just grounded in the small
hand-written knowledge base.

Two sources, tried in order:
  1. A bundled offline sample (`rag/datasets/vuln_samples.jsonl`) — CWE-labeled
     vulnerable/fixed patterns distilled from those datasets. Always available.
  2. (Optional) the real datasets from HuggingFace `datasets`, if installed and
     reachable — see `load_hf_dataset_docs`. Off by default so the pipeline
     stays offline-friendly.

Each produced doc matches the knowledge-base schema:
    {"title", "text", "tags", "source"}
so it can be indexed alongside `secure_coding_docs.json` transparently.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS_DIR = os.path.join(HERE, "datasets")
SAMPLES_PATH = os.path.join(DATASETS_DIR, "vuln_samples.jsonl")
HF_CACHE_PATH = os.path.join(DATASETS_DIR, "hf_cache.jsonl")


def _row_to_doc(row: Dict) -> Dict:
    dataset = row.get("dataset", "dataset")
    cwe = row.get("cwe", "")
    title = f"[{dataset}] {row.get('title', cwe)}"
    text_parts = [row.get("guidance", "")]
    if row.get("vulnerable"):
        text_parts.append(f"Vulnerable pattern ({row.get('language','')}): {row['vulnerable']}")
    tags = list(row.get("tags", []))
    if cwe and cwe.lower() not in {t.lower() for t in tags}:
        tags.append(cwe.lower())
    tags.append(f"dataset:{dataset.lower()}")
    return {
        "title": title,
        "text": " ".join(p for p in text_parts if p).strip(),
        "tags": tags,
        "source": dataset,
    }


def _read_rows(path: str) -> List[Dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_sample_docs(datasets_dir: str = DATASETS_DIR) -> List[Dict]:
    """Load every bundled/cached dataset file (`rag/datasets/*.jsonl`) as RAG
    docs. This automatically includes anything written by `fetch_datasets.py`
    (e.g. `hf_cache.jsonl` streamed from real HuggingFace datasets)."""
    docs = []
    for path in sorted(glob.glob(os.path.join(datasets_dir, "*.jsonl"))):
        for row in _read_rows(path):
            docs.append(_row_to_doc(row))
    return docs


def load_hf_dataset_docs(name: str, split: str = "train", limit: int = 200) -> List[Dict]:
    """Optional: pull a real dataset from HuggingFace `datasets` and adapt it.

    Best-effort and off the default path (needs the `datasets` package + network).
    Returns [] on any failure so callers never hard-depend on it.
    """
    try:
        from datasets import load_dataset  # noqa: PLC0415
    except Exception:
        return []
    try:
        ds = load_dataset(name, split=split, streaming=True)
    except Exception:
        return []

    docs: List[Dict] = []
    for i, row in enumerate(ds):
        if i >= limit:
            break
        # Devign/DiverseVul-style rows expose func + target(1=vulnerable).
        if int(row.get("target", row.get("label", 0)) or 0) != 1:
            continue
        func = row.get("func") or row.get("function") or row.get("code") or ""
        if not func:
            continue
        docs.append(_row_to_doc({
            "dataset": name.split("/")[-1],
            "cwe": row.get("cwe", ""),
            "language": row.get("language", ""),
            "title": "Vulnerable function sample",
            "vulnerable": func[:400],
            "guidance": "Labeled vulnerable in the source dataset; review against the "
                        "matching CWE remediation before shipping.",
            "tags": [t for t in [row.get("cwe", "")] if t],
        }))
    return docs


def load_dataset_docs(include_hf: bool = False, hf_specs: List[Dict] | None = None) -> List[Dict]:
    """Main entry point: bundled samples, plus optional HF datasets."""
    docs = load_sample_docs()
    if include_hf:
        for spec in (hf_specs or []):
            docs.extend(load_hf_dataset_docs(**spec))
    return docs


if __name__ == "__main__":
    d = load_dataset_docs()
    print(f"Loaded {len(d)} dataset-backed RAG docs")
    for doc in d[:3]:
        print(" -", doc["title"], "| tags:", doc["tags"])
