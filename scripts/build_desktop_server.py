"""Build a clean portable server using only allowlisted application assets."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]

def build():
    target=ROOT/'desktop/src-tauri/runtime'
    staging=ROOT/'desktop/.build'
    staging.mkdir(parents=True,exist_ok=True)
    engine=staging/('tuya-ipc-terminal.exe' if os.name=='nt' else 'tuya-ipc-terminal')
    subprocess.run(['go','build','-trimpath','-ldflags=-s -w','-o',str(engine),'.'],cwd=ROOT/'vendor/tuya-ipc-terminal',check=True,env={**os.environ,'CGO_ENABLED':'0'})
    sys.path.insert(0,str(ROOT/'src'))
    from ai_models import download
    from runtime_config import WEB_ASSETS
    public_web=staging/'web'
    if public_web.exists():shutil.rmtree(public_web)
    public_web.mkdir()
    for name in WEB_ASSETS:
        source=ROOT/'web'/name
        if source.is_symlink():raise ValueError('Web asset symlinks are not allowed')
        shutil.copy2(source,public_web/name)
    download(staging/'models')
    import imageio_ffmpeg
    ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe())
    # Normalize the bundled executable name; no dependence on the host PATH at runtime.
    ffmpeg_copy=staging/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg')
    shutil.copy2(ffmpeg,ffmpeg_copy)
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','tuya-local-server',
          '--distpath',str(staging/'dist'),'--workpath',str(staging/'work'),'--specpath',str(staging),
          '--paths',str(ROOT/'src'),'--collect-all','cv2']
    for source,destination in [(public_web,'web'),(staging/'models','models'),(ROOT/'vendor/nanodet','vendor/nanodet'),(ROOT/'LICENSE','licenses/project'),(ROOT/'NOTICE.md','licenses/project')]:
        args+=['--add-data',str(source)+os.pathsep+destination]
    args+=['--add-binary',str(engine)+os.pathsep+'bin','--add-binary',str(ffmpeg_copy)+os.pathsep+'bin',str(ROOT/'desktop/sidecar.py')]
    subprocess.run(args,cwd=ROOT,check=True)
    if target.exists():shutil.rmtree(target)
    shutil.copytree(staging/'dist/tuya-local-server',target)
    licenses=target/'licenses';licenses.mkdir(exist_ok=True)
    package=Path(imageio_ffmpeg.__file__).parent
    for name in ('LICENSE','LICENSE.txt','binaries/README.md'):
        source=package/name
        if source.exists():shutil.copy2(source,licenses/('imageio-ffmpeg-'+source.name))
    import importlib.metadata
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ('license','copying','notice')) and '.dist-info' in str(file) and file.suffix.lower() in ('','.txt','.md'):
                source=Path(distribution.locate_file(file))
                if source.is_file():
                    folder=licenses/distribution.metadata.get('Name','dependency');folder.mkdir(exist_ok=True)
                    shutil.copy2(source,folder/file.name)
    statement=subprocess.run([str(ffmpeg_copy),'-L'],capture_output=True,text=True,check=True)
    (licenses/'FFMPEG-LICENSE.txt').write_text(statement.stdout+statement.stderr)
    import requests
    for name in ('COPYING.GPLv2','COPYING.GPLv3','COPYING.LGPLv2.1','COPYING.LGPLv3'):
        response=requests.get('https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/'+name,timeout=30);response.raise_for_status()
        (licenses/name).write_bytes(response.content)
    shutil.copy2(ROOT/'vendor/tuya-ipc-terminal/LICENSE',licenses/'tuya-ipc-terminal-MIT.txt')
    print('Desktop server built without account data or camera images.')

if __name__=='__main__':build()
