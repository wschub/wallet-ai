from __future__ import annotations

import pytest

from wallet_ai.domain.recipients.masking import mask_display_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (None, "***"),
        ("", "***"),
        ("   ", "***"),
        ("A", "A***"),
        ("Á", "Á***"),
        ("María", "M***"),
        ("María García", "María G***"),
        ("José Ó", "José Ó***"),
    ],
)
def test_mask_display_name_handles_missing_and_short_accented_names(name, expected):
    assert mask_display_name(name) == expected
