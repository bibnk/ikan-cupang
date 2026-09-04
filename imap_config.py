# imap_config.py
# Loads IMAP server config from central PMJ imap database
# + lookup_imap_config() with parent domain fallback
import json
import os
import sys

# Central IMAP database path
# Production (Linux/VPS): /opt/pmj/imap/
# Development (Windows): C:\Users\Administrator\Desktop\PMJ\imap\
if sys.platform == "win32":
    IMAP_DB_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "PMJ", "imap")
else:
    IMAP_DB_DIR = "/opt/pmj/imap"

# Fallback: jika central dir belum ada, pakai local
if not os.path.isdir(IMAP_DB_DIR):
    IMAP_DB_DIR = os.path.dirname(os.path.abspath(__file__))

IMAP_CONFIG_PATH = os.path.join(IMAP_DB_DIR, "imap_config.json")
IMAP_SUCCESS_PATH = os.path.join(IMAP_DB_DIR, "imap_success.json")

# Load config
if os.path.exists(IMAP_CONFIG_PATH):
    with open(IMAP_CONFIG_PATH, "r", encoding="utf-8") as _f:
        DEFAULT_IMAP_CONFIG = json.load(_f)
else:
    DEFAULT_IMAP_CONFIG = {}

# Country-code TLDs yang punya 2 bagian (ne.jp, co.uk, com.br, dll)
_CC_TLDS = {
    "ne.jp", "or.jp", "co.jp", "ac.jp", "gr.jp", "go.jp", "ad.jp", "ed.jp",
    "co.uk", "org.uk", "me.uk", "ac.uk",
    "co.id", "or.id", "ac.id", "go.id", "sch.id", "my.id", "web.id",
    "co.za", "org.za", "ac.za",
    "co.kr", "ac.kr", "or.kr",
    "co.in", "org.in", "ac.in",
    "co.nz", "org.nz", "ac.nz",
    "co.zw", "co.bw", "co.ke", "co.tz", "co.mz",
    "co.th", "ac.th", "or.th",
    "com.au", "org.au", "net.au", "edu.au",
    "com.br", "org.br", "net.br",
    "com.mx", "org.mx", "net.mx",
    "com.ar", "org.ar", "net.ar",
    "com.tr", "org.tr", "net.tr", "edu.tr",
    "com.tw", "org.tw", "net.tw", "edu.tw",
    "com.co", "org.co", "net.co", "edu.co",
    "com.pe", "org.pe", "net.pe", "edu.pe",
    "com.pk", "org.pk", "net.pk", "edu.pk",
    "com.my", "org.my", "net.my", "edu.my",
    "com.np", "org.np",
    "com.cn", "org.cn", "net.cn", "edu.cn",
    "com.es", "org.es",
    "com.hk", "org.hk",
    "com.ph", "org.ph", "edu.ph",
    "com.bd", "org.bd",
    "com.ve", "org.ve",
    "com.ec", "org.ec",
    "com.sa", "org.sa",
    "com.bo", "org.bo",
    "com.tn", "org.tn",
    "com.vn",
    "edu.ye",
    "gob.ec", "gob.mx", "gob.pe",
}


def _get_parent_domain(domain):
    """
    Ambil parent domain. Contoh:
      am.em-net.ne.jp  -> em-net.ne.jp
      sub.example.co.uk -> example.co.uk
      sub.example.com  -> example.com
    """
    parts = domain.split(".")
    if len(parts) <= 2:
        return None

    for i in range(len(parts) - 2, 0, -1):
        suffix = ".".join(parts[i:])
        if suffix in _CC_TLDS:
            parent = ".".join(parts[i-1:])
            if parent != domain:
                return parent
            return None

    parent = ".".join(parts[-2:])
    if parent != domain:
        return parent
    return None


def lookup_imap_config(domain, extra_configs=None):
    """
    Cari IMAP config untuk domain dengan fallback ke parent domain.
    """
    configs = DEFAULT_IMAP_CONFIG

    cfg = configs.get(domain)
    if cfg:
        return cfg

    if extra_configs:
        cfg = extra_configs.get(domain)
        if cfg:
            return cfg

    parent = _get_parent_domain(domain)
    if parent:
        cfg = configs.get(parent)
        if cfg:
            return cfg
        if extra_configs:
            cfg = extra_configs.get(parent)
            if cfg:
                return cfg

        grandparent = _get_parent_domain(parent)
        if grandparent:
            cfg = configs.get(grandparent)
            if cfg:
                return cfg
            if extra_configs:
                cfg = extra_configs.get(grandparent)
                if cfg:
                    return cfg

    return None


def load_imap_success():
    """Load imap_success.json dari central database."""
    if os.path.exists(IMAP_SUCCESS_PATH):
        try:
            with open(IMAP_SUCCESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_imap_success(data):
    """Save imap_success.json ke central database."""
    try:
        os.makedirs(os.path.dirname(IMAP_SUCCESS_PATH), exist_ok=True)
        with open(IMAP_SUCCESS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
