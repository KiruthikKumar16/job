"""Job collection with JobSpy as the primary strategy and Playwright fallback.

Only collect pages you are permitted to access.  Platform markup and access
policies change frequently, so the browser selectors below are deliberately
best-effort rather than a guarantee of coverage.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, TypeVar, cast
from urllib.parse import quote_plus, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

# Minimum interval between requests to the same site (in seconds). Configurable via environment variable.
_MIN_REQUEST_INTERVAL = float(os.getenv("JOB_SCRAPER_MIN_REQUEST_INTERVAL", "1.0"))
_last_request_times: dict[str, float] = {}
_request_lock = threading.Lock()


def _rate_limit_site(site: str) -> None:
    """Ensure at least _MIN_REQUEST_INTERVAL seconds have passed since the last request to the given site.
    This is thread-safe and uses a lock to coordinate between threads.
    """
    with _request_lock:
        now = time.time()
        last_time = _last_request_times.get(site)
        if last_time is not None:
            elapsed = now - last_time
            if elapsed < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_times[site] = time.time()


NORMALIZED_COLUMNS = [
    "site",
    "title",
    "company",
    "location",
    "job_url",
    "description",
    "description_raw",
    "description_status",
    "date_posted",
    "salary_min",
    "salary_max",
    "currency",
    "card_summary",
]
METADATA_COLUMNS = ["search_term", "search_location", "scraped_at"]
SUPPORTED_PLATFORMS = {"linkedin", "indeed", "glassdoor", "naukri", "zip_recruiter"}
INDIA_PLATFORMS = ("linkedin", "indeed", "glassdoor", "naukri")
NAUKRI_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15",
)
_naukri_local = threading.local()
REQUEST_TIMEOUT_SECONDS = 30
RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.5

# python-jobspy 1.1.82 creates requests.Session instances in its own worker
# threads and omits timeout on several platform calls. Add a process-wide
# default at requests' transport boundary; explicit per-call timeouts remain
# unchanged, and this also covers those library-owned worker threads.
_ORIGINAL_SESSION_REQUEST: Callable[..., requests.Response] = cast(
    Callable[..., requests.Response],
    getattr(
        requests.sessions.Session.request,
        "_jobmarket_original",
        requests.sessions.Session.request,
    ),
)
_ORIGINAL_REQUEST_ATTRIBUTE = "_jobmarket_original"
_REQUEST_HOOK_ATTRIBUTE = "request"


def _session_request_with_timeout(
    session: requests.Session, method: str, url: str | bytes, **kwargs: Any
) -> requests.Response:
    if kwargs.get("timeout") is None:
        kwargs["timeout"] = REQUEST_TIMEOUT_SECONDS
    return _ORIGINAL_SESSION_REQUEST(session, method, url, **kwargs)


setattr(_session_request_with_timeout, _ORIGINAL_REQUEST_ATTRIBUTE, _ORIGINAL_SESSION_REQUEST)
setattr(requests.sessions.Session, _REQUEST_HOOK_ATTRIBUTE, _session_request_with_timeout)


class PlatformBlockedError(RuntimeError):
    """Raised when a platform returns an explicit block or CAPTCHA page."""


def _retryable_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status in {429, 500, 502, 503, 504}:
        return True
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    return bool(re.search(r"\b(429|500|502|503|504)\b", str(error)))


_T = TypeVar("_T")


def _retry_call(call: Callable[[], _T], *, platform: str, term: str, location: str) -> _T:
    """Retry transient network failures with bounded exponential jitter."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return call()
        except Exception as error:
            if attempt == RETRY_ATTEMPTS or not _retryable_error(error):
                raise
            ceiling = min(8.0, RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
            delay = random.uniform(0, ceiling)
            LOGGER.warning(
                "%s transient request failure for term=%r location=%r (attempt %d/%d); "
                "retrying in %.2fs: %s",
                platform,
                term,
                location,
                attempt,
                RETRY_ATTEMPTS,
                delay,
                _safe_error(error),
            )
            time.sleep(delay)
    raise RuntimeError("Retry loop ended unexpectedly")


def _is_challenge_page(html: str) -> bool:
    return bool(
        re.search(
            r"captcha|verify\s+you\s+are\s+human|unusual\s+traffic|access\s+denied|robot\s+check",
            html or "",
            re.I,
        )
    )


def _safe_error(error: Exception) -> str:
    return re.sub(r"(?i)(?:https?://)?[^/@\s]+@(?=[^/\s:]+:\d)", "***@", str(error))


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=NORMALIZED_COLUMNS)


