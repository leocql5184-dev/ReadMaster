import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from readmaster import server


class ExportRestartTests(unittest.TestCase):
    def test_loaded_document_restores_stop_event_and_interrupted_export(self):
        for state in ('ready','processing'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                folder=root/'documents'/('a'*24)
                folder.mkdir(parents=True)
                doc={'id':'a'*24,'layout_version':server.LAYOUT_VERSION,'state':'complete',
                     'blocks':[],'pages':[],'pdf_export':{'state':state,'translation_version':'v1'}}
                file=folder/'document.json'
                file.write_text(json.dumps(doc),encoding='utf-8')
                with patch.object(server,'DATA',root),patch.object(server,'DOCS',{}),patch.object(server,'STOPS',{}),patch.object(server,'MODELS',None),patch.object(server.logging,'basicConfig'):
                    http=server.create_server(0)
                    try:
                        self.assertIn(doc['id'],server.STOPS)
                        self.assertFalse(server.STOPS[doc['id']].is_set())
                        expected='paused' if state=='processing' else 'ready'
                        loaded=server.DOCS[doc['id']]
                        self.assertEqual(loaded['pdf_export']['state'],expected)
                        self.assertEqual(loaded['pdf_export']['translation_version'],'v1')
                        self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['pdf_export']['state'],expected)
                    finally:
                        http.server_close()


if __name__=='__main__':
    unittest.main()
