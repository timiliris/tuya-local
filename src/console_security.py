"""HTTP trust boundaries and optional local access sessions (no Tuya credentials)."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import hmac
import ipaddress
import json
import tempfile
import os
from pathlib import Path
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

SESSION_SECONDS = 12 * 60 * 60
COOKIE = 'tuya_local_session'


class Security:
    def __init__(self, storage=None):
        self.storage = Path(storage) if storage is not None else None
        self.salt = secrets.token_bytes(16)
        self.digest = None
        if self.storage is not None and self.storage.exists():
            try:
                record = json.loads(self.storage.read_text())
                if record.get('version') != 1 or record.get('algorithm') != 'scrypt-16384-8-1':
                    raise ValueError()
                salt = bytes.fromhex(record['salt'])
                digest = bytes.fromhex(record['digest'])
                if len(salt) != 16 or len(digest) != 32:
                    raise ValueError()
                self.salt, self.digest = salt, digest
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ValueError('Invalid saved access protection; repair the private configuration before startup') from None
        else:
            password = os.environ.get('TUYA_ACCESS_PASSWORD', '')
            password_file = os.environ.get('TUYA_ACCESS_PASSWORD_FILE', '')
            if password_file:
                password = Path(password_file).read_text().rstrip('\r\n')
                if not password:
                    raise ValueError('The configured access password file is empty')
            if password and not 12 <= len(password) <= 1024:
                raise ValueError('The access password must contain 12 to 1024 characters')
            self.digest = self.hash(password) if password else None
        self.public_origin = os.environ.get('TUYA_PUBLIC_ORIGIN', '').rstrip('/')
        if self.public_origin:
            parsed = urlsplit(self.public_origin)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('TUYA_PUBLIC_ORIGIN must be an HTTPS origin without a path')
        self.allowed_hosts = {host.strip().lower() for host in os.environ.get('TUYA_ALLOWED_HOSTS', '').split(',') if host.strip()}
        if self.public_origin:
            self.allowed_hosts.add(urlsplit(self.public_origin).hostname.lower())
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return self.digest is not None

    def hash(self, password, salt=None):
        return hashlib.scrypt(password.encode(), salt=self.salt if salt is None else salt, n=16384, r=8, p=1, dklen=32)

    def valid_host(self, authority):
        try:
            parsed = urlsplit('http://' + authority)
            host = parsed.hostname
            if not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                return False
            if host == 'localhost' or host.lower() in self.allowed_hosts:
                return True
            address = ipaddress.ip_address(host)
            return address.is_loopback or address.is_private and not address.is_unspecified and not address.is_multicast
        except (ValueError, TypeError):
            return False

    def request_error(self, handler):
        hosts = handler.headers.get_all('Host', [])
        if len(hosts) != 1 or not self.valid_host(hosts[0]):
            return 403, 'Unrecognized server address.'
        origin = handler.headers.get('Origin')
        expected = self.public_origin or 'http://' + hosts[0]
        if origin and origin != expected:
            return 403, 'Requests from another origin are blocked.'
        site = handler.headers.get('Sec-Fetch-Site')
        if site in {'cross-site', 'same-site'}:
            # Permit links to the front door, never embedded API/media requests.
            if not (handler.command == 'GET' and urlsplit(handler.path).path in {'/', '/index.html'} and handler.headers.get('Sec-Fetch-Mode') == 'navigate'):
                return 403, 'Requests from another origin are blocked.'
        return None

    def authenticated(self, cookie):
        if not self.enabled:
            return True
        try:
            cookies = SimpleCookie(cookie or '')
            token = cookies[COOKIE].value if COOKIE in cookies else ''
        except CookieError:
            return False
        with self.lock:
            expiry = self.sessions.get(token, 0)
            if expiry <= time.monotonic():
                self.sessions.pop(token, None)
                return False
            return True

    def allow_attempt(self, address):
        now = time.monotonic()
        with self.lock:
            for key in list(self.attempts):
                if self.attempts[key][0] <= now:
                    del self.attempts[key]
            for key, limit in ((address, 5), ('*', 30)):
                if self.attempts.get(key, (0, 0))[1] >= limit:
                    return False
            if len(self.attempts) >= 256:
                return False
            for key in (address, '*'):
                expiry, count = self.attempts.get(key, (now + 60, 0))
                self.attempts[key] = (expiry, count + 1)
        return True

    def new_session(self):
        # Call only while holding the security lock.
        now = time.monotonic()
        for key in list(self.sessions):
            if self.sessions[key] <= now:
                del self.sessions[key]
        while len(self.sessions) >= 64:
            self.sessions.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + SESSION_SECONDS
        return token

    def login(self, address, password):
        if not self.allow_attempt(address):
            return 429, None
        with self.lock:
            if not isinstance(password, str) or len(password) > 1024 or not self.enabled or not hmac.compare_digest(self.hash(password), self.digest):
                return 401, None
            return 200, self.new_session()

    def change_password(self, address, current, password, confirmation):
        if not self.allow_attempt(address):
            return 429, {'error': 'Trop de tentatives. Attendez une minute.'}, None
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            return 400, {'error': 'Utilisez un mot de passe de 12 à 1024 caractères.'}, None
        if password != confirmation:
            return 400, {'error': 'Les mots de passe ne correspondent pas.'}, None
        with self.lock:
            if self.enabled and (not isinstance(current, str) or len(current) > 1024 or not hmac.compare_digest(self.hash(current), self.digest)):
                return 400, {'error': 'Le mot de passe actuel est incorrect.'}, None
            if self.storage is None:
                return 503, {'error': 'Le stockage sécurisé du serveur est indisponible.'}, None
            salt = secrets.token_bytes(16)
            digest = self.hash(password, salt)
            record = {'version': 1, 'algorithm': 'scrypt-16384-8-1', 'salt': salt.hex(), 'digest': digest.hex()}
            temporary = None
            try:
                self.storage.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.web-access-', dir=self.storage.parent, delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(record, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.storage)
            except OSError:
                return 503, {'error': 'Le mot de passe n’a pas pu être enregistré. Réessayez.'}, None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self.salt, self.digest = salt, digest
            self.sessions.clear()
            return 200, {'enabled': True, 'authenticated': True}, self.new_session()

    def logout(self, cookie):
        try:
            cookies = SimpleCookie(cookie or '')
            with self.lock:
                if COOKIE in cookies:
                    self.sessions.pop(cookies[COOKIE].value, None)
        except CookieError:
            pass

    def cookie(self, token):
        return f'{COOKIE}={token or ""}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS if token else 0}' + ('; Secure' if self.public_origin else '')


class ConsoleHTTPServer(ThreadingHTTPServer):
    """Bound simultaneous requests and idle socket lifetime, including failed clients."""
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, security=None):
        self.security = security or Security()
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()
