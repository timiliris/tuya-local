"""Tuya Local: account, corrected RTSP engine, and local browser playback."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote

from procutil import creationflags, stop_process
from runtime_config import rtsp_host, port
from paths import install_root, engine_exe
from console_security import ConsoleHTTPServer, Security
RTSP_HOST = rtsp_host()
PLAYER_IDLE_TIMEOUT = 150
WEB = install_root() / 'web'
ALLOWED_ACTIONS = {'/api/qr/start', '/api/cameras/refresh', '/api/ptz/move', '/api/rtsp/start', '/api/settings/read', '/api/settings/write', '/api/settings/start', '/api/settings/status', '/api/settings/cancel'}
runtime = None


def upstream(path: str, body: dict | None = None):
    if path == '/api/state' and body is None:
        return runtime.state()
    return runtime.action(path, body or {})


def console_state(state: dict) -> dict:
    cameras = []
    for cam in state.get('cameras', []):
        cameras.append({key: cam.get(key) for key in ('deviceId', 'deviceName', 'rtspHd', 'rtspSd', 'online')})
    return {'loggedIn': bool(state.get('loggedIn')), 'phase': state.get('phase'),
            'hasQr': state.get('hasQr'), 'qrId': state.get('qrId'),
            'region': state.get('region'), 'regions': state.get('regions', {}),
            'cameras': cameras, 'engineRunning': bool(state.get('rtsp', {}).get('running')),
            'localOnly': True, 'wakeAttempts': 3,
            'player': 'WebRTC local, avec conversion locale de secours.'}


def playback_url(state: dict, device: str, quality: str) -> str:
    if quality not in ('hd', 'sd'):
        raise ValueError('Qualité inconnue.')
    camera = next((cam for cam in state.get('cameras', []) if cam.get('deviceId') == device), None)
    if camera is None:
        raise ValueError('Caméra inconnue.')
    path = urlparse(camera.get('rtspHd' if quality == 'hd' else 'rtspSd', '')).path
    if not path.startswith('/') or '\r' in path or '\n' in path:
        raise ValueError('Adresse vidéo invalide.')
    return f'rtsp://{RTSP_HOST}{quote(path, safe="/%")}'


def ffmpeg_command(url: str) -> list[str]:
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp',
            '-timeout', '110000000', '-allowed_media_types', 'video',
            '-analyzeduration', '1000000', '-probesize', '1000000', '-i', url, '-map', '0:v:0', '-an',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
            '-pix_fmt', 'yuv420p', '-profile:v', 'baseline', '-level:v', '5.1', '-threads', '2', '-vf', 'fps=12',
            '-g', '12', '-keyint_min', '12', '-sc_threshold', '0', '-bf', '0',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4', 'pipe:1']


class Players:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}

    def create(self, url: str, transport: str = 'mp4') -> str:
        if transport not in ('mp4', 'webrtc'):
            raise ValueError('Transport vidéo inconnu.')
        with self.lock:
            if len(self.items) >= 8:
                raise ValueError('Huit lecteurs sont déjà ouverts. Arrête un lecteur pour continuer.')
            token = secrets.token_urlsafe(24)
            self.items[token] = {'url': url, 'seen': time.monotonic(), 'process': None, 'claimed': False, 'transport': transport}
            return token

    def touch(self, token: str) -> bool:
        with self.lock:
            if token not in self.items:
                return False
            item = self.items[token]
            if item['process'] is not None and item['process'].poll() is not None:
                return False
            item['seen'] = time.monotonic()
            return True

    def open(self, token: str):
        with self.lock:
            item = self.items.get(token)
            if not item or item['claimed'] or item['transport'] != 'mp4':
                raise ValueError('Ce lecteur a expiré. Relance la vidéo.')
            item['claimed'] = True
            proc = subprocess.Popen(ffmpeg_command(item['url']), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=creationflags())
            item['process'] = proc
            return proc

    def offer(self, token: str, sdp: str) -> dict:
        if not isinstance(sdp, str) or len(sdp) > 60000:
            raise ValueError('Offre vidéo invalide.')
        with self.lock:
            item = self.items.get(token)
            if not item or item['claimed'] or item['transport'] != 'webrtc':
                raise ValueError('Ce lecteur a expiré. Relance la vidéo.')
            item['claimed'] = True
            proc = subprocess.Popen(
                [os.environ.get('TUYA_BROWSER_ENGINE', str(engine_exe())), 'browser-player'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=creationflags(),
            )
            item['process'] = proc
        timer = threading.Timer(112, lambda: self.stop(token))
        timer.daemon = True
        timer.start()
        try:
            proc.stdin.write(json.dumps({'url': item['url'], 'sdp': sdp}).encode())
            proc.stdin.close()
            line = proc.stdout.readline(65536)
            result = json.loads(line)
            if result.get('type') != 'answer' or not isinstance(result.get('sdp'), str):
                raise ValueError('Offre vidéo invalide.')
            with self.lock:
                if self.items.get(token) is not item:
                    raise ValueError('Ce lecteur a expiré. Relance la vidéo.')
            return result
        except (OSError, ValueError):
            self.stop(token)
            raise ValueError('WebRTC indisponible. Passage au lecteur de secours.') from None
        finally:
            timer.cancel()
            proc.stdout.close()

    def stop(self, token: str):
        with self.lock:
            item = self.items.pop(token, None)
        proc = (item or {}).get('process')
        if proc and proc.poll() is None:
            if os.name=='nt':
                stop_process(proc)
                return
            if item.get('transport') == 'webrtc':
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if item.get('transport') == 'webrtc':
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait(timeout=2)

    def reap(self):
        with self.lock:
            expired = [token for token, item in self.items.items() if time.monotonic() - item['seen'] > PLAYER_IDLE_TIMEOUT or item['process'] is not None and item['process'].poll() is not None]
        for token in expired:
            self.stop(token)


players = Players()


_ai = None
_ai_lock = threading.Lock()


def ai_service():
    global _ai
    with _ai_lock:
        if _ai is None:
            from console_ai import LocalAI
            from paths import tuya_data
            _ai = LocalAI(tuya_data())
        return _ai


def ai_camera(state, device):
    if not isinstance(device, str) or not any(cam.get('deviceId') == device for cam in state.get('cameras', [])):
        raise ValueError('Caméra inconnue.')
    return device


def ai_player(state, device, token):
    ai_camera(state, device)
    if not isinstance(token, str):
        raise ValueError('Ouvre la vidéo avant de lancer l’analyse.')
    with players.lock:
        item = players.items.get(token)
        if not item or not item['claimed'] or item['process'] is None or item['process'].poll() is not None or time.monotonic()-item['seen']>PLAYER_IDLE_TIMEOUT:
            raise ValueError('Ouvre la vidéo avant de lancer l’analyse.')
        if item['url'] not in {playback_url(state,device,'sd'),playback_url(state,device,'hd')}:
            raise ValueError('Caméra inconnue.')


class Handler(BaseHTTPRequestHandler):
    def version_string(self):
        return 'Tuya Local'

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()

    def guard(self):
        error = self.server.security.request_error(self)
        if error:
            self.json(error[0], {'error': error[1]})
            return False
        return True

    def log_message(self, *args):
        pass

    def json(self, status: int, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def session_response(self, token, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Set-Cookie', self.server.security.cookie(token))
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.guard():
            return
        path = urlparse(self.path).path
        try:
            if path == '/api/health':
                return self.json(200, {'service': 'tuya-local', 'localOnly': True})
            if path == '/api/access':
                return self.json(200, {'enabled': self.server.security.enabled, 'authenticated': self.server.security.authenticated(self.headers.get('Cookie'))})
            public = {'/login.js', '/login.css', '/brand.svg', '/locales.json', '/i18n.js'}
            if not self.server.security.authenticated(self.headers.get('Cookie')) and path not in public:
                if path in {'/', '/index.html'}:
                    return self.send_bytes((WEB / 'login.html').read_bytes(), 'text/html; charset=utf-8')
                return self.json(401, {'error': 'Authentication required.'})
            if path in {'/api/ai/state','/api/ai/events'}:
                devices = {cam['deviceId'] for cam in runtime.state().get('cameras', [])}
                return self.json(200, ai_service().state(devices) if path.endswith('/state') else ai_service().events(devices))
            if path.startswith('/api/ai/snapshot/') and path.endswith('.jpg'):
                devices = {cam['deviceId'] for cam in runtime.state().get('cameras', [])}
                return self.send_bytes(ai_service().snapshot(path.removeprefix('/api/ai/snapshot/').removesuffix('.jpg'),devices),'image/jpeg')
            if path == '/api/diagnostics':
                return self.json(200, runtime.diagnostics())
            if path == '/api/state':
                return self.json(200, console_state(upstream('/api/state')))
            if path == '/api/qr.png':
                data = runtime.qr()
                return self.send_bytes(data, 'image/png')
            if path.startswith('/stream/') and path.endswith('.mp4'):
                return self.stream(path.removeprefix('/stream/').removesuffix('.mp4'))
            from runtime_config import WEB_ASSETS
            assets = {'/' + name: name for name in WEB_ASSETS}
            assets['/'] = 'index.html'
            if path not in assets:
                return self.json(404, {'error': 'Page introuvable.'})
            file = WEB / assets[path]
            return self.send_bytes(file.read_bytes(), mimetypes.guess_type(str(file))[0] or 'text/plain')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json(502, {'error': 'Le moteur de caméras ne répond pas. Vérifie le serveur de bridge.'})

    def send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.guard():
            return
        try:
            path = urlparse(self.path).path
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return self.json(400, {'error': 'A single Content-Length is required.'})
            if self.headers.get_content_type() != 'application/json':
                return self.json(415, {'error': 'Use application/json.'})
            size = int(self.headers.get('Content-Length', '0'))
            limit = 400000 if path == '/api/ai/analyze' else 65536
            if size < 0 or size > limit:
                return self.json(413, {'error': 'Requête trop volumineuse.'})
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Requête invalide.')
            security = self.server.security
            if path == '/api/access/login':
                status, token = security.login(self.client_address[0], body.get('password'))
                if token:
                    return self.session_response(token, {})
                return self.json(status, {'error': 'Too many attempts. Wait one minute.' if status == 429 else 'Incorrect password.'})
            if not security.authenticated(self.headers.get('Cookie')):
                return self.json(401, {'error': 'Authentication required.'})
            if path == '/api/access/password':
                status, result, token = security.change_password(self.client_address[0], body.get('currentPassword'), body.get('password'), body.get('confirmation'))
                if not token:
                    return self.json(status, result)
                with players.lock:
                    active = list(players.items)
                self.session_response(token, result)
                def close_previous_players():
                    for player in active:
                        try:
                            players.stop(player)
                        except OSError:
                            pass
                threading.Thread(target=close_previous_players, daemon=True).start()
                return
            if path == '/api/access/logout':
                security.logout(self.headers.get('Cookie'))
                return self.session_response(None, {})
            if path in {'/api/ai/config','/api/ai/analyze','/api/ai/events/clear'}:
                if path.endswith('/clear'):
                    return self.json(200, ai_service().clear())
                state = runtime.state()
                device = ai_camera(state, body.get('deviceId'))
                if path.endswith('/config'):
                    return self.json(200, ai_service().configure(device, body.get('config')))
                ai_player(state, device, body.get('token'))
                try:
                    return self.json(200, ai_service().analyze(device, body.get('frame')))
                except ValueError:
                    raise
                except Exception:
                    return self.json(503, {'error': 'Le modèle IA n’a pas pu analyser cette image.'})
            if path == '/api/player/start':
                state = upstream('/api/state')
                url = playback_url(state, body.get('deviceId'), body.get('quality', 'sd'))
                if not state.get('rtsp', {}).get('running'):
                    upstream('/api/rtsp/start', {})
                transport = body.get('transport', 'mp4')
                token = players.create(url, transport)
                return self.json(200, {'token': token, 'url': f'/stream/{token}.mp4', 'transport': transport})
            if path == '/api/player/offer':
                return self.json(200, players.offer(body.get('token'), body.get('sdp')))
            if path == '/api/player/heartbeat':
                return self.json(200 if players.touch(body.get('token')) else 404, {'ok': True})
            if path == '/api/player/stop':
                players.stop(body.get('token'))
                return self.json(200, {'ok': True})
            if path in ALLOWED_ACTIONS:
                result = upstream(path, body)
                return self.json(200, result if path == '/api/ptz/move' or path.startswith('/api/settings/') else console_state(result))
            return self.json(404, {'error': 'Action inconnue.'})
        except (ValueError, TypeError) as exc:
            self.json(400, {'error': str(exc)})
        except Exception:
            self.json(502, {'error': 'La commande a échoué. La caméra peut être hors ligne ou incompatible.'})

    def stream(self, token):
        try:
            proc = players.open(token)
        except ValueError as exc:
            return self.json(404, {'error': str(exc)})
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            while chunk := proc.stdout.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            players.stop(token)
            proc.stdout.close()


def main(stop_event=None):
    global runtime
    if os.name != 'nt':
        os.umask(0o077)
    from paths import tuya_data
    security = Security(tuya_data() / "web-access.json")
    from console_runtime import Runtime
    runtime = Runtime()
    runtime.start()
    from console_ai_worker import AIWorkers
    service=ai_service()
    service.workers=AIWorkers(service,runtime.state)
    service.workers.start()
    def reap():
        while True:
            time.sleep(10)
            players.reap()
            if _ai is not None:
                _ai.cleanup()
    threading.Thread(target=reap, daemon=True).start()
    server = ConsoleHTTPServer((os.environ.get('TUYA_HTTP_HOST','127.0.0.1'), port('TUYA_HTTP_PORT',8787)), Handler, security)
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    if stop_event is not None:
        def watch_stop():
            stop_event.wait()
            shutdown()
        threading.Thread(target=watch_stop,daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        with players.lock:
            tokens = list(players.items)
        for token in tokens:
            players.stop(token)
        server.server_close()
        service.workers.stop()
        runtime.stop()


if __name__ == '__main__':
    main()
