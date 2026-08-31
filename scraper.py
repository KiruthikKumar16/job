"""Job collection with JobSpy as the primary strategy and Playwright fallback.

Only collect pages you are permitted to access.  Platform markup and access
policies change frequently, so the browser selectors below are deliberately
best-effort rather than a guarantee of coverage.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote_plus, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)
NORMALIZED_COLUMNS = [
    "site", "title", "company", "location", "job_url", "description",
    "date_posted", "salary_min", "salary_max", "currency",
]
METADATA_COLUMNS = ["search_term", "search_location", "scraped_at"]
SUPPORTED_PLATFORMS = {"linkedin", "indeed", "glassdoor", "naukri", "zip_recruiter"}
INDIA_PLATFORMS = ("linkedin", "indeed", "glassdoor", "naukri")
NAUKRI_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15",
)
_naukri_session = requests.Session()


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
            "description": _text(_first(row, ["description", "job_description"])),
            "date_posted": _first(row, ["date_posted", "date", "posted_date"]),
            "salary_min": _first(row, ["min_amount", "salary_min", "min_salary"]),
            "salary_max": _first(row, ["max_amount", "salary_max", "max_salary"]),
            "currency": _text(_first(row, ["currency", "salary_currency"])),
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


def _jobspy_fetch(term: str, location: str, site: str, max_results: int | None, proxies: list[str] | None,
                  country: str, hours_old: int | None = None) -> pd.DataFrame:
    """Run one JobSpy query; imports lazily so non-scraping commands still work."""
    from jobspy import scrape_jobs  # python-jobspy

    request_location = _normalise_glassdoor_location(location, country) if site == "glassdoor" else location
    options: dict[str, Any] = {
        "site_name": [site], "search_term": term, "location": request_location,
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
    result = scrape_jobs(**options)
    jobs = _normalise_jobspy(result if result is not None else pd.DataFrame(), site)
    if not jobs.empty:
        jobs["location"] = jobs["location"].replace("", request_location)
    return jobs


def _naukri_fetch(term: str, location: str, max_results: int | None) -> pd.DataFrame:
    """Fetch Naukri's public search HTML with a persistent, polite session."""
    url = _browser_url(term=term, location=location, site="naukri", country="India")
    headers = {"User-Agent": random.choice(NAUKRI_USER_AGENTS), "Accept-Language": "en-IN,en;q=0.9"}
    try:
        response = _naukri_session.get(url, headers=headers, timeout=30)
        if response.status_code == 406:
            LOGGER.warning("Naukri rejected the request with HTTP 406; skipping this query")
            return _empty_frame()
        response.raise_for_status()
        return _annotate_jobs(_extract_browser_cards(response.text, "naukri", location, max_results), term, location)
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


