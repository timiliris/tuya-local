"""Frozen server entry point; data and binaries are isolated from Docker."""
import os
import sys
import threading
from pathlib import Path
root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
os.environ['TUYA_BRIDGE_ROOT']=str(root)
os.environ['PATH']=str(root/'bin')+os.pathsep+os.environ.get('PATH','')
os.environ['TUYA_BROWSER_ENGINE']=str(root/'bin'/('tuya-ipc-terminal.exe' if os.name=='nt' else 'tuya-ipc-terminal'))
if '--self-test' in sys.argv:
    import io
    import subprocess
    sys.path.insert(0,str(root/'src'))
    from ai_models import Detector,MODELS
    from PIL import Image
    frame=io.BytesIO();Image.new('RGB',(320,180),'gray').save(frame,format='JPEG')
    detector=Detector(root/'models')
    for model in MODELS:detector.infer(frame.getvalue(),model)
    subprocess.run([os.environ['TUYA_BROWSER_ENGINE'],'--help'],stdout=subprocess.DEVNULL,check=True)
    subprocess.run([str(root/'bin'/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg')),'-version'],stdout=subprocess.DEVNULL,check=True)
    print('Bundled engine, decoder and both AI models passed.')
    raise SystemExit(0)
data=Path(os.environ['TUYA_DESKTOP_DATA'])
os.environ['XDG_DATA_HOME']=str(data/'data')
os.environ['XDG_CONFIG_HOME']=str(data/'config')
if os.name=='nt':os.environ['APPDATA']=str(data/'data')
sys.path.insert(0,str(root/'src'))
from console import main
stop_event=threading.Event()

def parent_watch():
    # Parent owns stdin. A crash or application exit closes it and stops the server.
    try:sys.stdin.buffer.read()
    finally:stop_event.set()

if __name__=='__main__':
    threading.Thread(target=parent_watch,daemon=True).start()
    main(stop_event)
