"""Temporary local RTSP reader. No decoder, media relay, recording or public socket."""
from __future__ import annotations

from contextlib import contextmanager
from runtime_config import rtsp_host, rtsp_port
import queue
import socket
import threading
import time
from urllib.parse import quote


class WakeCancelled(ValueError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise WakeCancelled('Réveil annulé. Aucun réglage envoyé.')


class LocalReader:
    def __init__(self, camera_path, cancel, timeout):
        self.url = 'rtsp://'+rtsp_host()+'/' + quote(camera_path.lstrip('/'), safe='') + '/sd'
        self.cancel = cancel
        self.deadline = time.monotonic() + timeout
        self.closed = threading.Event()
        self.ready = queue.Queue(maxsize=1)
        self.stream = None
        self.sequence = 0
        self.session = None
        self.confirmed = False
        self.worker = threading.Thread(target=self.run, daemon=True)

    def read(self, length):
        result = bytearray()
        while len(result) < length:
            check_cancel(self.cancel)
            if self.closed.is_set():
                raise OSError('reader closed')
            if not self.confirmed and time.monotonic() >= self.deadline:
                raise TimeoutError('wake deadline')
            try:
                data = self.stream.recv(length - len(result))
            except socket.timeout:
                continue
            if not data:
                raise OSError('local stream ended')
            result.extend(data)
        return bytes(result)

    def request(self, method, target, extra=None):
        self.sequence += 1
        headers = {'CSeq': str(self.sequence), **(extra or {})}
        from runtime_config import internal_rtsp_authorization
        authorization = internal_rtsp_authorization()
        if authorization:
            headers['Authorization'] = authorization
        if self.session:
            headers['Session'] = self.session
        request = method + ' ' + target + ' RTSP/1.0\r\n'
        request += ''.join(key + ': ' + value + '\r\n' for key, value in headers.items()) + '\r\n'
        self.stream.sendall(request.encode())
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            if len(header) >= 65536:
                raise OSError('invalid local RTSP response')
            header.extend(self.read(1))
        lines = header.decode('utf-8').split('\r\n')
        if not lines[0].startswith('RTSP/1.0 200 '):
            raise OSError('local RTSP rejected request')
        response = {key.strip().lower(): value.strip() for line in lines[1:] if ':' in line for key, value in [line.split(':', 1)]}
        length = int(response.get('content-length', '0'))
        if not 0 <= length <= 1048576:
            raise OSError('invalid local RTSP body')
        if length:
            self.read(length)
        return response

    def run(self):
        try:
            # Fixed loopback destination. SDP/cloud URLs can never redirect media.
            self.stream = socket.create_connection(('127.0.0.1', rtsp_port()), timeout=3)
            self.stream.settimeout(.25)
            self.request('DESCRIBE', self.url, {'Accept': 'application/sdp'})
            response = self.request('SETUP', self.url + '/video', {'Transport': 'RTP/AVP/TCP;unicast;interleaved=0-1'})
            self.session = response.get('session', '').split(';')[0]
            if not self.session:
                raise OSError('missing local RTSP session')
            self.request('PLAY', self.url)
            while not self.closed.is_set():
                header = self.read(4)
                if header[0] != 36:
                    raise OSError('invalid interleaved packet')
                packet = self.read(int.from_bytes(header[2:4], 'big'))
                # RTP on video channel zero, not RTCP or a bare PLAY response.
                if not self.confirmed and header[1] == 0 and len(packet) > 12 and packet[0] >> 6 == 2:
                    self.confirmed = True
                    self.ready.put(True)
        except (OSError, ValueError):
            if not self.confirmed:
                self.ready.put(False)
        finally:
            if self.stream:
                self.stream.close()

    def stop(self):
        self.closed.set()
        if self.stream:
            try:
                self.stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.worker.join(timeout=4)


@contextmanager
def local_camera_awake(camera_path, cancel=None, timeout=150):
    check_cancel(cancel)
    reader = LocalReader(camera_path, cancel, timeout)
    reader.worker.start()
    try:
        while True:
            check_cancel(cancel)
            remaining = reader.deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('La caméra ne répond pas au réveil. Aucun réglage envoyé.')
            try:
                ready = reader.ready.get(timeout=min(.25, remaining))
            except queue.Empty:
                continue
            check_cancel(cancel)
            if not ready:
                raise ValueError('La caméra ne répond pas au réveil. Aucun réglage envoyé.')
            break
        yield
    finally:
        reader.stop()
