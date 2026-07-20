from pathlib import Path
pt = Path("frontend/src/app/(dashboard)/search/page.tsx").read_text(encoding="utf-8")
# Find StreamingResponse usage
idx = pt.find("<StreamingResponse")
print(repr(pt[idx:idx+700]))
print("---")
print("faqs prop count", pt.count("faqs="))
print("ask.faqs count", pt.count("ask.faqs"))
