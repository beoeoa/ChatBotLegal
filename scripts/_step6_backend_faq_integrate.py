from pathlib import Path

# 1) Add faqs field to AskResponse model
mp = Path("api/models.py")
text = mp.read_text(encoding="utf-8")
if "faqs:" not in text:
    old = '    procedure_detail: Optional[Dict[str, Any]] = Field(None, description="Full procedural detail matching the question")'
    new = '''    procedure_detail: Optional[Dict[str, Any]] = Field(None, description="Full procedural detail matching the question")
    faqs: Optional[List[Dict[str, Any]]] = Field(None, description="Matched FAQs for Ask UI")'''
    if old not in text:
        raise SystemExit("AskResponse procedure_detail field not found")
    text = text.replace(old, new, 1)
    mp.write_text(text, encoding="utf-8", newline="\n")
    print("OK: models.py faqs field")
else:
    print("SKIP: models.py already has faqs")

# 2) Add FAQ match helper + integrate into search.py
sp = Path("api/routers/search.py")
st = sp.read_text(encoding="utf-8")

helper = '''
def _match_faqs_for_question(
    question: str,
    domain: str | None = None,
    ward_scope: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Match approved FAQs for Ask UI from local seed/store. Fail soft if missing."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    store_path = root / "notebook_data" / "faq_store.json"
    seed_path = root / "notebook_data" / "faq_seed.json"

    faqs: list[dict] = []
    try:
        if store_path.exists():
            data = json.loads(store_path.read_text(encoding="utf-8"))
            faqs = list(data.get("faqs") or [])
        elif seed_path.exists():
            faqs = list(json.loads(seed_path.read_text(encoding="utf-8")))
    except Exception:
        return []

    faqs = [f for f in faqs if (f.get("review_status") or "approved") == "approved"]
    if domain:
        faqs = [f for f in faqs if f.get("domain") == domain]
    if ward_scope:
        faqs = [
            f
            for f in faqs
            if not f.get("ward_scope")
            or ward_scope.lower() in str(f.get("ward_scope") or "").lower()
        ]

    q = (question or "").lower()
    tokens = [t for t in q.replace("?", " ").split() if len(t) > 2]
    phrases = [
        "khai sinh",
        "sang tên",
        "sang ten",
        "sổ đỏ",
        "so do",
        "đỗ xe",
        "do xe",
        "kết hôn",
        "ket hon",
        "xây dựng",
        "xay dung",
        "hộ kinh doanh",
        "ho kinh doanh",
        "khiếu nại",
        "khieu nai",
        "chứng tử",
        "chung tu",
        "tạm trú",
        "tam tru",
        "thừa kế",
        "thua ke",
    ]
    scored: list[tuple[int, dict]] = []
    for f in faqs:
        blob = f"{f.get('question','')} {f.get('answer','')}".lower()
        score = sum(1 for t in tokens if t in blob)
        for phrase in phrases:
            if phrase in q and phrase in blob:
                score += 3
        if score > 0:
            scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored[:limit]]


'''

if "_match_faqs_for_question" not in st:
    # insert before _match_procedure_detail
    anchor = "def _match_procedure_detail(question: str) -> dict | None:"
    if anchor not in st:
        raise SystemExit("anchor _match_procedure_detail not found")
    st = st.replace(anchor, helper + anchor, 1)
    print("OK: inserted _match_faqs_for_question")
else:
    print("SKIP: helper exists")

# Integrate into both ask paths: after procedure_match lines
old1 = """        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match)
        if not _has_sufficient_legal_evidence(ask_request.question, retrieval):
"""
new1 = """        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match)
        matched_faqs = _match_faqs_for_question(
            ask_request.question,
            domain=getattr(ask_request, "domain", None),
            ward_scope=getattr(ask_request, "ward_scope", None) or "Le Chan",
        )
        # Prefer FAQ UX: keep procedure only as short reference when FAQ exists.
        if matched_faqs and procedure_detail:
            procedure_detail = {
                "id": procedure_detail.get("id"),
                "name": procedure_detail.get("name"),
                "department": procedure_detail.get("department"),
                "reference_only": True,
                "summary": procedure_summary or procedure_detail.get("name"),
            }
        if not _has_sufficient_legal_evidence(ask_request.question, retrieval):
"""
count1 = st.count(old1)
if count1:
    st = st.replace(old1, new1)
    print(f"OK: integrated FAQ into evidence path x{count1}")
else:
    print("WARN: evidence path pattern not found")

# Ensure AskResponse constructions include faqs=matched_faqs where possible.
# Patch common return patterns by adding faqs after recommended_forms if present.
# Safer: monkey-patch by ensuring local variable exists and attach in constructors.

# For offline/local response builder around final AskResponse
# Look for recommended_forms=recommended_forms, and nearby AskResponse(
import re
# Add faqs= to AskResponse(...) calls that already pass procedure_detail=
# only if not already present nearby.

def inject_faqs(src: str) -> str:
    # If a block already has matched_faqs and AskResponse with procedure_detail, add faqs=
    pattern = re.compile(
        r"(AskResponse\([^\)]*?recommended_forms=recommended_forms,)(?!\s*faqs=)",
        re.S,
    )
    def repl(m):
        return m.group(1) + "\n            faqs=matched_faqs if 'matched_faqs' in locals() else None,"
    out, n = pattern.subn(repl, src)
    print(f"OK: inject faqs into AskResponse x{n}")
    return out

st = inject_faqs(st)

# Also integrate second procedure_match path (simple ask)
old2 = """        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match)
"""
# If remaining occurrences without matched_faqs, patch them
if "matched_faqs = _match_faqs_for_question" in st:
    # patch any remaining bare procedure_match pairs
    remaining = st.count(old2)
    if remaining:
        st = st.replace(
            old2,
            """        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match)
        matched_faqs = _match_faqs_for_question(
            ask_request.question,
            domain=getattr(ask_request, "domain", None),
            ward_scope=getattr(ask_request, "ward_scope", None) or "Le Chan",
        )
        if matched_faqs and procedure_detail:
            procedure_detail = {
                "id": procedure_detail.get("id"),
                "name": procedure_detail.get("name"),
                "department": procedure_detail.get("department"),
                "reference_only": True,
                "summary": procedure_summary or procedure_detail.get("name"),
            }
"""
        )
        print(f"OK: patched remaining procedure paths x{remaining}")

sp.write_text(st, encoding="utf-8", newline="\n")
print("search.py updated")
