"""Repair obvious UTF-8-as-Latin-1 mojibake in allow-listed files."""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "api" / "legal_crawl_service.py",
    ROOT / "api" / "crawlers" / "source_registry.py",
    ROOT / "frontend" / "src" / "app" / "(dashboard)" / "admin-control" / "page.tsx",
    ROOT / "notebook_data" / "legal-golden-set.json",
)
MARKERS = ("\u00c3", "\u00c2", "\u00c4", "\u00e1\u00ba", "\u00e1\u00bb", "\u00c6", "\u00e2\u20ac")


def marker_count(value: str) -> int:
    return sum(value.count(marker) for marker in MARKERS)


def repair(value: str) -> str:
    repaired_lines: list[str] = []
    for line in value.splitlines(keepends=True):
        if marker_count(line) < 2:
            repaired_lines.append(line)
            continue
        try:
            candidate = line.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            repaired_lines.append(line)
            continue
        repaired_lines.append(candidate if marker_count(candidate) < marker_count(line) else line)
    return "".join(repaired_lines)


def repair_crawler_prompt(value: str) -> str:
    """Use an ASCII prompt so mixed-encoding legacy text cannot reach the LLM."""
    prompt = '''prompt = f"""You are a Vietnamese administrative-law document reviewer.
Analyze the candidate metadata and the first part of its content. Return JSON only.

Title: {title}
Law number: {law_number}
Scope: {candidate.get('scope', 'unknown')}
Content excerpt:
{content_text[:3000]}

Allowed domain values: ho_tich_chung_thuc, dat_dai_xay_dung, cu_tru_an_ninh,
khieu_nai_to_cao_xu_phat, an_sinh_y_te_giao_duc.
Allowed scope values: central, haiphong, local.
Allowed official_level values: official, reference, internal.
Allowed effective_status values: con_hieu_luc, het_hieu_luc, chua_co_hieu_luc, khong_ro.
Allowed duplicate_risk values: none, possible, likely.
Return this schema: {
  "domain": "...", "scope": "...", "official_level": "...",
  "effective_status": "...", "duplicate_risk": "...",
  "confidence": 0.0, "reasons": ["short reason"]
}

Never invent a law number, effective date, legal status, deadline, fee, or authority.
Use low confidence when the metadata or content is incomplete. Do not use markdown.
"""'''
    return re.sub(r"prompt = f\"\"\".*?\"\"\"", prompt, value, count=1, flags=re.DOTALL)


def main() -> int:
    changed = []
    for path in TARGETS:
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        fixed = repair(original)
        if path.name == "legal_crawl_service.py":
            fixed = repair_crawler_prompt(fixed)
        if fixed != original:
            path.write_text(fixed, encoding="utf-8", newline="")
            changed.append(str(path))
    print({"changed": changed, "count": len(changed)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
