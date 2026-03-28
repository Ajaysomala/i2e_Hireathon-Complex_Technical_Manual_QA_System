# backend/retriever.py
# v3.2 FINAL — Multi-hop QA + Diagram Boost
#
# NEW: Pass 0 — Diagram boost query
#   Before normal retrieval, checks if query references a known visual concept
#   (Vee Model, SE Engine, lifecycle diagram, etc.) whose PDF page has sparse text.
#   If matched, injects a targeted boost query using surrounding narrative text
#   that IS indexed — bridging the gap between the visual diagram and the FAISS index.
#
# Pass 0 → diagram boost (only if visual concept detected)
# Pass 1 → normal FAISS search with acronym expansion + deduplication
# Pass 2 → cross-reference hop (follows "see Section X.X" links in retrieved text)
# Final  → merge all passes, deduplicate, return TOP_K

import os
import re
import pickle
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from backend.utils import expand_acronyms, get_diagram_boost_query

# ── CONFIG ────────────────────────────────────────────────────────
INDEX_DIR       = "faiss_index"
INDEX_FILE      = os.path.join(INDEX_DIR, "index.faiss")
CHUNKS_FILE     = os.path.join(INDEX_DIR, "chunks.pkl")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
TOP_K           = 5     # final chunks returned to LLM
FETCH_K         = 15    # raw FAISS pool before deduplication

# ── CROSS-REFERENCE PATTERNS ──────────────────────────────────────
# Matches: "Section 6.6", "Chapter 4", "Table G-6", "Appendix G", "Figure 2.1-1"
XREF_PATTERNS = [
    r'Section\s+(\d+[\.\d]*)',
    r'Chapter\s+(\d+)',
    r'Table\s+([A-Z]?[\d]+-[\d]+)',
    r'Appendix\s+([A-Z])',
    r'Figure\s+(\d+[\.\d-]*)',
    r'Sec\.\s+(\d+[\.\d]*)',
]


def extract_cross_references(text: str) -> list[str]:
    """
    Scan chunk text for cross-references to other sections.
    Returns list of reference strings to use as hop-2 queries.

    "criteria defined in Table G-6"     → ["Table G-6"]
    "see Section 6.6 for risk details"  → ["Section 6.6"]
    "refer to Appendix G"               → ["Appendix G"]
    """
    refs = []
    for pattern in XREF_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            refs.append(match.group(0))

    seen, unique = set(), []
    for r in refs:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


