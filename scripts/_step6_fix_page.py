from pathlib import Path
p = Path("frontend/src/app/(dashboard)/search/page.tsx")
text = p.read_text(encoding="utf-8")
old = """                  groundingStatus={ask.groundingStatus}
                  role={role}
                  showRagTrace={showRagTrace}
                />"""
new = """                  groundingStatus={ask.groundingStatus}
                  role={role}
                  showRagTrace={showRagTrace}
                  faqs={ask.faqs || undefined}
                />"""
if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8", newline="\n")
    print("OK: page passes faqs")
else:
    print("ERROR: pattern not found")
    if "showRagTrace={showRagTrace}" in text:
        text = text.replace(
            "showRagTrace={showRagTrace}",
            "showRagTrace={showRagTrace}\n                  faqs={ask.faqs || undefined}",
            1,
        )
        p.write_text(text, encoding="utf-8", newline="\n")
        print("OK: page passes faqs (alt)")
    else:
        print("FAIL")
