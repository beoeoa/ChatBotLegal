from scripts.collect_feature3_release_evidence import chunk_paths


def test_extracts_unique_search_chunks_only():
    html = '<script src="/_next/static/chunks/a.js"></script><link href="/_next/static/chunks/a.js"><script src="/_next/static/chunks/b.js?ignored"></script><script src="/_next/static/chunks/legacy.js" noModule></script>'

    assert chunk_paths(html) == ["/_next/static/chunks/a.js", "/_next/static/chunks/b.js"]
