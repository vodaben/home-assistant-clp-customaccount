"""Regression checks for token-wipe classification (Issue I1).

Runs without Home Assistant: imports only the pure helpers from const.py.
    python tests/test_auth_classification.py
"""
import datetime
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "clphk"),
)

from const import (  # noqa: E402
    is_auth_failure,
    is_transient,
    parse_datetime,
    parse_refresh_tokens,
    safe_float,
)


def test_bare_auth_statuses_are_fatal():
    assert is_auth_failure(401, None) is True
    assert is_auth_failure(403, None) is True


def test_expired_token_codes_are_fatal_regardless_of_status():
    # Reached only after a refresh already failed / no refresh token: token is dead.
    assert is_auth_failure(400, 906) is True
    assert is_auth_failure(400, 100001) is True


def test_transient_and_data_4xx_never_wipe():
    # The core of I1: a rate limit or a single bad request must NOT clear tokens.
    assert is_auth_failure(429, None) is False
    assert is_auth_failure(400, None) is False
    assert is_auth_failure(404, None) is False
    assert is_auth_failure(408, None) is False
    assert is_auth_failure(409, None) is False
    assert is_auth_failure(422, None) is False


def test_5xx_is_not_an_auth_failure():
    assert is_auth_failure(500, None) is False
    assert is_auth_failure(503, None) is False


def test_transient_statuses():
    assert is_transient(429) is True
    assert is_transient(408) is True
    assert is_transient(400) is False
    assert is_transient(401) is False
    assert is_transient(500) is False


def test_parse_refresh_tokens_valid():
    assert parse_refresh_tokens(
        {"data": {"access_token": "a", "refresh_token": "r", "expires_in": 3600}}
    ) == ("a", "r", 3600)


def test_parse_refresh_tokens_missing_expiry_is_ok():
    # expires_in is not used for control flow; absence must not fail the refresh.
    assert parse_refresh_tokens(
        {"data": {"access_token": "a", "refresh_token": "r"}}
    ) == ("a", "r", None)


def test_parse_refresh_tokens_missing_or_empty_tokens_is_none():
    assert parse_refresh_tokens({"data": {"refresh_token": "r"}}) is None
    assert parse_refresh_tokens({"data": {"access_token": "a"}}) is None
    assert parse_refresh_tokens({"data": {"access_token": "", "refresh_token": "r"}}) is None
    assert parse_refresh_tokens({"data": {"access_token": "a", "refresh_token": None}}) is None


def test_parse_refresh_tokens_bad_shapes_are_none():
    assert parse_refresh_tokens({"data": None}) is None
    assert parse_refresh_tokens({"data": "nope"}) is None
    assert parse_refresh_tokens({}) is None
    assert parse_refresh_tokens(None) is None
    assert parse_refresh_tokens("string") is None


def test_safe_float():
    assert safe_float(None) is None
    assert safe_float("") is None
    assert safe_float("abc") is None
    assert safe_float([]) is None
    # Zero must survive (a real reading), not collapse to None.
    assert safe_float("0") == 0.0
    assert safe_float(0) == 0.0
    assert safe_float("12.5") == 12.5
    assert safe_float(12.5) == 12.5


def test_parse_datetime():
    assert parse_datetime("20260618000000", "%Y%m%d%H%M%S") == datetime.datetime(2026, 6, 18, 0, 0, 0)
    assert parse_datetime(None, "%Y%m%d%H%M%S") is None
    assert parse_datetime("", "%Y%m%d%H%M%S") is None
    assert parse_datetime("garbage", "%Y%m%d%H%M%S") is None
    assert parse_datetime(12345, "%Y%m%d%H%M%S") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
