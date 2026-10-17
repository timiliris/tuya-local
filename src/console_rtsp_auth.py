"""Private loopback authorization broker shared by HTTP and the RTSP engine."""
from collections import OrderedDict
import base64
import binascii
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler

from console_security import ConsoleHTTPServer

INTERNAL_USER = '_tuya_local_engine'


class RTSPAuthorization:
    def __init__(self, security):
        self.security = security
        self.secret = secrets.token_urlsafe(32)
        self.internal = secrets.token_urlsafe(32)
        self.grants = OrderedDict()
        self.lock = threading.RLock()
        self.server = None

    def check(self, body):
        with self.lock, self.security.lock:
            now = time.monotonic()
            for key in list(self.grants):
                if self.grants[key]['expires'] <= now:
                    del self.grants[key]
            token = body.get('ticket', '')
            grant = self.grants.get(token)
            if not grant:
                if token:
                    return {}
                try:
                    scheme, encoded = body.get('authorization', '').split(' ', 1)
                    if scheme.lower() != 'basic' or len(encoded) > 2048:
                        return {}
                    name, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
                except (ValueError, UnicodeError, binascii.Error, AttributeError):
                    return {}
                internal = name == INTERNAL_USER and hmac.compare_digest(password, self.internal)
                if not internal:
                    address = 'rtsp:' + str(body.get('address', ''))[:100]
                    if not self.security.enabled or not self.security.allow_attempt(address):
                        return {}
                    user = next((item for item in self.security.users.values() if item['username'] == name.strip().lower()), None)
                    if not self.security.matches(user, password):
                        return {}
                    # Successful VLC connections do not consume the failure budget.
                    for key in (address, '*'):
                        expiry, count = self.security.attempts[key]
                        if count <= 1:
                            self.security.attempts.pop(key, None)
                        else:
                            self.security.attempts[key] = (expiry, count - 1)
                if len(self.grants) >= 256:
                    return {}
                token = secrets.token_urlsafe(32)
                grant = {'internal': internal, 'user': None if internal else dict(user), 'expires': now + 12 * 3600}
                self.grants[token] = grant
            user = grant['user']
            if not grant['internal'] and self.security.users.get(user['id']) != user:
                self.grants.pop(token, None)
                return {}
            device = body.get('deviceId', '')
            allowed = grant['internal'] or not device or user['role'] == 'admin' or user['cameraIds'] is None or device in user['cameraIds']
            return {'allowed': bool(allowed), 'ticket': token, 'internal': grant['internal'], 'admin': grant['internal'] or user['role'] == 'admin'}

    def start(self):
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if self.path != '/authorize' or self.headers.get('Transfer-Encoding') or not 0 < length <= 8192 or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + broker.secret):
                        raise ValueError()
                    body = json.loads(self.rfile.read(length))
                    result = broker.check(body) if isinstance(body, dict) else {}
                except (ValueError, TypeError):
                    self.send_error(403)
                    return
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)

        self.server = ConsoleHTTPServer(('127.0.0.1', 0), Handler, self.security)
        os.environ['TUYA_RTSP_AUTH_URL'] = f'http://127.0.0.1:{self.server.server_port}/authorize'
        os.environ['TUYA_RTSP_AUTH_SECRET'] = self.secret
        os.environ['TUYA_RTSP_INTERNAL_PASSWORD'] = self.internal
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
