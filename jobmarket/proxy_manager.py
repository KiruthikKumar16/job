"""Best-effort acquisition and validation of public proxy endpoints.

Public proxies are inherently unreliable and should not be trusted with
credentials or sensitive traffic.  This module only returns endpoints that
complete a short, unauthenticated health check.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import requests
from requests import Response

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 4
RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.25
TEST_URL = "https://api.ipify.org?format=json"

# Cache for proxy pool with TTL (time-to-live) in seconds.
_PROXY_POOL_CACHE: list[str] = []
_PROXY_POOL_TIMESTAMP: float = 0.0
_PROXY_POOL_LOCK = threading.Lock()
# TTL can be configured via environment variable, default to 300 seconds (5 minutes).
_PROXY_POOL_TTL = float(os.getenv("PROXY_POOL_TTL", "300.0"))


def _as_url(proxy: str) -> str:
    value = proxy.strip()
    return (
        value
        if value.startswith(("http://", "https://", "socks5://", "socks4://"))
        else f"http://{value}"
    )


def _has_proxy_credentials(proxy: str) -> bool:
    parsed = urlsplit(_as_url(proxy))
    return bool(parsed.username or parsed.password)


def _request_with_retry(url: str, *, timeout: int, **kwargs: Any) -> Response:
    """Issue an HTTP request with an explicit timeout and jittered retries."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.get(url, timeout=timeout, **kwargs)
            if response.status_code not in {429, 500, 502, 503, 504}:
                return response
            error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
            response.close()
            raise error
        except requests.RequestException as error:
            error_response = error.response
            status = error_response.status_code if error_response is not None else None
            retryable = isinstance(
                error, (requests.Timeout, requests.ConnectionError)
            ) or status in {429, 500, 502, 503, 504}
            if not retryable or attempt == RETRY_ATTEMPTS:
                raise
            delay = random.uniform(0, min(4.0, RETRY_BASE_SECONDS * (2 ** (attempt - 1))))
            LOGGER.warning(
                "HTTP request to %s failed (attempt %d/%d); retrying in %.2fs: %s",
                url,
                attempt,
                RETRY_ATTEMPTS,
                delay,
                error,
            )
            time.sleep(delay)
    raise RuntimeError("HTTP retry loop ended unexpectedly")


def test_proxy(proxy: str) -> bool:
    """Return whether a proxy reaches a small public IP endpoint quickly."""
    try:
        has_credentials = _has_proxy_credentials(proxy)
    except ValueError:
        LOGGER.warning("Skipping malformed proxy endpoint during public-proxy validation")
        return False
    if has_credentials:
        LOGGER.warning("Skipping proxy with embedded credentials during public-proxy validation")
        return False
    proxy_url = _as_url(proxy)
    try:
        started = perf_counter()
        response = _request_with_retry(
            TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "job-extraction-engine/1.0"},
        )
        try:
            elapsed = perf_counter() - started
            return response.ok and elapsed <= REQUEST_TIMEOUT_SECONDS
        finally:
            response.close()
    except requests.RequestException:
        return False


def _fetch_proxyscrape(limit: int) -> list[str]:
    response = _request_with_retry(
        "https://api.proxyscrape.com/v2/",
        params={
            "request": "getproxies",
            "protocol": "http",
            "timeout": 10000,
            "country": "all",
            "ssl": "all",
            "anonymity": "elite",
        },
        timeout=10,
        headers={"User-Agent": "job-extraction-engine/1.0"},
    )
    try:
        response.raise_for_status()
        return [f"http://{line.strip()}" for line in response.text.splitlines() if line.strip()][
            :limit
        ]
    finally:
        response.close()


def _fetch_geonode(limit: int) -> list[str]:
    response = _request_with_retry(
        "https://proxylist.geonode.com/api/proxy-list",
        params={
            "limit": limit,
            "page": 1,
            "sort_by": "lastChecked",
            "sort_type": "desc",
            "protocols": "http,https",
        },
        timeout=10,
        headers={"User-Agent": "job-extraction-engine/1.0"},
    )
    try:
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
            raise ValueError("Proxy source returned malformed JSON")
        return [
            f"http://{item['ip']}:{item['port']}"
            for item in payload["data"]
            if isinstance(item, dict) and item.get("ip") and item.get("port")
        ]
    finally:
        response.close()


def get_proxy_pool(limit: int = 20) -> list[str]:
    """Fetch and concurrently validate up to ``limit`` HTTP/S proxy URLs.
    Returns a cached proxy pool if it is still within the TTL window.
    """
    global _PROXY_POOL_CACHE, _PROXY_POOL_TIMESTAMP
    # Check if we have a valid cached pool
    current_time = time.time()
    with _PROXY_POOL_LOCK:
        if _PROXY_POOL_CACHE and (current_time - _PROXY_POOL_TIMESTAMP) < _PROXY_POOL_TTL:
            # Return a copy to avoid accidental modification of the cached list
            return [proxy for proxy in _PROXY_POOL_CACHE if not _has_proxy_credentials(proxy)]

    # If we are here, we need to fetch a new pool
    if limit < 1:
        return []
    candidates: list[str] = []
    source_limit = max(limit * 3, 20)
    for fetcher in (_fetch_proxyscrape, _fetch_geonode):
        try:
            candidates.extend(fetcher(source_limit))
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            LOGGER.warning("Public proxy source %s failed: %s", fetcher.__name__, error)
    unique_candidates = [
        proxy for proxy in dict.fromkeys(candidates) if not _has_proxy_credentials(proxy)
    ]
    working: list[str] = []
    with ThreadPoolExecutor(max_workers=min(12, len(unique_candidates) or 1)) as executor:
        futures = {executor.submit(test_proxy, proxy): proxy for proxy in unique_candidates}
        for future in as_completed(futures):
            try:
                if future.result():
                    working.append(futures[future])
                    if len(working) >= limit:
                        break
            except Exception as error:
                LOGGER.warning("Public proxy validation worker failed: %s", error)
                continue
    LOGGER.info("Validated %d of %d public proxies", len(working), len(unique_candidates))

    # Update the cache
    with _PROXY_POOL_LOCK:
        _PROXY_POOL_CACHE = working
        _PROXY_POOL_TIMESTAMP = current_time

    return working
