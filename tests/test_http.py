"""Integration tests against the running local server, no model calls."""
import hashlib
import json
import os
import unittest
import urllib.request
import urllib.error

BASE=os.environ.get('READMASTER_TEST_URL','http://127.0.0.1:8766')
ID='f285b0971ae4a790e402fb93'


@unittest.skipUnless(os.environ.get('READMASTER_TEST_URL'), 'Set READMASTER_TEST_URL for running-server integration tests')
class HttpTests(unittest.TestCase):
    def fetch(self,path,data=None,headers=None):
        request=urllib.request.Request(BASE+path,data,headers or {})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:return response.status,response.read()
        except urllib.error.HTTPError as response:return response.code,response.read()

    def test_health(self):
        status,body=self.fetch('/api/health')
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['ok'])

    def test_mutation_requires_non_simple_header(self):
        status,_=self.fetch('/api/pause',json.dumps({'id':ID}).encode(),{'Content-Type':'application/json'})
        self.assertEqual(status,403)

    def test_foreign_origin_rejected(self):
        status,_=self.fetch('/api/pause',b'{}',{'X-ReadMaster':'1','Origin':'https://evil.example'})
        self.assertEqual(status,403)

    def test_dns_rebinding_host_rejected(self):
        status,_=self.fetch('/api/health',headers={'Host':'evil.example'})
        self.assertEqual(status,403)

    def test_fake_pdf_rejected(self):
        status,_=self.fetch('/api/import?name=fake.pdf',b'not a pdf',{'X-ReadMaster':'1'})
        self.assertEqual(status,400)

    def test_original_pdf_retained(self):
        status,body=self.fetch('/api/document/'+ID+'/pdf')
        self.assertEqual(status,200)
        self.assertEqual(hashlib.sha256(body).hexdigest()[:24],ID)

    def test_invalid_page_rejected(self):
        status,_=self.fetch('/api/document/'+ID+'/image?page=0')
        self.assertEqual(status,400)

    def test_document_source_mapping(self):
        status,body=self.fetch('/api/document/'+ID)
        doc=json.loads(body)
        self.assertEqual(status,200)
        self.assertEqual(len(doc['pages']),33)
        for block in doc['blocks']:
            if block['sentences']:
                self.assertEqual(''.join(s['source'] for s in block['sentences']),block['source'])
                self.assertLessEqual(set(block['translations']),{s['id'] for s in block['sentences']})


if __name__=='__main__':unittest.main()