def _normalise_site(site: str) -> str:
    aliases = {"ziprecruiter": "zip_recruiter", "zip-recruiter": "zip_recruiter"}
    return aliases.get(site.strip().lower(), site.strip().lower())


def _normalise_user_proxies(proxies: list[str] | None) -> list[str] | None:
    """Validate CLI proxy values and convert URLs to JobSpy's host:port form."""
    if not proxies:
        return None
    normalised: list[str] = []
    for value in proxies:
        value = value.replace("\\", "").strip()
        parsed = urlparse(value if "://" in value else f"http://{value}")
        if parsed.scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname:
            raise ValueError(f"Invalid proxy URL: {value!r}")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"Invalid proxy port in {value!r}; use a numeric port") from error
        if port is None:
            raise ValueError(f"Proxy needs a numeric port: {value!r}")
        credentials = ""
        if parsed.username:
            credentials = parsed.username
            if parsed.password:
                credentials += f":{parsed.password}"
            credentials += "@"
        normalised.append(f"{credentials}{parsed.hostname}:{port}")
    return normalised


def _normalise_glassdoor_location(location: str, country: str) -> str:
    """Give Glassdoor the city-country form expected by its search endpoint."""
    value = _text(location)
    if not value:
        return value
    if "," in value or value.casefold().endswith(country.casefold()):
        return value
    return f"{value}, {country}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _first(row: pd.Series, names: Iterable[str]) -> Any:
    for name in names:
        if name in row.index and pd.notna(row[name]) and str(row[name]).strip():
            return row[name]
    return None


def _strip_html(value: Any) -> str:
    """Return readable plain text from a full description or HTML fragment."""
    text = _text(value)
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    for non_content in soup(("script", "style", "noscript")):
        non_content.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _description_fields(value: Any) -> tuple[str, str]:
    text = _strip_html(value)
    return text, "ok" if text else "missing"


