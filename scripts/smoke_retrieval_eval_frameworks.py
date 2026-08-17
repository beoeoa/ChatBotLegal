#!/usr/bin/env python
"""Prove the pinned Ragas and DeepEval adapters call local Ollama only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.retrieval_eval_generation import (  # noqa: E402
    build_generation_provenance,
    require_local_ollama_url,
)


def _ollama_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - URL is local-only
        return json.loads(response.read().decode("utf-8"))


def _model_digest(base_url: str, model: str) -> str:
    details = _ollama_json(base_url, "/api/show", {"model": model})
    digest = str(details.get("details", {}).get("digest") or details.get("digest") or "")
    if digest:
        return digest
    tags = _ollama_json(base_url, "/api/tags").get("models", [])
    for item in tags:
        if item.get("name") == model or item.get("model") == model:
            return str(item.get("digest", ""))
    raise RuntimeError(f"cannot resolve digest for local Ollama model {model}")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--generation-model", default="qwen2.5:3b")
    parser.add_argument("--embedding-model", default="nomic-embed-text:latest")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "reports/retrieval-release-v2/eval-framework-local-smoke-v1.json",
    )
    args = parser.parse_args()
    base_url = require_local_ollama_url(args.ollama_url)

    import deepeval
    import ragas
    from deepeval.models import OllamaModel
    from langchain_core.prompt_values import StringPromptValue
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    version = str(_ollama_json(base_url, "/api/version").get("version", ""))
    generation_digest = _model_digest(base_url, args.generation_model)
    embedding_digest = _model_digest(base_url, args.embedding_model)
    provenance = build_generation_provenance(
        ragas_version=ragas.__version__,
        deepeval_version=deepeval.__version__,
        ollama_version=version,
        generation_model=args.generation_model,
        generation_model_digest=generation_digest,
        embedding_model=args.embedding_model,
        embedding_model_digest=embedding_digest,
        ollama_url=base_url,
    )

    prompt = "Chỉ trả về chính xác chuỗi LOCAL_OK, không thêm ký tự khác."
    ragas_llm = LangchainLLMWrapper(
        ChatOllama(model=args.generation_model, base_url=base_url, temperature=0)
    )
    ragas_result = ragas_llm.generate_text(StringPromptValue(text=prompt), temperature=0)
    ragas_text = str(ragas_result.generations[0][0].text).strip()
    ragas_embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(model=args.embedding_model, base_url=base_url)
    )
    vector = ragas_embeddings.embed_query("kiểm tra embedding cục bộ")

    deepeval_model = OllamaModel(
        model=args.generation_model,
        base_url=base_url,
        temperature=0,
    )
    deepeval_result = deepeval_model.generate(prompt)
    deepeval_text = str(deepeval_result[0] if isinstance(deepeval_result, tuple) else deepeval_result).strip()

    if "LOCAL_OK" not in ragas_text or "LOCAL_OK" not in deepeval_text:
        raise RuntimeError("one or more local framework smoke responses were invalid")
    if not vector or not all(isinstance(item, float) for item in vector):
        raise RuntimeError("local embedding smoke did not return a numeric vector")

    report = {
        "schema_version": "retrieval-eval-framework-local-smoke-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "api_cost_usd": 0,
        "provenance": provenance,
        "checks": {
            "ragas_local_generation": {"passed": True, "response_sha256": _sha(ragas_text)},
            "ragas_local_embedding": {"passed": True, "dimension": len(vector)},
            "deepeval_local_generation": {"passed": True, "response_sha256": _sha(deepeval_text)},
            "external_endpoint_configured": False,
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = _sha(canonical)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "api_cost_usd": 0, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
