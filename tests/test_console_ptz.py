import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from console_ptz import pulse, move

class PtzTests(unittest.TestCase):
    @patch('console_ptz.time.sleep')
    def test_movement_is_followed_by_stop(self, sleep):
        mobile=Mock();mobile._request.return_value=True
        result=pulse(mobile,'camera','right')
        dps=[json.loads(call.args[1]['dps']) for call in mobile._request.call_args_list]
        self.assertEqual(dps,[{'119':'2'},{'116':True}])
        self.assertEqual(result['response']['via'],'cloud')
        sleep.assert_called_once_with(.35)

    def test_failure_still_sends_stop(self):
        mobile=Mock();mobile._request.side_effect=[RuntimeError('failed'),True]
        with self.assertRaises(RuntimeError):pulse(mobile,'camera','left')
        self.assertEqual(json.loads(mobile._request.call_args_list[-1].args[1]['dps']),{'116':True})

    def test_stop_does_not_send_movement(self):
        mobile=Mock();mobile._request.return_value=True
        pulse(mobile,'camera','stop')
        self.assertEqual(mobile._request.call_count,1)
        with self.assertRaises(ValueError):move(Path('/unused'),'camera','arbitrary')

if __name__=='__main__':unittest.main()