def _fetch_detail_description(
    job_url: str, *, platform: str, term: str, location: str
) -> tuple[str, str]:
    """Fetch a public detail page as a fallback when JobSpy lacks a description."""
    parsed = urlparse(job_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "", "missing"

    response = None
    try:

        def request_detail() -> requests.Response:
            detail_response = requests.get(
                job_url,
                headers={"User-Agent": random.choice(NAUKRI_USER_AGENTS)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if detail_response.status_code == 429:
                try:
                    detail_response.close()
                except (AttributeError, OSError) as close_error:
                    LOGGER.debug("Could not close HTTP 429 detail response: %s", close_error)
                detail_response.raise_for_status()
            return detail_response

        response = _retry_call(
            request_detail,
            platform=platform,
            term=term,
            location=location,
        )
        if response.status_code in {401, 403}:
            return "", "blocked"
        response.raise_for_status()
        markup = response.text or ""
        if _is_challenge_page(markup):
            return "", "blocked"
        text = _strip_html(markup)
        return (text, "ok") if text else ("", "missing")
    except Exception as error:
        if _is_block_error(error):
            LOGGER.warning(
                "Description detail page blocked for platform=%s term=%r location=%r url=%r: %s",
                platform,
                term,
                location,
                job_url,
                _safe_error(error),
            )
            return "", "blocked"
        LOGGER.warning(
            "Description detail fetch failed for platform=%s term=%r location=%r url=%r: %s",
            platform,
            term,
            location,
            job_url,
            _safe_error(error),
        )
        return "", "missing"
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                LOGGER.debug(
                    "Could not close detail response for platform=%s url=%r", platform, job_url
                )


def _complete_descriptions(
    frame: pd.DataFrame, *, platform: str, term: str, location: str
) -> pd.DataFrame:
    """Apply the JobSpy -> detail page -> missing description fallback chain."""
    result = frame.copy()
    for column in ("description", "description_raw", "description_status", "job_url"):
        if column not in result:
            result[column] = ""
    for index, row in result.iterrows():
        source_text = _text(row.get("description_raw")) or _text(row.get("description"))
        text, status = _description_fields(source_text)
        if text:
            result.at[index, "description"] = text
            result.at[index, "description_raw"] = text
            result.at[index, "description_status"] = "ok"
            continue
        _rate_limit_site(platform)
        detail, detail_status = _fetch_detail_description(
            _text(row.get("job_url")),
            platform=platform,
            term=term,
            location=location,
        )
        result.at[index, "description"] = detail
        result.at[index, "description_raw"] = detail
        result.at[index, "description_status"] = detail_status
    return result


def _normalise_jobspy(raw: pd.DataFrame, site: str) -> pd.DataFrame:
    """Map JobSpy's version-dependent column names to the public schema."""
    records: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        record = {
            "site": _text(_first(row, ["site"])) or site,
            "title": _text(_first(row, ["title", "job_title"])),
            "company": _text(_first(row, ["company", "company_name"])),
            "location": _text(_first(row, ["location", "job_location"])),
            "job_url": _text(_first(row, ["job_url", "url", "job_url_direct"])),
            "description": _description_fields(_first(row, ["description", "job_description"]))[0],
            "description_raw": _description_fields(_first(row, ["description", "job_description"]))[
                0
            ],
            "description_status": _description_fields(
                _first(row, ["description", "job_description"])
            )[1],
            "date_posted": _first(row, ["date_posted", "date", "posted_date"]),
            "salary_min": _first(row, ["min_amount", "salary_min", "min_salary"]),
            "salary_max": _first(row, ["max_amount", "salary_max", "max_salary"]),
            "currency": _text(_first(row, ["currency", "salary_currency"])),
            "card_summary": None,
        }
        records.append(record)
    return pd.DataFrame.from_records(records, columns=NORMALIZED_COLUMNS)


def _annotate_jobs(frame: pd.DataFrame, term: str, location: str) -> pd.DataFrame:
    result = frame.copy()
    result["search_term"] = term
    result["search_location"] = location
    result["scraped_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _is_block_error(error: Exception) -> bool:
    message = str(error).lower()
    markers = ("403", "429", "rate limit", "captcha", "access denied", "anti-bot", "blocked")
    return any(marker in message for marker in markers)


def _jobspy_fetch(
    term: str,
    location: str,
    site: str,
    max_results: int | None,
    proxies: list[str] | None,
    country: str,
    hours_old: int | None = None,
) -> pd.DataFrame:
    """Run one JobSpy query; imports lazily so non-scraping commands still work."""
    from jobspy import scrape_jobs  # python-jobspy

    request_location = (
        _normalise_glassdoor_location(location, country) if site == "glassdoor" else location
    )
    options: dict[str, Any] = {
        "site_name": [site],
        "search_term": term,
        "location": request_location,
        "results_wanted": max_results if max_results is not None else 10_000,
    }
    if site in {"indeed", "glassdoor"}:
        options["country_indeed"] = country
    if site == "linkedin":
        options["linkedin_fetch_description"] = True
    if hours_old is not None:
        options["hours_old"] = hours_old
    if proxies:
        # JobSpy round-robins this list. Its accepted format is host:port or
        # user:password@host:port; use the same address format in --proxies.
        options["proxies"] = proxies
    result = _retry_call(
        lambda: scrape_jobs(**options),
        platform=site,
        term=term,
        location=location,
    )
    jobs = _normalise_jobspy(result if result is not None else pd.DataFrame(), site)
    if not jobs.empty:
        jobs["location"] = jobs["location"].replace("", request_location)
    return jobs


def _naukri_fetch(term: str, location: str, max_results: int | None) -> pd.DataFrame:
    """Fetch Naukri's public search HTML with a persistent, polite session per thread."""
    url = _browser_url(term=term, location=location, site="naukri", country="India")
    headers = {"User-Agent": random.choice(NAUKRI_USER_AGENTS), "Accept-Language": "en-IN,en;q=0.9"}
    # Get or create a session for the current thread
    session = getattr(_naukri_local, "session", None)
    if session is None:
        session = requests.Session()
        _naukri_local.session = session
    try:

        def request() -> requests.Response:
            response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            try:
                if response.status_code != 406:
                    response.raise_for_status()
            except Exception:
                response.close()
                raise
            return response

        response = _retry_call(request, platform="naukri", term=term, location=location)
        try:
            if response.status_code == 406:
                LOGGER.warning(
                    "Naukri rejected the request for term=%r location=%r with HTTP 406",
                    term,
                    location,
                )
                return _empty_frame()
            if _is_challenge_page(response.text):
                raise PlatformBlockedError("Naukri returned a CAPTCHA or access challenge page")
            return _annotate_jobs(
                _extract_browser_cards(response.text, "naukri", location, max_results),
                term,
                location,
            )
        finally:
            response.close()
    except requests.RequestException as error:
        if "406" in str(error):
            LOGGER.warning("Naukri returned HTTP 406; skipping this query")
            return _empty_frame()
        raise


def _browser_url(site: str, term: str, location: str, country: str) -> str:
    query, place = quote_plus(term), quote_plus(location)
    urls = {
        "linkedin": f"https://www.linkedin.com/jobs/search/?keywords={query}&location={place}",
        "indeed": f"https://{'in.indeed.com' if country.casefold() == 'india' else 'www.indeed.com'}/jobs?q={query}&l={place}",
        "glassdoor": f"https://{'www.glassdoor.co.in' if country.casefold() == 'india' else 'www.glassdoor.com'}/Job/jobs.htm?sc.keyword={query}",
        "naukri": f"https://www.naukri.com/{quote_plus(term).replace('+', '-')}-jobs-in-{quote_plus(location).replace('+', '-')}",
        "zip_recruiter": f"https://www.ziprecruiter.com/jobs-search?search={query}&location={place}",
    }
    return urls[site]


async def _playwright_fetch(
    term: str, location: str, site: str, max_results: int | None, proxy: str | None, country: str
) -> pd.DataFrame:
    """Render a public search page and extract semantic job-card attributes."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    launch_options: dict[str, Any] = {"headless": True}
    if proxy:
        launch_options["proxy"] = {"server": f"http://{proxy}" if "://" not in proxy else proxy}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
        )
        # Applies browser-fingerprint compatibility scripts; it does not solve
        # CAPTCHAs or otherwise bypass an access challenge.
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()
        page.set_default_navigation_timeout(45_000)
        page.set_default_timeout(45_000)
        try:
            browser_location = (
                _normalise_glassdoor_location(location, country)
                if site == "glassdoor"
                else location
            )
            await page.goto(
                _browser_url(site, term, browser_location, country),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            await page.wait_for_timeout(random.randint(1200, 2500))
            for _ in range(2):
                await page.mouse.wheel(0, 1200)
                await page.wait_for_timeout(600)
            html = await page.content()
            if _is_challenge_page(html):
                raise PlatformBlockedError(f"{site} returned a CAPTCHA or access challenge page")
        finally:
            await browser.close()
    return _extract_browser_cards(html, site, location, max_results)


def _extract_browser_cards(
    html: str, site: str, fallback_location: str, limit: int | None
) -> pd.DataFrame:
    """Use common job-card markup, keeping extraction tolerant of DOM changes."""
    soup = BeautifulSoup(html, "lxml")
    selectors = [
        "li[data-occludable-job-id]",
        "div.job_seen_beacon",
        "article[data-testid*=job]",
        "li[class*=job]",
        "div[class*=job-card]",
        "article[class*=job]",
    ]
    cards: list[Any] = []
    for selector in selectors:
        cards = soup.select(selector)
        if cards:
            break

    # Site-specific selectors for job title/link elements to improve reliability
    link_selectors = {
        "linkedin": "h3.base-search-card__title a, h3 a[class*=job-title], a[data-control-name*=job_search_result]",
        "indeed": "h2.jobTitle a[data-jk], h2 a[class*=jobTitle], a[data-hn*=job]",
        "glassdoor": "a.jobLink, a[data-testid*=job-title], a[class*=jobLink]",
        "naukri": "a.title, a[class*=title], h2 a",
        "zip_recruiter": "h2.job_title a, a[class*=job_title], a[data-testid*=job-title]",
    }

    records: list[dict[str, Any]] = []
    for card in cards[:limit]:
        title_node = card.select_one("h2, h3, [class*=title], a[class*=job]")
        company_node = card.select_one("[class*=company], [data-testid*=employer]")
        location_node = card.select_one("[class*=location], [data-testid*=location]")

        # Use site-specific selector for link, fallback to generic if not found or not matching site
        link = None
        if site in link_selectors:
            link = card.select_one(link_selectors[site])
        if not link:  # Fallback to generic selector
            link = card.select_one("a[href]")

        text = card.get_text(" ", strip=True)
        if not title_node or not _text(title_node.get_text(" ", strip=True)):
            continue
        href = link.get("href", "") if link else ""
        if href.startswith("/"):
            domains = {
                "linkedin": "https://www.linkedin.com",
                "indeed": "https://www.indeed.com",
                "glassdoor": "https://www.glassdoor.com",
                "naukri": "https://www.naukri.com",
                "zip_recruiter": "https://www.ziprecruiter.com",
            }
            href = domains[site] + href
        records.append(
            {
                "site": site,
                "title": title_node.get_text(" ", strip=True),
                "company": company_node.get_text(" ", strip=True) if company_node else "",
                "location": location_node.get_text(" ", strip=True)
                if location_node
                else fallback_location,
                "job_url": href,
                "description": None,
                "date_posted": None,
                "salary_min": None,
                "salary_max": None,
                "currency": "",
                "card_summary": text,
            }
        )
    return pd.DataFrame.from_records(records, columns=NORMALIZED_COLUMNS)


def fetch_jobs(
    search_terms: list[str],
    locations: list[str],
    platforms: list[str],
    max_results: int | None = 50,
    proxies: list[str] | None = None,
    country: str = "India",
    hours_old: int | None = None,
    proxy: str | None = None,
    use_public_proxies: bool = False,
) -> pd.DataFrame:
    """Fetch jobs for all query combinations, falling back after access blocks.

    A failed platform/query is logged and recorded while other combinations
    continue. Public proxies are considered only when explicitly enabled.
    """
    if not search_terms or not locations or (max_results is not None and max_results < 1):
        return _empty_frame()
    proxies = _normalise_user_proxies(proxies)
    selected = [_normalise_site(item) for item in platforms]
    invalid = set(selected) - SUPPORTED_PLATFORMS
    if invalid:
        raise ValueError(f"Unsupported platforms: {', '.join(sorted(invalid))}")
    frames: list[pd.DataFrame] = []
    platform_status: dict[str, str] = {}
    for term, location, site in (
        (query, place, platform)
        for query in search_terms
        for place in locations
        for platform in selected
    ):
        query_key = f"{site}:{location}:{term}"
        # Use the passed, user-configured proxy for this call when provided.
        effective_proxies = [proxy] if proxy else proxies
        try:
            # Apply rate limiting for this site
            _rate_limit_site(site)
            if site == "naukri":
                # Keep the existing random sleep for Naukri as well
                time.sleep(random.uniform(1.0, 3.0))
                jobs = _naukri_fetch(term, location, max_results)
                jobs = _complete_descriptions(jobs, platform=site, term=term, location=location)
                jobs = _annotate_jobs(jobs, term, location)
            else:
                jobs = _jobspy_fetch(
                    term, location, site, max_results, effective_proxies, country, hours_old
                )
                jobs = _complete_descriptions(jobs, platform=site, term=term, location=location)
                jobs = _annotate_jobs(jobs, term, location)
            frames.append(jobs)
            platform_status[query_key] = "success" if not jobs.empty else "empty"
            if jobs.empty and site in {"glassdoor", "naukri"}:
                LOGGER.warning(
                    "%s returned no records for %s; its response may have been blocked or rejected",
                    site.title(),
                    location,
                )
            else:
                LOGGER.info("JobSpy completed: %s / %s / %s", site, term, location)
        except Exception as error:
            LOGGER.error(
                "Scrape failed for platform=%s term=%r location=%r: %s",
                site,
                term,
                location,
                _safe_error(error),
            )
            if not _is_block_error(error) and not (site == "naukri" and "406" in str(error)):
                platform_status[query_key] = f"failed: {_safe_error(error)}"
                continue
            block_reason = _safe_error(error)
            if use_public_proxies and not proxies and not proxy:
                try:
                    from jobmarket.proxy_manager import get_proxy_pool

                    public_proxies = get_proxy_pool()
                    if public_proxies:
                        LOGGER.info(
                            "Retrying platform=%s term=%r location=%r through %d opt-in public proxies",
                            site,
                            term,
                            location,
                            len(public_proxies),
                        )
                        _rate_limit_site(site)
                        jobs = _jobspy_fetch(
                            term, location, site, max_results, public_proxies, country, hours_old
                        )
                        jobs = _complete_descriptions(
                            jobs, platform=site, term=term, location=location
                        )
                        jobs = _annotate_jobs(jobs, term, location)
                        frames.append(jobs)
                        platform_status[query_key] = (
                            "success (public proxy retry)"
                            if not jobs.empty
                            else f"failed: {block_reason}; public proxy retry returned no jobs"
                        )
                        continue
                    LOGGER.warning(
                        "No validated public proxies for platform=%s term=%r location=%r",
                        site,
                        term,
                        location,
                    )
                except Exception as proxy_error:
                    LOGGER.error(
                        "Opt-in public proxy retry failed for platform=%s term=%r location=%r: %s",
                        site,
                        term,
                        location,
                        _safe_error(proxy_error),
                    )
            LOGGER.warning(
                "Blocked platform=%s term=%r location=%r; trying browser fallback",
                site,
                term,
                location,
            )
            try:
                # Apply rate limiting for Playwright fallback
                _rate_limit_site(site)
                jobs = asyncio.run(
                    _playwright_fetch(term, location, site, max_results, proxy, country)
                )
                jobs = _complete_descriptions(jobs, platform=site, term=term, location=location)
                jobs = _annotate_jobs(jobs, term, location)
                frames.append(jobs)
                platform_status[query_key] = (
                    "success (browser fallback)"
                    if not jobs.empty
                    else f"failed: {block_reason}; browser fallback returned no jobs"
                )
            except Exception as fallback_error:
                LOGGER.error(
                    "Browser fallback failed for platform=%s term=%r location=%r: %s",
                    site,
                    term,
                    location,
                    _safe_error(fallback_error),
                )
                platform_status[query_key] = f"failed: {_safe_error(fallback_error)}"
    if not frames:
        result = _empty_frame()
        result.attrs["platform_status"] = platform_status
        return result
    result = pd.concat(frames, ignore_index=True).reindex(
        columns=NORMALIZED_COLUMNS + METADATA_COLUMNS
    )
    result["date_posted"] = pd.to_datetime(result["date_posted"], errors="coerce", utc=True)
    result = result.drop_duplicates(subset=["job_url"], keep="first")
    result.attrs["platform_status"] = platform_status
    return result.reset_index(drop=True)
