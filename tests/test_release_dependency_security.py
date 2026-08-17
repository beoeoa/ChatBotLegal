from pathlib import Path
import ast
import json


def test_release_dependency_constraints_are_explicit() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    retrieval = Path("requirements/legal-retrieval-docker.txt").read_text(
        encoding="utf-8"
    )

    assert '"pillow>=12.3.0,<13.0"' in pyproject
    assert 'override-dependencies = ["pillow>=12.3.0,<13.0"]' in pyproject
    assert '"lxml-html-clean>=0.4.5"' in pyproject
    assert "chromadb==1.5.9" in retrieval


def test_release_image_excludes_uninstalled_vendored_crawl4ai_metadata() -> None:
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert "external/crawl4ai" in dockerignore


def test_release_runtime_removes_unused_vulnerable_npm_trees() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "/usr/lib/node_modules/npm" in dockerfile
    assert "nodejs_wheel/lib/node_modules/npm" in dockerfile
    assert "rm -rf" in dockerfile
    assert "patch_moviepy_pillow_metadata.py" in dockerfile


def test_chromadb_rce_vex_matches_non_server_runtime_architecture() -> None:
    source = Path("scripts/legal_search_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    route_paths = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "post", "put", "patch", "delete"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    vex = json.loads(
        Path(
            "deploy/security/chromadb-persistent-client.openvex.json"
        ).read_text(encoding="utf-8")
    )
    statement = vex["statements"][0]

    assert "chromadb.PersistentClient" in source
    assert "chromadb.server" not in source
    assert not any(path.startswith("/api/v2/") for path in route_paths)
    assert statement["vulnerability"]["name"] == "CVE-2026-45829"
    assert statement["products"] == [{"@id": "pkg:pypi/chromadb@1.5.9"}]
    assert statement["status"] == "not_affected"
    assert statement["justification"] == "vulnerable_code_not_in_execute_path"
