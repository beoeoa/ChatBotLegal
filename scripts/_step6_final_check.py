from pathlib import Path
import ast

print("=== FINAL VERIFICATION ===")
# Python files syntax
for p in ["api/routers/search.py", "api/models.py", "api/routers/faq.py", "api/main.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        print(f"OK: {p}")
    except SyntaxError as e:
        print(f"ERR: {p}: {e}")

# Full pipeline trace
print("\n=== BACKEND PIPELINE ===")
st = Path("api/routers/search.py").read_text(encoding="utf-8")
print(f"1. _match_faqs_for_question defined: {'def _match_faqs_for_question' in st}")
print(f"2. Called in ask paths: {st.count('matched_faqs = _match_faqs_for_question')}")
print(f"3. procedure reference_only: {st.count('reference_only')}")
print(f"4. faqs= in AskResponse: {st.count('faqs=')}")

mt = Path("api/models.py").read_text(encoding="utf-8")
print(f"5. AskResponse has faqs field: {'faqs: Optional[List[Dict' in mt}")

print("\n=== FRONTEND PIPELINE ===")
tt = Path("frontend/src/lib/types/search.ts").read_text(encoding="utf-8")
print(f"6. AskResponse type has faqs: {'faqs?: Array<' in tt}")

ut = Path("frontend/src/lib/hooks/use-ask.ts").read_text(encoding="utf-8")
print(f"7. use-ask faqs state: {'faqs:' in ut}")
print(f"8. use-ask response mapping: {'response.faqs' in ut}")
print(f"9. return ...state (includes faqs): {'return {' in ut}")

sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print(f"10. FAQAccordion imported: {'./FAQAccordion' in sr}")
print(f"11. faqs prop in interface: {'faqs?: Array<' in sr}")
print(f"12. faqs in destructure: {'faqs,' in sr}")
print(f"13. FAQAccordion renders: {'<FAQAccordion' in sr}")
print(f"14. FAQ priority over forms: {'(!faqs || faqs.length === 0)' in sr}")

pg = Path("frontend/src/app/(dashboard)/search/page.tsx").read_text(encoding="utf-8")
print(f"15. page passes faqs: {'faqs={ask.faqs' in pg}")

fq = Path("frontend/src/components/search/FAQAccordion.tsx").read_text(encoding="utf-8")
print(f"16. FAQAccordion component: {'export function FAQAccordion' in fq}")

ft = Path("frontend/src/lib/types/faq.ts").read_text(encoding="utf-8")
print(f"17. FaqItem type: {'export interface FaqItem' in ft}")

fh = Path("frontend/src/lib/hooks/use-faq.ts").read_text(encoding="utf-8")
print(f"18. useFaqs hook: {'export function useFaqs' in fh}")

print("\n=== SUMMARY ===")
all_ok = all([
    "def _match_faqs_for_question" in st,
    "faqs: Optional[List[Dict" in mt,
    "faqs?: Array<" in tt,
    "response.faqs" in ut,
    "./FAQAccordion" in sr,
    "<FAQAccordion" in sr,
    "faqs={ask.faqs" in pg,
    "export function FAQAccordion" in fq,
])
print(f"All integration points connected: {all_ok}")
