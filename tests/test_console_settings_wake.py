import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from console_settings_wake import local_camera_awake

class LocalWakeTests(unittest.TestCase):
    def camera(self, packet=True, reject=False):
        client, server=socket.socketpair()
        self.addCleanup(client.close);self.addCleanup(server.close)
        requests=[];closed=threading.Event()
        def serve():
            file=server.makefile('rb')
            try:
                for sequence in range(1,4):
                    lines=[]
                    while True:
                        line=file.readline()
                        if not line:return
                        lines.append(line)
                        if line==b'\r\n':break
                    requests.append(b''.join(lines).decode())
                    code='404 Not Found' if reject else '200 OK'
                    server.sendall(f'RTSP/1.0 {code}\r\nCSeq: {sequence}\r\nSession: local;timeout=60\r\nContent-Length: 0\r\n\r\n'.encode())
                    if reject:return
                if packet:
                    # RTCP first: this must not count as camera video.
                    server.sendall(b'$\x01\x00\x04test')
                    payload=b'\x80\x60'+b'\0'*10+b'video'
                    server.sendall(b'$\0'+len(payload).to_bytes(2,'big')+payload)
                file.read()
            except OSError:
                pass
            finally:
                file.close();server.close();closed.set()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        return client,requests,closed,thread

    def test_reader_verifies_real_rtp_and_holds_only_the_local_connection(self):
        client,requests,closed,thread=self.camera()
        with patch('console_settings_wake.socket.create_connection',return_value=client) as connect:
            with local_camera_awake('/Outside /?'):
                self.assertFalse(closed.is_set())
                connect.assert_called_once_with(('127.0.0.1',8554),timeout=3)
            self.assertTrue(closed.wait(1))
        self.assertIn('rtsp://127.0.0.1:8554/Outside%20%2F%3F/sd',requests[0])
        self.assertIn('/sd/video',requests[1]);self.assertIn('interleaved=0-1',requests[1])
        self.assertIn('Session: local',requests[2]);thread.join(timeout=1)

    def test_play_response_without_video_is_not_a_confirmed_wake(self):
        client,requests,closed,thread=self.camera(packet=False)
        with patch('console_settings_wake.socket.create_connection',return_value=client):
            with self.assertRaises(ValueError):
                with local_camera_awake('Outside',timeout=.2):self.fail('no RTP received')
        self.assertTrue(closed.wait(1))

    @patch('console_settings_wake.socket.create_connection')
    def test_cancelled_operation_never_opens_a_socket(self,connect):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(ValueError):
            with local_camera_awake('Outside',cancel):pass
        connect.assert_not_called()

    def test_rejected_stream_fails_and_releases_socket(self):
        client,requests,closed,thread=self.camera(reject=True)
        with patch('console_settings_wake.socket.create_connection',return_value=client):
            with self.assertRaises(ValueError):
                with local_camera_awake('Outside'):pass
        self.assertTrue(closed.wait(1))

    def test_cancel_while_waiting_for_rtp_releases_the_socket(self):
        client,requests,closed,thread=self.camera(packet=False)
        cancel=threading.Event();timer=threading.Timer(.05,cancel.set);timer.start()
        try:
            with patch('console_settings_wake.socket.create_connection',return_value=client):
                with self.assertRaisesRegex(ValueError,'Réveil annulé'):
                    with local_camera_awake('Outside',cancel):pass
            self.assertTrue(closed.wait(1))
        finally:timer.cancel()
