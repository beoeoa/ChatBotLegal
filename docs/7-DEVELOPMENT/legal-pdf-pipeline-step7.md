# Legal PDF pipeline (B??c 7)

## Font and encoding

Internal legal-PDF exports embed `assets/fonts/noto-sans/NotoSans-VF.ttf`. The font is licensed under the **SIL Open Font License 1.1**; the accompanying license is retained at `assets/fonts/noto-sans/OFL.txt`. PDF core fonts are not used because they do not reliably contain Vietnamese glyphs.

The generator receives Python Unicode strings from the indexed document record. Before export, it rejects likely mojibake markers (`?`, `?`, `?`, `?`, `??`, `??`) rather than emitting a corrupted legal extract.

## Original vs internal extract

- If `data/uploads/pdfs/{doc_id}.pdf` exists, the API streams that original file without rendering it again. It returns `Content-Type: application/pdf`, attachment disposition, automatic `Content-Length` from `FileResponse`, `Cache-Control`, `X-Legal-Pdf-Origin: original-file`, and latency telemetry.
- Otherwise the retrieval service creates an internal extract labelled **?B?N TR?CH XU?T T? KHO H? TH?NG?** and explicitly says it is not a gazette/original issuing-agency PDF.

## Performance

Document metadata/article index has a five-minute in-memory TTL cache. Content is fetched only for `?article=N` or `?include_content=true`. Internal PDF exports are written to `LEGAL_PDF_ARTIFACT_DIR` (default `<LEGAL_DATA_ROOT>/pdf_artifacts`) and reused for `LEGAL_PDF_CACHE_MAX_AGE_SECONDS` (default 24h). Concurrent requests for the same artifact share one generator.

Responses include cache/origin/timing headers. The viewer shows non-blocking PDF preparation state and local view/download latency; failure is surfaced as a toast rather than an uncaught client error.

## Internal document viewer

Ask citations use the canonical internal route `/legal-documents/{doc_id}?article={article_number}` as the primary link. The original `source_url` is preserved only as provenance metadata and is not the default user-facing navigation target, because external catalog URLs can expire or return 404.

The viewer first fetches document metadata and a lightweight article index from `GET /api/legal/docs/{doc_id}`. It lazy-loads indexed content only when a cited article is requested, then scrolls to the article anchor and applies a light outline/background. A cited clause or point is highlighted only when that exact marker occurs in the imported text; otherwise the viewer highlights only the verified article.

Downloads keep the same source distinction: `data/uploads/pdfs/{doc_id}.pdf` is served as the original file when available; otherwise the system exports a cached, Unicode-embedded internal extract labelled **?B?n tr?ch xu?t t? kho h? th?ng?**. It must not be presented as an official gazette or original issuing-agency file.

