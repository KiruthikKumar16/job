from unittest.mock import Mock

import pandas as pd
import pytest
import requests
from jobmarket import proxy_manager, scraper
from jobmarket.cli import build_parser


@pytest.fixture(autouse=True)
def disable_rate_limit_in_tests(monkeypatch):
    monkeypatch.setattr(scraper, "_MIN_REQUEST_INTERVAL", 0)


def _http_response(status: int, body: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    response._content_consumed = True
    response.url = "https://example.test/jobs"
    return response


def test_proxy_validation_times_out_and_retries(monkeypatch):
    request = Mock(side_effect=requests.Timeout("timed out"))
    monkeypatch.setattr(proxy_manager.requests, "get", request)
    monkeypatch.setattr(proxy_manager.time, "sleep", lambda _delay: None)

    assert proxy_manager.test_proxy("127.0.0.1:8888") is False
    assert request.call_count == proxy_manager.RETRY_ATTEMPTS
    assert all(
        call.kwargs["timeout"] == proxy_manager.REQUEST_TIMEOUT_SECONDS
        for call in request.call_args_list
    )


def test_requests_transport_adds_timeout_to_library_calls(monkeypatch):
    calls = []

    def fake_request(_session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return kwargs

    monkeypatch.setattr(scraper, "_ORIGINAL_SESSION_REQUEST", fake_request)
    session = requests.Session()

    assert (
        session.request("GET", "https://example.test/no-timeout")["timeout"]
        == scraper.REQUEST_TIMEOUT_SECONDS
    )
    assert session.request("GET", "https://example.test/has-timeout", timeout=7)["timeout"] == 7
    assert len(calls) == 2


def test_naukri_retries_429_with_timeout_and_jitter(monkeypatch):
    session = Mock()
    session.get.side_effect = [
        _http_response(429, "rate limited"),
        _http_response(429, "rate limited"),
        _http_response(200, "<html><body>No jobs</body></html>"),
    ]
    monkeypatch.setattr(scraper._naukri_local, "session", session, raising=False)
    monkeypatch.setattr(scraper, "_extract_browser_cards", lambda *_args: pd.DataFrame())
    delays = []
    monkeypatch.setattr(scraper.time, "sleep", delays.append)
    monkeypatch.setattr(scraper.random, "uniform", lambda low, high: high / 2)

    result = scraper._naukri_fetch("data analyst", "Pune", 10)

    assert result.empty
    assert session.get.call_count == 3
    assert all(
        call.kwargs["timeout"] == scraper.REQUEST_TIMEOUT_SECONDS
        for call in session.get.call_args_list
    )
    assert len(delays) == 2
    assert 0 < delays[0] < delays[1]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, "<html>CAPTCHA verify you are human</html>"),
        (403, "<html>Forbidden</html>"),
    ],
)
def test_captcha_failure_is_reported_without_stopping_other_platforms(
    monkeypatch, caplog, status, body
):
    naukri_session = Mock()
    naukri_session.get.return_value = _http_response(status, body)
    monkeypatch.setattr(scraper._naukri_local, "session", naukri_session, raising=False)

    def browser_fallback(term, location, site, *_args):
        if site == "naukri":
            raise scraper.PlatformBlockedError("CAPTCHA challenge")
        return pd.DataFrame()

    def jobspy_fetch(term, location, site, *_args):
        if site == "indeed":
            return pd.DataFrame(
                [
                    {
                        "site": site,
                        "title": "Analyst",
                        "company": "Acme",
                        "location": location,
                        "job_url": "https://example.test/analyst",
                        "description": "",
                    }
                ]
            )
        return pd.DataFrame()

    monkeypatch.setattr(scraper, "_playwright_fetch", browser_fallback)
    monkeypatch.setattr(scraper, "_jobspy_fetch", jobspy_fetch)

    result = scraper.fetch_jobs(["data analyst"], ["Pune"], ["naukri", "indeed"])

    assert result["site"].tolist() == ["indeed"]
    statuses = result.attrs["platform_status"]
    assert statuses["naukri:Pune:data analyst"].startswith("failed:")
    assert statuses["indeed:Pune:data analyst"] == "success"
    assert "platform=naukri" in caplog.text
    assert "term='data analyst'" in caplog.text


