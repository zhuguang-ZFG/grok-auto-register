#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from databricks_pipeline import captcha

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_HTML = (FIXTURE_DIR / "dbx_recaptcha_snippet.html").read_text(encoding="utf-8")


def _mock_urlopen(body: dict) -> MagicMock:
    """Build a mock response for urllib.request.urlopen context manager."""
    cm = MagicMock()  # context manager
    resp = MagicMock()  # response object
    resp.read.return_value = json.dumps(body).encode()
    cm.__enter__.return_value = resp
    return cm


# -----------------------------------------------------------------
# detect_recaptcha tests
# -----------------------------------------------------------------


class _FakePage:
    """Minimal DrissionPage-like object for testing detection."""

    def __init__(self, url: str = "https://login.databricks.com/signup", html: str = ""):
        self._url = url
        self._html = html
        self._elements: dict = {}

    @property
    def url(self) -> str:
        return self._url

    @property
    def html(self) -> str:
        return self._html

    def ele(self, css: str, timeout: float = 1):
        try:
            return self._elements[css]
        except KeyError:
            raise Exception("not found")


class _FakeElement:
    def __init__(self, attrs: dict):
        self._attrs = attrs

    def attr(self, name: str) -> str | None:
        return self._attrs.get(name)


def test_detect_with_grecaptcha_class():
    """Detect via .g-recaptcha element."""
    page = _FakePage(
        url="https://login.databricks.com/signup",
        html=FIXTURE_HTML,
    )
    el = _FakeElement({"data-sitekey": "6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b"})
    page._elements["css:.g-recaptcha"] = el

    result = captcha.detect_recaptcha(page)
    assert result is not None
    assert result["sitekey"] == "6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b"
    assert result["visible"] is True
    assert result["is_enterprise"] is False


def test_detect_returns_none():
    """No recaptcha on page returns None."""
    page = _FakePage(html="<html><body><p>no captcha here</p></body></html>")
    result = captcha.detect_recaptcha(page)
    assert result is None


def test_detect_fallback_html_regex():
    """Fallback regex extraction when no element found."""
    html_with_key = """<html><body>
      <div id="recaptcha" data-sitekey="6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b"></div>
    </body></html>"""
    page = _FakePage(html=html_with_key)
    result = captcha.detect_recaptcha(page)
    assert result is not None
    assert result["sitekey"] == "6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b"


def test_detect_enterprise():
    """Detect enterprise flag from HTML."""
    html_ent = """<html><head><script src="https://www.google.com/recaptcha/enterprise.js"></script></head>
    <body><div class="g-recaptcha" data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"></div></body></html>"""
    page = _FakePage(html=html_ent)
    el = _FakeElement({"data-sitekey": "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"})
    page._elements["css:.g-recaptcha"] = el

    result = captcha.detect_recaptcha(page)
    assert result is not None
    assert result["is_enterprise"] is True


# -----------------------------------------------------------------
# solve_recaptcha_capsolver tests
# -----------------------------------------------------------------


def test_solve_recaptcha_capsolver_success():
    """Mock CapSolver HTTP and verify happy path."""
    mock_create = _mock_urlopen({"errorId": 0, "taskId": "abc-123-def"})
    mock_poll = _mock_urlopen({
        "errorId": 0,
        "status": "ready",
        "solution": {"gRecaptchaResponse": "03AGdBq27..." + "x" * 80}
    })

    def _side_effect(req, *a, **kw):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        if "getTaskResult" in url:
            return mock_poll
        return mock_create

    with patch("databricks_pipeline.captcha.urllib.request.urlopen", side_effect=_side_effect):
        token = captcha.solve_recaptcha_capsolver(
            website_url="https://login.databricks.com/signup",
            website_key="6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b",
            api_key="test-api-key",
            is_enterprise=False,
        )
    assert token.startswith("03AGdBq27")


def test_solve_requires_api_key():
    """Missing API key raises ValueError."""
    with pytest.raises(ValueError, match="API key"):
        captcha.solve_recaptcha_capsolver(
            website_url="https://example.com",
            website_key="some-key",
            api_key="",
        )


def test_solve_requires_sitekey():
    """Missing sitekey raises ValueError."""
    with pytest.raises(ValueError, match="sitekey"):
        captcha.solve_recaptcha_capsolver(
            website_url="https://example.com",
            website_key="",
            api_key="some-key",
        )


def test_solve_capsolver_error_response():
    """CapSolver errorId != 0 raises RuntimeError."""
    mock_resp = _mock_urlopen({
        "errorId": 1,
        "errorDescription": "Invalid key"
    })

    with patch("databricks_pipeline.captcha.urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Invalid key"):
            captcha.solve_recaptcha_capsolver(
                website_url="https://example.com",
                website_key="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI",
                api_key="bad-key",
            )


def test_solve_enterprise_task_type():
    """Enterprise flag uses ReCaptchaV2EnterpriseTaskProxyLess."""
    calls = []

    def _capture(req, *a, **kw):
        body = json.loads(req.data)
        calls.append(body)
        return _mock_urlopen({"errorId": 0, "taskId": "task-123"})

    mock_poll = _mock_urlopen({
        "errorId": 0,
        "status": "ready",
        "solution": {"gRecaptchaResponse": "token" + "x" * 30}
    })

    def _side_effect(req, *a, **kw):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        if "getTaskResult" in url:
            return mock_poll
        return _capture(req, *a, **kw)

    with patch("databricks_pipeline.captcha.urllib.request.urlopen", side_effect=_side_effect):
        try:
            captcha.solve_recaptcha_capsolver(
                website_url="https://example.com",
                website_key="6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe",
                api_key="key",
                is_enterprise=True,
            )
        except RuntimeError:
            pass

    assert len(calls) >= 1
    assert calls[0]["task"]["type"] == "ReCaptchaV2EnterpriseTaskProxyLess"
    # task type encodes enterprise; extra isEnterprise field not required


# -----------------------------------------------------------------
# inject_recaptcha_token tests
# -----------------------------------------------------------------


def test_inject_recaptcha_token_empty():
    """Empty token returns False."""
    assert captcha.inject_recaptcha_token(MagicMock(), "") is False


def test_inject_recaptcha_token_no_page():
    """No page returns False."""
    assert captcha.inject_recaptcha_token(None, "some-token") is False


def test_inject_recaptcha_token_js_ok():
    """When run_js returns ok, inject succeeds."""
    page = MagicMock()
    page.run_js.return_value = "ok"
    assert captcha.inject_recaptcha_token(page, "valid-token-here") is True
    page.run_js.assert_called_once()


def test_inject_recaptcha_token_js_fallback():
    """When run_js fails, fallback to direct element access."""
    page = MagicMock()
    page.run_js.side_effect = Exception("js fail")
    ta = MagicMock()
    page.ele.return_value = ta
    assert captcha.inject_recaptcha_token(page, "fallback-token") is True
    ta.input.assert_called_once_with("fallback-token")


# -----------------------------------------------------------------
# Integration
# -----------------------------------------------------------------


def test_detect_from_fixture_html():
    """Scan fixture HTML via regex fallback."""
    result = captcha.detect_recaptcha(_FakePage(html=FIXTURE_HTML))
    assert result is not None
    assert result["sitekey"] == "6LfX4v4UAAAAAGnVc6jBmYFhVgX8eQkMZn0R1a2b"
    assert result["is_enterprise"] is False
