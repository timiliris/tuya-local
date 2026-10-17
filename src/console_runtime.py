"""Account and corrected RTSP engine owned by the Tuya Local container."""
from __future__ import annotations

import threading
import re
import time
from paths import tuya_data


class Runtime:
    def __init__(self, backend=None):
        if backend is None:
            import server as backend
        self.backend = backend
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.worker = None

    def start(self):
        if self.backend.client.login:
            self.backend.rtsp.start()
        self.worker = threading.Thread(target=self.monitor, daemon=True)
        self.worker.start()

    def monitor(self):
        while not self.closed.wait(5):
            with self.lock:
                if self.backend.client.login and not self.backend.rtsp.running():
                    try:
                        self.backend.rtsp.start()
                    except Exception:
                        pass

    def state(self):
        return self.backend.public_state()

    def diagnostics(self):
        patterns = {
            'Camera wake command sent': 'wake_sent',
            'Camera startup wake pulse sent': 'wake_repeated',
            'Camera startup offer retransmitted': 'offer_repeated',
            'Camera startup HD command repeated': 'hd_repeated',
            'WebRTC connection established': 'webrtc_connected',
            'Successfully sent first video packet': 'video_received',
            'Retrying camera wake and local video connection': 'new_attempt',
            'Failed to start WebRTC bridge': 'startup_failed',
            'Camera disconnect details': 'camera_disconnected',
            'Executing delayed shutdown': 'idle_shutdown',
        }
        with self.backend.rtsp._lock:
            lines = list(self.backend.rtsp.log)
        events = []
        for line in lines:
            for pattern, event in patterns.items():
                if pattern not in line:
                    continue
                item = {'time': line[:12], 'event': event}
                if event == 'camera_disconnected':
                    match = re.search(r'\bcode=(\d+)\b', line)
                    if match:
                        item['code'] = int(match.group(1))
                if event == 'startup_failed':
                    item['reason'] = ('camera_disconnected' if 'camera client disconnected' in line
                                      else 'video_timeout' if 'startup deadline' in line
                                      else 'connection_failed')
                events.append(item)
        return events[-80:]

    def qr(self):
        if not self.backend.client.token:
            raise ValueError('Aucun QR code disponible.')
        return self.backend.qr_png_bytes()

    def qr_start(self, region):
        with self.lock:
            if self.backend.client.login:
                raise ValueError('Le compte est déjà connecté.')
            self.backend.client.set_region(region)
            self.backend.client.generate_qr()
            generation = self.backend.client.generation
            self.backend.set_phase('waiting', '')
            threading.Thread(target=self.poll_login, args=(generation,), daemon=True).start()
        return self.state()

    def poll_login(self, generation):
        client = self.backend.client
        try:
            while not self.closed.is_set() and client.generation == generation:
                if client.wait_login(timeout_s=80, interval_s=1, generation=generation):
                    if self.closed.is_set() or client.generation != generation:
                        return
                    with self.lock:
                        client.save_session()
                        client.discover_cameras()
                        self.backend.rtsp.start()
                        self.backend.set_phase('logged_in', '')
                    return
                if self.closed.is_set() or client.generation != generation:
                    return
                client.generate_qr()
                generation = client.generation
        except Exception:
            self.backend.set_phase('error', '')

    def action(self, path, body):
        with self.lock:
            if path == '/api/qr/start':
                return self.qr_start(body.get('region', 'we'))
            if path == '/api/cameras/refresh':
                self.backend.client.discover_cameras()
                self.backend.restart_rtsp_engine()
                return self.state()
            if path == '/api/rtsp/start':
                self.backend.rtsp.start()
                return self.state()
            if path == '/api/ptz/move':
                from console_ptz import move
                return move(tuya_data(), body.get('deviceId'), body.get('direction'))
            raise ValueError('Action inconnue.')

    def stop(self):
        self.closed.set()
        self.backend.client.generation += 1
        if self.worker:
            self.worker.join(timeout=6)
        if self.backend.rtsp.running():
            self.backend.rtsp.stop()
