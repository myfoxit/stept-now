"""Google Drive connector: folder recursion, export mime routing, raw-file 2MB
cap, PDF/DOCX recorded-skip (which must not block pruning), pagination, caps,
config validation. Token seam monkeypatched; all HTTP respx-mocked."""

from __future__ import annotations

import httpx
import respx

from tests.knowledge.test_connectors import (
    create_source,
    doc_text,
    get_source_json,
    list_docs,
    sync_now,
)

DRIVE = "https://www.googleapis.com/drive/v3"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class SeamConnection:
    """The attribute surface the connectors read off an IntegrationConnection."""

    def __init__(self, *, provider, connection_id="conn-g", meta=None):
        self.id = connection_id
        self.provider = provider
        self.status = "connected"
        self.meta = meta or {}


def patch_tokens_seam(monkeypatch, connection, token="seam-access-token"):
    """Fake BE-A's app.integrations.tokens seam (see docs/INTEGRATIONS-CONTRACTS.md)."""
    from app.integrations import tokens

    calls: list[tuple[str, str]] = []

    async def get_connection(session, workspace_id, connection_id):
        calls.append((workspace_id, connection_id))
        return connection

    async def get_valid_access_token(session, conn):
        return token

    monkeypatch.setattr(tokens, "get_connection", get_connection, raising=False)
    monkeypatch.setattr(tokens, "get_valid_access_token", get_valid_access_token, raising=False)
    return calls


def drive_file(file_id, name, mime, **extra):
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "webViewLink": f"https://drive.example.com/view/{file_id}",
        **extra,
    }


def files_page(files, next_token=None):
    body = {"files": files}
    if next_token:
        body["nextPageToken"] = next_token
    return httpx.Response(200, json=body)


def folder_route(folder_id, response):
    return respx.get(
        f"{DRIVE}/files", params={"q": f"'{folder_id}' in parents and trashed=false"}
    ).mock(return_value=response)


async def make_gdrive_source(client, ctx, *, config=None):
    return await create_source(
        client,
        ctx,
        type="gdrive",
        config={"connection_id": "conn-g", "folder_ids": ["root1"], **(config or {})},
    )


GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GSLIDES = "application/vnd.google-apps.presentation"
GFOLDER = "application/vnd.google-apps.folder"


# ---------------------------------------------------------------------------
# recursion + export routing
# ---------------------------------------------------------------------------


