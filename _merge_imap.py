#!/usr/bin/env python3
"""
Paste your JSON data into _imap_data.json in this same folder,
then run this script to merge it into imap_success.json.

Usage:
  1. Save your JSON as _imap_data.json
  2. Run: python _merge_imap.py
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
data_file = os.path.join(HERE, '_imap_data.json')
out_file = os.path.join(HERE, 'imap_success.json')

if not os.path.exists(data_file):
    print("ERROR: _imap_data.json not found!")
    print("Please save your JSON data as _imap_data.json first.")
    exit(1)

with open(data_file, 'r', encoding='utf-8') as f:
    new_data = json.load(f)

existing = {}
if os.path.exists(out_file):
    try:
        with open(out_file, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass

existing.update(new_data)

with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(existing, f, indent=2)

print(f"✅ Written {len(existing)} entries to imap_success.json")

# Cleanup
os.remove(data_file)
os.remove(os.path.abspath(__file__))
print("Cleaned up temp files.")
