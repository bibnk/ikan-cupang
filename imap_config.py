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

# Seed config: domain IMAP server yang tidak mengikuti pola imap.{domain}
# Ini base config — file imap_config.json (jika ada) menambah/override ini.
_SEED_IMAP_CONFIG = {
        "gmail.com": {"server": "imap.gmail.com", "port": 993},
        "googlemail.com": {"server": "imap.gmail.com", "port": 993},
        "yahoo.com": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.co.id": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.co.uk": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.co.in": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.com.au": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.de": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.fr": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.it": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.es": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.ca": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.com.br": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.com.mx": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.com.ar": {"server": "imap.mail.yahoo.com", "port": 993},
        "yahoo.co.jp": {"server": "imap.mail.yahoo.co.jp", "port": 993},
        "outlook.com": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.com": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.co.uk": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.fr": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.de": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.it": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.es": {"server": "imap-mail.outlook.com", "port": 993},
        "hotmail.com.br": {"server": "imap-mail.outlook.com", "port": 993},
        "live.com": {"server": "imap-mail.outlook.com", "port": 993},
        "live.co.uk": {"server": "imap-mail.outlook.com", "port": 993},
        "live.fr": {"server": "imap-mail.outlook.com", "port": 993},
        "live.de": {"server": "imap-mail.outlook.com", "port": 993},
        "live.it": {"server": "imap-mail.outlook.com", "port": 993},
        "live.com.au": {"server": "imap-mail.outlook.com", "port": 993},
        "msn.com": {"server": "imap-mail.outlook.com", "port": 993},
        "icloud.com": {"server": "imap.mail.me.com", "port": 993},
        "me.com": {"server": "imap.mail.me.com", "port": 993},
        "mac.com": {"server": "imap.mail.me.com", "port": 993},
        "mail.com": {"server": "imap.mail.com", "port": 993},
        "gmx.com": {"server": "imap.gmx.com", "port": 993},
        "gmx.de": {"server": "imap.gmx.de", "port": 993},
        "gmx.net": {"server": "imap.gmx.net", "port": 993},
        "gmx.at": {"server": "imap.gmx.at", "port": 993},
        "gmx.ch": {"server": "imap.gmx.ch", "port": 993},
        "zoho.com": {"server": "imap.zoho.com", "port": 993},
        "zoho.eu": {"server": "imap.zoho.eu", "port": 993},
        "fastmail.com": {"server": "imap.fastmail.com", "port": 993},
        "fastmail.fm": {"server": "imap.fastmail.com", "port": 993},
        "yandex.com": {"server": "imap.yandex.com", "port": 993},
        "yandex.ru": {"server": "imap.yandex.ru", "port": 993},
        "yandex.ua": {"server": "imap.yandex.ua", "port": 993},
        "aol.com": {"server": "imap.aol.com", "port": 993},
        "163.com": {"server": "imap.163.com", "port": 993},
        "126.com": {"server": "imap.126.com", "port": 993},
        "yeah.net": {"server": "imap.yeah.net", "port": 993},
        "qq.com": {"server": "imap.qq.com", "port": 993},
        "foxmail.com": {"server": "imap.qq.com", "port": 993},
        "naver.com": {"server": "imap.naver.com", "port": 993},
        "nate.com": {"server": "imap.nate.com", "port": 993},
        "daum.net": {"server": "imap.daum.net", "port": 993},
        "hanmail.net": {"server": "imap.daum.net", "port": 993},
        "tutanota.com": {"server": "mail.tutanota.com", "port": 993},
        "tutamail.com": {"server": "mail.tutanota.com", "port": 993},
        "keemail.me": {"server": "mail.tutanota.com", "port": 993},
        "mail.ru": {"server": "imap.mail.ru", "port": 993},
        "inbox.ru": {"server": "imap.mail.ru", "port": 993},
        "list.ru": {"server": "imap.mail.ru", "port": 993},
        "bk.ru": {"server": "imap.mail.ru", "port": 993},
        "rambler.ru": {"server": "mail.rambler.ru", "port": 993},
        "interia.pl": {"server": "poczta.interia.pl", "port": 993},
        "poczta.fm": {"server": "poczta.poczta.fm", "port": 993},
        "wp.pl": {"server": "imap.wp.pl", "port": 993},
        "onet.pl": {"server": "imap.poczta.onet.pl", "port": 993},
        "web.de": {"server": "imap.web.de", "port": 993},
        "t-online.de": {"server": "secureimap.t-online.de", "port": 993},
        "freenet.de": {"server": "mx.freenet.de", "port": 993},
        "arcor.de": {"server": "imap.arcor.de", "port": 993},
        "comcast.net": {"server": "imap.comcast.net", "port": 993},
        "verizon.net": {"server": "imap.verizon.net", "port": 993},
        "att.net": {"server": "imap.mail.att.net", "port": 993},
        "bellsouth.net": {"server": "imap.mail.att.net", "port": 993},
        "sbcglobal.net": {"server": "imap.mail.att.net", "port": 993},
        "rocketmail.com": {"server": "imap.mail.yahoo.com", "port": 993},
        "ymail.com": {"server": "imap.mail.yahoo.com", "port": 993},
        "earthlink.net": {"server": "imap.earthlink.net", "port": 993},
        "cox.net": {"server": "imap.cox.net", "port": 993},
        "charter.net": {"server": "mobile.charter.net", "port": 993},
        "spectrum.net": {"server": "mobile.charter.net", "port": 993},
        "twc.com": {"server": "mail.twc.com", "port": 993},
        "rr.com": {"server": "mail.twc.com", "port": 993},
        "optonline.net": {"server": "mail.optonline.net", "port": 993},
        "optimum.net": {"server": "mail.optimum.net", "port": 993},
        "windstream.net": {"server": "imap.windstream.net", "port": 993},
        "centurylink.net": {"server": "mail.centurylink.net", "port": 993},
        "embarqmail.com": {"server": "imap.embarqmail.com", "port": 993},
        "q.com": {"server": "imap.q.com", "port": 993},
        "icloud.com": {"server": "imap.mail.me.com", "port": 993},
    }

# Load file config (jika ada) dan merge dengan seed.
# File config bisa menambah domain baru atau override seed.
# File kosong {} tetap pakai seed (tidak menimpa jadi kosong).
_file_config = {}
if os.path.exists(IMAP_CONFIG_PATH):
    try:
        with open(IMAP_CONFIG_PATH, "r", encoding="utf-8") as _f:
            _file_config = json.load(_f)
    except Exception:
        _file_config = {}
DEFAULT_IMAP_CONFIG = {**_SEED_IMAP_CONFIG, **_file_config}

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