async def test_gdrive_recurses_folders_and_routes_exports(client, workspace_ctx, monkeypatch):
    calls = patch_tokens_seam(monkeypatch, SeamConnection(provider="google"), token="goog-tok")
    source = await make_gdrive_source(client, workspace_ctx)
    with respx.mock:
        root_route = folder_route(
            "root1",
            files_page(
                [
                    drive_file("d1", "Doc One", GDOC),
                    drive_file("sh1", "Numbers", GSHEET),
                    drive_file("sub1", "Subfolder", GFOLDER),
                    drive_file("img1", "logo.png", "image/png"),  # silently ignored
                ]
            ),
        )
        folder_route(
            "sub1",
            files_page(
                [
                    drive_file("sl1", "Deck", GSLIDES),
                    drive_file("f1", "notes.md", "text/markdown", size="500"),
                ]
            ),
        )
        respx.get(f"{DRIVE}/files/d1/export", params={"mimeType": "text/markdown"}).mock(
            return_value=httpx.Response(200, text="# Doc One\n\nExported body text.")
        )
        respx.get(f"{DRIVE}/files/sh1/export", params={"mimeType": "text/csv"}).mock(
            return_value=httpx.Response(200, text="col_a,col_b\nalpha,beta")
        )
        respx.get(f"{DRIVE}/files/sl1/export", params={"mimeType": "text/plain"}).mock(
            return_value=httpx.Response(200, text="Slide one speaker text.")
        )
        respx.get(f"{DRIVE}/files/f1", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="# Notes\n\nRemember the thing.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert calls == [(workspace_ctx.id, "conn-g")]
    assert root_route.calls.last.request.headers["authorization"] == "Bearer goog-tok"

    docs = await list_docs(client, workspace_ctx, source["id"])
    by_title = {d["title"]: d for d in docs}
    assert set(by_title) == {"Doc One", "Numbers", "Deck", "notes.md"}  # png ignored
    assert all(d["status"] == "indexed" for d in docs)
    assert by_title["Doc One"]["uri"] == "https://drive.example.com/view/d1"
    assert by_title["Doc One"]["mime"] == "text/markdown"
    assert by_title["Numbers"]["mime"] == "text/csv"
    assert by_title["Deck"]["mime"] == "text/plain"
    assert by_title["notes.md"]["mime"] == "text/markdown"
    sheet_text = await doc_text(client, workspace_ctx, by_title["Numbers"]["id"])
    assert "| col_a | col_b |" in sheet_text  # CSV export rendered as a markdown table
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_gdrive_pdf_skip_is_recorded_but_pruning_still_runs(
    client, workspace_ctx, monkeypatch
):
    patch_tokens_seam(monkeypatch, SeamConnection(provider="google"))
    source = await make_gdrive_source(client, workspace_ctx)
    with respx.mock:
        folder_route(
            "root1",
            files_page(
                [
                    drive_file("f1", "notes.md", "text/markdown", size="100"),
                    drive_file("f2", "guide.md", "text/markdown", size="100"),
                ]
            ),
        )
        respx.get(f"{DRIVE}/files/f1", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="Notes body.")
        )
        respx.get(f"{DRIVE}/files/f2", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="Guide body.")
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert len(await list_docs(client, workspace_ctx, source["id"])) == 2

    with respx.mock:  # guide.md vanished; a PDF appeared
        folder_route(
            "root1",
            files_page(
                [
                    drive_file("f1", "notes.md", "text/markdown", size="100"),
                    drive_file("p1", "report.pdf", "application/pdf", size="9000"),
                ]
            ),
        )
        respx.get(f"{DRIVE}/files/f1", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="Notes body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["uri"] for d in docs] == ["https://drive.example.com/view/f1"]  # guide.md pruned
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    # The skip is recorded in the sync summary (contract: never silent), but a
    # skip is not an error — the source stays "idle".
    assert "report.pdf" in refreshed["error"]
    assert "not indexed yet" in refreshed["error"]
    assert refreshed["status"] == "idle"


async def test_gdrive_oversized_raw_file_skipped_with_note(client, workspace_ctx, monkeypatch):
    patch_tokens_seam(monkeypatch, SeamConnection(provider="google"))
    source = await make_gdrive_source(client, workspace_ctx)
    with respx.mock:
        folder_route(
            "root1",
            # 3MB — over the 2MB raw cap; alt=media is never mocked, so a
            # download attempt would fail the sync with a different error.
            files_page([drive_file("big1", "big.txt", "text/plain", size="3000000")]),
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert await list_docs(client, workspace_ctx, source["id"]) == []
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert "big.txt" in refreshed["error"]
    assert "2MB" in refreshed["error"]


# ---------------------------------------------------------------------------
# pagination + caps
# ---------------------------------------------------------------------------


async def test_gdrive_follows_next_page_token(client, workspace_ctx, monkeypatch):
    patch_tokens_seam(monkeypatch, SeamConnection(provider="google"))
    source = await make_gdrive_source(client, workspace_ctx)
    with respx.mock:
        list_route = respx.get(
            f"{DRIVE}/files", params={"q": "'root1' in parents and trashed=false"}
        ).mock(
            side_effect=[
                files_page(
                    [drive_file("f1", "one.md", "text/markdown", size="10")], next_token="t2"
                ),
                files_page([drive_file("f2", "two.md", "text/markdown", size="10")]),
            ]
        )
        respx.get(f"{DRIVE}/files/f1", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="One.")
        )
        respx.get(f"{DRIVE}/files/f2", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="Two.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert list_route.call_count == 2
    assert list_route.calls[1].request.url.params["pageToken"] == "t2"
    assert len(await list_docs(client, workspace_ctx, source["id"])) == 2


async def test_gdrive_max_files_caps_the_haul(client, workspace_ctx, monkeypatch):
    patch_tokens_seam(monkeypatch, SeamConnection(provider="google"))
    source = await make_gdrive_source(client, workspace_ctx, config={"max_files": 1})
    with respx.mock:
        folder_route(
            "root1",
            files_page(
                [
                    drive_file("f1", "one.md", "text/markdown", size="10"),
                    drive_file("f2", "two.md", "text/markdown", size="10"),  # never fetched
                ]
            ),
        )
        respx.get(f"{DRIVE}/files/f1", params={"alt": "media"}).mock(
            return_value=httpx.Response(200, text="One.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["one.md"]
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


async def test_gdrive_config_validation(client, workspace_ctx):
    bad_bodies = [
        {"type": "gdrive", "name": "D", "config": {"folder_ids": ["root1"]}},  # no connection
        {"type": "gdrive", "name": "D", "config": {"connection_id": "conn-g"}},  # no folders
        {
            "type": "gdrive",
            "name": "D",
            "config": {"connection_id": "conn-g", "folder_ids": []},
        },
        {
            "type": "gdrive",
            "name": "D",
            "config": {"connection_id": "conn-g", "folder_ids": ["../evil"]},  # bad id chars
        },
        {
            "type": "gdrive",
            "name": "D",
            "config": {"connection_id": "conn-g", "folder_ids": ["root1"], "max_files": 0},
        },
    ]
    for body in bad_bodies:
        response = await client.post(
            f"{workspace_ctx.base}/knowledge/sources",
            json=body,
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, (body, response.text)

    clamped = await create_source(
        client,
        workspace_ctx,
        type="gdrive",
        config={"connection_id": "conn-g", "folder_ids": ["root1"], "max_files": 9999},
    )
    assert clamped["config"]["max_files"] == 500
