# Legal retrieval request compatibility

## Incident

The answer API sent `scope_filter` to the local VNLegal-LAL service, but the
retrieval service's Pydantic `SearchRequest` did not declare that field. The
request was rejected before search execution, appearing to the frontend as
`LEGAL_RETRIEVAL_UNAVAILABLE` with HTTP 503.

## Fix

`scope_filter` is now an optional, bounded request field. It is preserved in
the trace for routing diagnostics while `domain` remains the authoritative
legal-domain filter. This keeps backend and retrieval-service versions
compatible without weakening effective-date or candidate filtering.

## Verification

The exact Vietnamese question payload now returns HTTP 200 from
`POST http://127.0.0.1:8765/search`, with 8 retrieved results and
`trace.scope_filter = "local"`. Health reports the VNLegal-LAL collection as
healthy before requests are sent.

## Retrieval ranking check

For the standard birth-registration question with the
`ho_tich_chung_thuc` domain, the first results are now the active
`60/2014/QH13` provisions at Articles 16, 15, and 13, followed by relevant
`123/2015/NĐ-CP` provisions. The earlier result set was not empty; its topic
ranking allowed unrelated active documents to outrank the direct
birth-registration provisions, which caused the answer engine to incorrectly
describe the law as missing.
