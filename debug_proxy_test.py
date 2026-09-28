#!/usr/bin/env python3
"""Debug script to reproduce the proxy manager test issue."""

import sys
import os
# Add the current directory to the path so we can import proxy_manager
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from unittest.mock import patch
import proxy_manager

def test_no_working_proxies():
    """Reproduce the failing test."""
    print("Testing get_proxy_pool with no working proxies...")

    # Clear any existing cache to simulate fresh start
    proxy_manager._PROXY_POOL_CACHE = []
    proxy_manager._PROXY_POOL_TIMESTAMP = 0.0

    with patch('proxy_manager._fetch_proxyscrape') as mock_fetch_proxyscrape, \
         patch('proxy_manager._fetch_geonode') as mock_fetch_geonode, \
         patch('proxy_manager.test_proxy') as mock_test_proxy:

        # Mock fetchers to return candidates
        mock_fetch_proxyscrape.return_value = ["http://scrape1:8080", "http://scrape2:8080"]
        mock_fetch_geonode.return_value = ["http://geonode1:8080", "http://geonode2:8080"]

        # Mock test_proxy to always return False (no working proxies)
        mock_test_proxy.return_value = False

        # Check initial cache state
        print(f"Initial cache: {proxy_manager._PROXY_POOL_CACHE}")
        print(f"Initial timestamp: {proxy_manager._PROXY_POOL_TIMESTAMP}")

        # Call get_proxy_pool
        result = proxy_manager.get_proxy_pool(limit=10)

        print(f"Result: {result}")
        print(f"Expected: []")

        # Check final cache state
        print(f"Final cache: {proxy_manager._PROXY_POOL_CACHE}")
        print(f"Final timestamp: {proxy_manager._PROXY_POOL_TIMESTAMP}")

        # Check what was called
        print(f"_fetch_proxyscrape called: {mock_fetch_proxyscrape.called}")
        print(f"_fetch_geonode called: {mock_fetch_geonode.called}")
        print(f"test_proxy call count: {mock_test_proxy.call_count}")

        if result != []:
            print(f"ERROR: Expected empty list but got {result}")
            return False
        else:
            print("SUCCESS: Got expected empty list")
            return True

if __name__ == "__main__":
    success = test_no_working_proxies()
    if not success:
        sys.exit(1)