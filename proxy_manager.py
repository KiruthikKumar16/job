"""Best-effort acquisition and validation of public proxy endpoints.

Public proxies are inherently unreliable and should not be trusted with
credentials or sensitive traffic.  This module only returns endpoints that
complete a short, unauthenticated health check.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 4
TEST_URL = "https://api.ipify.org?format=json"

# Cache for proxy pool with TTL (time-to-live) in seconds.
_PROXY_POOL_CACHE: list[str] = []
_PROXY_POOL_TIMESTAMP: float = 0.0
_PROXY_POOL_LOCK = threading.Lock()
# TTL can be configured via environment variable, default to 300 seconds (5 minutes).
_PROXY_POOL_TTL = float(os.getenv("PROXY_POOL_TTL", "300.0"))


def _as_url(proxy: str) -> str:
    value = proxy.strip()
    return value if value.startswith(("http://", "https://", "socks5://", "socks4://")) else f"http://{value}"


def test_proxy(proxy: str) -> bool:
    """Return whether a proxy reaches a small public IP endpoint quickly."""
    proxy_url = _as_url(proxy)
    try:
        started = perf_counter()
        response = requests.get(
            TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "job-extraction-engine/1.0"},
        )
        elapsed = perf_counter() - started
        return response.ok and elapsed <= REQUEST_TIMEOUT_SECONDS
    except requests.RequestException:
        return False


def _fetch_proxyscrape(limit: int) -> list[str]:
    response = requests.get(
        "https://api.proxyscrape.com/v2/",
        params={"request": "getproxies", "protocol": "http", "timeout": 10000, "country": "all", "ssl": "all", "anonymity": "elite"},
        timeout=10,
        headers={"User-Agent": "job-extraction-engine/1.0"},
    )
    response.raise_for_status()
    return [f"http://{line.strip()}" for line in response.text.splitlines() if line.strip()][:limit]


def _fetch_geonode(limit: int) -> list[str]:
    response = requests.get(
        "https://proxylist.geonode.com/api/proxy-list",
        params={"limit": limit, "page": 1, "sort_by": "lastChecked", "sort_type": "desc", "protocols": "http,https"},
        timeout=10,
        headers={"User-Agent": "job-extraction-engine/1.0"},
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return [f"http://{item['ip']}:{item['port']}" for item in payload.get("data", []) if item.get("ip") and item.get("port")]


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
            return list(_PROXY_POOL_CACHE)

    # If we are here, we need to fetch a new pool
    if limit < 1:
        return []
    candidates: list[str] = []
    source_limit = max(limit * 3, 20)
    for fetcher in (_fetch_proxyscrape, _fetch_geonode):
        try:
            candidates.extend(fetcher(source_limit))
        except requests.RequestException as error:
            LOGGER.warning("Proxy source unavailable: %s", error)
    unique_candidates = list(dict.fromkeys(candidates))
    working: list[str] = []
    with ThreadPoolExecutor(max_workers=min(12, len(unique_candidates) or 1)) as executor:
        futures = {executor.submit(test_proxy, proxy): proxy for proxy in unique_candidates}
        for future in as_completed(futures):
            try:
                if future.result():
                    working.append(futures[future])
                    if len(working) >= limit:
                        break
            except requests.RequestException:
                continue
    LOGGER.info("Validated %d of %d public proxies", len(working), len(unique_candidates))

    # Update the cache
    with _PROXY_POOL_LOCK:
        _PROXY_POOL_CACHE = working
        _PROXY_POOL_TIMESTAMP = current_time

    return working
