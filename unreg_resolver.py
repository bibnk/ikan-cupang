# unreg_resolver.py
# Auto-resolve unreg domains: MX lookup + direct IMAP test
# Saves results to imap_config.json

import os
import json
import ssl
import socket
import threading
import concurrent.futures

_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(_DIR, "imap_config.json")

# MX -> IMAP mapping table
_MX_MAPPINGS = [
    (["google", "gmail", "googlemail"], "imap.gmail.com", 993),
    (["outlook", "microsoft", "office365", "hotmail", "protection.outlook"], "imap-mail.outlook.com", 993),
    (["yahoodns", "yahoo"], "imap.mail.yahoo.com", 993),
    (["yandex"], "imap.yandex.com", 993),
    (["zoho"], "imap.zoho.com", 993),
    (["secureserver.net", "godaddy"], "imap.secureserver.net", 993),
    (["hostinger"], "imap.hostinger.com", 993),
    (["titan.email"], "imap.titan.email", 993),
    (["ovh.net", "ovh.com"], "ssl0.ovh.net", 993),
    (["ionos", "1and1", "kundenserver"], "imap.ionos.com", 993),
    (["strato"], "imap.strato.de", 993),
    (["namecheap", "privateemail"], "mail.privateemail.com", 993),
    (["dreamhost"], "imap.dreamhost.com", 993),
    (["fastmail"], "imap.fastmail.com", 993),
    (["migadu"], "imap.migadu.com", 993),
    (["gandi.net"], "mail.gandi.net", 993),
    (["mail.com", "mail-com"], "imap.mail.com", 993),
    (["aruba.it", "arubabusiness", "aruba."], "imaps.aruba.it", 993),
    (["register.it"], "imaps.register.it", 993),
    (["emailsrvr.com", "rackspace"], "secure.emailsrvr.com", 993),
    (["lolipop.jp"], "imap.lolipop.jp", 993),
    (["xserver"], "imap.xserver.jp", 993),
    (["mailhostbox.com"], "imap.mailhostbox.com", 993),
    (["mxhichina.com", "hichina"], "imap.mxhichina.com", 993),
    (["simply.com", "mx.simply"], "mail.simply.com", 993),
    (["nominalia.com"], "imap.nominalia.com", 993),
    (["one.com", "mailpod"], "imap.one.com", 993),
    (["zaq.ne.jp"], "imap.zaq.ne.jp", 993),
    (["mclink.it", "vmailhub.mclink"], "imap.mclink.it", 993),
    (["transip.email"], "imap.transip.email", 993),
    (["online.net"], "imap.online.net", 993),
    (["clara.net"], "imap.clara.net", 993),
    (["domeneshop.no"], "imap.domeneshop.no", 993),
    (["heteml.jp"], "mail.heteml.jp", 993),
    (["jellyfish.systems"], "imap.jellyfish.systems", 993),
    (["breakthur.com"], "imap.breakthur.com", 993),
    (["mail.ru", "mxs.mail.ru"], "imap.mail.ru", 993),
    (["rambler.ru"], "imap.rambler.ru", 993),
    (["i.ua"], "imap.i.ua", 993),
    (["ukr.net"], "imap.ukr.net", 993),
    (["libero.it"], "imapmail.libero.it", 993),
    (["virgilio.it", "tin.it"], "in.virgilio.it", 993),
    (["tiscali.it"], "imap.tiscali.it", 993),
    (["wp.pl"], "imap.wp.pl", 993),
    (["onet.pl"], "imap.poczta.onet.pl", 993),
    (["interia.pl"], "imap.interia.pl", 993),
    (["seznam.cz"], "imap.seznam.cz", 993),
    (["web.de"], "imap.web.de", 993),
    (["gmx.net", "gmx.com"], "imap.gmx.com", 993),
    (["free.fr", "proxad"], "imap.free.fr", 993),
    (["orange.fr", "wanadoo"], "imap.orange.fr", 993),
    (["laposte.net"], "imap.laposte.net", 993),
    (["sfr.fr"], "imap.sfr.fr", 993),
    (["qboxmail.com"], "imap.qboxmail.com", 993),
    (["serverplan.com"], "imap.serverplan.com", 993),
    (["netsons.com"], "imap.netsons.com", 993),
    (["siteground.com"], "imap.siteground.com", 993),
    (["plus.net"], "imap.plus.net", 993),
    (["sakura.ne.jp"], "imap.sakura.ne.jp", 993),
    (["onamae.ne.jp"], "imap.onamae.ne.jp", 993),
    (["biglobe"], "imap.biglobe.ne.jp", 993),
    (["plala"], "imap.plala.or.jp", 993),
    (["nifty"], "imap.nifty.ne.jp", 993),
    (["sezampro.rs"], "imap.sezampro.rs", 993),
    (["rdslink.ro"], "imap.rdslink.ro", 993),
    (["icloud", "apple"], "imap.mail.me.com", 993),
    (["qq.com"], "imap.qq.com", 993),
    (["163.com", "netease"], "imap.163.com", 993),
    (["aol.com"], "imap.aol.com", 993),
    (["naver.com"], "imap.naver.com", 993),
    (["enmail.co"], "imap.enmail.co", 993),
    # Spam filters / no IMAP
    (["mimecast"], None, None),
    (["spamexperts"], None, None),
    (["cloudflare"], None, None),
    (["amazonaws"], None, None),
    (["sendgrid"], None, None),
    (["mailgun"], None, None),
    (["postmarkapp"], None, None),
    (["forwardemail.net"], None, None),
    (["mailchannels"], None, None),
    (["securence"], None, None),
    (["b5z.net"], None, None),
    (["generic-isp"], None, None),
    (["barracuda"], None, None),
    (["proofpoint", "pphosted"], None, None),
]


