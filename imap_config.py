# imap_config.py
# Konfigurasi IMAP Server untuk berbagai domain email.
# File ini dipisahkan dari script utama agar lebih rapi dan mudah di-maintain.

DEFAULT_IMAP_CONFIG = {
    # --- Google ---
    "gmail.com": {"server": "imap.gmail.com", "port": 993},

    # --- Microsoft (Outlook/Hotmail/Live/MSN) ---
    "outlook.com": {"server": "imap-mail.outlook.com", "port": 993},

    # --- Yahoo ---
    "yahoo.com": {"server": "imap.mail.yahoo.com", "port": 993},

    # --- Spectrum / Charter / Bresnan ---
    # Server: mobile.charter.net (plain IMAP, no SSL/TLS)
    "charter.net": {"server": "mobile.charter.net", "port": 143, "ssl": False},
    "spectrum.net": {"server": "mobile.charter.net", "port": 143, "ssl": False},
    "bresnan.net": {"server": "mobile.charter.net", "port": 143, "ssl": False},

    # --- Brighthouse / Road Runner (Brighthouse regions) ---
    # Server: mail.brighthouse.com (plain IMAP, no SSL/TLS)
    "brighthouse.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "bak.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "bham.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "cfl.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "emore.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "eufala.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "indy.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "mi.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "panhandle.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},
    "tampabay.rr.com": {"server": "mail.brighthouse.com", "port": 143, "ssl": False},

    # --- TWC / Road Runner (semua .rr.com lain & TWC) ---
    # Server: mail.twc.com (plain IMAP, no SSL/TLS)
    "twc.com": {"server": "mail.twc.com", "port": 143, "ssl": False},
    "rr.com": {"server": "mail.twc.com", "port": 143, "ssl": False},
    "nycap.rr.com": {"server": "mail.twc.com", "port": 143, "ssl": False},
    "twmi.rr.com": {"server": "mail.twc.com", "port": 143, "ssl": False},
    "berkshire.rr.com": {"server": "mail.twc.com", "port": 143, "ssl": False},

    # --- Dual-domain (Roadrunner / Adelphia) ---
    # Login harus pakai domain roadrunner.com (plain IMAP, no SSL/TLS)
    "roadrunner.com": {"server": "mail.twc.com", "port": 143, "ssl": False},
    "adelphia.net": {"server": "mail.twc.com", "port": 143, "ssl": False},

    # --- Freenet ---
    "freenet.de": {"server": "mx.freenet.de", "port": 993},
    "freenet.com": {"server": "mx.freenet.de", "port": 993},
    "freenetmail.de": {"server": "mx.freenet.de", "port": 993},

    # --- Rambler ---
    "rambler.ru": {"server": "imap.rambler.ru", "port": 993},
    "ro.ru": {"server": "imap.rambler.ru", "port": 993},

    # --- Proximus / Belgacom / Skynet ---
    "belgacom.net": {"server": "imap.proximus.be", "port": 993},
    "skynet.be": {"server": "imap.proximus.be", "port": 993},

    # --- Datazug (Swiss ISPs) ---
    "datazug.ch": {"server": "imap01.datazug.ch", "port": 993},
    "fibermail.ch": {"server": "imap01.datazug.ch", "port": 993},
    "wwz.one": {"server": "imap01.datazug.ch", "port": 993},
    "wwzmail.ch": {"server": "imap01.datazug.ch", "port": 993},
    "raonet.ch": {"server": "imap01.datazug.ch", "port": 993},

    # --- Swiss ISPs (lainnya) ---
    "teleport.ch": {"server": "imap.breitband.ch", "port": 993},
    "shinternet.ch": {"server": "mail.shinternet.ch", "port": 993},
    "thurweb.ch": {"server": "mail1.thurweb.ch", "port": 993},
    "greenmail.ch": {"server": "imap.mail-ch.ch", "port": 993},
    "eblcom.ch": {"server": "imap.eblcom.ch", "port": 993},
    "bluewin.ch": {"server": "imaps.bluewin.ch", "port": 993},
    "bluemail.ch": {"server": "imaps.bluewin.ch", "port": 993},

    # --- Polish ISPs ---
    "op.pl": {"server": "imap.poczta.onet.pl", "port": 993},
    "opoczta.pl": {"server": "imap.poczta.onet.pl", "port": 993},
    "spoko.pl": {"server": "imap.poczta.onet.pl", "port": 993},
    "vp.pl": {"server": "imap.poczta.onet.pl", "port": 993},
    "o2.pl": {"server": "poczta.o2.pl", "port": 993},
    "tlen.pl": {"server": "poczta.o2.pl", "port": 993},
    "wp.pl": {"server": "imap.wp.pl", "port": 993},
    "gazeta.pl": {"server": "imap.gazeta.pl", "port": 993},
    "interia.pl": {"server": "poczta.interia.pl", "port": 993},
    "interia.eu": {"server": "poczta.interia.pl", "port": 993},
    "poczta.fm": {"server": "poczta.interia.pl", "port": 993},

    # --- German ISPs ---
    "pep4teens.de": {"server": "imap.1und1.de", "port": 993},
    "online.de": {"server": "imap.1und1.de", "port": 993},
    "1und1.de": {"server": "imap.1und1.de", "port": 993},
    "posteo.de": {"server": "posteo.de", "port": 993},
    "mail.de": {"server": "imap.mail.de", "port": 993},
    "vodafonemail.de": {"server": "imap.vodafonemail.de", "port": 993},
    "arcor.de": {"server": "imap.arcor.de", "port": 993},
    "telekom.de": {"server": "imap.t-online.de", "port": 993},
    "t-online.de": {"server": "imap.t-online.de", "port": 993},
    "web.de": {"server": "imap.web.de", "port": 993},
    "webmail.de": {"server": "imap.web.de", "port": 993},

    # --- GMX (Deutsche) ---
    "gmx.net": {"server": "imap.gmx.net", "port": 993},
    "gmx.at": {"server": "imap.gmx.net", "port": 993},
    "gmx.de": {"server": "imap.gmx.net", "port": 993},
    "gmx.ch": {"server": "imap.gmx.net", "port": 993},
    "gmx.eu": {"server": "imap.gmx.net", "port": 993},
    "gmx.org": {"server": "imap.gmx.net", "port": 993},
    "gmx.biz": {"server": "imap.gmx.net", "port": 993},
    "gmx.info": {"server": "imap.gmx.net", "port": 993},
    "mein.gmx": {"server": "imap.gmx.net", "port": 993},
    "mail.gmx": {"server": "imap.gmx.net", "port": 993},
    "email.gmx": {"server": "imap.gmx.net", "port": 993},

    # --- GMX (International) ---
    "gmx.com": {"server": "imap.gmx.com", "port": 993},
    "gmx.fr": {"server": "imap.gmx.com", "port": 993},
    "gmx.ca": {"server": "imap.gmx.com", "port": 993},
    "gmx.ru": {"server": "imap.gmx.com", "port": 993},
    "gmx.tm": {"server": "imap.gmx.com", "port": 993},
    "gmx.us": {"server": "imap.gmx.com", "port": 993},
    "gmx.co.uk": {"server": "imap.gmx.com", "port": 993},
    "gmx.es": {"server": "imap.gmx.com", "port": 993},
    "gmx.cn": {"server": "imap.gmx.com", "port": 993},
    "gmx.co.in": {"server": "imap.gmx.com", "port": 993},
    "gmx.com.br": {"server": "imap.gmx.com", "port": 993},
    "gmx.com.my": {"server": "imap.gmx.com", "port": 993},
    "gmx.hk": {"server": "imap.gmx.com", "port": 993},
    "gmx.ie": {"server": "imap.gmx.com", "port": 993},
    "gmx.ph": {"server": "imap.gmx.com", "port": 993},
    "gmx.pt": {"server": "imap.gmx.com", "port": 993},
    "gmx.se": {"server": "imap.gmx.com", "port": 993},
    "gmx.sg": {"server": "imap.gmx.com", "port": 993},
    "gmx.tw": {"server": "imap.gmx.com", "port": 993},
    "gmx.com.tr": {"server": "imap.gmx.com", "port": 993},
    "gmx.it": {"server": "imap.gmx.com", "port": 993},
    "gmx.li": {"server": "imap.gmx.com", "port": 993},

    # --- French ISPs ---
    "online.fr": {"server": "imap.free.fr", "port": 993},
    "orange.fr": {"server": "imap.orange.fr", "port": 993},
    "wanadoo.fr": {"server": "imap.orange.fr", "port": 993},
    "sfr.fr": {"server": "imap.sfr.fr", "port": 993},

    # --- Italian ISPs ---
    "aruba.it": {"server": "imaps.pec.aruba.it", "port": 993},
    "pec.it": {"server": "imaps.pec.aruba.it", "port": 993},
    "libero.it": {"server": "imapmail.libero.it", "port": 993},
    "fastwebnet.it": {"server": "imap.fastwebnet.it", "port": 993},
    "virgilio.it": {"server": "in.virgilio.it", "port": 993},
    "alice.it": {"server": "in.alice.it", "port": 143},

    # --- Czech / Slovak ISPs ---
    "seznam.cz": {"server": "imap.seznam.cz", "port": 993},
    "atlas.cz": {"server": "imap.atlas.cz", "port": 993},
    "centrum.cz": {"server": "imap.centrum.cz", "port": 993},
    "tiscali.cz": {"server": "imap.tiscali.cz", "port": 993},
    "centrum.sk": {"server": "imap.centrum.sk", "port": 993},

    # --- Hungarian ISPs ---
    "citromail.hu": {"server": "imap.citromail.hu", "port": 993},

    # --- Bulgarian ISPs ---
    "abv.bg": {"server": "imap.abv.bg", "port": 993},
    "mail.bg": {"server": "imap.mail.bg", "port": 993},

    # --- Russian ISPs ---
    "yandex.ru": {"server": "imap.yandex.ru", "port": 993},
    "uralweb.ru": {"server": "imap.mail.ru", "port": 993},

    # --- American ISPs ---
    "centurylink.net": {"server": "mail.centurylink.net", "port": 993},
    "comcast.net": {"server": "imap.comcast.net", "port": 993},
    "windstream.net": {"server": "imap.windstream.net", "port": 993},
    "juno.com": {"server": "imap.juno.com", "port": 143},
    "aol.com": {"server": "imap.aol.com", "port": 993},
    "usa.com": {"server": "imap.mail.com", "port": 993},
    "webmail.com": {"server": "imap.mail.com", "port": 993},
    "peoplepc.com": {"server": "imap.peoplepc.com", "port": 143},

    # --- Optimum / Suddenlink / Optonline ---
    "optonline.net": {"server": "mail.optonline.net", "port": 993},
    "optimum.net": {"server": "mail.optonline.net", "port": 993},
    "suddenlink.net": {"server": "mail.optonline.net", "port": 993},

    # --- Canadian ISPs ---
    "shaw.ca": {"server": "imap.shaw.ca", "port": 993},
    "videotron.ca": {"server": "imap.videotron.ca", "port": 993},

    # --- Australian ISPs ---
    "bigpond.com": {"server": "imap.telstra.com", "port": 993},
    "dodo.com.au": {"server": "imap.telstra.com", "port": 993},
    "tpg.com.au": {"server": "mail.tpg.com.au", "port": 143},
    "iprimus.com.au": {"server": "imap.iprimus.com.au", "port": 143},
    "iinet.net.au": {"server": "mail.iinet.net.au", "port": 993},

    # --- Brazilian ISPs ---
    "terra.com.br": {"server": "imap.terra.com", "port": 993},

    # --- Argentine ISPs ---
    "fibertel.com.ar": {"server": "imap.fibertel.com.ar", "port": 993},

    # --- Portuguese ISPs ---
    "sapo.pt": {"server": "imap.sapo.pt", "port": 993},

    # --- Spanish ISPs ---
    "telefonica.net": {"server": "imap.telefonica.net", "port": 143},

    # --- UK ISPs ---
    "talktalk.net": {"server": "mail.talktalk.net", "port": 993},
    "btinternet.com": {"server": "mail.btinternet.com", "port": 993},
    "ntlworld.com": {"server": "imap.ntlworld.com", "port": 993},
    "blueyonder.co.uk": {"server": "imap4.blueyonder.co.uk", "port": 993},

    # --- Danish ISPs ---
    "events.dk": {"server": "imap.yousee.dk", "port": 993},

    # --- Israeli ISPs ---
    "netvision.net.il": {"server": "mail.netvision.net.il", "port": 143},

    # --- Indian ISPs ---
    "rediffmail.com": {"server": "imap.rediffmailpro.com", "port": 993},

    # --- Chinese ISPs ---
    "163.com": {"server": "imap.163.com", "port": 993},
    "126.com": {"server": "imap.126.com", "port": 993},
    "sina.com": {"server": "imap.sina.com", "port": 993},
    "eyou.com": {"server": "imap.eyou.com", "port": 143},

    # --- Korean ISPs ---
    "naver.com": {"server": "imap.naver.com", "port": 993},

    # --- Apple / iCloud ---
    "icloud.com": {"server": "imap.mail.me.com", "port": 993},

    # --- Zoho ---
    "zoho.eu": {"server": "imap.zoho.eu", "port": 993},

    # --- Other International ---
    "bestmail24.eu": {"server": "imap.bestmail24.eu", "port": 993},
    "azmailz.com": {"server": "zmail.mail.plala.or.jp", "port": 993},
    "imailzone.com": {"server": "imap.zone.eu", "port": 993},
}
