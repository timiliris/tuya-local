"""Publish the reviewed Markdown-only wiki using the user's Git CLI access."""
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]

def publish():
    with tempfile.TemporaryDirectory(prefix='tuya-local-wiki-') as tmp:
        destination=Path(tmp)/'wiki'
        subprocess.run(['git','clone','https://github.com/timiliris/tuya-local.wiki.git',str(destination)],check=True)
        # Remove only the superseded pages introduced by this migration.
        obsolete=['Installation-Docker','Application-bureau','Cameras-et-reglages','IA-locale','Reseau-et-confidentialite','Depannage','Developpement','Contribution','Licences','English']
        for name in obsolete:
            (destination/(name+'.md')).unlink(missing_ok=True)
        for source in (ROOT/'wiki').glob('*.md'):
            if source.is_symlink():raise ValueError('Wiki symlinks are not allowed')
            shutil.copy2(source,destination/source.name)
        def git(*args,**kwargs):return subprocess.run(['git','-C',str(destination),*args],check=True,**kwargs)
        git('config','user.name','timiliris');git('config','user.email','103514954+timiliris@users.noreply.github.com')
        git('add','--','*.md');git('diff','--cached','--check')
        if subprocess.run(['git','-C',str(destination),'diff','--cached','--quiet']).returncode==0:return
        git('commit','-m','Organize Tuya Local installation, desktop, AI and contributor guides')
        git('push','origin','HEAD')

if __name__=='__main__':publish()
