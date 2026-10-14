"""Exercise every HTTP regression in an isolated copy of the real-paper library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from readmaster import server


def main():
    ident='f285b0971ae4a790e402fb93'
    original=ROOT/'output/ReadMaster-Client-Windows-x64/data/documents'/ident
    data=ROOT/'.build'/('comet-http-'+uuid.uuid4().hex[:8])
    folder=data/'documents'/ident
    folder.mkdir(parents=True)
    for name in ('original.pdf','document.json'):
        shutil.copy2(original/name,folder/name)
    server.DATA=data
    server.DOCS.clear()
    http=server.create_server(0)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    env=dict(os.environ)
    env['READMASTER_TEST_URL']=f'http://127.0.0.1:{http.server_port}'
    try:
        subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-q'],
                       cwd=ROOT,env=env,check=True)
        report={'passed':True,'http_tests_enabled':True,'live_models_tested':False,
                'library':str(data),'port':http.server_port}
        (ROOT/'.build/comet-http-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
    finally:
        http.shutdown()
        thread.join(timeout=5)
        http.server_close()


if __name__=='__main__':
    main()