class Retriever:
    """
    Singleton retriever with 3-pass retrieval strategy.

    Pass 0 (NEW) — Diagram boost
      Detects visual concept queries (Vee Model, SE Engine, lifecycle diagrams)
      and injects a narrative-text boost query to find surrounding section chunks.
      WHY: These are full-page diagrams in the PDF — pypdf extracts near-zero text
      from the diagram page itself. The boost uses indexed narrative text instead.

    Pass 1 — Direct FAISS search
      embed expanded query → FAISS fetch-15 → deduplicate by section → top 5

    Pass 2 — Cross-reference hop
      Scan pass-1 chunks for "see Section X.X" → re-query → merge

    Final — merge all, re-deduplicate, return TOP_K
    """

    def __init__(self):
        self.index     = None
        self.chunks    = []
        self.model     = None
        self.is_loaded = False

    def load(self):
        if not os.path.exists(INDEX_FILE):
            raise FileNotFoundError(
                f"FAISS index not found at {INDEX_FILE}. "
                "Run: python -m backend.ingestor first."
            )
        if not os.path.exists(CHUNKS_FILE):
            raise FileNotFoundError(
                f"Chunks file not found at {CHUNKS_FILE}. "
                "Run: python -m backend.ingestor first."
            )

        print("[RETRIEVER] Loading FAISS index...")
        self.index = faiss.read_index(INDEX_FILE)

        print("[RETRIEVER] Loading chunks metadata...")
        with open(CHUNKS_FILE, "rb") as f:
            self.chunks = pickle.load(f)

        print(f"[RETRIEVER] Loading embedding model: {EMBEDDING_MODEL}")
        self.model = SentenceTransformer(EMBEDDING_MODEL)

        self.is_loaded = True
        print(f"[RETRIEVER] Ready. {self.index.ntotal} vectors loaded.")

    # ── INTERNAL: raw FAISS search ────────────────────────────────
    def _search_raw(self, query: str, k: int = FETCH_K) -> list[dict]:
        """
        Embed query (with acronym expansion) and return top-k raw results.
        Does NOT deduplicate — caller handles dedup.
        """
        expanded = expand_acronyms(query)
        vec = self.model.encode(
            [expanded], convert_to_numpy=True
        ).astype(np.float32)
        faiss.normalize_L2(vec)

        k = min(k, self.index.ntotal)
        scores, indices = self.index.search(vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk = self.chunks[idx].copy()
            chunk["relevance_score"] = float(score)
            results.append(chunk)
        return results

    # ── INTERNAL: deduplicate by section_title ────────────────────
    def _deduplicate(self, chunks: list[dict], limit: int) -> list[dict]:
        """
        Keep only highest-scoring chunk per unique section_title.
        Input must be pre-sorted by relevance_score descending.
        """
        seen, unique = set(), []
        for chunk in chunks:
            key = chunk.get("section_title", "").strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(chunk)
            if len(unique) >= limit:
                break
        return unique

    # ── PUBLIC: full multi-pass search ───────────────────────────
    def search(self, question: str, top_k: int = TOP_K) -> list[dict]:
        """
        3-pass retrieval: diagram boost → direct search → cross-ref hop.

        Pass 0 (diagram boost): If the question references a known visual
          concept (Vee Model, SE Engine, lifecycle diagram), inject a boost
          query built from surrounding narrative text that IS in the FAISS index.
          This fixes the core failure mode where diagram pages have sparse
          extracted text and return irrelevant results.

        Pass 1: Standard FAISS search with acronym expansion.

        Pass 2: Follow cross-references found in pass-1 chunks.
        """
        if not self.is_loaded:
            raise RuntimeError("Retriever not loaded. Call load() first.")

        all_chunks = []

        # ── PASS 0: Diagram boost ─────────────────────────────────
        # WHY: Visual concepts (Vee Model = Figure 2.1-1) exist as full-page
        # diagrams in the NASA PDF. pypdf extracts near-zero text from those
        # pages, so no chunk matches the query "What is the Vee Model?".
        # We inject a targeted narrative boost query using words from the
        # surrounding section text that WAS successfully indexed.
        boost_query = get_diagram_boost_query(question)
        if boost_query:
            print(f"[RETRIEVER] Pass 0 (diagram boost): '{boost_query[:60]}...'")
            boost_raw = self._search_raw(boost_query, k=FETCH_K)
            boost_sorted = sorted(boost_raw,
                                  key=lambda x: x["relevance_score"], reverse=True)
            boost_chunks = self._deduplicate(boost_sorted, limit=3)  # take top 3
            for c in boost_chunks:
                c["hop"] = 1  # treat as direct match
                # Slight score boost so these rank above tangential pass-1 results
                c["relevance_score"] = min(c["relevance_score"] * 1.10, 1.0)
            all_chunks.extend(boost_chunks)
            print(f"[RETRIEVER] Pass 0: {len(boost_chunks)} diagram-aware chunks")

        # ── PASS 1: Direct FAISS search ───────────────────────────
        pass1_raw    = self._search_raw(question, k=FETCH_K)
        pass1_sorted = sorted(pass1_raw,
                              key=lambda x: x["relevance_score"], reverse=True)
        pass1_chunks = self._deduplicate(pass1_sorted, limit=top_k)

        print(f"[RETRIEVER] Pass 1: {len(pass1_chunks)} unique sections")
        for c in pass1_chunks:
            c["hop"] = 1
        all_chunks.extend(pass1_chunks)

        # ── PASS 2: Cross-reference hop ───────────────────────────
        all_xrefs = []
        for chunk in pass1_chunks:
            all_xrefs.extend(extract_cross_references(chunk.get("text", "")))

        seen_x, unique_xrefs = set(), []
        for x in all_xrefs:
            if x.lower() not in seen_x:
                seen_x.add(x.lower())
                unique_xrefs.append(x)

        hop2_chunks = []
        if unique_xrefs:
            print(f"[RETRIEVER] Pass 2: {len(unique_xrefs)} cross-refs → {unique_xrefs[:3]}")
            for xref in unique_xrefs[:3]:
                hop_query = f"{xref} {question}"
                hop_raw   = self._search_raw(hop_query, k=5)
                for c in hop_raw:
                    c["hop"] = 2
                    c["relevance_score"] *= 0.85
                hop2_chunks.extend(hop_raw)
                print(f"[RETRIEVER]   ↳ '{xref}' → {len(hop_raw)} candidates")
        else:
            print("[RETRIEVER] Pass 2: no cross-references detected")

        all_chunks.extend(hop2_chunks)

        # ── MERGE + FINAL DEDUP ───────────────────────────────────
        merged_sorted = sorted(all_chunks,
                               key=lambda x: x["relevance_score"], reverse=True)
        final = self._deduplicate(merged_sorted, limit=top_k)

        h1 = sum(1 for c in final if c.get("hop") == 1)
        h2 = sum(1 for c in final if c.get("hop") == 2)
        print(f"[RETRIEVER] Final: {len(final)} chunks (pass-1: {h1}, cross-ref: {h2})")

        return final

    def get_stats(self) -> dict:
        return {
            "index_loaded": self.is_loaded,
            "total_chunks": len(self.chunks) if self.chunks else 0,
            "model_loaded": self.model is not None
        }


# Global singleton — imported by main.py
retriever = Retriever()