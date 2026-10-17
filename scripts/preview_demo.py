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

if __name__ == '__main__':
    # Ignore inherited credentials: this preview uses an isolated in-memory runtime.
    for name in ('TUYA_ACCESS_PASSWORD', 'TUYA_ACCESS_PASSWORD_FILE', 'TUYA_ALLOWED_HOSTS', 'TUYA_PUBLIC_ORIGIN'):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix='tuya-docs-') as data:
        console.runtime = Demo()
        console._ai = LocalAI(Path(data))
        server = ConsoleHTTPServer(('127.0.0.1', 18889), console.Handler, Security(Path(data) / 'web-access.json'))
        protected = Security()
        protected.digest = protected.hash(os.urandom(32).hex())
        login = ConsoleHTTPServer(('127.0.0.1', 18890), console.Handler, protected)
        threading.Thread(target=login.serve_forever, daemon=True).start()
        print('Synthetic documentation preview: http://127.0.0.1:18889/ (login preview: 18890)', flush=True)
        try: server.serve_forever()
        finally: server.server_close(); login.shutdown(); login.server_close()
