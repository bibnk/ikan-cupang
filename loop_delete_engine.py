# loop_delete_engine.py
# Background loop delete — continuously delete emails matching sender/keyword.

import imaplib
import email
import email.utils
import re
import socket
import threading
import time
from email.header import decode_header
from datetime import datetime, timedelta

from imap_config import DEFAULT_IMAP_CONFIG, lookup_imap_config

socket.setdefaulttimeout(60)

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def try_imap_variants(domain, email_address, password):
    """Try common IMAP server prefixes for auto-discovery."""
    domains_to_check = [domain]
    if domain.endswith('.jp'):
        parts = domain.split('.')
        is_complex_tld = len(parts) >= 3 and len(parts[-2]) <= 3
        if (is_complex_tld and len(parts) > 3) or (not is_complex_tld and len(parts) > 2):
            root_domain = '.'.join(parts[1:])
            if root_domain not in domains_to_check:
                domains_to_check.append(root_domain)

    for d in domains_to_check:
        for port in [993, 143]:
            for prefix in ["imap", "mail"]:
                server = f"{prefix}.{d}"
                try:
                    if port == 993:
                        m = imaplib.IMAP4_SSL(server, port, timeout=15)
                    else:
                        m = imaplib.IMAP4(server, port, timeout=15)
                        m.starttls()
                    m.login(email_address, password)
                    m.logout()
                    return {"server": server, "port": port}
                except Exception:
                    continue
    return None


class LoopDeleteJob:
    """A background job that continuously deletes emails matching criteria."""

    def __init__(self, job_id, email_address, password, senders=None, keywords=None, interval=5):
        self.job_id = job_id
        self.email_address = email_address
        self.password = password
        self.senders = senders or []
        self.keywords = keywords or []
        self.interval = max(2, interval)  # Min 2 seconds between cycles

        self.is_running = False
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

        # Stats
        self.cycles = 0
        self.total_deleted = 0
        self.last_cycle_deleted = 0
        self.last_cycle_time = None
        self.started_at = None
        self.error = None
        self.status = "idle"  # idle, running, stopped, error

        # Resolve IMAP config
        domain = email_address.split('@')[-1].lower()
        self.imap_config = lookup_imap_config(domain)
        if not self.imap_config:
            self.imap_config = try_imap_variants(domain, email_address, password)

    def start(self):
        if not self.imap_config:
            self.status = "error"
            self.error = f"Gagal menemukan konfigurasi IMAP untuk domain"
            return False

        self.is_running = True
        self.stop_event.clear()
        self.status = "running"
        self.started_at = datetime.now().strftime('%H:%M:%S')
        self.error = None

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        self.is_running = False
        self.status = "stopped"

    def get_status(self):
        with self.lock:
            return {
                "job_id": self.job_id,
                "email": self.email_address,
                "status": self.status,
                "cycles": self.cycles,
                "total_deleted": self.total_deleted,
                "last_cycle_deleted": self.last_cycle_deleted,
                "last_cycle_time": self.last_cycle_time,
                "started_at": self.started_at,
                "error": self.error,
                "senders": self.senders,
                "keywords": self.keywords,
                "interval": self.interval,
            }

    def _loop(self):
        """Main loop — runs in background thread."""
        while not self.stop_event.is_set():
            try:
                deleted = self._run_one_cycle()
                with self.lock:
                    self.cycles += 1
                    self.last_cycle_deleted = deleted
                    self.total_deleted += deleted
                    self.last_cycle_time = datetime.now().strftime('%H:%M:%S')
            except Exception as e:
                with self.lock:
                    self.error = str(e)
                    self.cycles += 1
                    self.last_cycle_deleted = 0
                    self.last_cycle_time = datetime.now().strftime('%H:%M:%S')

            # Wait with check for stop
            for _ in range(self.interval * 2):
                if self.stop_event.is_set():
                    break
                time.sleep(0.5)

        with self.lock:
            self.status = "stopped"
            self.is_running = False

    def _run_one_cycle(self):
        """Connect, search, delete, disconnect. Returns count of deleted emails."""
        mail = None
        deleted_count = 0

        try:
            if self.imap_config['port'] == 993:
                mail = imaplib.IMAP4_SSL(self.imap_config['server'], self.imap_config['port'])
            else:
                mail = imaplib.IMAP4(self.imap_config['server'], self.imap_config['port'])
                mail.starttls()
            mail.login(self.email_address, self.password)
        except Exception as e:
            with self.lock:
                self.error = f"Login gagal: {e}"
            return 0

        since_date = (datetime.now() - timedelta(days=3650)).strftime('%d-%b-%Y')

        try:
            mail.select("INBOX")

            uids_to_delete = set()

            # Search by sender
            for sender in self.senders:
                try:
                    criteria = f'(SINCE "{since_date}" FROM "{sender}")'
                    status, msgs = mail.uid('SEARCH', None, criteria)
                    if status == "OK" and msgs and msgs[0]:
                        uids_to_delete.update(msgs[0].split())
                except Exception:
                    continue

            # Search by keyword (SUBJECT)
            for kw in self.keywords:
                try:
                    criteria = f'(SINCE "{since_date}" SUBJECT "{kw}")'
                    status, msgs = mail.uid('SEARCH', None, criteria)
                    if status == "OK" and msgs and msgs[0]:
                        uids_to_delete.update(msgs[0].split())
                except Exception:
                    continue

            if uids_to_delete:
                uids_str = b','.join(uids_to_delete)

                # Mark as deleted
                mail.uid('STORE', uids_str, '+FLAGS', '(\\Deleted)')
                mail.expunge()
                deleted_count = len(uids_to_delete)

                with self.lock:
                    self.error = None

                # Also purge from Trash-like folders
                self._purge_trash(mail)

        except Exception as e:
            with self.lock:
                self.error = f"Error siklus: {e}"
        finally:
            try:
                mail.logout()
            except Exception:
                pass

        return deleted_count

    def _purge_trash(self, mail):
        """Find and empty all Trash-like folders."""
        trash_names = [
            '[Gmail]/Trash', '[Gmail]/Bin', 'Trash', 'Deleted Items',
            'Deleted Messages', 'Deleted', 'INBOX.Trash', 'INBOX.Deleted Items',
        ]

        # Discover more folders from LIST
        try:
            status, folder_list = mail.list()
            if status == 'OK' and folder_list:
                for fl in folder_list:
                    if fl:
                        decoded = fl.decode() if isinstance(fl, bytes) else str(fl)
                        lower = decoded.lower()
                        if any(t in lower for t in ['trash', 'deleted', 'papierkorb', 'bin', 'corbeille']):
                            match = re.search(r'"([^"]+)"\s*$', decoded)
                            if match:
                                fname = match.group(1)
                                if fname not in trash_names:
                                    trash_names.append(fname)
                            else:
                                parts = decoded.rsplit(' ', 1)
                                if len(parts) == 2:
                                    fname = parts[1].strip()
                                    if fname not in trash_names:
                                        trash_names.append(fname)
        except Exception:
            pass

        for folder in trash_names:
            try:
                status, _ = mail.select(folder)
                if status != 'OK':
                    continue
                status, msgs = mail.search(None, 'ALL')
                if status == 'OK' and msgs and msgs[0]:
                    for eid in msgs[0].split():
                        mail.store(eid, '+FLAGS', '(\\Deleted)')
                    mail.expunge()
            except Exception:
                continue