def test_empty_results_are_reported_as_empty(monkeypatch):
    monkeypatch.setattr(scraper, "_jobspy_fetch", lambda *_args: pd.DataFrame())
    result = scraper.fetch_jobs(["designer"], ["Delhi"], ["indeed"])
    assert result.empty
    assert result.attrs["platform_status"] == {"indeed:Delhi:designer": "empty"}


def test_malformed_platform_data_is_logged_and_other_query_continues(monkeypatch, caplog):
    def jobspy_fetch(term, location, site, *_args):
        if site == "indeed":
            raise ValueError("malformed response payload")
        return pd.DataFrame(
            [
                {
                    "site": site,
                    "title": "Engineer",
                    "company": "Acme",
                    "location": location,
                    "job_url": "https://example.test/engineer",
                    "description": "",
                }
            ]
        )

    monkeypatch.setattr(scraper, "_jobspy_fetch", jobspy_fetch)
    result = scraper.fetch_jobs(["engineer"], ["Pune"], ["indeed", "linkedin"])

    assert result["site"].tolist() == ["linkedin"]
    assert result.attrs["platform_status"]["indeed:Pune:engineer"].startswith("failed: malformed")
    assert "platform=indeed" in caplog.text
    assert "location='Pune'" in caplog.text


def test_public_proxy_fallback_is_opt_in_and_not_used_with_user_proxy(monkeypatch):
    get_pool = Mock(return_value=["public.example:8080"])
    monkeypatch.setattr(proxy_manager, "get_proxy_pool", get_pool)
    monkeypatch.setattr(scraper, "_jobspy_fetch", Mock(side_effect=RuntimeError("403 forbidden")))
    monkeypatch.setattr(scraper, "_playwright_fetch", lambda *_args: pd.DataFrame())

    scraper.fetch_jobs(["analyst"], ["Pune"], ["indeed"])
    get_pool.assert_not_called()
    scraper.fetch_jobs(
        ["analyst"],
        ["Pune"],
        ["indeed"],
        proxies=["user:secret@proxy:8080"],
        use_public_proxies=True,
    )
    get_pool.assert_not_called()


def test_public_proxy_fallback_runs_only_after_explicit_opt_in(monkeypatch):
    get_pool = Mock(return_value=["public.example:8080"])
    monkeypatch.setattr(proxy_manager, "get_proxy_pool", get_pool)
    frame = pd.DataFrame(
        [
            {
                "site": "indeed",
                "title": "Analyst",
                "company": "Acme",
                "location": "Pune",
                "job_url": "https://example.test/analyst",
                "description": "",
            }
        ]
    )
    jobspy = Mock(side_effect=[RuntimeError("429 rate limited"), frame])
    monkeypatch.setattr(scraper, "_jobspy_fetch", jobspy)

    result = scraper.fetch_jobs(["analyst"], ["Pune"], ["indeed"], use_public_proxies=True)

    assert not result.empty
    get_pool.assert_called_once_with()
    assert jobspy.call_args_list[-1].args[4] == ["public.example:8080"]
    assert result.attrs["platform_status"]["indeed:Pune:analyst"] == "success (public proxy retry)"


def test_public_proxy_pool_rejects_embedded_credentials(monkeypatch):
    request = Mock()
    monkeypatch.setattr(proxy_manager.requests, "get", request)
    assert proxy_manager.test_proxy("user:secret@proxy.example:8080") is False
    request.assert_not_called()


def test_cli_public_proxy_flag_is_false_unless_explicitly_enabled():
    parser = build_parser()
    assert parser.parse_args([]).use_public_proxies is False
    assert parser.parse_args(["--use-public-proxies"]).use_public_proxies is True


def test_malformed_proxy_source_data_is_rejected_and_closed(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"ip": "not-a-list"}}
    request = Mock(return_value=response)
    monkeypatch.setattr(proxy_manager.requests, "get", request)

    with pytest.raises(ValueError, match="malformed JSON"):
        proxy_manager._fetch_geonode(5)

    assert request.call_args.kwargs["timeout"] == 10
    response.close.assert_called_once()
