# imap_config.py
# Loads IMAP server config from imap_config.json
# + lookup_imap_config() with parent domain fallback
import json
import os

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imap_config.json")
with open(_config_path, "r", encoding="utf-8") as _f:
    DEFAULT_IMAP_CONFIG = json.load(_f)

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
        return None  # sudah root domain

    # Cek apakah ada CC TLD (2 bagian)
    for i in range(len(parts) - 2, 0, -1):
        suffix = ".".join(parts[i:])
        if suffix in _CC_TLDS:
            # Parent = 1 level di atas CC TLD
            parent = ".".join(parts[i-1:])
            if parent != domain:
                return parent
            return None

    # Standard TLD (1 bagian: .com, .net, .it, .es, dll)
    # Parent = last 2 parts
    parent = ".".join(parts[-2:])
    if parent != domain:
        return parent
    return None


def lookup_imap_config(domain, extra_configs=None):
    """
    Cari IMAP config untuk domain dengan fallback ke parent domain.
    
    Flow:
    1. Cek exact match: configs["am.em-net.ne.jp"]
    2. Kalau ga ketemu, cek parent: configs["em-net.ne.jp"]
    3. Kalau parent juga ga ketemu, return None (auto-discover nanti yang handle)
    
    Args:
        domain: email domain (lowercase)
        extra_configs: dict tambahan (e.g. imap_success.json cache)
    Returns:
        dict {"server": ..., "port": ...} atau None
    """
    configs = DEFAULT_IMAP_CONFIG
    
    # 1. Exact match di default config
    cfg = configs.get(domain)
    if cfg:
        return cfg
    
    # 2. Exact match di extra configs (imap_success cache)
    if extra_configs:
        cfg = extra_configs.get(domain)
        if cfg:
            return cfg
    
    # 3. Parent domain fallback
    parent = _get_parent_domain(domain)
    if parent:
        cfg = configs.get(parent)
        if cfg:
            return cfg
        if extra_configs:
            cfg = extra_configs.get(parent)
            if cfg:
                return cfg
        
        # 4. Grandparent (untuk kasus sub.sub.domain.ne.jp)
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
