import base64
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from console_ai import LocalAI,DEFAULT,validate_config,filter_objects,decode_frame,RETENTION,MAX_EVENTS


def frame(size=(320,180)):
    output=io.BytesIO();Image.new('RGB',size,'gray').save(output,format='JPEG')
    return base64.b64encode(output.getvalue()).decode()


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000.;self.detector=Mock();self.detector.infer.return_value=[{'class':'person','score':.9,'box':[.1,.1,.5,.9]}]
        self.service=LocalAI(Path(self.temp.name),self.detector,clock=lambda:self.now,monotonic=lambda:self.now)
        self.config=copy.deepcopy(DEFAULT);self.config['enabled']=True

    def enable(self,**changes):
        self.config.update(changes);self.service.configure('camera',self.config)

    def analyze(self):return self.service.analyze('camera',frame())

    def test_disabled_by_default_never_loads_a_model(self):
        self.assertTrue(self.analyze()['disabled']);self.detector.infer.assert_not_called()
        self.assertFalse(self.service.config('other')['enabled'])

    def test_settings_are_private_persistent_and_copied(self):
        self.enable(confidence=.8)
        config=self.service.config('camera');config['classes'].clear()
        self.assertTrue(self.service.config('camera')['classes'])
        again=LocalAI(Path(self.temp.name),self.detector)
        self.assertEqual(again.config('camera')['confidence'],.8)
        self.assertEqual(self.service.config_file.stat().st_mode & 0o777,0o600)

    def test_filter_applies_classes_confidence_size_and_zone(self):
        config=copy.deepcopy(DEFAULT);config.update(classes=['person'],zone=[.2,.2,.8,.8],minArea=.05)
        objects=[{'class':'person','score':.9,'box':[.3,.3,.6,.6]}, {'class':'person','score':.9,'box':[0,0,.1,.1]},
                 {'class':'car','score':.99,'box':[.3,.3,.6,.6]}, {'class':'person','score':.1,'box':[.3,.3,.6,.6]},
                 {'class':'person','score':.99,'box':[.3,.3,.31,.31]}]
        self.assertEqual(len(filter_objects(objects,config)),1)

    def test_events_need_confirmation_and_respect_cooldown(self):
        self.enable();self.analyze();self.assertEqual(self.service.events({'camera'}),[])
        self.now+=2;self.analyze();self.assertEqual(len(self.service.events({'camera'})),1)
        self.now+=2;self.analyze();self.assertEqual(len(self.service.events({'camera'})),1)
        self.now+=30;self.analyze();self.now+=2;self.analyze()
        self.assertEqual(len(self.service.events({'camera'})),2)
        self.assertEqual(self.service.events({'foreign'}),[])

    def test_missing_object_resets_confirmation_streak(self):
        self.enable();self.analyze();self.now+=2;self.detector.infer.return_value=[];self.analyze()
        self.now+=2;self.detector.infer.return_value=[{'class':'person','score':.9,'box':[.1,.1,.5,.9]}];self.analyze()
        self.assertEqual(self.service.events({'camera'}),[])

    def test_no_image_stored_unless_explicitly_enabled(self):
        self.enable(confirmations=1);self.analyze();event=self.service.events({'camera'})[0]
        self.assertFalse(event['snapshot'])
        with self.assertRaises(ValueError):self.service.snapshot(event['id'],{'camera'})
        self.enable(snapshots=True,confirmations=1);self.now+=2;self.analyze()
        event=self.service.events({'camera'})[0];self.assertTrue(event['snapshot'])
        self.assertEqual(self.service.snapshot(event['id'],{'camera'}),base64.b64decode(frame()))
        with self.assertRaises(ValueError):self.service.snapshot(event['id'],{'foreign'})

    def test_rate_limit_and_busy_model_skip_without_inference(self):
        self.enable();self.analyze();self.assertTrue(self.analyze()['skipped'])
        self.now+=2;self.service.infer_gate.acquire()
        try:self.assertTrue(self.analyze()['skipped'])
        finally:self.service.infer_gate.release()
        self.assertEqual(self.detector.infer.call_count,1)

    def test_disable_during_inference_cannot_create_an_event(self):
        self.enable(confirmations=1)
        def infer(*_):
            self.service.configure('camera',dict(self.config,enabled=False))
            return [{'class':'person','score':.9,'box':[.1,.1,.5,.9]}]
        self.detector.infer.side_effect=infer
        self.assertTrue(self.analyze()['skipped']);self.assertEqual(self.service.events({'camera'}),[])

    def test_model_failure_always_releases_inference_gate(self):
        self.enable();self.detector.infer.side_effect=RuntimeError('model failed')
        with self.assertRaises(RuntimeError):self.analyze()
        self.assertTrue(self.service.infer_gate.acquire(blocking=False));self.service.infer_gate.release()

    def test_history_is_bounded_and_expires_with_snapshots(self):
        self.enable(confirmations=1,snapshots=True)
        for _ in range(MAX_EVENTS+2):self.analyze();self.now+=31
        events=self.service.events({'camera'});self.assertEqual(len(events),MAX_EVENTS)
        self.now+=RETENTION+1;self.service.cleanup();self.assertEqual(self.service.events({'camera'}),[])

    def test_image_bounds_and_invalid_data_are_rejected(self):
        for encoded in ['not base64','A'*350001,frame((641,180))]:
            with self.assertRaises(ValueError):decode_frame(encoded)
        self.assertGreater(len(decode_frame(frame())),0)

    def test_invalid_settings_do_not_overwrite_saved_values(self):
        self.enable()
        for change in [{'enabled':'true'},{'model':'foreign'},{'classes':[]},{'classes':['face']},{'confidence':float('nan')},{'zone':[.9,0,.1,1]},{'interval':True},{'snapshots':'yes'}]:
            with self.assertRaises(ValueError):self.service.configure('camera',{**self.config,**change})
        self.assertEqual(self.service.config('camera'),self.config)

    def test_clear_removes_events_and_resets_confirmation(self):
        self.enable(confirmations=1);self.analyze();self.service.clear()
        self.assertEqual(self.service.events({'camera'}),[]);self.assertEqual(self.service.tracks,{})


class ActivePlayerTests(unittest.TestCase):
    def test_analysis_requires_a_live_player_bound_to_the_same_camera(self):
        import console
        state={'cameras':[{'deviceId':'a','rtspSd':'rtsp://127.0.0.1:8554/A/sd','rtspHd':'rtsp://127.0.0.1:8554/A/hd'},
                          {'deviceId':'b','rtspSd':'rtsp://127.0.0.1:8554/B/sd','rtspHd':'rtsp://127.0.0.1:8554/B/hd'}]}
        players=console.Players();token=players.create('rtsp://127.0.0.1:8554/A/sd')
        with patch.object(console,'players',players):
            with self.assertRaises(ValueError):console.ai_player(state,'a',token)
            process=Mock();process.poll.return_value=None;players.items[token].update(process=process,claimed=True)
            console.ai_player(state,'a',token)
            with self.assertRaises(ValueError):console.ai_player(state,'b',token)
            process.poll.return_value=0
            with self.assertRaises(ValueError):console.ai_player(state,'a',token)

if __name__=='__main__':unittest.main()
