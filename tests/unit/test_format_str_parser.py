import re

from src.stages.normalize import apply_format_str


def _match(pattern: str, text: str) -> re.Match:
    m = re.match(pattern, text)
    assert m is not None
    return m


def test_plain_group():
    m = _match(r"^(\d{8})$", "20160929")
    assert apply_format_str("{1}", m) == "20160929"


def test_slice():
    m = _match(r"^(\d{8})$", "20160929")
    assert apply_format_str("{1:0:4}", m) == "2016"


def test_composed_date():
    m = _match(r"^(\d{8})$", "20160929")
    result = apply_format_str("{1:0:4}-{1:4:6}-{1:6:8}", m)
    assert result == "2016-09-29"


def test_empty_format_str_returns_group1():
    m = _match(r"^(\d{6})$", "160929")
    assert apply_format_str("", m) == "160929"


def test_none_format_str_returns_group1():
    m = _match(r"^(\d{6})$", "160929")
    assert apply_format_str(None, m) == "160929"
