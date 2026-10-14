import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from readmaster import progress,server

class OutlineTests(unittest.TestCase):
    def test_bilingual_outline_preserves_bookmarks_and_saved_translation(self):
        doc={'id':'test','outline':[{'level':1,'title':'1 Introduction','page':1},{'level':2,'title':'1.1 Setup','page':2,'translation':'1.1 实验设置'}],'terms':[],'events':[]}
        group=server.outline_blocks(doc);self.assertEqual(len(group),1)
        doc['task']=progress.begin(group,1,2,'test')
        class Agent:
            live_calls=0
            def call(self,role,payload,schema):
                self.live_calls+=1
                return {'translations':[{'id':payload['sentences'][0]['id'],'text':'1 引言'}]}
        with tempfile.TemporaryDirectory() as temp,patch.object(server,'DATA',Path(temp)):
            server.translate_outline(Agent(),doc,threading.Event(),True)
            saved=json.loads((Path(temp)/'documents/test/document.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['outline'][0],{'level':1,'title':'1 Introduction','page':1,'translation':'1 引言','translation_status':'translated'})
            self.assertEqual(saved['outline'][1]['translation'],'1.1 实验设置')
            self.assertEqual(saved['task']['done_units'],1)
            self.assertEqual(server.outline_blocks(saved),[])

    def test_outline_pause_does_not_call_model(self):
        doc={'outline':[{'title':'Introduction','page':1}]};stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):server.translate_outline(None,doc,stop)
