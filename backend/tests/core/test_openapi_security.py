"""The exported OpenAPI document declares how to authenticate (main.py hook)."""

from __future__ import annotations


async def test_openapi_declares_bearer_and_widget_schemes(app):
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]

    assert schemes["BearerAuth"]["type"] == "http"
    assert schemes["BearerAuth"]["scheme"] == "bearer"
    # The description must cover BOTH credentials that ride this header.
    assert "sk_stept_" in schemes["BearerAuth"]["description"]
    assert "access token" in schemes["BearerAuth"]["description"]

    assert schemes["WidgetToken"]["type"] == "apiKey"
    assert schemes["WidgetToken"]["in"] == "header"
    assert schemes["WidgetToken"]["name"] == "X-Widget-Token"

    # Global default: generated clients send Authorization everywhere…
    assert schema["security"] == [{"BearerAuth": []}]


async def test_public_trees_opt_out_of_the_global_requirement(app):
    schema = app.openapi()
    paths = schema["paths"]

    # …except the trees that authenticate by other means (or not at all).
    assert paths["/api/v1/auth/login"]["post"]["security"] == []
    assert paths["/api/v1/auth/signup"]["post"]["security"] == []
    assert paths["/api/v1/healthz"]["get"]["security"] == []

    widget_paths = [p for p in paths if p.startswith("/api/widget")]
    channel_paths = [p for p in paths if p.startswith("/api/channels")]
    portal_paths = [p for p in paths if p.startswith("/portal")]
    assert widget_paths and channel_paths and portal_paths
    for path in (*widget_paths, *channel_paths, *portal_paths):
        for operation in paths[path].values():
            assert operation["security"] == [], path

    # A workspace-scoped route carries no override — it inherits the global
    # bearer requirement.
    workspace_path = next(p for p in paths if p.startswith("/api/v1/w/"))
    for operation in paths[workspace_path].values():
        assert "security" not in operation


async def test_openapi_hook_is_idempotent_under_the_schema_cache(app):
    first = app.openapi()
    second = app.openapi()
    assert second is first  # FastAPI's cache still works through the wrapper
    assert second["security"] == [{"BearerAuth": []}]
    assert set(second["components"]["securitySchemes"]) == {"BearerAuth", "WidgetToken"}
