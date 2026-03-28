# backend/ingestor.py
# UPGRADED v3: Table extraction + Diagram/Figure awareness
#
# THREE chunk types produced:
#   "text"   — normal section text (as before)
#   "table"  — structured table extracted via pdfplumber, stored as markdown
#   "figure" — figure caption + surrounding context for diagram awareness
#
# WHY this matters (from Problem Statement 2):
#   Tables: "entry criteria for PDR" lives in Table G-6 across multiple pages.
#           Text extraction loses row/column relationships. pdfplumber preserves them.
#   Figures: "What does the Vee Model look like?" — the Vee Model is Figure 2.1-1.
#            We can't parse the image, but we CAN extract the caption and surrounding
#            descriptive paragraphs, making diagrams searchable by concept.

import os
import re
import pickle
import numpy as np
import faiss

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    print("[INGESTOR] WARNING: pdfplumber not installed. Table extraction disabled.")
    print("[INGESTOR] Install with: pip install pdfplumber")

try:
    from pypdf import PdfReader
except ImportError:
    print("[INGESTOR] ERROR: pypdf not installed. Text extraction will fail.")
    print("[INGESTOR] Install with: pip install pypdf")
    raise  # can't proceed without pypdf for text extraction

from sentence_transformers import SentenceTransformer

# ── CONFIG ────────────────────────────────────────────────────────
PDF_PATH    = "data/nasa_handbook.pdf"
INDEX_DIR   = "faiss_index"
INDEX_FILE  = os.path.join(INDEX_DIR, "index.faiss")
CHUNKS_FILE = os.path.join(INDEX_DIR, "chunks.pkl")

EMBEDDING_MODEL  = "all-MiniLM-L6-v2"
MIN_CHUNK_WORDS  = 30     # skip chunks shorter than this (noise)
MAX_CHUNK_WORDS  = 400    # split chunks longer than this
TABLE_MIN_ROWS   = 2      # ignore trivial 1-row "tables" (usually formatting)
TABLE_MIN_COLS   = 2      # ignore single-column pseudo-tables

# ── SECTION HEADING PATTERNS ──────────────────────────────────────
# Matches: "1.", "2.1", "3.4.2", "6.3.2.1" at start of line
SECTION_PATTERN = re.compile(r'^(\d+(?:\.\d+){0,3})\s+(.+)$')

# ── FIGURE CAPTION PATTERNS ──────────────────────────────────────
# Matches: "Figure 2.1-1.", "Figure 3-2 ", "Fig. 4.1"
FIGURE_PATTERN = re.compile(
    r'(Figure\s+\d+[\.\d-]*\.?\s+.{10,120}|Fig\.\s+\d+[\.\d-]*\.?\s+.{10,120})',
    re.IGNORECASE
)

