"""Regression checks for token-wipe classification (Issue I1).

Runs without Home Assistant: imports only the pure helpers from const.py.
    python tests/test_auth_classification.py
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "clphk"),
)

from const import is_auth_failure, is_transient  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
