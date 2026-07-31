"""App surveys API: CRUD, question validation matrix, publish gating, results +
responses endpoints, authz, and workspace isolation."""

from __future__ import annotations

from tests.surveys.conftest import (
    FULL_DECK,
    NPS_AND_TEXT,
    NPS_ID,
    RATING_ID,
    SELECT_ID,
    TEXT_ID,
    create_survey,
    publish_survey,
    widget_key_for,
)


async def test_survey_crud_lifecycle(client, workspace_ctx):
    survey = await create_survey(client, workspace_ctx, name="Pulse")
    assert survey["status"] == "draft"
    assert survey["version"] == 1
    assert survey["presentation"] == "slideout"
    assert survey["frequency"] == {"type": "once", "cooldown_hours": None}
    assert survey["thanks_message"] == "Thanks for the feedback!"

    listing = await client.get(f"{workspace_ctx.base}/surveys", headers=workspace_ctx.owner_headers)
    assert [s["name"] for s in listing.json()] == ["Pulse"]

    got = await client.get(
        f"{workspace_ctx.base}/surveys/{survey['id']}", headers=workspace_ctx.owner_headers
    )
    assert got.json()["name"] == "Pulse"

    assert (await publish_survey(client, workspace_ctx, survey["id"]))["status"] == "live"

    paused = await client.post(
        f"{workspace_ctx.base}/surveys/{survey['id']}/pause", headers=workspace_ctx.owner_headers
    )
    assert paused.json()["status"] == "paused"

    deleted = await client.delete(
        f"{workspace_ctx.base}/surveys/{survey['id']}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200
    gone = await client.get(
        f"{workspace_ctx.base}/surveys/{survey['id']}", headers=workspace_ctx.owner_headers
    )
    assert gone.status_code == 404


async def test_targeting_round_trips(client, workspace_ctx):
    survey = await create_survey(
        client,
        workspace_ctx,
        questions=FULL_DECK,
        presentation="modal",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "pro"}],
        },
        schedule={"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-12-31T00:00:00Z"},
        frequency={"type": "every_time", "cooldown_hours": 48},
        priority=3,
        theme={"accent": "#00ff00"},
        thanks_message="Much appreciated!",
    )
    assert survey["presentation"] == "modal"
    assert survey["trigger"] == {"type": "url_match", "url_pattern": "*/inbox*"}
    assert survey["audience"]["filters"][0]["value"] == "pro"
    assert survey["schedule"]["start_at"].startswith("2026-01-01")
    assert survey["frequency"] == {"type": "every_time", "cooldown_hours": 48}
    assert survey["priority"] == 3
    assert survey["thanks_message"] == "Much appreciated!"
    assert [q["type"] for q in survey["questions"]] == ["nps", "text", "rating", "select"]
    assert survey["questions"][3]["options"] == ["Search", "A friend", "Conference"]
    assert survey["questions"][0]["options"] is None


async def test_question_validation_matrix(client, workspace_ctx):
    cases = [
        # select with too few options
        [{"type": "select", "question": "Pick", "options": ["only-one"]}],
        # select with too many options
        [{"type": "select", "question": "Pick", "options": [f"o{i}" for i in range(7)]}],
        # select without options
        [{"type": "select", "question": "Pick"}],
        # duplicate options
        [{"type": "select", "question": "Pick", "options": ["a", "a"]}],
        # options on a non-select question
        [{"type": "nps", "question": "Score", "options": ["a", "b"]}],
        # unknown type
        [{"type": "emoji", "question": "?"}],
        # blank question text
        [{"type": "text", "question": ""}],
        # too many questions
        [{"type": "text", "question": f"Q{i}"} for i in range(11)],
    ]
    for questions in cases:
        resp = await client.post(
            f"{workspace_ctx.base}/surveys",
            json={"name": "Bad", "questions": questions},
            headers=workspace_ctx.owner_headers,
        )
        assert resp.status_code == 422, (questions, resp.text)


