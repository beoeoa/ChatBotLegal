from pathlib import Path
import ast

files = [
    "api/routers/search.py",
    "api/models.py",
    "api/routers/faq.py",
    "api/main.py",
    "frontend/src/lib/types/search.ts",
    "frontend/src/lib/hooks/use-ask.ts",
    "frontend/src/components/search/StreamingResponse.tsx",
    "frontend/src/app/(dashboard)/search/page.tsx",
    "frontend/src/components/search/FAQAccordion.tsx",
    "frontend/src/lib/types/faq.ts",
    "frontend/src/lib/hooks/use-faq.ts",
]

print("=== SYNTAX CHECK ===")
for p in files:
    if p.endswith(".py"):
        try:
            text = Path(p).read_text(encoding="utf-8")
            ast.parse(text)
            print(f"OK: {p}")
        except SyntaxError as e:
            print(f"ERR: {p}: {e}")
    else:
        print(f"SKIP (TS): {p}")

print("\n=== KEY INTEGRATION POINTS ===")
# search.py
st = Path("api/routers/search.py").read_text(encoding="utf-8")
print(f"_match_faqs_for_question: {st.count('_match_faqs_for_question')}")
print(f"matched_faqs: {st.count('matched_faqs')}")
print(f"faqs=: {st.count('faqs=')}")

# models.py
mt = Path("api/models.py").read_text(encoding="utf-8")
print(f"models faqs field: {'faqs:' in mt}")

# use-ask.ts
ut = Path("frontend/src/lib/hooks/use-ask.ts").read_text(encoding="utf-8")
print(f"use-ask faqs state: {'faqs:' in ut}")
print(f"use-ask faqs mapping: {'response.faqs' in ut}")

# StreamingResponse
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print(f"Streaming FAQAccordion: {'FAQAccordion' in sr}")
print(f"Streaming faqs prop: {'faqs,' in sr}")
print(f"Streaming faqs interface: {'faqs?: Array<' in sr}")

# page.tsx
pt = Path("frontend/src/app/(dashboard)/search/page.tsx").read_text(encoding="utf-8")
print(f"page faqs prop: {'faqs={ask.faqs}' in pt}")

# FAQAccordion
fq = Path("frontend/src/components/search/FAQAccordion.tsx").read_text(encoding="utf-8")
print(f"FAQAccordion component: {'export function FAQAccordion' in fq}")

# Check procedure reference_only logic
print(f"\nprocedure reference_only: {'reference_only' in st}")
print(f"faq priority over procedure: {'matched_faqs and procedure_detail' in st}")
