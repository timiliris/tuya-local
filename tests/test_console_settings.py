import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from console_settings import Settings, describe, validate


def metadata(value=False):
    return {'localKey': 'private', 'sid': 'private', 'schema': json.dumps([
        {'id': 87, 'code': 'basic_osd', 'mode': 'rw', 'property': {'type': 'bool'}},
        {'id': 88, 'code': 'device_restart', 'mode': 'rw', 'property': {'type': 'bool'}},
        {'id': 89, 'code': 'wireless_electricity', 'mode': 'rw', 'property': {'type': 'value', 'min': 0, 'max': 100}},
        {'id': 90, 'code': 'motion_sensitivity', 'mode': 'rw', 'property': {'type': 'enum', 'range': ['0', '2']}},
        {'id': 91, 'code': 'wireless_lowpower', 'mode': 'rw', 'property': {'type': 'value', 'min': 10, 'max': 50, 'step': 5}},
    ]), 'dps': {'87': value, '88': False, '89': 90, '90': '2', '91': 20, '250': 'private'}}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.service = Settings(Path('/unused'))
        self.body = {'code': 'basic_osd', 'value': True, 'expectedValue': False}

    def test_only_curated_values_are_exposed_and_battery_is_readonly(self):
        result = describe(metadata()); text = json.dumps(result)
        for key in ('private', 'localKey', 'sid', 'device_restart'):
            self.assertNotIn(key, text)
        self.assertEqual(len(result['settings']), 4)
        battery = next(f for f in result['settings'] if f['code'] == 'wireless_electricity')
        self.assertFalse(battery['writable'])
        self.assertTrue(result['videoLocal'])

    def test_values_types_modes_and_enum_ranges_are_validated(self):
        fields = {f['code']: f for f in describe(metadata())['settings']}
        for code, values in [('basic_osd', [1, 'true', None]), ('motion_sensitivity', ['1', 2]), ('wireless_lowpower', [11, 55, 20.0, True])]:
            for value in values:
                with self.assertRaises(ValueError): validate(fields[code], value)
        validate(fields['motion_sensitivity'], '2')
        validate(fields['wireless_lowpower'], 25)
        with self.assertRaises(ValueError): validate(fields['wireless_electricity'], 50)

    @patch('console_settings.time.sleep')
    def test_write_uses_actual_schema_id_then_reads_reported_value(self, sleep):
        mobile = Mock(); mobile._request.side_effect = [metadata(), True, metadata(True)]
        result = self.service.exchange(mobile, 'camera', self.body, True)
        publish = mobile._request.call_args_list[1]
        self.assertEqual(json.loads(publish.args[1]['dps']), {'87': True})
        self.assertEqual(result['writeStatus'], 'reported')
        self.assertEqual(mobile._request.call_count, 3)

    @patch('console_settings.time.sleep')
    def test_cloud_acceptance_is_not_reported_as_execution(self, sleep):
        mobile = Mock(); mobile._request.side_effect = [metadata(), True, metadata(), metadata()]
        result = self.service.exchange(mobile, 'camera', self.body, True)
        self.assertEqual(result['writeStatus'], 'pending')
        self.assertIs(result['settings'][0]['value'], False)

    @patch('console_settings.time.sleep')
    def test_readback_failure_keeps_accepted_status(self, sleep):
        mobile = Mock(); mobile._request.side_effect = [metadata(), True, RuntimeError('private')]
        result = self.service.exchange(mobile, 'camera', self.body, True)
        self.assertEqual(result['writeStatus'], 'pending')
        self.assertNotIn('private', json.dumps(result))

    def test_reject_unknown_readonly_stale_and_invalid_without_publish(self):
        for body in [dict(self.body, code='sd_format'), dict(self.body, code='wireless_electricity'), dict(self.body, expectedValue=True), dict(self.body, value=1), {'code':'basic_osd','value':True}]:
            mobile = Mock(); mobile._request.return_value = metadata()
            with self.assertRaises(ValueError): self.service.exchange(mobile, 'camera', body, True)
            self.assertEqual(mobile._request.call_count, 1)

    def test_unchanged_is_not_published(self):
        mobile = Mock(); mobile._request.return_value = metadata()
        result = self.service.exchange(mobile, 'camera', dict(self.body, value=False), True)
        self.assertEqual(result['writeStatus'], 'unchanged')
        self.assertEqual(mobile._request.call_count, 1)

    @patch('console_settings.TuyaClient')
    def test_unknown_camera_is_rejected_before_cloud_call(self, client):
        client.return_value.cameras = [{'deviceId': 'owned'}]
        with patch('console_settings.CloudMobile') as mobile:
            with self.assertRaises(ValueError): self.service.request({'deviceId': 'other'})
            mobile.assert_not_called()

    def test_schema_mismatch_and_malformed_constraints_cannot_enable_controls(self):
        data = metadata(); schema = json.loads(data['schema']); schema[0]['property']['type'] = 'raw'; schema[-1]['property']['scale'] = 100
        data['schema'] = schema
        self.assertNotIn('basic_osd', [f['code'] for f in describe(data)['settings']])
        self.assertNotIn('wireless_lowpower', [f['code'] for f in describe(data)['settings']])

    def test_http_settings_preserve_typed_response_and_reject_foreign_origin(self):
        import console
        payload = json.dumps({'deviceId': 'owned'}).encode()
        handler = console.Handler.__new__(console.Handler)
        handler.path = '/api/settings/read'
        handler.headers = {'Content-Length': str(len(payload)), 'Host': '127.0.0.1:8787'}
        handler.rfile = io.BytesIO(payload)
        handler.json = Mock()
        runtime = Mock(); runtime.action.return_value = describe(metadata())
        with patch.object(console, 'runtime', runtime):
            handler.do_POST()
            handler.json.assert_called_once_with(200, runtime.action.return_value)
            runtime.action.assert_called_once_with('/api/settings/read', {'deviceId': 'owned'})
            runtime.reset_mock(); handler.json.reset_mock()
            handler.headers['Origin'] = 'https://other.example'
            handler.do_POST()
            self.assertEqual(handler.json.call_args.args[0], 403)
            runtime.action.assert_not_called()

    def test_runtime_settings_do_not_acquire_video_runtime_lock(self):
        from console_runtime import Runtime
        backend = Mock(); runtime = Runtime(backend); runtime.settings = Mock(); runtime.lock = Mock()
        runtime.action('/api/settings/read', {'deviceId':'camera'})
        runtime.settings.request.assert_called_once_with({'deviceId':'camera'}, write=False)
        runtime.lock.acquire.assert_not_called()


class AutoWakeTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock
        self.service=Settings(Path('/unused'), ensure_engine=Mock())
        self.context=MagicMock()
        self.service.camera_wake=Mock(return_value=self.context)
        self.body={'code':'basic_osd','value':True,'expectedValue':False}

    @patch('console_settings.time.sleep')
    def test_battery_camera_is_woken_before_write_and_released_after_readback(self, sleep):
        mobile=Mock();mobile._request.side_effect=[metadata(),metadata(),True,metadata(True)]
        stages=[]
        result=self.service.exchange(mobile,'camera',self.body,True,progress=stages.append)
        self.assertEqual(stages,['waking','applying','confirming'])
        self.context.__enter__.assert_called_once()
        self.context.__exit__.assert_called_once()
        self.assertEqual(result['writeStatus'],'reported')
        self.assertEqual(mobile._request.call_args_list[2].args[0],'thing.m.device.dp.publish')

    def test_failed_wake_sends_no_setting(self):
        self.context.__enter__.side_effect=ValueError('wake timeout')
        mobile=Mock();mobile._request.return_value=metadata()
        with self.assertRaises(ValueError):self.service.exchange(mobile,'camera',self.body,True)
        self.assertEqual(mobile._request.call_count,1)

    def test_phone_change_during_wake_prevents_overwriting_it(self):
        mobile=Mock();mobile._request.side_effect=[metadata(),metadata(True)]
        with self.assertRaises(ValueError):self.service.exchange(mobile,'camera',self.body,True)
        self.context.__exit__.assert_called_once()
        self.assertEqual(mobile._request.call_count,2)

    def test_cancelled_wake_cleans_up_and_never_publishes(self):
        import threading
        cancel=threading.Event()
        mobile=Mock();mobile._request.return_value=metadata()
        with self.assertRaises(ValueError):self.service.exchange(mobile,'camera',self.body,True,progress=lambda stage:cancel.set(),cancel=cancel)
        self.assertEqual(mobile._request.call_count,1)
        self.context.__exit__.assert_called_once()

    def test_invalid_batch_is_fully_validated_before_wake(self):
        mobile=Mock();mobile._request.return_value=metadata()
        body={'changes':[self.body,{'code':'sd_format','value':True,'expectedValue':False}]}
        with self.assertRaises(ValueError):self.service.exchange(mobile,'camera',body,True)
        self.service.camera_wake.assert_not_called()
        self.assertEqual(mobile._request.call_count,1)

    @patch('console_settings.time.sleep')
    def test_batch_uses_one_wake_and_one_publish(self,sleep):
        final=metadata(True);final['dps']['90']='0'
        mobile=Mock();mobile._request.side_effect=[metadata(),metadata(),True,final]
        body={'changes':[self.body,{'code':'motion_sensitivity','value':'0','expectedValue':'2'}]}
        result=self.service.exchange(mobile,'camera',body,True)
        self.service.camera_wake.assert_called_once()
        self.assertEqual(json.loads(mobile._request.call_args_list[2].args[1]['dps']),{'87':True,'90':'0'})
        self.assertEqual(result['writeStatus'],'reported')

    def test_background_job_reports_errors_without_cloud_details(self):
        import time
        self.service.connection=Mock()
        self.service.request=Mock(side_effect=RuntimeError('private credentials'))
        token=self.service.start({'deviceId':'owned','changes':[self.body]})['job']
        deadline=time.monotonic()+1
        while not self.service.job_state(token)['done'] and time.monotonic()<deadline:time.sleep(.001)
        state=self.service.job_state(token)
        self.assertTrue(state['done']);self.assertEqual(state['stage'],'error')
        self.assertNotIn('private',json.dumps(state))
        self.assertNotIn('device',state)

    def test_unknown_job_token_is_rejected(self):
        with self.assertRaises(ValueError):self.service.job_state('unknown')

class SettingsReadRetryTests(unittest.TestCase):
    @patch('console_settings.time.sleep')
    def test_read_retries_once_without_replaying_any_write(self,sleep):
        service=Settings(Path('/unused'));mobile=Mock()
        mobile._request.side_effect=[RuntimeError('temporary'),metadata()]
        self.assertEqual(len(service.exchange(mobile,'camera',{},False)['settings']),4)
        self.assertEqual([call.args[0] for call in mobile._request.call_args_list],['thing.m.device.get']*2)
        sleep.assert_called_once_with(.6)

    def test_settings_reads_do_not_wait_for_a_writer_waking_the_camera(self):
        service=Settings(Path('/unused'));service.connection=Mock();service.exchange=Mock(return_value={'settings':[]})
        service.guard=Mock()
        self.assertEqual(service.request({'deviceId':'owned'}),{'settings':[]})
        service.guard.acquire.assert_not_called()

if __name__ == '__main__': unittest.main()
