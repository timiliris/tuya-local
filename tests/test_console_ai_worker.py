import base64
import copy
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from console_ai import LocalAI,DEFAULT
from console_ai_worker import AIWorkers,source_url,effective_mode,command

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=LocalAI(Path(self.temp.name),Mock())
        self.camera={'deviceId':'camera','rtspSd':'rtsp://untrusted.example:99/camera/sd','skill':'{"lowPower":0,"videos":[{}]}'}
        self.manager=AIWorkers(self.service,lambda:{'cameras':[self.camera]})
        self.addCleanup(self.manager.stop)

    def test_sources_are_fixed_loopback_and_passive_by_default(self):
        self.assertEqual(source_url(self.camera,'passive'),'rtsp://127.0.0.1:8554/camera/sd?ai=passive')
        self.assertEqual(effective_mode(self.camera,DEFAULT),'passive')
        config={**DEFAULT,'sourceMode':'continuous'}
        self.assertEqual(effective_mode(self.camera,config),'continuous')
        self.assertEqual(effective_mode({'skill':{'videos':[{}],'webrtc':1}},config),'continuous')
        for skill in ['{"lowPower":1}','{}','invalid',None]:
            self.assertEqual(effective_mode({**self.camera,'skill':skill},config),'passive')
        args=command(source_url(self.camera,'passive'));self.assertIn('image2pipe',args);self.assertIn('-an',args)

    def test_legacy_configs_preserve_activation_and_filters(self):
        legacy={**DEFAULT,'enabled':True,'confidence':.8};legacy.pop('sourceMode')
        self.service.configure('camera',legacy)
        reloaded=LocalAI(Path(self.temp.name),Mock())
        self.assertTrue(reloaded.config('camera')['enabled'])
        self.assertEqual(reloaded.config('camera')['confidence'],.8)
        self.assertEqual(reloaded.config('camera')['sourceMode'],'passive')

    def test_startup_runs_enabled_camera_without_browser_and_disable_stops_it(self):
        self.service.configure('camera',{**DEFAULT,'enabled':True})
        entered=threading.Event();stopped=threading.Event()
        def reader(device,url,mode,cancel):
            entered.set();cancel.wait(3);stopped.set()
        self.manager.read=reader
        self.manager.start();self.assertTrue(entered.wait(2))
        self.service.configure('camera',DEFAULT)
        self.assertTrue(stopped.wait(2))

    def test_reader_failure_retries_and_closes_process(self):
        cancel=threading.Event();proc=Mock();proc.poll.return_value=None
        def launch(*args,**kwargs):cancel.set();return proc
        with patch('console_ai_worker.subprocess.Popen',side_effect=launch),patch('console_ai_worker.selectors.DefaultSelector',side_effect=ValueError):
            self.manager.read('camera','rtsp://127.0.0.1:8554/a?ai=passive','passive',cancel)
        proc.terminate.assert_called_once();proc.stdout.close.assert_called_once()
        self.assertEqual(self.manager.statuses['camera']['phase'],'waiting')

    def test_latest_results_are_owned_and_timestamped(self):
        from test_console_ai import frame
        self.service.detector.infer.return_value=[{'class':'person','score':.9,'box':[.1,.1,.5,.9]}]
        self.service.configure('camera',{**DEFAULT,'enabled':True,'confirmations':1})
        result=self.service.analyze('camera',frame())
        self.assertIn('person',result['confirmed']);self.assertGreater(result['time'],0)
        self.assertEqual(self.service.state({'camera'})['results']['camera'],result)
        self.assertNotIn('camera',self.service.state({'other'})['results'])
        self.service.configure('camera',DEFAULT)
        self.assertEqual(self.service.state({'camera'})['results']['camera'],{})
