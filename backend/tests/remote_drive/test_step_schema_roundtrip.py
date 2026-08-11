"""Recorder-stamped step fields survive the schema layer.

The recorder now stamps every captured step with the page it lives on (`url`)
and whether a real click advances it (`advance_on_click`); the widget player
navigates between pages on them. The extension PUTs steps through
``/dap/tours/{id}/steps`` (``DapStepsPut`` → ``TourStepIn`` →
``model_dump(by_alias=True)``) and reads them back as ``TourStepOut`` — both
directions must keep the fields, and tolerate their absence (older steps).

``app.services.tours._normalize_steps`` rebuilds a fixed field set at persist
time and passes both fields through as well — covered at the bottom of this
file, completing the end-to-end round trip.
"""

from __future__ import annotations

from app.schemas.tours import TourStepIn, TourStepOut
from app.services.tours import _CONTENT_FIELDS, _normalize_steps


def _recorded_step(**extra: object) -> dict[str, object]:
    return {
        "id": "s1",
        "type": "tooltip",
        "selector": "#save",
        "fallback_selectors": [],
        "text_hint": "save",
        "title": "Click on “save”",
        "body": "",
        "placement": "auto",
        "advance": {"on": "element_click"},
        **extra,
    }


def test_url_and_advance_on_click_survive_in_and_out():
    step = TourStepIn.model_validate(
        _recorded_step(url="https://app.example.com/settings", advance_on_click=True)
    )
    dumped = step.model_dump(by_alias=True)
    assert dumped["url"] == "https://app.example.com/settings"
    assert dumped["advance_on_click"] is True

    echoed = TourStepOut.model_validate(dumped)
    assert echoed.url == "https://app.example.com/settings"
    assert echoed.advance_on_click is True


def test_old_steps_without_the_fields_stay_valid():
    step = TourStepIn.model_validate(_recorded_step())
    assert step.url is None
    assert step.advance_on_click is None
    # Stored pre-v2.2 rows echo cleanly too.
    out = TourStepOut.model_validate({"id": "s1"})
    assert out.url is None
    assert out.advance_on_click is None


def test_blank_url_normalizes_to_none():
    step = TourStepIn.model_validate(_recorded_step(url="   "))
    assert step.url is None


def test_normalize_steps_keeps_url_and_advance_on_click():
    """Persist-time normalization must not strip the recorder's page context."""
    [normalized] = _normalize_steps(
        [_recorded_step(url="https://app.example.com/settings", advance_on_click=True)]
    )
    assert normalized["url"] == "https://app.example.com/settings"
    assert normalized["advance_on_click"] is True

    [legacy] = _normalize_steps([_recorded_step()])
    assert legacy["url"] is None
    assert legacy["advance_on_click"] is False

    # Editing either field is a content change: it must bump the tour version
    # (both participate in the content signature).
    assert "url" in _CONTENT_FIELDS
    assert "advance_on_click" in _CONTENT_FIELDS


def test_wire_key_for_wait_steps_is_untouched_by_the_new_fields():
    """Regression guard: the by_alias dump (the shape the dap router persists)
    keeps the `for` alias while carrying the new fields alongside."""
    step = TourStepIn.model_validate(
        {
            "id": "w1",
            "type": "wait",
            "title": "Wait for /done",
            "wait": {"for": "url", "url_pattern": "https://x/*", "timeout_ms": 10_000},
            "url": "https://app.example.com/checkout",
        }
    )
    dumped = step.model_dump(by_alias=True)
    assert dumped["wait"]["for"] == "url"
    assert dumped["url"] == "https://app.example.com/checkout"
