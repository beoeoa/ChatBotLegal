from pathlib import Path
# Fix faq.py: pydantic Field regex is deprecated in v2, use pattern
p = Path("api/routers/faq.py")
text = p.read_text(encoding="utf-8")
old = 'review_status: str = Field(default="draft", regex="^(draft|approved|rejected)$")'
new = 'review_status: str = Field(default="draft", pattern="^(draft|approved|rejected)$")'
if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8", newline="\n")
    print("OK: fixed Field regex->pattern")
else:
    print("SKIP or already fixed")
print("faq.py has require_admin:", "require_admin" in text)
print("faq.py has list_faqs:", "list_faqs" in text)
