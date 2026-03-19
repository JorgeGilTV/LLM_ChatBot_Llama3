#!/usr/bin/env python3
"""List all registered Flask routes"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import flask_app

print("="*60)
print("Registered Flask Routes:")
print("="*60)

for rule in flask_app.url_map.iter_rules():
    methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
    print(f"{rule.rule:40s} {methods:20s} -> {rule.endpoint}")

print("="*60)
print(f"Total routes: {len(list(flask_app.url_map.iter_rules()))}")
