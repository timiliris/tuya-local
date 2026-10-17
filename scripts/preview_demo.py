"""Local documentation preview with synthetic cameras; never opens real media or Tuya."""
from pathlib import Path
import os
import sys
import tempfile
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import console
from console_security import ConsoleHTTPServer, Security
from console_ai import LocalAI

class Demo:
    def state(self):
        return {'loggedIn': True, 'rtsp': {'running': True}, 'cameras': [
            {'deviceId': f'demo-{i}', 'deviceName': name, 'online': True, 'rtspHd': f'rtsp://127.0.0.1:8554/demo-{i}/hd', 'rtspSd': f'rtsp://127.0.0.1:8554/demo-{i}/sd'}
            for i, name in enumerate(('Demo · Entrance', 'Demo · Garden'))]}
    def action(self, path, body):
        raise ValueError('Documentation preview: camera connections are disabled.')

class AccountPreviewHandler(console.Handler):
    """Auto-select synthetic preview sessions; never used by the production server."""
    def do_GET(self):
        self.preview_front = self.path in ('/', '/index.html')
        if self.preview_front:
            if 'Cookie' in self.headers: del self.headers['Cookie']
            self.headers['Cookie'] = self.server.preview_cookie
        super().do_GET()

    def end_headers(self):
        if getattr(self, 'preview_front', False):
            self.send_header('Set-Cookie', self.server.preview_cookie)
        super().end_headers()


if __name__ == '__main__':
    # Ignore inherited credentials: this preview uses an isolated in-memory runtime.
    for name in ('TUYA_ACCESS_PASSWORD', 'TUYA_ACCESS_PASSWORD_FILE', 'TUYA_ALLOWED_HOSTS', 'TUYA_PUBLIC_ORIGIN'):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix='tuya-docs-') as data:
        console.runtime = Demo()
        console._ai = LocalAI(Path(data))
        security = Security(Path(data) / 'web-access.json')
        if '--users' in sys.argv:
            for name, role in (('admin-demo', 'admin'), ('reader-demo', 'viewer')):
                user = security.make_user(name, role, os.urandom(32).hex())
                security.users[user['id']] = user
            server = ConsoleHTTPServer(('127.0.0.1', 18889), AccountPreviewHandler, security)
            viewer = ConsoleHTTPServer(('127.0.0.1', 18891), AccountPreviewHandler, security)
            for instance, role in ((server, 'admin'), (viewer, 'viewer')):
                identifier = next(user['id'] for user in security.users.values() if user['role'] == role)
                instance.preview_cookie = security.cookie(security.new_session(identifier))
            threading.Thread(target=viewer.serve_forever, daemon=True).start()
        else:
            server = ConsoleHTTPServer(('127.0.0.1', 18889), console.Handler, security)
        protected = Security()
        user = protected.make_user('admin', 'admin', os.urandom(32).hex())
        protected.users[user['id']] = user
        login = ConsoleHTTPServer(('127.0.0.1', 18890), console.Handler, protected)
        threading.Thread(target=login.serve_forever, daemon=True).start()
        print('Synthetic documentation preview: http://127.0.0.1:18889/ (login preview: 18890)', flush=True)
        try: server.serve_forever()
        finally:
            server.server_close(); login.shutdown(); login.server_close()
            if '--users' in sys.argv: viewer.shutdown(); viewer.server_close()
