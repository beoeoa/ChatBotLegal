# Two-tier legal retrieval trace

The default legal search uses the reviewed Hai Phong/commune scope collection
(`legal_chunks_vnlegal_lal_haiphong`). It is the fast path for the five pilot
domains and local procedures.

On 2026-07-14 the reviewed scope was expanded from 132,656 to 159,530 chunks
by adding 1,800 active, source-backed documents already classified into the
pilot domains. The immutable pre-change manifest is stored at
`J:\legal-chatbot-data\backups\legal_scope\core_scope_before_expand_2026-07-14.json`.
No source embedding was recomputed; the fast Chroma collection was rebuilt by
copying the existing VNLegal-LAL vectors.

The UI uses broad routing labels. The retrieval service resolves them without
weakening the domain boundary: `dat_dai_xay_dung` maps to
`dat_dai_moi_truong` and `xay_dung_do_thi`; `an_sinh_y_te_giao_duc` maps to
`an_sinh_y_te` and `giao_duc_van_hoa`; `ho_tich_chung_thuc` maps to
`tu_phap_ho_tich`.

When the core tier has no results, low query overlap, or fewer than three
distinct active articles, the answer service retries once against the active
source collection (`legal_chunks_vnlegal_lal`). The expanded tier retains
effective-date checks, active document/article checks, legal-domain filtering,
deduplication, and excludes documents marked as another province. It does not
silently search the entire corpus on every question.

When `include_trace=true`, retrieval reports the selected `retrieval_tier`,
collection, fallback reason, candidate counts, effective-date filtering,
deduplicated sources, and sources passed to the answer model. Officer
`allowed_domains` remains an authorization boundary.
