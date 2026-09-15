"""Offline tests for live-release URL reachability retries.

These mock HTTP responses and do not touch the network. They cover the
hardening in ``_assert_url_reachable``: retry on 5xx then succeed, hard-fail
on 404/401/403, and skip after persistent CDN 5xx.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tests_dir)
sys.path.insert(0, _repo_root)

from tests.test_release_info_live import (  # noqa: E402
    _URL_REACHABILITY_ATTEMPTS,
    _assert_url_reachable,
    _download_check_headers,
)

_PROBE_URL = "https://github.com/example/repo/releases/download/v1.0.0/asset.tar.gz"
_SESSION_PATCH = "tests.test_release_info_live.requests.Session"
_SLEEP_PATCH = "tests.test_release_info_live.time.sleep"


def _response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


def _session_with_side_effects(head_effects, get_effects=None) -> MagicMock:
    session = MagicMock()
    session.head.side_effect = list(head_effects)
    session.get.side_effect = list(get_effects or [])
    return session


def _patch_session(mock_session_cls: MagicMock, session: MagicMock) -> None:
    mock_session_cls.return_value.__enter__.return_value = session
    mock_session_cls.return_value.__exit__.return_value = False


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_retries_on_500_then_success(mock_session_cls, mock_sleep) -> None:
    """HTTP 500 on HEAD+GET is retried; a later 200 succeeds without skip/fail."""
    session = _session_with_side_effects(
        head_effects=[_response(500), _response(200)],
        get_effects=[_response(500)],
    )
    _patch_session(mock_session_cls, session)

    _assert_url_reachable(_PROBE_URL, "besu")

    assert session.head.call_count == 2
    assert session.get.call_count == 1
    mock_sleep.assert_called_once_with(1)
    get_headers = session.get.call_args.kwargs["headers"]
    assert get_headers["User-Agent"] == _download_check_headers()["User-Agent"]
    assert "Authorization" not in get_headers
    assert get_headers["Range"] == "bytes=0-0"


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_hard_fails_immediately_on_404(mock_session_cls, mock_sleep) -> None:
    """404 after HEAD+GET is a real missing asset — do not retry or skip."""
    session = _session_with_side_effects(
        head_effects=[_response(404)],
        get_effects=[_response(404)],
    )
    _patch_session(mock_session_cls, session)

    with pytest.raises(AssertionError, match=r"URL not reachable \(404\)"):
        _assert_url_reachable(_PROBE_URL, "besu")

    assert session.head.call_count == 1
    assert session.get.call_count == 1
    mock_sleep.assert_not_called()


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_hard_fails_immediately_on_401(mock_session_cls, mock_sleep) -> None:
    session = _session_with_side_effects(
        head_effects=[_response(401)],
        get_effects=[_response(401)],
    )
    _patch_session(mock_session_cls, session)

    with pytest.raises(AssertionError, match=r"URL not reachable \(401\)"):
        _assert_url_reachable(_PROBE_URL, "besu")

    mock_sleep.assert_not_called()


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_head_403_falls_through_to_successful_get(mock_session_cls, mock_sleep) -> None:
    """Azure blob HEAD may 403; a successful ranged GET still counts as reachable."""
    session = _session_with_side_effects(
        head_effects=[_response(403)],
        get_effects=[_response(206)],
    )
    _patch_session(mock_session_cls, session)

    _assert_url_reachable(_PROBE_URL, "besu")

    assert session.head.call_count == 1
    assert session.get.call_count == 1
    mock_sleep.assert_not_called()


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_skips_after_persistent_5xx(mock_session_cls, mock_sleep) -> None:
    """Exhausted 5xx retries skip (CDN blip) instead of failing Nightly."""
    session = _session_with_side_effects(
        head_effects=[_response(500)] * _URL_REACHABILITY_ATTEMPTS,
        get_effects=[_response(500)] * _URL_REACHABILITY_ATTEMPTS,
    )
    _patch_session(mock_session_cls, session)

    with pytest.raises(pytest.skip.Exception, match="transient CDN/asset HTTP 500"):
        _assert_url_reachable(_PROBE_URL, "besu")

    assert session.head.call_count == _URL_REACHABILITY_ATTEMPTS
    assert session.get.call_count == _URL_REACHABILITY_ATTEMPTS
    assert mock_sleep.call_args_list == [((1,),), ((2,),), ((4,),)]


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_retries_timeout_then_success(mock_session_cls, mock_sleep) -> None:
    session = _session_with_side_effects(
        head_effects=[requests.Timeout("timed out"), _response(200)],
    )
    _patch_session(mock_session_cls, session)

    _assert_url_reachable(_PROBE_URL, "besu")

    assert session.head.call_count == 2
    session.get.assert_not_called()
    mock_sleep.assert_called_once_with(1)


@patch(_SLEEP_PATCH)
@patch(_SESSION_PATCH)
def test_skips_after_persistent_429(mock_session_cls, mock_sleep) -> None:
    session = _session_with_side_effects(
        head_effects=[_response(429)] * _URL_REACHABILITY_ATTEMPTS,
        get_effects=[_response(429)] * _URL_REACHABILITY_ATTEMPTS,
    )
    _patch_session(mock_session_cls, session)

    with pytest.raises(pytest.skip.Exception, match="transient CDN/asset HTTP 429"):
        _assert_url_reachable(_PROBE_URL, "besu")

    assert mock_sleep.call_count == _URL_REACHABILITY_ATTEMPTS - 1
