"""Shutdown only terminates the process owned by this runtime."""
import sys
import subprocess
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procutil import stop_process

def test_owned_process_cleanup():
    with patch('procutil.os.name','posix'):
        verify_posix_cleanup()
    process=Mock();process.pid=12345;process.poll.return_value=None
    with patch('procutil.os.name','nt'),patch('procutil.creationflags',return_value=0),patch('procutil.subprocess.run') as run:
        stop_process(process)
        assert run.call_args.args[0]==['taskkill.exe','/PID','12345','/T','/F']

def verify_posix_cleanup():
    process=Mock();process.poll.return_value=None
    stop_process(process);process.terminate.assert_called_once();process.kill.assert_not_called()
    stopped=Mock();stopped.poll.return_value=0
    stop_process(stopped);stopped.terminate.assert_not_called()
    slow=Mock();slow.poll.return_value=None;slow.wait.side_effect=[subprocess.TimeoutExpired('owned',8),0]
    stop_process(slow);slow.kill.assert_called_once()

if __name__=='__main__':
    test_owned_process_cleanup()
    print('Owned-process cleanup passed')
