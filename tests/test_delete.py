import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from readmaster import server


class DeleteTests(unittest.TestCase):
    def test_delete_removes_only_library_copy(self):
        ident='a'*24
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';folder=data/'documents'/ident
            (folder/'cache').mkdir(parents=True);(folder/'cache/result.json').write_text('{}')
            (folder/'original.pdf').write_bytes(b'%PDF-test');(folder/'document.json').write_text('{}')
            original=root/'user-original.pdf';original.write_bytes(b'%PDF-test')
            other=data/'documents'/('b'*24);other.mkdir();(other/'document.json').write_text('{}')
            docs={ident:{'state':'complete'}}
            with patch.object(server,'DATA',data),patch.object(server,'DOCS',docs),patch.object(server,'ACTIVE_TASKS',set()),patch.object(server,'STOPS',{ident:threading.Event()}):
                server.delete_document(ident)
                self.assertFalse(folder.exists());self.assertNotIn(ident,docs);self.assertNotIn(ident,server.STOPS)
                self.assertEqual(original.read_bytes(),b'%PDF-test');self.assertTrue(other.exists())
                with self.assertRaises(ValueError):server.delete_document(ident)

    def test_active_and_finishing_worker_cannot_be_deleted(self):
        ident='a'*24
        for state,active in [('processing',set()),('paused',{ident}),('failed',{ident})]:
            with self.subTest(state=state),tempfile.TemporaryDirectory() as temp:
                data=Path(temp);folder=data/'documents'/ident;folder.mkdir(parents=True)
                docs={ident:{'state':state}}
                with patch.object(server,'DATA',data),patch.object(server,'DOCS',docs),patch.object(server,'ACTIVE_TASKS',active):
                    with self.assertRaises(ValueError):server.delete_document(ident)
                    self.assertTrue(folder.exists());self.assertIn(ident,docs)

    def test_invalid_directory_id_is_rejected(self):
        ident='../outside'
        with patch.object(server,'DOCS',{ident:{'state':'ready'}}),patch.object(server,'ACTIVE_TASKS',set()):
            with self.assertRaises(ValueError):server.delete_document(ident)

    def test_http_delete_requires_header_and_cleans_library(self):
        ident='a'*24
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);folder=data/'documents'/ident;folder.mkdir(parents=True)
            docs={ident:{'id':ident,'state':'complete','name':'test.pdf','pages':[],'blocks':[]}}
            with patch.object(server,'DATA',data),patch.object(server,'DOCS',docs),patch.object(server,'ACTIVE_TASKS',set()),patch.object(server,'STOPS',{}):
                http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
                base=f'http://127.0.0.1:{http.server_port}'
                try:
                    payload=json.dumps({'id':ident}).encode()
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(base+'/api/delete',payload))
                    self.assertEqual(error.exception.code,403);self.assertTrue(folder.exists())
                    req=urllib.request.Request(base+'/api/delete',payload,{'X-ReadMaster':'1'})
                    with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['ok'])
                    with urllib.request.urlopen(base+'/api/documents') as r:self.assertEqual(json.load(r),[])
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/api/document/'+ident)
                    self.assertEqual(error.exception.code,404)
                finally:http.shutdown();http.server_close();worker.join()
