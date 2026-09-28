import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import proxy_manager

print("Module:", proxy_manager)
print("_PROXY_POOL_CACHE:", getattr(proxy_manager, '_PROXY_POOL_CACHE', 'NOT FOUND'))
print("_PROXY_POOL_TIMESTAMP:", getattr(proxy_manager, '_PROXY_POOL_TIMESTAMP', 'NOT FOUND'))

# Try to set it
proxy_manager._PROXY_POOL_CACHE = ["test"]
print("After setting:")
print("_PROXY_POOL_CACHE:", proxy_manager._PROXY_POOL_CACHE)