def _mx_to_imap(mx):
    mx = mx.lower().rstrip(".")
    for keywords, server, port in _MX_MAPPINGS:
        for kw in keywords:
            if kw in mx:
                return (server, port) if server else None
    return None


def _try_imap_connect(server, port=993):
    """Try SSL connect, return True if IMAP banner."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = socket.create_connection((server, port), timeout=5)
        ssock = ctx.wrap_socket(sock, server_hostname=server)
        banner = ssock.recv(1024).decode("utf-8", errors="replace")
        ssock.close()
        return "OK" in banner or "IMAP" in banner.upper() or "* " in banner
    except Exception:
        return False


def resolve_unreg_domains(domains, progress_callback=None):
    """
    Resolve a list of unreg domains to IMAP configs.
    
    Args:
        domains: list of domain strings
        progress_callback: optional fn(phase, checked, total, found) for progress
    
    Returns:
        dict: {added: int, total_config: int, results: {domain: {server, port}}}
    """
    try:
        import dns.resolver
        has_dns = True
    except ImportError:
        has_dns = False

    from imap_config import lookup_imap_config, _get_parent_domain, DEFAULT_IMAP_CONFIG

    # Load current config
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Filter already known
    missing = list(set(d.lower().strip().rstrip(".") for d in domains 
                       if d and "." in d and not lookup_imap_config(d.lower().strip().rstrip("."))))

    if not missing:
        return {"added": 0, "total_config": len(config), "results": {}}

    lock = threading.Lock()
    mx_results = {}
    no_mx = set()

    # Phase 1: MX resolve
    if has_dns:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
        resolver.timeout = 3
        resolver.lifetime = 5

        def get_mx(domain):
            try:
                answers = resolver.resolve(domain, "MX")
                mx = str(sorted(answers, key=lambda r: r.preference)[0].exchange).lower().rstrip(".")
                imap = _mx_to_imap(mx)
                if imap:
                    parent = _get_parent_domain(domain)
                    target = parent if parent and parent not in config and parent not in mx_results else domain
                    with lock:
                        if target not in mx_results and target not in config:
                            mx_results[target] = {"server": imap[0], "port": imap[1]}
            except Exception:
                with lock:
                    no_mx.add(domain)

        if progress_callback:
            progress_callback("mx", 0, len(missing), 0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
            list(ex.map(get_mx, missing))

        if progress_callback:
            progress_callback("mx", len(missing), len(missing), len(mx_results))

    # Phase 2: Direct IMAP test for unmapped domains
    still_missing = [d for d in missing if d not in mx_results and d not in no_mx 
                     and not lookup_imap_config(d) and _get_parent_domain(d) not in mx_results]

    direct_results = {}

    def find_imap(domain):
        for server in [f"imap.{domain}", f"mail.{domain}", f"imaps.{domain}", domain]:
            if _try_imap_connect(server, 993):
                with lock:
                    direct_results[domain] = {"server": server, "port": 993}
                return
        for server in [f"imap.{domain}", f"mail.{domain}"]:
            if _try_imap_connect(server, 143):
                with lock:
                    direct_results[domain] = {"server": server, "port": 143}
                return

    if still_missing:
        if progress_callback:
            progress_callback("imap", 0, len(still_missing), 0)

        batch_size = 100
        for i in range(0, len(still_missing), batch_size):
            batch = still_missing[i:i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
                list(ex.map(find_imap, batch))
            if progress_callback:
                progress_callback("imap", min(i + batch_size, len(still_missing)),
                                  len(still_missing), len(direct_results))

    # Merge and save
    all_new = {}
    all_new.update(mx_results)
    all_new.update(direct_results)

    if all_new:
        for domain, cfg in all_new.items():
            if domain not in config:
                config[domain] = cfg

        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, sort_keys=True, ensure_ascii=False)

        # Reload the module config
        DEFAULT_IMAP_CONFIG.update(all_new)

    return {
        "added": len(all_new),
        "total_config": len(config),
        "mx_mapped": len(mx_results),
        "direct_found": len(direct_results),
        "no_mx": len(no_mx),
        "still_missing": len(missing) - len(all_new) - len(no_mx),
        "results": all_new,
    }
