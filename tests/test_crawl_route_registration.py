from api.main import app


def test_candidate_first_crawler_routes_are_not_shadowed_by_legacy_router():
    # FastAPI 0.140 keeps included routers as lazy route branches, so
    # ``app.routes`` no longer consists only of concrete routes.  OpenAPI is
    # the stable, public view of every registered path.
    openapi_paths = app.openapi()["paths"]
    paths = set(openapi_paths)

    assert "/api/legal/crawl/sources" in paths
    assert "/api/legal/crawl/scan" in paths
    assert "/api/legal/crawl/preview" in paths
    assert "/api/legal/proposals" in paths
    assert "/api/legal/proposals/candidates" in paths
    assert "/api/legal/proposals/preview" not in paths
    assert "/api/legal/proposals/weekly-monitor" not in paths
    assert "/api/legacy/legal/crawl/sources" in paths
    assert "/api/legacy/legal/crawl/run-all" in paths
    assert {"get", "post"}.issubset(openapi_paths["/api/legal/crawl/sources"])
    assert "delete" in openapi_paths["/api/legal/crawl/sources/{source_id}"]
