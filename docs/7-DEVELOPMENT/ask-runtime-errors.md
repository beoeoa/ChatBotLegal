# Ask runtime errors

The legal Q&A graph requires the VNLegal-LAL retrieval service before it calls
the selected language model. In the Docker pilot, the API reaches this Windows
host service through `host.docker.internal:8765`.

Start retrieval with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_legal_search.ps1
```

Start the complete Docker application and retrieval service with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_all.ps1
```

Verify all Q&A dependencies with:

```text
GET http://127.0.0.1:5055/api/search/health
```

The endpoint reports `legal_retrieval` and `ollama` independently. Cloud Q&A
requires legal retrieval; local Q&A requires both components.

Non-streaming Q&A errors use structured FastAPI details:

```json
{
  "detail": {
    "code": "LEGAL_RETRIEVAL_UNAVAILABLE",
    "message": "...",
    "how_to_fix": ["..."],
    "retryable": true
  }
}
```

The search UI renders this detail below the Ask button. Do not replace it with
the generic Axios message, because that hides the failing component and repair
steps.
