import json
from pathlib import Path
import unittest
from readmaster.pipeline import split_periods


@unittest.skipUnless((Path(__file__).parent/'paper_sets'/'references.json').exists(), 'Prepare the reference-paper fixtures first')
class PaperFixtureTests(unittest.TestCase):
    def test_real_paper_source_coverage(self):
        root=Path(__file__).parent/'paper_sets'
        files=list(root.glob('*.json'))
        self.assertGreaterEqual(len(files),5,'Run scripts/prepare_test_sets.py first')
        seen=0
        for file in files:
            fixture=json.loads(file.read_text(encoding='utf-8'))
            for block in fixture.get('blocks',[]):
                if not block['sentences']:
                    continue
                self.assertEqual(''.join(s['source'] for s in block['sentences']),block['source'])
                self.assertEqual(len({s['id'] for s in block['sentences']}),len(block['sentences']))
                for sentence in block['sentences']:
                    self.assertEqual(block['source'][sentence['start']:sentence['end']],sentence['source'])
                seen+=1
        self.assertGreater(seen,20)

    def test_reference_region_preserved_and_appendix_available(self):
        root=Path(__file__).parent/'paper_sets'
        refs=json.loads((root/'references.json').read_text(encoding='utf-8'))
        self.assertFalse(any(b['sentences'] for b in refs['blocks']))
        appendix=json.loads((root/'appendix.json').read_text(encoding='utf-8'))
        self.assertTrue(any(b['sentences'] for b in appendix['blocks']))


if __name__=='__main__':unittest.main()
