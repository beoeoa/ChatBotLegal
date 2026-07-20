from pathlib import Path


def test_docker_build_allows_pilot_sse_without_enabling_it_by_default():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "ARG NEXT_PUBLIC_ASK_SSE_ENABLED=false" in dockerfile
    assert "ENV NEXT_PUBLIC_ASK_SSE_ENABLED=${NEXT_PUBLIC_ASK_SSE_ENABLED}" in dockerfile
