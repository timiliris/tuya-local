"""Persistent local accounts, role checks and bounded HTTP trust boundaries."""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import hmac
import ipaddress
import json
import tempfile
import os
import re
from pathlib import Path
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

SESSION_SECONDS = 12 * 60 * 60
COOKIE = 'tuya_local_session'
ALGORITHM = 'scrypt-16384-8-1'


class Security:
    def __init__(self, storage=None):
        self.storage = Path(storage) if storage is not None else None
        self.users = {}
        self.dummy_salt = secrets.token_bytes(16)
        if self.storage is not None and self.storage.exists():
            try:
                record = json.loads(self.storage.read_text())
                if record.get('algorithm') != ALGORITHM:
                    raise ValueError()
                if record.get('version') == 1:
                    records = [{'id': 'legacy-admin', 'username': 'admin', 'role': 'admin', 'salt': record['salt'], 'digest': record['digest']}]
                elif record.get('version') == 2:
                    records = record['users']
                else:
                    raise ValueError()
                if not isinstance(records, list) or not 1 <= len(records) <= 32:
                    raise ValueError()
                names = set()
                for user in records:
                    if set(user) != {'id', 'username', 'role', 'salt', 'digest'} or not isinstance(user['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', user['id']):
                        raise ValueError()
                    if self.username(user['username']) != user['username'] or user['role'] not in ('admin', 'viewer'):
                        raise ValueError()
                    if user['id'] in self.users or user['username'] in names or len(bytes.fromhex(user['salt'])) != 16 or len(bytes.fromhex(user['digest'])) != 32:
                        raise ValueError()
                    self.users[user['id']] = user
                    names.add(user['username'])
                if not any(user['role'] == 'admin' for user in self.users.values()):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ValueError('Invalid saved access protection; repair the private configuration before startup') from None
        else:
            password = os.environ.get('TUYA_ACCESS_PASSWORD', '')
            password_file = os.environ.get('TUYA_ACCESS_PASSWORD_FILE', '')
            if password_file:
                password = Path(password_file).read_text().rstrip('\r\n')
                if not password:
                    raise ValueError('The configured access password file is empty')
            if password:
                self.validate_password(password, password)
                user = self.make_user('admin', 'admin', password, 'legacy-admin')
                self.users[user['id']] = user
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
        self.lock = threading.RLock()

    @property
    def enabled(self):
        return bool(self.users)

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9._-]{3,40}', value.strip().lower()):
            raise ValueError('Utilisez un identifiant de 3 à 40 lettres, chiffres, points ou tirets.')
        return value.strip().lower()

    @staticmethod
    def validate_password(password, confirmation):
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError('Utilisez un mot de passe de 12 à 1024 caractères.')
        if password != confirmation:
            raise ValueError('Les mots de passe ne correspondent pas.')

    @staticmethod
    def hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)

    def make_user(self, username, role, password, identifier=None):
        salt = secrets.token_bytes(16)
        return {'id': identifier or secrets.token_hex(16), 'username': username, 'role': role, 'salt': salt.hex(), 'digest': self.hash(password, salt).hex()}

    def matches(self, user, password):
        if not isinstance(password, str) or len(password) > 1024:
            return False
        salt = bytes.fromhex(user['salt']) if user else self.dummy_salt
        digest = self.hash(password, salt)
        return user is not None and hmac.compare_digest(digest, bytes.fromhex(user['digest']))

    @staticmethod
    def token(cookie):
        try:
            cookies = SimpleCookie(cookie or '')
            return cookies[COOKIE].value if COOKIE in cookies else ''
        except CookieError:
            return ''

    def principal(self, cookie):
        with self.lock:
            if not self.enabled:
                return {'id': 'bootstrap', 'username': '', 'role': 'admin'}
            token = self.token(cookie)
            session = self.sessions.get(token)
            if not session or session['expires'] <= time.monotonic():
                self.sessions.pop(token, None)
                return None
            user = self.users.get(session['userId'])
            return {key: user[key] for key in ('id', 'username', 'role')} if user else None

    def authenticated(self, cookie):
        return self.principal(cookie) is not None

    def is_admin(self, cookie):
        return (self.principal(cookie) or {}).get('role') == 'admin'

    def access(self, cookie):
        principal = self.principal(cookie)
        return {'enabled': self.enabled, 'authenticated': principal is not None, 'user': principal, 'canManage': bool(principal and principal['role'] == 'admin')}

    def owner(self, cookie):
        if not self.principal(cookie):
            return None
        return self.token(cookie) if self.enabled else 'bootstrap'

    def active_owners(self):
        with self.lock:
            if not self.enabled:
                return {'bootstrap'}
            return {token for token, session in self.sessions.items() if session['expires'] > time.monotonic() and session['userId'] in self.users}

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

    def new_session(self, identifier):
        now = time.monotonic()
        for key in list(self.sessions):
            if self.sessions[key]['expires'] <= now:
                del self.sessions[key]
        while len(self.sessions) >= 64:
            self.sessions.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {'expires': now + SESSION_SECONDS, 'userId': identifier}
        return token

    def login(self, address, password, username='admin'):
        if not self.allow_attempt(address):
            return 429, None
        with self.lock:
            name = username.strip().lower() if isinstance(username, str) else ''
            user = next((user for user in self.users.values() if user['username'] == name), None)
            if not self.matches(user, password):
                return 401, None
            return 200, self.new_session(user['id'])

    def persist(self, users):
        if self.storage is None:
            raise ValueError('Le stockage sécurisé du serveur est indisponible.')
        temporary = None
        try:
            self.storage.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.web-access-', dir=self.storage.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump({'version': 2, 'algorithm': ALGORITHM, 'users': list(users.values())}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.storage)
        except OSError:
            raise ValueError('Les comptes n’ont pas pu être enregistrés. Réessayez.') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.users = users

    def revoke(self, identifier):
        for token in list(self.sessions):
            if self.sessions[token]['userId'] == identifier:
                del self.sessions[token]

    def change_password(self, address, current, password, confirmation, cookie=None, username='admin'):
        if not self.allow_attempt(address):
            return 429, {'error': 'Trop de tentatives. Attendez une minute.'}, None
        try:
            self.validate_password(password, confirmation)
            with self.lock:
                actor = self.principal(cookie)
                if not actor or actor['role'] != 'admin':
                    return 403, {'error': 'Accès réservé aux administrateurs.'}, None
                if self.enabled:
                    user = self.users[actor['id']]
                    if not self.matches(user, current):
                        return 400, {'error': 'Le mot de passe actuel est incorrect.'}, None
                    name, identifier = user['username'], user['id']
                else:
                    name, identifier = self.username(username), secrets.token_hex(16)
                updated = dict(self.users)
                updated[identifier] = self.make_user(name, 'admin', password, identifier)
                self.persist(updated)
                self.revoke(identifier)
                token = self.new_session(identifier)
                return 200, self.access(self.cookie(token)), token
        except ValueError as error:
            return (503 if 'enregistrés' in str(error) or 'stockage' in str(error) else 400), {'error': str(error)}, None

    def public_users(self, cookie):
        with self.lock:
            if not self.is_admin(cookie):
                raise PermissionError('Accès réservé aux administrateurs.')
            return {'users': [{key: user[key] for key in ('id', 'username', 'role')} for user in sorted(self.users.values(), key=lambda user: user['username'])]}

    def manage_user(self, cookie, operation, body):
        with self.lock:
            actor = self.principal(cookie)
            if not actor or actor['role'] != 'admin' or not self.enabled:
                raise PermissionError('Accès réservé aux administrateurs.')
            updated = dict(self.users)
            identifier = body.get('id')
            if operation == 'create':
                if len(updated) >= 32:
                    raise ValueError('La limite de 32 utilisateurs est atteinte.')
                name = self.username(body.get('username'))
                if any(user['username'] == name for user in updated.values()):
                    raise ValueError('Cet identifiant est déjà utilisé.')
                role = body.get('role')
                if role not in ('admin', 'viewer'):
                    raise ValueError('Rôle inconnu.')
                self.validate_password(body.get('password'), body.get('confirmation'))
                user = self.make_user(name, role, body['password'])
                updated[user['id']] = user
            else:
                if not isinstance(identifier, str) or identifier not in updated:
                    raise ValueError('Utilisateur introuvable.')
                if identifier == actor['id']:
                    raise ValueError('Votre compte administrateur doit rester actif. Utilisez le formulaire de mot de passe pour le modifier.')
                if operation == 'delete':
                    del updated[identifier]
                elif operation == 'update':
                    role = body.get('role')
                    if role not in ('admin', 'viewer'):
                        raise ValueError('Rôle inconnu.')
                    user = dict(updated[identifier]); user['role'] = role
                    if body.get('password') or body.get('confirmation'):
                        self.validate_password(body.get('password'), body.get('confirmation'))
                        user = self.make_user(user['username'], role, body['password'], identifier)
                    updated[identifier] = user
                else:
                    raise ValueError('Action inconnue.')
            if not any(user['role'] == 'admin' for user in updated.values()):
                raise ValueError('Au moins un administrateur doit rester actif.')
            self.persist(updated)
            if identifier:
                self.revoke(identifier)
            return self.public_users(cookie)

    def logout(self, cookie):
        with self.lock:
            self.sessions.pop(self.token(cookie), None)

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
