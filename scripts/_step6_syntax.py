from pathlib import Path
import re, ast

# Syntax check search.py and models.py and faq.py
for p in ["api/routers/search.py", "api/models.py", "api/routers/faq.py", "api/main.py"]:
    text = Path(p).read_text(encoding="utf-8")
    try:
        ast.parse(text)
        print(f"SYNTAX OK: {p}")
    except SyntaxError as e:
        print(f"SYNTAX ERROR: {p}: {e}")

# Check matched_faqs usage
st = Path("api/routers/search.py").read_text(encoding="utf-8")
print("matched_faqs count", st.count("matched_faqs"))
print("faqs=matched_faqs count", st.count("faqs=matched_faqs"))
print("_match_faqs_for_question count", st.count("_match_faqs_for_question"))
# show a few AskResponse injections
for m in re.finditer(r"faqs=matched_faqs[^\n]*", st):
    print(" ", m.group(0)[:120])