# ── APPENDIX HEADING PATTERNS ─────────────────────────────────────
APPENDIX_PATTERN = re.compile(r'^(APPENDIX\s+[A-Z])\s*[:\-–]?\s*(.*)$', re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════
# PART 1 — TEXT + FIGURE CHUNKS  (via pypdf)
# ══════════════════════════════════════════════════════════════════

def extract_section_title(line: str) -> tuple[str, str] | None:
    """
    Try to parse a line as a section heading.
    Returns (section_number, section_name) or None.
    """
    m = SECTION_PATTERN.match(line.strip())
    if m:
        return m.group(1), m.group(2).strip()
    m2 = APPENDIX_PATTERN.match(line.strip())
    if m2:
        return m2.group(1), m2.group(2).strip() or m2.group(1)
    return None


def get_parent_section(section_number: str) -> str:
    """
    Derive parent section from section number.
    "6.3.2.1" → "6.3.2"
    "6.3.2"   → "6.3"
    "6.3"     → "6"
    "6"       → ""
    """
    parts = section_number.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def extract_text_and_figure_chunks(pdf_path: str) -> list[dict]:
    """
    Extract text chunks and figure caption chunks from PDF using pypdf.

    Section detection:
      - Scan each line for section heading patterns (e.g. "6.3.2 Entry Criteria")
      - Accumulate text under each heading until next heading starts
      - Store section_title, parent_section, page_number, paragraph_index

    Figure detection:
      - Scan each page for lines matching "Figure X-X. Description..."
      - Capture the caption + up to 5 surrounding lines as context
      - Store as chunk_type="figure" for diagram-aware retrieval

    WHY pypdf for text (not pdfplumber):
      pypdf gives cleaner text flow for narrative paragraphs.
      pdfplumber is better for tables (used in Part 2 below).
      We use both tools for what each does best.
    """
    reader = PdfReader(pdf_path)
    chunks = []

    current_section_num  = "0"
    current_section_name = "Introduction"
    current_text_lines   = []
    current_page         = 1
    paragraph_counter    = 0

    def flush_text_chunk():
        """Save accumulated lines as a text chunk."""
        nonlocal paragraph_counter
        text = " ".join(current_text_lines).strip()
        words = text.split()
        if len(words) < MIN_CHUNK_WORDS:
            return  # too short, skip

        # Split long chunks at sentence boundaries
        if len(words) > MAX_CHUNK_WORDS:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            buffer, buf_words = [], 0
            for sent in sentences:
                sw = len(sent.split())
                if buf_words + sw > MAX_CHUNK_WORDS and buffer:
                    paragraph_counter += 1
                    chunks.append({
                        "type":           "text",
                        "section_title":  f"{current_section_num} {current_section_name}".strip(),
                        "section_number": current_section_num,
                        "parent_section": get_parent_section(current_section_num),
                        "page_number":    current_page,
                        "paragraph_index": f"paragraph {paragraph_counter}",
                        "text":           " ".join(buffer).strip(),
                    })
                    buffer, buf_words = [sent], sw
                else:
                    buffer.append(sent)
                    buf_words += sw
            if buffer:
                paragraph_counter += 1
                chunks.append({
                    "type":           "text",
                    "section_title":  f"{current_section_num} {current_section_name}".strip(),
                    "section_number": current_section_num,
                    "parent_section": get_parent_section(current_section_num),
                    "page_number":    current_page,
                    "paragraph_index": f"paragraph {paragraph_counter}",
                    "text":           " ".join(buffer).strip(),
                })
        else:
            paragraph_counter += 1
            chunks.append({
                "type":           "text",
                "section_title":  f"{current_section_num} {current_section_name}".strip(),
                "section_number": current_section_num,
                "parent_section": get_parent_section(current_section_num),
                "page_number":    current_page,
                "paragraph_index": f"paragraph {paragraph_counter}",
                "text":           text,
            })

    print(f"[INGESTOR] Extracting text + figures from {len(reader.pages)} pages...")

    for page_num, page in enumerate(reader.pages, 1):
        raw = page.extract_text()
        if not raw:
            continue

        lines = raw.split("\n")

        for line_idx, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # ── Check for section heading ─────────────────────
            heading = extract_section_title(line_stripped)
            if heading:
                # Flush current chunk before starting new section
                flush_text_chunk()
                current_text_lines  = []
                current_section_num, current_section_name = heading
                current_page        = page_num
                paragraph_counter   = 0
                continue

            # ── Check for figure caption ──────────────────────
            fig_match = FIGURE_PATTERN.search(line_stripped)
            if fig_match:
                caption = fig_match.group(0).strip()
                # Grab surrounding context (lines before + after caption)
                context_lines = lines[max(0, line_idx-3): line_idx+6]
                context = " ".join(l.strip() for l in context_lines if l.strip())

                figure_text = (
                    f"FIGURE CAPTION: {caption}\n"
                    f"Context: {context}"
                )

                chunks.append({
                    "type":            "figure",
                    "section_title":   f"{current_section_num} {current_section_name}".strip(),
                    "section_number":  current_section_num,
                    "parent_section":  get_parent_section(current_section_num),
                    "page_number":     page_num,
                    "paragraph_index": f"figure caption",
                    "text":            figure_text,
                    "caption":         caption,
                })
                print(f"[INGESTOR]   Figure found p.{page_num}: {caption[:60]}...")
                # Don't skip — also accumulate the line into text chunk
                # so surrounding text context is preserved

            # ── Accumulate into current text chunk ────────────
            current_text_lines.append(line_stripped)
            current_page = page_num

    # Flush last chunk
    flush_text_chunk()

    text_count   = sum(1 for c in chunks if c["type"] == "text")
    figure_count = sum(1 for c in chunks if c["type"] == "figure")
    print(f"[INGESTOR] Text chunks: {text_count} | Figure chunks: {figure_count}")

    return chunks


# ══════════════════════════════════════════════════════════════════
# PART 2 — TABLE CHUNKS  (via pdfplumber)
# ══════════════════════════════════════════════════════════════════

def table_to_markdown(table: list[list]) -> str:
    """
    Convert a pdfplumber table (list of lists) to markdown format.

    Input:  [["Header1", "Header2"], ["Row1A", "Row1B"], ...]
    Output:
      | Header1 | Header2 |
      |---------|---------|
      | Row1A   | Row1B   |

    WHY markdown:
      The embedding model understands markdown table structure.
      "| Entry Criteria | Description |" embeds closer to
      "entry criteria" queries than raw CSV would.
    """
    if not table or not table[0]:
        return ""

    # Clean None values
    cleaned = []
    for row in table:
        cleaned.append([str(cell).strip() if cell is not None else "" for cell in row])

    if not cleaned:
        return ""

    # Build markdown
    header = cleaned[0]
    rows   = cleaned[1:]

    md_lines = []
    md_lines.append("| " + " | ".join(header) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in rows:
        # Pad row if fewer cells than header
        while len(row) < len(header):
            row.append("")
        md_lines.append("| " + " | ".join(row) + " |")

    return "\n".join(md_lines)


def find_table_title(page_text: str, table_bbox: tuple) -> str:
    """
    Try to find the title of a table by looking at text immediately
    above the table bounding box on the same page.

    Looks for lines matching "Table X-X", "Table G-X" patterns.
    Falls back to "Table on page N" if not found.
    """
    if not page_text:
        return ""

    # Find all "Table X" references in page text
    matches = re.findall(
        r'Table\s+[A-Z]?[\d]+-[\d]+[:\.\s][^\n]{0,80}',
        page_text,
        re.IGNORECASE
    )
    if matches:
        return matches[-1].strip()  # last match = most likely the one above
    return ""


def extract_table_chunks(pdf_path: str) -> list[dict]:
    """
    Extract structured table chunks from PDF using pdfplumber.

    For each page:
      1. Detect tables using pdfplumber's table finder
      2. Skip trivial tables (< TABLE_MIN_ROWS rows or < TABLE_MIN_COLS cols)
      3. Convert to markdown using table_to_markdown()
      4. Try to find the table's title from surrounding text
      5. Store as chunk_type="table"

    WHY this is critical:
      "What are the entry criteria for PDR?" — the answer is in Table G-6
      which spans pages 271-273. Text extraction loses the row/column
      structure entirely. pdfplumber preserves it as a queryable markdown table.

    Multi-page tables:
      pdfplumber detects tables per-page. For tables that span pages,
      we produce one chunk per page-portion. Each chunk includes the
      table title so they can be linked by the LLM.
    """
    if not PDFPLUMBER_AVAILABLE:
        print("[INGESTOR] pdfplumber not available — skipping table extraction")
        return []

    table_chunks = []
    table_counter = 0

    print(f"[INGESTOR] Extracting tables from PDF with pdfplumber...")

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            try:
                tables = page.extract_tables()
            except Exception as e:
                print(f"[INGESTOR]   Page {page_num} table extraction error: {e}")
                continue

            if not tables:
                continue

            # Get page text for title detection
            page_text = page.extract_text() or ""

            for table in tables:
                if not table:
                    continue

                # Filter trivial tables
                num_rows = len(table)
                num_cols = max(len(row) for row in table) if table else 0

                if num_rows < TABLE_MIN_ROWS or num_cols < TABLE_MIN_COLS:
                    continue

                # Convert to markdown
                md = table_to_markdown(table)
                if not md or len(md.split()) < 10:
                    continue

                # Try to find table title
                title = find_table_title(page_text, None)
                table_counter += 1
                display_title = title if title else f"Table {table_counter} (Page {page_num})"

                # Build searchable text: title + markdown content
                searchable_text = (
                    f"TABLE: {display_title}\n\n"
                    f"{md}"
                )

                table_chunks.append({
                    "type":            "table",
                    "section_title":   display_title,
                    "section_number":  "",
                    "parent_section":  "",
                    "page_number":     page_num,
                    "paragraph_index": f"table",
                    "text":            searchable_text,
                    "table_markdown":  md,
                    "table_title":     display_title,
                    "num_rows":        num_rows,
                    "num_cols":        num_cols,
                })

                print(f"[INGESTOR]   Table p.{page_num}: {display_title[:50]}... "
                      f"({num_rows}r × {num_cols}c)")

    print(f"[INGESTOR] Table chunks extracted: {len(table_chunks)}")
    return table_chunks


# ══════════════════════════════════════════════════════════════════
# PART 3 — BUILD FAISS INDEX
# ══════════════════════════════════════════════════════════════════

def build_index(chunks: list[dict]) -> bool:
    """
    Embed all chunks and build FAISS index.

    Embedding strategy:
      - text chunks:   embed the raw text
      - table chunks:  embed "TABLE: {title}\n{markdown}" —
                       title carries semantic weight for retrieval
      - figure chunks: embed "FIGURE CAPTION: {caption}\nContext: {context}" —
                       caption describes what the diagram shows

    Uses L2 normalization → cosine similarity search.
    """
    print(f"[INGESTOR] Embedding {len(chunks)} chunks...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    texts = [chunk["text"] for chunk in chunks]

    # Batch encode for speed
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        convert_to_numpy=True
    ).astype(np.float32)

    # Normalize for cosine similarity
    faiss.normalize_L2(embeddings)

    # Build FAISS flat index (exact search — 885 chunks is small enough)
    dim   = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)  # Inner Product on normalized = cosine sim
    index.add(embeddings)

    # Save index + chunks
    os.makedirs(INDEX_DIR, exist_ok=True)
    faiss.write_index(index, INDEX_FILE)
    with open(CHUNKS_FILE, "wb") as f:
        pickle.dump(chunks, f)

    print(f"[INGESTOR] FAISS index saved: {index.ntotal} vectors, dim={dim}")
    return True


# ══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def run_ingestion(pdf_path: str = PDF_PATH) -> bool:
    """
    Full ingestion pipeline:
      1. Extract text + figure chunks (pypdf)
      2. Extract table chunks (pdfplumber)
      3. Merge all chunks
      4. Build + save FAISS index

    Chunk type breakdown (approximate for 270-page NASA handbook):
      text   : ~850 chunks  (narrative sections)
      table  : ~40  chunks  (review criteria, TRL levels, milestones)
      figure : ~30  chunks  (Vee Model, lifecycle diagrams, process flows)
    """
    if not os.path.exists(pdf_path):
        print(f"[INGESTOR] ERROR: PDF not found at {pdf_path}")
        print(f"[INGESTOR] Download from: https://www.nasa.gov/wp-content/uploads/"
              f"2018/09/nasa_systems_engineering_handbook_0.pdf")
        return False

    print(f"[INGESTOR] Starting ingestion of: {pdf_path}")
    print("=" * 60)

    # Step 1: Text + Figure chunks
    text_figure_chunks = extract_text_and_figure_chunks(pdf_path)

    # Step 2: Table chunks
    table_chunks = extract_table_chunks(pdf_path)

    # Step 3: Merge
    all_chunks = text_figure_chunks + table_chunks

    # Summary
    text_count   = sum(1 for c in all_chunks if c["type"] == "text")
    table_count  = sum(1 for c in all_chunks if c["type"] == "table")
    figure_count = sum(1 for c in all_chunks if c["type"] == "figure")

    print("=" * 60)
    print(f"[INGESTOR] Total chunks: {len(all_chunks)}")
    print(f"           ├─ text   : {text_count}")
    print(f"           ├─ table  : {table_count}")
    print(f"           └─ figure : {figure_count}")
    print("=" * 60)

    # Step 4: Build FAISS index
    success = build_index(all_chunks)

    if success:
        print("[INGESTOR] ✅ Ingestion complete. System ready.")
    else:
        print("[INGESTOR] ❌ Ingestion failed at index build step.")

    return success


# ── RUN DIRECTLY ──────────────────────────────────────────────────
if __name__ == "__main__":
    run_ingestion()