async def test_version_bumps_only_on_question_change(client, workspace_ctx):
    survey = await create_survey(client, workspace_ctx)
    sid = survey["id"]

    renamed = await client.patch(
        f"{workspace_ctx.base}/surveys/{sid}",
        json={"name": "Renamed"},
        headers=workspace_ctx.owner_headers,
    )
    assert renamed.json()["version"] == 1

    same = await client.patch(
        f"{workspace_ctx.base}/surveys/{sid}",
        json={"questions": NPS_AND_TEXT},
        headers=workspace_ctx.owner_headers,
    )
    assert same.json()["version"] == 1

    changed = await client.patch(
        f"{workspace_ctx.base}/surveys/{sid}",
        json={"questions": [{"type": "text", "question": "Anything else?"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert changed.json()["version"] == 2


async def test_publish_requires_questions(client, workspace_ctx):
    empty = await create_survey(client, workspace_ctx, name="Empty", questions=[])
    resp = await client.post(
        f"{workspace_ctx.base}/surveys/{empty['id']}/publish", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 409


async def test_results_and_responses_endpoints(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await create_survey(client, workspace_ctx)
    await publish_survey(client, workspace_ctx, survey["id"])

    scores = [10, 9, 8, 0]
    for score in scores:
        resp = await client.post(
            f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
            json={
                "answers": [
                    {"question_id": NPS_ID, "value": score},
                    {"question_id": TEXT_ID, "value": f"score {score}"},
                ],
                "completed": True,
            },
        )
        assert resp.status_code == 200, resp.text
    # One partial submission (counts in `responses`, not in `completed`).
    await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": NPS_ID, "value": 7}], "completed": False},
    )

    results = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/results",
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    assert results["responses"] == 5
    assert results["completed"] == 4
    assert results["completion_rate"] == 0.8
    # 2 promoters (10, 9), 1 passive (8), 1 detractor (0) → 50 − 25 = 25.
    assert results["nps"] == {"score": 25, "promoters": 2, "passives": 1, "detractors": 1}
    assert results["ratings"] is None
    assert results["select"] == []
    assert len(results["text_answers"]) == 4
    assert results["by_day"][0]["responses"] == 5

    responses = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/responses?limit=2",
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    assert responses["total"] == 5
    assert responses["limit"] == 2
    assert len(responses["items"]) == 2
    assert responses["items"][0]["completed"] is False  # newest first


async def test_rating_and_select_results(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await create_survey(client, workspace_ctx, questions=FULL_DECK)
    await publish_survey(client, workspace_ctx, survey["id"])

    for rating, choice in ((5, "Search"), (4, "Search"), (2, "A friend")):
        resp = await client.post(
            f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
            json={
                "answers": [
                    {"question_id": NPS_ID, "value": 9},
                    {"question_id": RATING_ID, "value": rating},
                    {"question_id": SELECT_ID, "value": choice},
                ],
                "completed": True,
            },
        )
        assert resp.status_code == 200, resp.text

    results = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/results",
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    assert results["ratings"]["avg"] == 3.67
    assert results["ratings"]["distribution"] == {"1": 0, "2": 1, "3": 0, "4": 1, "5": 1}
    assert results["select"] == [
        {
            "question_id": SELECT_ID,
            "question": "How did you hear about us?",
            "counts": {"Search": 2, "A friend": 1, "Conference": 0},
        }
    ]


async def test_authz_viewer_cannot_mutate(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    survey = await create_survey(client, workspace_ctx)

    assert (await client.get(f"{workspace_ctx.base}/surveys", headers=viewer)).status_code == 200
    assert (
        await client.get(f"{workspace_ctx.base}/surveys/{survey['id']}/results", headers=viewer)
    ).status_code == 200

    created = await client.post(
        f"{workspace_ctx.base}/surveys", json={"name": "Nope"}, headers=viewer
    )
    assert created.status_code == 403
    patched = await client.patch(
        f"{workspace_ctx.base}/surveys/{survey['id']}", json={"name": "Nope"}, headers=viewer
    )
    assert patched.status_code == 403
    published = await client.post(
        f"{workspace_ctx.base}/surveys/{survey['id']}/publish", headers=viewer
    )
    assert published.status_code == 403
    deleted = await client.delete(f"{workspace_ctx.base}/surveys/{survey['id']}", headers=viewer)
    assert deleted.status_code == 403


async def test_cross_workspace_isolation(client, workspace_ctx):
    survey = await create_survey(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"

    assert (
        await client.get(f"{other_base}/surveys", headers=workspace_ctx.owner_headers)
    ).json() == []
    for path in ("", "/results", "/responses"):
        resp = await client.get(
            f"{other_base}/surveys/{survey['id']}{path}", headers=workspace_ctx.owner_headers
        )
        assert resp.status_code == 404, path
