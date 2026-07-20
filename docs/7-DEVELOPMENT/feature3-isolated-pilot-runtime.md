# Feature 3 isolated pilot runtime

Feature 3 validation uses an API/frontend container isolated from production by
its own SurrealDB namespace and database. It binds only to localhost ports
`5056` (API) and `8503` (frontend); production continues on `5055` and `8502`.

## Cloud configuration

`scripts/configure_feature3_pilot.py` is dry-run by default. With explicit
operator approval and `--apply`, it copies only the current default chat
model, its encrypted credential record, and a narrowed `default_models`
record. It never exports a key, writes a key to an evidence file, or changes
the production namespace. The pilot must have the same encryption key to use
the encrypted credential through the normal application credential layer.

The verifier stores only record identifiers, provider/model metadata and a
one-way fingerprint. `/ready` is the authoritative confirmation that database,
retrieval, cloud model and optional Ollama fallback are usable.

## Pilot test accounts

`scripts/create_feature3_pilot_accounts.py` creates one citizen and one
officer in the pilot only. Passwords and bearer tokens are generated in memory,
then stored once in a mode-0600 private dotenv file. The script does not print
credentials and refuses to overwrite an existing credential file.

`scripts/verify_feature3_pilot_progress.py` uses that protected file and emits
only HTTP status, event names, timing and event counts. It deliberately drops
all SSE `data:` payloads, so questions, responses, citations, identities,
tokens and provider exceptions never enter release evidence.

## CUDA comparison

When production retrieval must remain on CPU, start a separate host retrieval
process on a different loopback port with the verified CUDA venv. Point only
the pilot `LEGAL_SEARCH_URL` to that port. This permits an apples-to-apples
comparison without restarting or changing the production service.

The CUDA comparison is release evidence, not a quality approval. The 30-case
legal review remains an expert-only gate and cannot be auto-approved.
