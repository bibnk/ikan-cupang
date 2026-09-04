# imap_config.py
# Loads IMAP server config from imap_config.json
import json
import os

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imap_config.json")
with open(_config_path, "r", encoding="utf-8") as _f:
    DEFAULT_IMAP_CONFIG = json.load(_f)
