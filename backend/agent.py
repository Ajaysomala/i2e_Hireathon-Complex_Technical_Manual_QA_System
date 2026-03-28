# backend/agent.py
# v3 FINAL — Multi-hop awareness + table/figure chunk handling
# FIX: time.sleep() replaced with asyncio.sleep() — was blocking FastAPI event loop
# FIX: generate_answer is now async-compatible via asyncio.sleep on rate limit

import os
import asyncio
from groq import Groq
from dotenv import load_dotenv
from backend.utils import expand_acronyms

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────
GROQ_MODEL  = "llama-3.3-70b-versatile"
TEMPERATURE = 0.1
MAX_TOKENS  = 1024

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── SYSTEM PROMPT ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a precise technical assistant specialising in
NASA Systems Engineering. You answer questions about the NASA Systems
Engineering Handbook (SP-2016-6105 Rev2).

RULES:
1. Answer ONLY from the provided context chunks. Never fabricate information.
2. Cite EXACT locations: section title, page number, paragraph or table if visible.
   Example: "Section 6.3.2, Page 145, Table 6-4"
3. Chunks labelled [CROSS-REFERENCE] were retrieved by following a cross-reference
   from a primary chunk. Use them to connect reasoning across chapters explicitly.
   Example: "Section 6.6 defines risk inputs, which Section 6.8 then requires as
   entry criteria for technical reviews (cross-chapter link)."
4. Chunks labelled [TABLE] contain structured tabular data. When answering about
   criteria, milestones, or TRL levels, prefer table chunks and list items clearly.
5. Chunks labelled [FIGURE] contain figure captions. Reference them when the
   question asks about diagrams, flowcharts, or visual models.
6. If the answer spans multiple sections, synthesise clearly and cite each source.
7. If context does not contain the answer, say exactly:
   "This information was not found in the retrieved sections.
    Try rephrasing your question or asking about a related topic."
8. Expand acronyms on first use: TRL (Technology Readiness Level), etc.
9. For review entry/exit criteria, always use numbered lists.
10. Never invent section numbers, table references, or page numbers.

RESPONSE FORMAT:
[Direct answer — structured with bullets or numbered lists where appropriate]

Sources used:
- Section: [title], Page: [n][, Table/Paragraph if visible][  ← CROSS-REF if hop-2]
"""


def build_context(chunks: list[dict]) -> str:
    """
    Format retrieved chunks into numbered context blocks for the LLM.
    Labels: [PRIMARY] [CROSS-REFERENCE] [TABLE] [FIGURE]
    Includes parent_section + paragraph_index for citation precision.
    """
    context_parts = []

    for i, chunk in enumerate(chunks, 1):
        chunk_type = chunk.get("type", "text")
        hop        = chunk.get("hop", 1)

        if chunk_type == "table":
            label = "[TABLE]"
        elif chunk_type == "figure":
            label = "[FIGURE]"
        elif hop == 2:
            label = "[CROSS-REFERENCE]"
        else:
            label = "[PRIMARY]"

        parent = chunk.get("parent_section", "")
        para   = chunk.get("paragraph_index", "")

        parent_line = f"Parent Section: {parent}\n" if parent else ""
        para_line   = f"Paragraph/Location: {para}\n" if para else ""

        context_parts.append(
            f"[CHUNK {i}] {label}\n"
            f"Section: {chunk['section_title']}\n"
            f"{parent_line}"
            f"Page: {chunk['page_number']}\n"
            f"{para_line}"
            f"Content:\n{chunk['text']}\n"
        )

    return "\n---\n".join(context_parts)


def calculate_confidence(chunks: list[dict]) -> float:
    """
    Weighted confidence: top-3 chunks with rank decay + hop discount.
    Scale: cosine sim [0.25, 0.95] → confidence [0.0, 1.0]
    Thresholds: >0.75 HIGH, 0.50-0.75 MED, <0.50 LOW
    """
    if not chunks:
        return 0.0

    rank_weights = [0.60, 0.25, 0.15]
    top3 = chunks[:3]

    weighted_score = 0.0
    for i, chunk in enumerate(top3):
        raw_score  = chunk.get("relevance_score", 0.0)
        hop_factor = 1.0 if chunk.get("hop", 1) == 1 else 0.70
        weighted_score += rank_weights[i] * raw_score * hop_factor

    confidence = (weighted_score - 0.25) / (0.95 - 0.25)
    return round(min(1.0, max(0.0, confidence)), 2)


def generate_answer(
    question: str,
    chunks: list[dict],
    conversation_history: list[dict] | None = None
) -> str:
    """
    Call Groq LLM with expanded question + multi-hop context chunks.

    FIX v3.1: Removed blocking time.sleep(10) from rate-limit retry.
    The Groq client is synchronous, so we use a simple retry message
    instead of sleeping inside the async request handler.

    WHY: time.sleep() in a FastAPI route blocks the entire event loop,
    freezing all other requests. The correct fix for async retry would
    be asyncio.sleep(), but since groq.Client is sync, the safest
    approach for a demo is to return a retry message immediately.
    """
    if conversation_history is None:
        conversation_history = []

    expanded_question = expand_acronyms(question)
    context = build_context(chunks)

    hop2_count = sum(1 for c in chunks if c.get("hop", 1) == 2)
    hop_note   = (
        f"Note: {hop2_count} chunk(s) were retrieved via cross-reference "
        f"following (marked [CROSS-REFERENCE]). Use them to link reasoning "
        f"across chapters explicitly.\n\n"
        if hop2_count > 0 else ""
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for turn in conversation_history[-3:]:
        messages.append(turn)

    user_message = (
        f"Context from NASA Systems Engineering Handbook:\n\n"
        f"{hop_note}"
        f"{context}\n\n"
        f"Question: {expanded_question}\n\n"
        f"Answer based strictly on the context above. "
        f"Cite exact section titles and page numbers. "
        f"If cross-reference chunks are present, explicitly connect "
        f"the reasoning between chapters in your answer:"
    )
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        return response.choices[0].message.content

    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "rate_limit" in error_str.lower():
            # FIX: Do NOT sleep here — blocks async event loop.
            # Return a clear retry message instead.
            print("[AGENT] Groq rate limit hit — returning retry message.")
            return (
                "⚠ Groq API rate limit reached. "
                "Please wait 10–15 seconds and try again.\n"
                "(Free tier: ~30 requests/minute)"
            )
        return f"Error generating answer: {error_str}"