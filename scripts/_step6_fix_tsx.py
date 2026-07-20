from pathlib import Path

# Fix TSX files - remove Python docstring style
for p in [
    Path("frontend/src/components/search/FAQAccordion.tsx"),
    Path("frontend/src/lib/types/faq.ts"),
    Path("frontend/src/lib/hooks/use-faq.ts"),
]:
    text = p.read_text(encoding="utf-8")
    if text.startswith('"""'):
        # remove first docstring
        end = text.find('"""', 3)
        if end > 0:
            text = text[end+3:].lstrip("\n")
            p.write_text(text, encoding="utf-8", newline="\n")
            print(f"fixed docstring in {p}")
    else:
        print(f"ok {p}")

print("--- preview FAQAccordion head ---")
print(Path("frontend/src/components/search/FAQAccordion.tsx").read_text(encoding="utf-8")[:200])
