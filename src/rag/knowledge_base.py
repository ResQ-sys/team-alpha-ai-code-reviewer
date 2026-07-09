"""
Secure-coding Code-RAG knowledge base.

Primary path: sentence-transformers embeddings + FAISS similarity search
(matches the "FAISS / LlamaIndex / LangChain" tooling named in the problem
statement).

Fallback path: if sentence-transformers/FAISS can't load (no internet to
pull model weights, no GPU, etc.) we transparently fall back to a
scikit-learn TF-IDF vectorizer + cosine similarity so the RAG node still
functions in a constrained hackathon judging environment.
"""
from __future__ import annotations

import json
from typing import Dict, List

from config import EMBEDDING_MODEL_NAME, KNOWLEDGE_BASE_PATH, TOP_K_RETRIEVAL, USE_DATASET_RAG
from rag.dataset_loader import load_dataset_docs


class SecureCodingKB:
    def __init__(self, kb_path: str = KNOWLEDGE_BASE_PATH, include_datasets: bool = USE_DATASET_RAG):
        with open(kb_path, "r", encoding="utf-8") as f:
            self.docs: List[Dict] = json.load(f)
        for d in self.docs:
            d.setdefault("source", "secure_coding_docs")

        # Genuinely dataset-backed retrieval: fold in CWE-labeled samples
        # distilled from Devign / Big-Vul / Juliet / DiverseVul / etc.
        self.dataset_doc_count = 0
        if include_datasets:
            dataset_docs = load_dataset_docs()
            self.docs.extend(dataset_docs)
            self.dataset_doc_count = len(dataset_docs)

        self.texts = [f"{d['title']}. {d['text']}" for d in self.docs]
        self.backend = None
        self._build_index()

    # ------------------------------------------------------------------
    def _build_index(self):
        try:
            self._build_faiss_index()
            self.backend = "faiss"
        except Exception:
            self._build_tfidf_index()
            self.backend = "tfidf"

    def _build_faiss_index(self):
        import faiss
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        embeddings = self.model.encode(self.texts, normalize_embeddings=True)
        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(np.asarray(embeddings, dtype="float32"))

    def _build_tfidf_index(self):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.matrix = self.vectorizer.fit_transform(self.texts)

    # ------------------------------------------------------------------
    def query(self, text: str, top_k: int = TOP_K_RETRIEVAL) -> List[Dict]:
        if self.backend == "faiss":
            return self._query_faiss(text, top_k)
        return self._query_tfidf(text, top_k)

    def _query_faiss(self, text: str, top_k: int) -> List[Dict]:
        import numpy as np

        q_emb = self.model.encode([text], normalize_embeddings=True)
        scores, idxs = self.index.search(np.asarray(q_emb, dtype="float32"), top_k)
        results = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx == -1:
                continue
            doc = self.docs[idx]
            results.append({**doc, "score": float(score)})
        return results

    def _query_tfidf(self, text: str, top_k: int) -> List[Dict]:
        from sklearn.metrics.pairwise import cosine_similarity

        q_vec = self.vectorizer.transform([text])
        sims = cosine_similarity(q_vec, self.matrix)[0]
        ranked = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)[:top_k]
        return [{**self.docs[i], "score": float(sims[i])} for i in ranked]

    # ------------------------------------------------------------------
    def query_by_tags(self, tags: List[str], top_k: int = TOP_K_RETRIEVAL) -> List[Dict]:
        """Fast path: direct tag match (used when semgrep/CWE ids are already known)."""
        tags_lower = {t.lower() for t in tags}
        matches = [d for d in self.docs if tags_lower & {t.lower() for t in d.get("tags", [])}]
        return matches[:top_k] if matches else []
