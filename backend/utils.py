# backend/utils.py — NASA acronym resolver + diagram term mapper
# FIX v3.2: Added DIAGRAM_TERMS for visual concepts whose PDF pages have sparse
# text (diagrams, flowcharts). Used by retriever.py to inject section-targeted
# boost queries so these topics are actually retrieved.

NASA_ACRONYMS = {
    # ── Reviews ───────────────────────────────────────────────────
    "SRR":   "System Requirements Review",
    "SDR":   "System Definition Review",
    "MDR":   "Mission Definition Review",
    "PDR":   "Preliminary Design Review",
    "CDR":   "Critical Design Review",
    "PRR":   "Production Readiness Review",
    "ORR":   "Operational Readiness Review",
    "FRR":   "Flight Readiness Review",
    "SAR":   "System Acceptance Review",
    "MRR":   "Mission Readiness Review",
    "DR":    "Design Review",
    "PIR":   "Post-Implementation Review",

    # ── Lifecycle & Process ───────────────────────────────────────
    "KDP":   "Key Decision Point",
    "TRL":   "Technology Readiness Level",
    "MRL":   "Manufacturing Readiness Level",
    "SE":    "Systems Engineering",
    "SEP":   "Systems Engineering Plan",
    "SEMP":  "Systems Engineering Management Plan",
    "ConOps":"Concept of Operations",
    "CONOPS":"Concept of Operations",
    "WBS":   "Work Breakdown Structure",
    "PBS":   "Product Breakdown Structure",
    "OBS":   "Organizational Breakdown Structure",
    "ICD":   "Interface Control Document",
    "ICR":   "Interface Control and Requirements",
    "SRD":   "System Requirements Document",
    "IRD":   "Interface Requirements Document",

    # ── Verification & Validation ─────────────────────────────────
    "V&V":   "Verification and Validation",
    "TEMP":  "Test and Evaluation Master Plan",
    "VCD":   "Verification Control Document",

    # ── Measures ──────────────────────────────────────────────────
    "MOE":   "Measure of Effectiveness",
    "MOP":   "Measure of Performance",
    "TPM":   "Technical Performance Measure",
    "KPP":   "Key Performance Parameter",

    # ── Modelling ─────────────────────────────────────────────────
    "SysML": "Systems Modeling Language",
    "MBSE":  "Model-Based Systems Engineering",
    "CAD":   "Computer-Aided Design",

    # ── Risk ──────────────────────────────────────────────────────
    "RAM":   "Risk and Maintainability",
    "FMEA":  "Failure Modes and Effects Analysis",
    "FTA":   "Fault Tree Analysis",
    "PRA":   "Probabilistic Risk Assessment",

    # ── Organisations / Documents ─────────────────────────────────
    "NPR":   "NASA Procedural Requirements",
    "NID":   "NASA Interim Directive",
    "SP":    "Special Publication",
    "NASA":  "National Aeronautics and Space Administration",
    "OCE":   "Office of Chief Engineer",

    # ── Project Management ────────────────────────────────────────
    "PM":    "Project Manager",
    "PI":    "Principal Investigator",
    "CM":    "Configuration Management",
    "CCB":   "Change Control Board",
    "EVM":   "Earned Value Management",
    "SOW":   "Statement of Work",
    "RFP":   "Request for Proposal",
}


# ── DIAGRAM TERMS ─────────────────────────────────────────────────
# Maps natural language query keywords → section search terms
#
# WHY THIS EXISTS:
#   The Vee Model, SE Engine, and lifecycle diagrams are primarily VISUAL
#   in the NASA handbook. pypdf extracts little-to-no text from diagram pages
#   (Figure 2.1-1 is a full-page flowchart). This means no FAISS chunk
#   exists with "Vee Model" in the text — retrieval returns wrong sections.
#
#   FIX: When a query contains a known diagram keyword, the retriever
#   runs an additional Pass 0 boost query using the section description
#   text that DOES exist around that diagram. This bridges the gap between
#   the visual diagram and the surrounding narrative text that was indexed.
#
# FORMAT: { trigger_keyword_lowercase: "boost search query" }
#   The boost query uses words from the handbook's surrounding narrative,
#   not the diagram itself — those words ARE in the FAISS index.

DIAGRAM_TERMS = {
    # Vee Model — Figure 2.1-1, described in Section 2.0 / 2.1
    "vee model": (
        "systems engineering Vee Model lifecycle decomposition "
        "integration verification left side right side"
    ),
    "vee":  (
        "systems engineering Vee lifecycle decomposition "
        "integration verification left side right side"
    ),
    "se engine": (
        "NASA systems engineering engine Figure 2.1 "
        "requirements decomposition verification integration"
    ),
    "systems engineering engine": (
        "NASA SE engine process Figure 2.1 "
        "system design verification integration"
    ),

    # Lifecycle Phase Diagram — described across Chapter 2 + Appendix
    "lifecycle": (
        "project lifecycle phases pre-phase A phase B C D E F "
        "formulation implementation"
    ),
    "lifecycle diagram": (
        "project lifecycle phases formulation implementation "
        "pre-phase A through F Key Decision Points"
    ),
    "phase diagram": (
        "lifecycle phase A B C D E F formulation implementation "
        "Key Decision Points milestones"
    ),

    # Process flow diagrams referenced throughout
    "process flow": (
        "systems engineering process flow requirements decomposition "
        "design verification validation"
    ),
    "flowchart": (
        "systems engineering process flow decision tree "
        "requirements design verification"
    ),
    "decision tree": (
        "systems engineering decision process flow "
        "requirements design review criteria"
    ),
}


def get_diagram_boost_query(question: str) -> str | None:
    """
    Check if a question references a known diagram/visual concept.
    Returns a boost query string if matched, None otherwise.

    WHY: Visual concepts in the handbook have sparse PDF text.
    The boost query uses surrounding narrative text that WAS indexed,
    bridging the gap between the query and the FAISS index.

    Example:
      "What is the Vee Model?"
      → matched by "vee model" key
      → returns "systems engineering Vee lifecycle decomposition ..."
      → retriever uses this as Pass 0 to find Section 2.0/2.1 chunks
    """
    q_lower = question.lower()
    for trigger, boost_query in DIAGRAM_TERMS.items():
        if trigger in q_lower:
            print(f"[UTILS] Diagram term detected: '{trigger}' → injecting boost query")
            return boost_query
    return None


def expand_acronyms(text: str) -> str:
    """
    Expand NASA acronyms in a query or chunk before embedding.

    WHY: Embedding models treat "PDR" and "Preliminary Design Review"
    as semantically distant tokens. Expanding bridges the vector gap
    so retrieval actually finds the right section.

    Example:
      "What are the entry criteria for PDR?"
      → "What are the entry criteria for PDR (Preliminary Design Review)?"
    """
    words = text.split()
    expanded = []

    for word in words:
        stripped = word.strip("?.,;:()\n\t")

        if stripped in NASA_ACRONYMS:
            full_form = NASA_ACRONYMS[stripped]
            suffix = word[len(stripped):]
            expanded.append(f"{stripped} ({full_form}){suffix}")
        else:
            expanded.append(word)

    return " ".join(expanded)