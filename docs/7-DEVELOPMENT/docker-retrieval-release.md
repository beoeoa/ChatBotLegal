# Docker retrieval release fallback

The release retrieval service uses the VNLegal-LAL vector index when the
persisted Chroma data can be opened by the bundled runtime. If an older index
is incompatible, the service remains ready in `vector_index_available=false`
mode and searches the reviewed PostgreSQL legal corpus through its existing
lexical, effectivity and domain filters.

This fallback does not invent citations and does not reuse conversation
evidence. It is an availability measure only; release benchmarking should be
run again after a compatible vector index has been built. Rollback is restoring
the prior image or supplying a compatible `chroma_store` directory; no legal
source records are modified.
