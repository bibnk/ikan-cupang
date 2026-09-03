#!/usr/bin/env python3
"""Populate imap_success.json with the user-provided IMAP configs."""
import json, os

# Parse the raw JSON the user provided
RAW = r'''
PLACEHOLDER
'''

# Since the data is provided as user input, we'll read it from the
# temporary _imap_data.json file instead
data_file = os.path.join(os.path.dirname(__file__), '_imap_data.json')
out_file = os.path.join(os.path.dirname(__file__), 'imap_success.json')

if os.path.exists(data_file):
    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Merge with existing if any
    existing = {}
    if os.path.exists(out_file):
        try:
            with open(out_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass
    
    existing.update(data)
    
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2)
    
    print(f"Written {len(existing)} entries to imap_success.json")
    
    # Clean up
    os.remove(data_file)
    print("Cleaned up temp file")
else:
    print("No _imap_data.json found. Please create it first.")
