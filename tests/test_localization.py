"""Guard UI translation coverage and dynamically rendered preview labels.

These checks run without a Home Assistant process. Actual frontend rendering
in both languages is separately required before the first stable release.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from custom_components.more_records.ui_labels import (
    GAP_LABELS,
    STATUS_LABELS,
    preview_labels,
    ui_language,
)

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "more_records"
PLACEHOLDER = re.compile(r"(?<!{){([a-z][a-z0-9_]*)}(?!})")
BROKEN_TEXT = re.compile(r"(?i)\b(?:foo_bar_foo|todo|tbd|placeholder)\b")


def flatten(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, path))
        else:
            result[path] = value
    return result


def catalogs():
    return {
        name: json.loads((ROOT / file).read_text(encoding="utf-8"))
        for name, file in {
            "strings": "strings.json",
            "en": "translations/en.json",
            "de": "translations/de.json",
        }.items()
    }


def test_all_translated_paths_and_placeholders_match():
    data = {lang: flatten(catalog) for lang, catalog in catalogs().items()}
    assert set(data["strings"]) == set(data["en"]) == set(data["de"])
    for key in data["strings"]:
        samples = [data[lang][key] for lang in ("strings", "en", "de")]
        assert all(isinstance(sample, str) and sample.strip() for sample in samples), key
        assert len({frozenset(PLACEHOLDER.findall(sample)) for sample in samples}) == 1, key
        assert not any(BROKEN_TEXT.search(sample) for sample in samples), key


def test_every_options_flow_field_and_error_has_localized_string():
    j = catalogs()
    required = {
        "init": ("operation",),
        "import": ("entity_id", "source", "gap_mode", "daily_midnight_support"),
        "csv": ("file",),
        "historian_connect": ("url", "api_key"),
        "historian_search": ("query",),
        "historian_point": ("point_id", "start_date", "end_date"),
        "confirm": ("confirm", "confirm_complete_history"),
        "export": ("entity_id", "start_time", "end_time"),
        "export_confirm": ("confirm",),
        "export_download": (),
    }
    for catalog in j.values():
        steps = catalog["options"]["step"]
        for step, fields in required.items():
            assert steps[step]["title"].strip()
            assert steps[step]["description"].strip()
            assert set(fields) <= set(steps[step].get("data", {})), step
        for section in ("config", "options"):
            for error in catalog[section].get("error", {}).values():
                assert isinstance(error, str) and error.strip()


def test_selectors_never_use_untranslated_raw_values():
    texts = catalogs()
    choices = {
        "operation": {"import", "export"},
        "import_source": {"csv", "ccu_historian"},
        "gap_mode": {"hold_last", "oldest_recorder", "unavailable"},
    }
    for catalog in texts.values():
        for key, values in choices.items():
            assert set(catalog["selector"][key]["options"]) == values
            assert all(
                catalog["selector"][key]["options"][value] != value
                or value == "import"
                for value in values
            )
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
    for selector in choices:
        assert f'"translation_key": "{selector}"' in source


@pytest.mark.parametrize("lang", ["de", "de-DE", "en", "fr", None])
def test_preview_values_never_display_internal_codes(lang):
    labels = preview_labels(lang, "hold_last", True, True)
    assert labels["gap_mode"] in GAP_LABELS[ui_language(lang)].values()
    assert labels["daily_support_enabled"] == STATUS_LABELS[ui_language(lang)]["yes"]
    assert labels["gap_marker_empty"] == STATUS_LABELS[ui_language(lang)]["none"]
    assert "hold_last" not in labels.values()
    assert "coverage_historian" not in labels.values()


def test_preview_translations_match_actual_select_selector_labels():
    texts = catalogs()
    for lang in ("en", "de"):
        assert (
            GAP_LABELS[lang]
            == texts[lang]["selector"]["gap_mode"]["options"]
        )
        assert (
            preview_labels(lang, "unavailable", False, False)["coverage"]
            == STATUS_LABELS[lang]["coverage_csv"]
        )