async def _playwright_fetch(term: str, location: str, site: str, max_results: int, proxy: str | None,
                            country: str) -> pd.DataFrame:
    """Render a public search page and extract semantic job-card attributes."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    launch_options: dict[str, Any] = {"headless": True}
    if proxy:
        launch_options["proxy"] = {"server": f"http://{proxy}" if "://" not in proxy else proxy}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 1000}, locale="en-US",
        )
        # Applies browser-fingerprint compatibility scripts; it does not solve
        # CAPTCHAs or otherwise bypass an access challenge.
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()
        try:
            browser_location = _normalise_glassdoor_location(location, country) if site == "glassdoor" else location
            await page.goto(_browser_url(site, term, browser_location, country), wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(random.randint(1200, 2500))
            for _ in range(2):
                await page.mouse.wheel(0, 1200)
                await page.wait_for_timeout(600)
            html = await page.content()
        finally:
            await browser.close()
    return _extract_browser_cards(html, site, location, max_results)


def _extract_browser_cards(html: str, site: str, fallback_location: str, limit: int | None) -> pd.DataFrame:
    """Use common job-card markup, keeping extraction tolerant of DOM changes."""
    soup = BeautifulSoup(html, "lxml")
    selectors = [
        "li[data-occludable-job-id]", "div.job_seen_beacon", "article[data-testid*=job]",
        "li[class*=job]", "div[class*=job-card]", "article[class*=job]",
    ]
    cards = []
    for selector in selectors:
        cards = soup.select(selector)
        if cards:
            break
    records: list[dict[str, Any]] = []
    for card in cards[:limit]:
        title_node = card.select_one("h2, h3, [class*=title], a[class*=job]")
        company_node = card.select_one("[class*=company], [data-testid*=employer]")
        location_node = card.select_one("[class*=location], [data-testid*=location]")
        link = card.select_one("a[href]")
        text = card.get_text(" ", strip=True)
        if not title_node or not _text(title_node.get_text(" ", strip=True)):
            continue
        href = link.get("href", "") if link else ""
        if href.startswith("/"):
            domains = {"linkedin": "https://www.linkedin.com", "indeed": "https://www.indeed.com",
                       "glassdoor": "https://www.glassdoor.com", "naukri": "https://www.naukri.com",
                       "zip_recruiter": "https://www.ziprecruiter.com"}
            href = domains[site] + href
        records.append({
            "site": site, "title": title_node.get_text(" ", strip=True),
            "company": company_node.get_text(" ", strip=True) if company_node else "",
            "location": location_node.get_text(" ", strip=True) if location_node else fallback_location,
            "job_url": href, "description": text, "date_posted": None,
            "salary_min": None, "salary_max": None, "currency": "",
        })
    return pd.DataFrame.from_records(records, columns=NORMALIZED_COLUMNS)


def fetch_jobs(search_terms: list[str], locations: list[str], platforms: list[str], max_results: int | None = 50,
               proxies: list[str] | None = None, country: str = "India", hours_old: int | None = None) -> pd.DataFrame:
    """Fetch jobs for all query combinations, falling back after access blocks.

    A non-blocking primary failure is logged and skipped; a recognized block
    triggers Playwright for that exact platform/query combination.
    """
    if not search_terms or not locations or (max_results is not None and max_results < 1):
        return _empty_frame()
    proxies = _normalise_user_proxies(proxies)
    selected = [_normalise_site(item) for item in platforms]
    invalid = set(selected) - SUPPORTED_PLATFORMS
    if invalid:
        raise ValueError(f"Unsupported platforms: {', '.join(sorted(invalid))}")
    frames: list[pd.DataFrame] = []
    for index, (term, location, site) in enumerate(
        (query, place, platform)
        for query in search_terms
        for place in locations
        for platform in selected
    ):
        proxy = proxies[index % len(proxies)] if proxies else None
        active_proxies = proxies
        try:
            if site == "naukri":
                time.sleep(random.uniform(1.0, 3.0))
                jobs = _naukri_fetch(term, location, max_results)
            else:
                jobs = _jobspy_fetch(term, location, site, max_results, active_proxies, country, hours_old)
                jobs = _annotate_jobs(jobs, term, location)
            frames.append(jobs)
            if jobs.empty and site in {"glassdoor", "naukri"}:
                LOGGER.warning("%s returned no records for %s; its response may have been blocked or rejected", site.title(), location)
            else:
                LOGGER.info("JobSpy completed: %s / %s / %s", site, term, location)
        except Exception as error:
            if not _is_block_error(error) and not (site == "naukri" and "406" in str(error)):
                LOGGER.error("JobSpy failed for %s: %s", site, error)
                continue
            if not active_proxies:
                try:
                    from proxy_manager import get_proxy_pool

                    active_proxies = get_proxy_pool()
                    if active_proxies:
                        LOGGER.info("Retrying %s with %d validated public proxies", site, len(active_proxies))
                        frames.append(_jobspy_fetch(term, location, site, max_results, active_proxies, country, hours_old))
                        continue
                    LOGGER.warning("No working public proxies available for %s", site)
                except Exception as proxy_error:
                    LOGGER.warning("Proxy retry failed for %s: %s", site, proxy_error)
            LOGGER.warning("JobSpy blocked for %s; using Playwright fallback", site)
            try:
                browser_proxy = active_proxies[index % len(active_proxies)] if active_proxies else proxy
                frames.append(asyncio.run(_playwright_fetch(term, location, site, max_results, browser_proxy, country)))
            except Exception as fallback_error:
                LOGGER.error("Browser fallback failed for %s: %s", site, fallback_error)
    if not frames:
        return _empty_frame()
    result = pd.concat(frames, ignore_index=True).reindex(columns=NORMALIZED_COLUMNS + METADATA_COLUMNS)
    result["date_posted"] = pd.to_datetime(result["date_posted"], errors="coerce", utc=True)
    result = result.drop_duplicates(subset=["job_url"], keep="first")
    return result.reset_index(drop=True)
