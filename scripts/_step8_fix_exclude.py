from pathlib import Path
p = Path("scripts/legal_search_server.py")
t = p.read_text(encoding="utf-8")

# Fix 1: line ~1029 - replace bindparams approach
old1 = ''').bindparams(bindparam("exclude_chunk_ids", expanding=True))
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(_normalized_terms(haystack).count(t) >= 1 for t in terms):
                matched_rows.append(row)
        return matched_rows'''

new1 = ''').execution_options(stream_results=False)
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(_normalized_terms(haystack).count(t) >= 1 for t in terms):
                matched_rows.append(row)
        return matched_rows'''

if old1 in t:
    t = t.replace(old1, new1, 1)
    print("FIXED 1")
else:
    print("WARN: FIX 1 not found exact")

# Fix 2: line ~1166
old2 = ''').bindparams(bindparam("exclude_chunk_ids", expanding=True))
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(_normalized_terms(haystack).count(p) >= 1 for p in normalized_phrases):
                matched_rows.append(row)
        return matched_rows'''

new2 = ''').execution_options(stream_results=False)
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]

        matched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not _is_current(row, as_of):
                continue
            haystack = " ".join(
                _normalized_terms(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("article_title", "chunk_heading", "content", "document_title")
                    )
                )
            )
            if any(_normalized_terms(haystack).count(p) >= 1 for p in normalized_phrases):
                matched_rows.append(row)
        return matched_rows'''

if old2 in t:
    t = t.replace(old2, new2, 1)
    print("FIXED 2")
else:
    print("WARN: FIX 2 not found exact")

p.write_text(t, encoding="utf-8", newline="\n")
print("DONE")
