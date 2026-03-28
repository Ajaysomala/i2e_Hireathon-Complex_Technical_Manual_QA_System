# backend/main.py
# v3 FINAL — FastAPI app, all routes
# FIX: /shutdown now cross-platform (SIGTERM on Unix, CTRL_C_EVENT on Windows)
# FIX: CORS tightened to localhost origins only (was allow_origins=["*"])
# FIX: Removed unused `signal` top-level import replaced with inline sys.platform check
# Run with: uvicorn backend.main:app --reload

import os
import sys
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.models import (
    QuestionRequest, AnswerResponse,
    SourceChunk, IngestStatusResponse, HealthResponse
)
from backend.retriever import retriever
from backend.agent import generate_answer, calculate_confidence
from backend.ingestor import run_ingestion
from backend.utils import expand_acronyms

# ── LIFESPAN ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[STARTUP] Loading retriever...")
    try:
        retriever.load()
        print("[STARTUP] Retriever ready.")
    except FileNotFoundError as e:
        print(f"[STARTUP WARNING] {e}")
        print("[STARTUP] Run ingestion first: python -m backend.ingestor")
    yield
    print("[SHUTDOWN] Shutting down.")

# ── APP ───────────────────────────────────────────────────────────
app = FastAPI(
    title="NASA Handbook QA System",
    description="Complex Technical Manual QA — i2e Hireathon 2026",
    version="3.1.0",
    lifespan=lifespan
)

# FIX: CORS restricted to localhost only — was allow_origins=["*"] which
# accepts requests from any domain. For a local demo, this is safe and
# correct. Change to ["*"] only if deploying to a remote server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",   # in case of separate dev server
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# ── ROUTES ────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
async def serve_frontend():
    return FileResponse("frontend/index.html")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    stats = retriever.get_stats()
    return HealthResponse(
        status="operational" if stats["index_loaded"] else "index_not_loaded",
        index_loaded=stats["index_loaded"],
        total_chunks=stats["total_chunks"],
        model_loaded=stats["model_loaded"]
    )


@app.post("/clear")
async def clear_history():
    return {"status": "history cleared"}


@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: QuestionRequest):
    """
    Main QA endpoint with multi-hop retrieval.

    Pipeline:
      1. Expand acronyms in question
      2. Pass-1: FAISS search → fetch-15 → deduplicate-to-5
      3. Pass-2: follow cross-references found in pass-1 chunks
      4. Merge pass-1 + pass-2, final dedup
      5. Build context with hop labels (PRIMARY / CROSS-REFERENCE / TABLE / FIGURE)
      6. Groq LLaMA-3.3-70B generates grounded answer with citations
      7. Weighted confidence score (hop-2 chunks discounted)
      8. Return answer + sources with hop + chunk_type metadata
    """
    if not retriever.is_loaded:
        raise HTTPException(
            status_code=503,
            detail="Knowledge base not loaded. Run ingestion first."
        )
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    start_time = time.time()

    original_question = request.question.strip()
    expanded_question = expand_acronyms(original_question)

    chunks = retriever.search(original_question)

    if not chunks:
        return AnswerResponse(
            answer="No relevant sections found. Try rephrasing your question.",
            sources=[],
            confidence=0.0,
            query_time_ms=0.0,
            expanded_query=expanded_question if expanded_question != original_question else ""
        )

    answer     = generate_answer(
        question=original_question,
        chunks=chunks,
        conversation_history=request.conversation_history
    )
    confidence = calculate_confidence(chunks)

    sources = [
        SourceChunk(
            page_number    = chunk["page_number"],
            section_title  = chunk["section_title"],
            text_preview   = chunk["text"][:200] + "...",
            relevance_score= round(chunk["relevance_score"], 3),
            parent_section = chunk.get("parent_section", ""),
            paragraph_hint = chunk.get("paragraph_index", ""),
            hop            = chunk.get("hop", 1),
            chunk_type     = chunk.get("type", "text")
        )
        for chunk in chunks
    ]

    query_time = (time.time() - start_time) * 1000

    return AnswerResponse(
        answer        = answer,
        sources       = sources,
        confidence    = confidence,
        query_time_ms = round(query_time, 1),
        expanded_query= expanded_question if expanded_question != original_question else ""
    )


@app.post("/ingest", response_model=IngestStatusResponse)
async def trigger_ingestion():
    try:
        success = run_ingestion()
        if success:
            retriever.load()
            stats = retriever.get_stats()
            return IngestStatusResponse(
                status="success",
                total_chunks=stats["total_chunks"],
                total_pages=270,
                message="NASA Handbook ingested and index built successfully."
            )
        return IngestStatusResponse(
            status="error", total_chunks=0, total_pages=0,
            message="Ingestion failed. Check PDF path."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shutdown")
async def shutdown():
    """
    FIX: Cross-platform shutdown.
    SIGTERM works on Linux/Mac. On Windows, use os.kill with CTRL_C_EVENT.
    WHY the original broke: signal.SIGTERM on Windows raises OSError.
    """
    pid = os.getpid()
    if sys.platform == "win32":
        import signal
        os.kill(pid, signal.CTRL_C_EVENT)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    return {"status": "shutting down"}