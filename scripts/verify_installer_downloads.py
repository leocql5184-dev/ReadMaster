"""Exercise the real C# transfer code against a local, fault-injecting HTTP server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
DATA=(bytes(range(256))*8192)
SHA=hashlib.sha256(DATA).hexdigest()
RANGES=[]


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
        RANGES.append((self.path,offset))
        if self.path=='/ignore-range': offset=0
        body=(b'X'*len(DATA)) if self.path=='/corrupt' else DATA
        if self.path=='/oversize': body+=DATA
        self.send_response(206 if offset else 200)
        if offset:self.send_header('Content-Range',f'bytes {offset}-{len(body)-1}/{len(body)}')
        self.send_header('Content-Length',str(len(body)-offset))
        self.end_headers()
        try:
            if self.path=='/slow':
                for i in range(offset,len(body),4096):
                    self.wfile.write(body[i:i+4096]);self.wfile.flush();time.sleep(.03)
            else: self.wfile.write(body[offset:])
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass


def main():
    out=ROOT/'.build/installer-download-tests';out.mkdir(exist_ok=True)
    compiler=Path(os.environ.get('SystemRoot','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    probe=out/'BootstrapperProbe.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/main:BootstrapperProbe','/out:'+str(probe),
                    '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
                    str(ROOT/'installer/Bootstrapper.cs'),str(ROOT/'tests/BootstrapperProbe.cs')],check=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    results=[]
    try:
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            for name,seed,expected in [('normal',0,0),('resume',43123,0),('ignore-range',76123,0),('corrupt',0,3),('oversize',0,3),('slow',0,2)]:
                path=Path(tmp)/(name+'.bin')
                if seed:path.with_suffix('.bin.partial').write_bytes(DATA[:seed])
                args=[str(probe),f'http://127.0.0.1:{server.server_port}/{name}',str(path),path.name,str(len(DATA)),SHA]
                if name=='slow':args.append('200')
                run=subprocess.run(args,capture_output=True,text=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
                assert run.returncode==expected,(name,run.returncode,run.stdout,run.stderr)
                if expected==0:assert path.read_bytes()==DATA
                else:assert not path.exists()
                if seed:assert (('/'+name,seed)) in RANGES
                results.append({'case':name,'passed':True,'exit_code':run.returncode})
            for name, setup_exit in [('cleanup-after-success', 0), ('cleanup-after-setup-cancel', 2),
                                     ('cleanup-after-setup-failure', 4)]:
                args = [str(probe), 'cache', str(Path(tmp)/name), str(setup_exit)]
                run = subprocess.run(args, capture_output=True, text=True, timeout=15,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                assert run.returncode == 0, (name, run.returncode, run.stdout, run.stderr)
                results.append({'case': name, 'passed': True, 'exit_code': run.returncode})
    finally:server.shutdown();server.server_close();thread.join()
    report={'passed':True,'cases':results,'scope':'Production transfer over loopback HTTP and production process wait and cache cleanup with a simulated setup executable.'}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
