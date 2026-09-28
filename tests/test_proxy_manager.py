"""Tests for proxy_manager.py."""
import sys
import os
# Add the current directory to the path so we can import proxy_manager
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from unittest.mock import patch, MagicMock
import pytest
import requests
import time
import proxy_manager
from proxy_manager import test_proxy, get_proxy_pool, _as_url


def test_as_url():
    """Test the _as_url helper function."""
    # Test URLs that already have protocol
    assert _as_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert _as_url("https://proxy.example.com:8080") == "https://proxy.example.com:8080"
    assert _as_url("socks5://proxy.example.com:1080") == "socks5://proxy.example.com:1080"
    assert _as_url("socks4://proxy.example.com:1080") == "socks4://proxy.example.com:1080"

    # Test URLs without protocol (should get http:// prepended)
    assert _as_url("proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert _as_url("192.168.1.100:3128") == "http://192.168.1.100:3128"

    # Test edge cases
    assert _as_url("  proxy.example.com:8080  ") == "http://proxy.example.com:8080"  # strips whitespace
    assert _as_url("") == "http://"  # empty string


@patch('proxy_manager._fetch_proxyscrape')
@patch('proxy_manager._fetch_geonode')
@patch('proxy_manager.test_proxy')
def test_get_proxy_pool_uses_cache_when_valid(mock_test_proxy, mock_fetch_geonode, mock_fetch_proxyscrape):
    """Test that get_proxy_pool returns cached pool when still within TTL."""
    # Save original cache state
    original_cache = proxy_manager._PROXY_POOL_CACHE.copy()
    original_timestamp = proxy_manager._PROXY_POOL_TIMESTAMP
    original_ttl = proxy_manager._PROXY_POOL_TTL

    try:
        # Set up cache with recent timestamp
        proxy_manager._PROXY_POOL_CACHE = ["cached1:8080", "cached2:8080"]
        proxy_manager._PROXY_POOL_TIMESTAMP = time.time() - 100  # 100 seconds ago
        proxy_manager._PROXY_POOL_TTL = 300  # 5 minutes TTL

        # Call get_proxy_pool
        result = proxy_manager.get_proxy_pool(limit=10)

        # Should return cached pool without calling fetchers
        assert result == ["cached1:8080", "cached2:8080"]
        mock_fetch_proxyscrape.assert_not_called()
        mock_fetch_geonode.assert_not_called()
        mock_test_proxy.assert_not_called()
    finally:
        # Restore original cache state
        proxy_manager._PROXY_POOL_CACHE = original_cache
        proxy_manager._PROXY_POOL_TIMESTAMP = original_timestamp
        proxy_manager._PROXY_POOL_TTL = original_ttl


@patch('proxy_manager._fetch_proxyscrape')
@patch('proxy_manager._fetch_geonode')
@patch('proxy_manager.test_proxy')
def test_get_proxy_pool_fetch_new_when_cache_expired(mock_test_proxy, mock_fetch_geonode, mock_fetch_proxyscrape):
    """Test that get_proxy_pool fetches new pool when cache is expired."""
    # Save original cache state
    original_cache = proxy_manager._PROXY_POOL_CACHE.copy()
    original_timestamp = proxy_manager._PROXY_POOL_TIMESTAMP
    original_ttl = proxy_manager._PROXY_POOL_TTL

    try:
        # Set up expired cache
        proxy_manager._PROXY_POOL_CACHE = ["old1:8080"]
        proxy_manager._PROXY_POOL_TIMESTAMP = time.time() - 400  # 400 seconds ago
        proxy_manager._PROXY_POOL_TTL = 300  # 5 minutes TTL

        # Mock fetchers to return some candidates
        mock_fetch_proxyscrape.return_value = ["http://scrape1:8080", "http://scrape2:8080"]
        mock_fetch_geonode.return_value = ["http://geonode1:8080", "http://geonode2:8080"]

        # Mock test_proxy to return alternating True/False to simulate some working proxies
        def mock_test_proxy_side_effect(proxy):
            # Make first two proxies work, rest fail
            if proxy in ["http://scrape1:8080", "http://scrape2:8080"]:
                return True
            return False

        mock_test_proxy.side_effect = mock_test_proxy_side_effect

        # Call get_proxy_pool
        result = proxy_manager.get_proxy_pool(limit=2)

        # Should have fetched new proxies and tested them
        assert mock_fetch_proxyscrape.called
        assert mock_fetch_geonode.called
        assert mock_test_proxy.call_count >= 2  # At least called for the proxies tested

        # Should return working proxies (limited to requested amount)
        assert len(result) <= 2
        # All returned proxies should be from our working set
        for proxy in result:
            assert proxy in ["http://scrape1:8080", "http://scrape2:8080"]
    finally:
        # Restore original cache state
        proxy_manager._PROXY_POOL_CACHE = original_cache
        proxy_manager._PROXY_POOL_TIMESTAMP = original_timestamp
        proxy_manager._PROXY_POOL_TTL = original_ttl