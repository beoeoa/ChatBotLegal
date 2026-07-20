from pathlib import Path
import ast

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")

# Fix has_where_to_submit
old = '''def has_where_to_submit(answer: str) -> bool:
    keywords = ["nộp", "ubnd", "phường", "quận", "một cửa", "cơ quan", "sở", "văn phòng", "chi nhánh"]
    f = fold(answer)
    return any(k in f for k in keywords)'''
new = '''def has_where_to_submit(answer: str) -> bool:
    keywords = ["nop", "ubnd", "phuong", "quan", "mot cua", "co quan", "so ", "van phong", "chi nhanh", "uy ban"]
    f = fold(answer)
    return any(k in f for k in keywords)'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK where_to_submit")
else:
    print("WARN where_to_submit")

old = '''def has_document_checklist(answer: str) -> bool:
    keywords = ["giấy tờ", "hồ sơ", "tờ khai", "cccd", "cmnd", "chứng minh", "giấy chứng", "bản sao", "bản chính"]
    f = fold(answer)
    return any(k in f for k in keywords)'''
new = '''def has_document_checklist(answer: str) -> bool:
    keywords = ["giay to", "ho so", "to khai", "cccd", "cmnd", "chung minh", "giay chung", "ban sao", "ban chinh"]
    f = fold(answer)
    return any(k in f for k in keywords)'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK document_checklist")
else:
    print("WARN document_checklist")

# Fix score_officer expect_block keywords
old = '''        blocked = (
            domain_mismatch is True
            or "sai lĩnh vực" in fold(answer)
            or "không thuộc" in fold(answer)
            or "chuyển lĩnh vực" in fold(answer)
            or "không phụ trách" in fold(answer)
            or "domain_mismatch" in fold(str(resp.get("raw") or {}))
        )
        scores["hard_block"] = blocked
        scores["has_suggestion"] = bool(resp.get("suggested_domain")) or "gợi ý" in fold(answer) or "chuyển" in fold(answer) or blocked'''
new = '''        f = fold(answer)
        blocked = (
            domain_mismatch is True
            or "sai linh vuc" in f
            or "khong thuoc" in f
            or "chuyen linh vuc" in f
            or "khong phu trach" in f
            or "vui long chon" in f
            or "domain_mismatch" in fold(str(resp.get("raw") or {}))
        )
        scores["hard_block"] = blocked
        scores["has_suggestion"] = bool(resp.get("suggested_domain")) or "goi y" in f or "chuyen sang" in f or "chuyen" in f or blocked'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK hard_block keywords")
else:
    print("WARN hard_block keywords")

# Improve has_citation_link to catch "Dieu 651" style and law numbers already present
old = '''def has_citation_link(citations: list | None, answer: str) -> bool:
    """Check if answer has natural legal citation or citations array has links."""
    if citations:
        for c in citations:
            if c.get("source_url") or c.get("fallback_search_url") or c.get("law_number"):
                return True
    # Natural citation patterns
    patterns = [
        r"Ngh[iị] đ[iị]nh\s+\d+/\d{4}",
        r"Lu[aậ]t\s+\d+/\d{4}",
        r"Th[oô]ng tư\s+\d+/\d{4}",
        r"\d+/\d{4}/[NQĐTĐCPB]+",
        r"Đi[eề]u\s+\d+",
    ]
    return any(re.search(p, answer or "", flags=re.I) for p in patterns)'''
new = '''def has_citation_link(citations: list | None, answer: str) -> bool:
    """Check if answer has natural legal citation or citations array has links."""
    if citations:
        for c in citations:
            if c.get("source_url") or c.get("fallback_search_url") or c.get("law_number") or c.get("article_number"):
                return True
    # Natural citation patterns (accented + folded)
    patterns = [
        r"Ngh[iị][ \u00a0]*đ[iị]nh\s+\d+/\d{4}",
        r"Lu[aậ]t\s+\d+/\d{4}",
        r"Th[oô]ng\s*tư\s+\d+/\d{4}",
        r"\d+/\d{4}/[A-Za-zĐđ\-]+",
        r"Đi[eề]u\s+\d+",
        r"Dieu\s+\d+",
        r"Luat\s+\d+/\d{4}",
        r"Nghi\s*dinh\s+\d+/\d{4}",
        r"Thong\s*tu\s+\d+/\d{4}",
    ]
    if any(re.search(p, answer or "", flags=re.I) for p in patterns):
        return True
    f = fold(answer or "")
    return bool(re.search(r"dieu\s+\d+", f) or re.search(r"\d+/\d{4}/", f) or re.search(r"luat\s+\d+", f) or re.search(r"nghi\s*dinh\s+\d+", f))'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK citation patterns")
else:
    print("WARN citation patterns")

# Also make has_source_link pass if has_citation is true is already there.
# Improve score_citizen has_source_link to treat natural citation as source
old = '''    scores["has_source_link"] = bool(citations) or "vbpl" in fold(answer) or "nguồn" in fold(answer) or scores["has_citation"]'''
new = '''    scores["has_source_link"] = bool(citations) or "vbpl" in fold(answer) or "nguon" in fold(answer) or scores["has_citation"]'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK source_link")
else:
    # maybe already partially different
    if 'scores["has_source_link"]' in text:
        text = text.replace(
            'scores["has_source_link"] = bool(citations) or "vbpl" in fold(answer) or "nguồn" in fold(answer) or scores["has_citation"]',
            'scores["has_source_link"] = bool(citations) or "vbpl" in fold(answer) or "nguon" in fold(answer) or scores["has_citation"]',
            1,
        )
        print("OK source_link alt")
    else:
        print("WARN source_link")

p.write_text(text, encoding="utf-8", newline="\n")
ast.parse(p.read_text(encoding="utf-8"))
print("syntax OK")
