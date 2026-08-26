"""The validator page: rendering, accessibility structure, and colour contrast.

The four result states are toggled client-side, so these tests assert that each
state renders into the document correctly labelled and wired, rather than
driving a browser. Browser-level verification is the checklist in
docs/MANUAL_UI_TEST.md -- see PHASE1_REPORT.md for why Playwright was not added.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "validator.css"
JS = ROOT / "static" / "validator.js"
TEMPLATE = ROOT / "templates" / "validator.html"


@pytest.fixture(scope="module")
def markup() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ------------------------------------------------------------ page rendering


@pytest.mark.requires_db
def test_page_renders(client: TestClient, icd10se_embedded: str) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="sv"' in response.text
    assert "Beslutsstöd, inte automatisk kodning" in response.text
    assert "2026-sample" in response.text


@pytest.mark.requires_db
def test_static_assets_are_served(client: TestClient) -> None:
    for path, marker in (
        ("/static/validator.css", "--c-accent"),
        ("/static/validator.js", "function render"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text


@pytest.mark.requires_db
def test_test_mode_banner_is_shown_for_a_fake_provider(
    client: TestClient, icd10se_embedded: str
) -> None:
    """The banner replaces the old grey footer note as how test mode is said."""
    body = client.get("/").text
    assert "Testläge" in body
    assert "Ingen säkerhetsskattning visas" in body


def test_test_mode_banner_is_absent_for_a_live_provider() -> None:
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    rendered = templates.get_template("validator.html").render(
        versions=[{"system": "icd10se", "version": "2026", "count": 38928}],
        provider_kind="live",
        llm_provider="anthropic",
        llm_model="claude-opus-5",
        embedding_provider="openai_compat",
        embedding_model="text-embedding-3-small",
        app_version="0.1.0",
    )
    assert "Testläge" not in rendered
    assert "claude-opus-5" in rendered


def test_missing_terminology_is_announced() -> None:
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    rendered = templates.get_template("validator.html").render(
        versions=[],
        provider_kind="fake",
        llm_provider="fake",
        llm_model="f",
        embedding_provider="fake",
        embedding_model="f",
        app_version="0.1.0",
    )
    assert "Ingen terminologi är laddad" in rendered
    assert "disabled" in rendered


# ------------------------------------------------- one section per result state


@pytest.mark.parametrize(
    ("section_id", "label", "heading_id"),
    [
        ("state-suggestion", "Förslag", "forslag-rubrik"),
        ("state-nomatch", "Ingen tillräcklig träff", "ingen-rubrik"),
        ("state-failed", "Omrankning misslyckades", "fel-rubrik"),
        ("state-decided", "Beslut registrerat", "beslut-rubrik"),
    ],
)
def test_each_result_state_renders_labelled(
    markup: str, section_id: str, label: str, heading_id: str
) -> None:
    """Four states, four distinct visual signatures, each with its own heading."""
    assert f'id="{section_id}"' in markup
    assert label in markup
    assert f'aria-labelledby="{heading_id}"' in markup
    assert f'id="{heading_id}"' in markup


def test_each_state_has_a_distinct_card_modifier(markup: str) -> None:
    css = CSS.read_text(encoding="utf-8")
    for modifier in ("card--suggestion", "card--nomatch", "card--failed", "card--decided"):
        assert modifier in markup, modifier
        assert f".{modifier}" in css, modifier


def test_no_good_match_offers_only_the_two_valid_actions(markup: str) -> None:
    """There is nothing to accept, so no accept button is offered at all."""
    section = markup[markup.index('id="state-nomatch"') : markup.index('id="state-failed"')]
    assert "Bekräfta: ingen kod" in section
    assert "Ange kod manuellt" in section
    assert "Godkänn" not in section


def test_decision_actions_come_before_the_evidence_table(markup: str) -> None:
    """The buttons must not sit below thirty rows of numbers."""
    assert markup.index('id="s-actions"') < markup.index('id="cand-rows"')


def test_there_is_exactly_one_candidate_table(markup: str) -> None:
    """The old page listed the same concepts twice, in two tables."""
    assert markup.count("<table") == 1
    assert markup.count("<tbody") == 1


# ---------------------------------------------------------------- semantics


def test_semantic_landmarks_and_table_structure(markup: str) -> None:
    for fragment in (
        "<main>",
        "<header>",
        "<footer",
        "<caption",
        'scope="col"',
        "<details",
        "<summary",
        'aria-live="polite"',
        'class="skip-link"',
    ):
        assert fragment in markup, fragment


def test_every_input_has_a_label_and_description(markup: str) -> None:
    for field in ("text", "validator_id", "final_code", "note", "target"):
        assert f'for="{field}"' in markup, field
        assert f'id="{field}"' in markup, field
    for field in ("text", "validator_id", "final_code"):
        assert re.search(rf'id="{field}"[^>]*aria-describedby=', markup), field


def test_errors_are_bound_to_their_inputs(markup: str) -> None:
    for error_id in ("text-error", "validator-error", "code-error"):
        assert f'id="{error_id}"' in markup, error_id
        assert error_id in markup


def test_all_buttons_declare_a_type(markup: str) -> None:
    """A bare <button> inside a form submits it."""
    for match in re.finditer(r"<button\b[^>]*>", markup):
        assert "type=" in match.group(0), match.group(0)


# ------------------------------------------------------ self-contained assets


def test_no_external_resources_are_referenced() -> None:
    """No CDN, no external font, no icon library, no tracker."""
    for path in (TEMPLATE, CSS, JS):
        text = path.read_text(encoding="utf-8")
        for pattern in ("http://", "https://", "//cdn", "@import url("):
            assert pattern not in text, f"{path.name} references {pattern}"


def test_stylesheet_uses_tokens_not_magic_colours() -> None:
    css = CSS.read_text(encoding="utf-8")
    root = css[css.index(":root {") : css.index("*, *::before")]
    # Every literal colour is defined in :root; rules reference var(--...).
    body = css[css.index("*, *::before") :]
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert literals == [], f"hard-coded colours outside :root: {literals}"
    assert root.count("--c-") >= 15


def test_reduced_motion_is_respected() -> None:
    assert "prefers-reduced-motion" in CSS.read_text(encoding="utf-8")


def test_focus_is_always_visible() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "--focus-ring" in css
    assert "outline-offset" in css


def test_touch_targets_meet_the_minimum() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "--tap: 44px" in css
    assert "min-height: var(--tap)" in css


def test_numeric_columns_are_tabular() -> None:
    assert "font-variant-numeric: tabular-nums" in CSS.read_text(encoding="utf-8")


# ------------------------------------------------------------ colour contrast


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = []
    for index in (0, 2, 4):
        raw = int(value[index : index + 2], 16) / 255
        channels.append(raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _tokens() -> dict[str, str]:
    css = CSS.read_text(encoding="utf-8")
    return dict(re.findall(r"(--c-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6});", css))


# (foreground token, background token, minimum) -- WCAG 2.1 AA:
# 1.4.3 text 4.5:1, 1.4.11 non-text UI 3:1.
TEXT_PAIRS = [
    ("--c-text", "--c-bg", 4.5),
    ("--c-text-muted", "--c-bg", 4.5),
    ("--c-text-muted", "--c-surface", 4.5),
    ("--c-text", "--c-surface", 4.5),
    ("--c-accent", "--c-bg", 4.5),
    ("--c-accent", "--c-info-bg", 4.5),
    ("--c-accent-text", "--c-accent", 4.5),
    ("--c-ok", "--c-bg", 4.5),
    ("--c-ok", "--c-ok-bg", 4.5),
    ("--c-warn", "--c-bg", 4.5),
    ("--c-warn", "--c-warn-bg", 4.5),
    ("--c-error", "--c-bg", 4.5),
    ("--c-error", "--c-error-bg", 4.5),
]
UI_PAIRS = [
    ("--c-border", "--c-bg", 3.0),
    ("--c-border-strong", "--c-bg", 3.0),
    ("--c-accent", "--c-bg", 3.0),
    ("--c-warn-border", "--c-warn-bg", 3.0),
    ("--c-ok", "--c-bg", 3.0),
    ("--c-error", "--c-bg", 3.0),
]


@pytest.mark.parametrize(("fg", "bg", "minimum"), TEXT_PAIRS + UI_PAIRS)
def test_contrast_meets_wcag_aa(fg: str, bg: str, minimum: float) -> None:
    tokens = _tokens()
    ratio = _contrast(tokens[fg], tokens[bg])
    assert ratio >= minimum, f"{fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1"
