"""Console playback validation, privacy, and viewer lifecycle."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from console import Players, console_state, playback_url, ffmpeg_command, PLAYER_IDLE_TIMEOUT

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.state = {'loggedIn':True,'user':{'uid':'private'},'rtsp':{'running':True,'log':['private-session']},'cameras':[{'deviceId':'camera','deviceName':'Jardin','localKey':'private','rtspHd':'rtsp://127.0.0.1:8554/Jardin/hd','rtspSd':'rtsp://127.0.0.1:8554/Jardin/sd'}]}

    def test_state_does_not_expose_credentials_or_logs(self):
        state = console_state(self.state)
        self.assertNotIn('private',json.dumps(state))
        self.assertTrue(state['engineRunning'])
        self.assertEqual(state['wakeAttempts'],3)

    def test_playback_only_accepts_discovered_cameras_and_known_quality(self):
        self.assertEqual(playback_url(self.state,'camera','hd'),'rtsp://127.0.0.1:8554/Jardin/hd')
        with self.assertRaises(ValueError):playback_url(self.state,'rtsp://public.example/video','hd')
        with self.assertRaises(ValueError):playback_url(self.state,'camera','../../private')
        self.state['cameras'][0]['rtspHd']='rtsp://public.example/path/hd'
        self.assertEqual(playback_url(self.state,'camera','hd'),'rtsp://127.0.0.1:8554/path/hd')

    def test_process_starts_only_for_a_claimed_viewer_and_is_reaped(self):
        players=Players()
        with patch('console.subprocess.Popen') as popen:
            proc=Mock();proc.poll.return_value=None;popen.return_value=proc
            token=players.create('rtsp://127.0.0.1/camera/hd')
            popen.assert_not_called()
            self.assertIs(players.open(token),proc)
            with self.assertRaises(ValueError):players.open(token)
            self.assertTrue(players.touch(token))
            players.items[token]['seen']=time.monotonic()-PLAYER_IDLE_TIMEOUT-1
            players.reap()
            proc.terminate.assert_called_once()
            self.assertFalse(players.touch(token))
            self.assertNotIn(token,players.items)

    def test_stop_and_bounded_capacity(self):
        players=Players()
        for _ in range(8):players.create('rtsp://127.0.0.1/video')
        with self.assertRaises(ValueError):players.create('rtsp://127.0.0.1/video')
        token=next(iter(players.items));players.stop(token);players.stop(token)
        self.assertEqual(len(players.items),7)

    def test_webrtc_offer_is_single_use_and_passes_only_server_selected_url(self):
        import io
        players=Players()
        token=players.create('rtsp://127.0.0.1:8554/Jardin/hd','webrtc')
        with patch('console.subprocess.Popen') as popen:
            proc=Mock(); proc.stdout=io.BytesIO(b'{"type":"answer","sdp":"local-answer","codec":"video/H265"}\n')
            proc.stdin=Mock();popen.return_value=proc
            answer=players.offer(token,'browser-offer')
            self.assertEqual(answer['codec'],'video/H265')
            payload=json.loads(proc.stdin.write.call_args.args[0])
            self.assertEqual(payload,{'url':'rtsp://127.0.0.1:8554/Jardin/hd','sdp':'browser-offer'})
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            with self.assertRaises(ValueError):players.offer(token,'browser-offer')
            with self.assertRaises(ValueError):players.open(token)

    def test_webrtc_failure_and_stop_terminate_whole_process_group(self):
        import io
        players=Players()
        token=players.create('rtsp://127.0.0.1:8554/Jardin/hd','webrtc')
        with patch('console.subprocess.Popen') as popen, patch('console.os.killpg',create=True) as kill, patch('console.os.name','posix'):
            proc=Mock();proc.poll.return_value=None;proc.pid=1234
            proc.stdout=io.BytesIO(b'');popen.return_value=proc
            with self.assertRaisesRegex(ValueError,'WebRTC indisponible'):
                players.offer(token,'offer')
            self.assertFalse(players.touch(token))
            kill.assert_called_once()
            proc.wait.assert_called_once()

    def test_invalid_webrtc_transport_or_oversized_offer_never_starts_process(self):
        players=Players()
        with self.assertRaises(ValueError):players.create('rtsp://127.0.0.1/video','remote')
        token=players.create('rtsp://127.0.0.1/video','webrtc')
        with patch('console.subprocess.Popen') as popen:
            with self.assertRaises(ValueError):players.offer(token,'x'*60001)
            popen.assert_not_called()

    def test_heartbeat_rejects_a_dead_player_process(self):
        players=Players()
        token=players.create('rtsp://127.0.0.1:8554/Jardin/hd')
        process=Mock();process.poll.return_value=1
        players.items[token]['process']=process
        self.assertFalse(players.touch(token))
        process.poll.return_value=None
        self.assertTrue(players.touch(token))

    def test_ffmpeg_streams_in_memory_without_recording(self):
        command=ffmpeg_command('rtsp://127.0.0.1/video')
        self.assertEqual(command[-1],'pipe:1')
        self.assertIn('libx264',command)
        self.assertNotIn('-rw_timeout',command)
        self.assertEqual(command[command.index('-allowed_media_types')+1],'video')
        self.assertIn('frag_keyframe+empty_moov+default_base_moof',command)
        self.assertEqual(command[command.index('-rtsp_transport')+1],'tcp')

if __name__=='__main__':unittest.main()
