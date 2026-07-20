from api.main import app


def test_candidate_first_crawler_routes_are_not_shadowed_by_legacy_router():
    paths = {route.path for route in app.routes}

    assert "/api/legal/crawl/sources" in paths
    assert "/api/legal/crawl/scan" in paths
    assert "/api/legacy/legal/crawl/sources" in paths
    assert "/api/legacy/legal/crawl/run-all" in paths
