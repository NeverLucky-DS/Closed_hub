"""HR contact parsing before LLM extraction."""

from __future__ import annotations

import pytest

from services.hr_service import normalize_hr_contact_ref, try_parse_hr_contact_line


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@RecruiterName", "@recruitername"),
        ("  @valid_user  ", "@valid_user"),
        ("79991234567", "79991234567"),
    ],
)
def test_try_parse_hr_contact_line_valid(raw: str, expected: str) -> None:
    assert try_parse_hr_contact_line(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["hello", "@ab", "12345", ""],
)
def test_try_parse_hr_contact_line_invalid(raw: str) -> None:
    assert try_parse_hr_contact_line(raw) is None


def test_normalize_hr_contact_ref_username() -> None:
    assert normalize_hr_contact_ref("@SomeUser") == "@someuser"
