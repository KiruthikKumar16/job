with open('proxy_manager.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines[75:120], start=76):  # Lines 76-119
    print(f"{i:3d}: {line.rstrip()}")