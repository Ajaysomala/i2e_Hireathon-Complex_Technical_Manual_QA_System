# backend/models.py
# UPGRADED v3: SourceChunk exposes hop (1=direct, 2=cross-ref) and chunk_type
# These fields drive the badge display in the frontend source cards

from pydantic import BaseModel
from typing import List, Optional

# ── REQUEST MODELS ────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str
    conversation_history: Optional[List[dict]] = []

# ── RESPONSE MODELS ───────────────────────────────────────────────

class SourceChunk(BaseModel):
    page_number:    int
    section_title:  str
    text_preview:   str           # first 200 chars
    relevance_score: float        # cosine similarity, real not fake
    parent_section: Optional[str] = ""   # e.g. "6.3" for chunk in "6.3.2.1"
    paragraph_hint: Optional[str] = ""   # e.g. "paragraph 2" / "Table G-6"
    hop:            Optional[int] = 1    # 1=direct match, 2=cross-reference hop
    chunk_type:     Optional[str] = "text"  # "text" | "table" | "figure"

class AnswerResponse(BaseModel):
    answer:         str
    sources:        List[SourceChunk]
    confidence:     float         # weighted avg, calibrated 0–1
    query_time_ms:  float
    expanded_query: Optional[str] = ""  # shows what acronyms were expanded

class IngestStatusResponse(BaseModel):
    status:       str   # "success" or "error"
    total_chunks: int
    total_pages:  int
    message:      str

class HealthResponse(BaseModel):
    status:       str
    index_loaded: bool
    total_chunks: int
    model_loaded: bool