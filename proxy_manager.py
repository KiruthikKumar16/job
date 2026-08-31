"""Best-effort acquisition and validation of public proxy endpoints.

Public proxies are inherently unreliable and should not be trusted with
credentials or sensitive traffic.  This module only returns endpoints that
complete a short, unauthenticated health check.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 4
TEST_URL = "https://api.ipify.org?format=json"


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
    """Fetch and concurrently validate up to ``limit`` HTTP/S proxy URLs."""
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
    return working
