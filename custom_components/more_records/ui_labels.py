"""Locale-safe labels for dynamically generated options-flow preview values.

Home Assistant translates static flow text using translations/<language>.json.
These labels are *placeholder values*, which are not automatically translated by
Home Assistant. Keep these small runtime dictionaries in sync with selector
translations through tests. No raw internal enum or sentinel reaches the UI.
"""

from __future__ import annotations

GAP_LABELS = {
    "en": {
        "hold_last": "Hold last state of import",
        "oldest_recorder": "Use oldest state of Recorder",
        "unavailable": "Mark gap unavailable",
    },
    "de": {
        "hold_last": "Letzten Importzustand beibehalten",
        "oldest_recorder": "Ältesten Recorder-Zustand übernehmen",
        "unavailable": "Lücke als nicht verfügbar markieren",
    },
}

STATUS_LABELS = {
    "en": {
        "yes": "Yes",
        "no": "No",
        "none": "None",
        "coverage_historian": "Optional verification of complete CCU-Historian history",
        "coverage_csv": "CSV import cannot verify continuous history",
    },
    "de": {
        "yes": "Ja",
        "no": "Nein",
        "none": "Keine",
        "coverage_historian": "Optionale Prüfung einer vollständigen CCU-Historian-Historie",
        "coverage_csv": "CSV-Import kann keine lückenlose Historie nachweisen",
    },
}


def ui_language(language: str | None) -> str:
    """Use the Home Assistant configured language, falling back to English."""
    return "de" if language and language.lower().split("-")[0] == "de" else "en"


def preview_labels(language: str | None, gap_mode: str, is_historian: bool,
                   daily_support: bool) -> dict[str, str]:
    """Return translated placeholder values for an options-flow preview."""
    lang = ui_language(language)
    if gap_mode not in GAP_LABELS[lang]:
        raise ValueError("Unknown gap mode")
    status = STATUS_LABELS[lang]
    return {
        "gap_mode": GAP_LABELS[lang][gap_mode],
        "gap_marker_empty": status["none"],
        "daily_support_enabled": status["yes"] if daily_support else status["no"],
        "coverage": status["coverage_historian" if is_historian else "coverage_csv"],
    }
