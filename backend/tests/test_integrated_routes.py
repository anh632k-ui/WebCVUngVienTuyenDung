from collections import Counter

from fastapi.routing import APIRoute

from main import app

EXPECTED_METHODS = {
    "/api/v1/admin/users": {"get"},
    "/api/v1/admin/users/{id}": {"patch"},
    "/api/v1/resumes": {"get"},
    "/api/v1/resumes/{id}": {"get", "delete"},
    "/api/v1/resumes/{id}/status": {"get"},
    "/api/v1/jobs": {"get"},
    "/api/v1/jobs/{id}": {"get", "delete"},
    "/api/v1/jobs/{id}/status": {"patch"},
    "/api/v1/jobs/{id}/leaderboard": {"get"},
    "/api/v1/matching": {"get"},
    "/api/v1/matching/{match_id}": {"get"},
    "/api/v1/matching/{match_id}/gap-analysis": {"get"},
}


def test_integrated_openapi_contains_every_completed_feature_route() -> None:
    paths = app.openapi()["paths"]
    for path, methods in EXPECTED_METHODS.items():
        assert path in paths
        assert set(paths[path]) == methods

    assert paths["/api/v1/jobs/{id}"]["get"]["tags"] == ["Jobs"]
    assert paths["/api/v1/jobs/{id}/leaderboard"]["get"]["tags"] == ["Matching"]


def test_integrated_app_has_no_duplicate_method_path_registrations() -> None:
    registrations = Counter(
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
        for method in route.methods
    )
    duplicates = {registration: count for registration, count in registrations.items() if count > 1}
    assert duplicates == {}
