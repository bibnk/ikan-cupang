"""
Shared pytest configuration for centralize-imap-success-master spec tests.

Ensures the project root is on sys.path so `import imap_engine` works.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
