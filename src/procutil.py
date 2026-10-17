"""Process ownership helpers shared by supported operating systems."""
import os
import subprocess

def creationflags():
    return subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def stop_process(process, timeout=8):
    if process is None or process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=creationflags())
    else:
        process.terminate()
    try:process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
