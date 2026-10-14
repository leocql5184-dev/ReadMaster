import copy
import unittest
from unittest.mock import patch

from readmaster import server


class QualityCompatibilityTests(unittest.TestCase):
    def document(self):
        return {'id':'old','state':'complete','blocks':[{'id':'b','page':1,'source':'One.',
            'sentences':[{'id':'s','source':'One.'}], 'translations':{'s':'第一句。'}, 'status':'accepted'}]}

    def test_legacy_acceptance_and_exports_are_not_silently_invalidated(self):
        doc = self.document()
        original = copy.deepcopy(doc)
        with patch('readmaster.babeldoc_runtime.available', return_value=True):
            public = server.public_doc(doc)
        self.assertEqual(public['state'], 'complete')
        self.assertEqual(public['stats']['accepted'], 1)
        self.assertEqual(public['quality']['status'], 'not_evaluated')
        self.assertEqual(doc, original)

    def test_new_unpassed_quality_is_not_hidden_as_legacy(self):
        doc=self.document()
        doc['state']='needs_review'
        doc['blocks'][0]['status']='needs_review'
        doc['quality']={'version':server.QUALITY_VERSION,'status':'needs_review','scores':{'s':.79}}
        with patch('readmaster.babeldoc_runtime.available', return_value=True):
            public=server.public_doc(doc)
        self.assertEqual(public['state'],'needs_review')
        self.assertEqual(public['stats']['accepted'],0)
        self.assertEqual(public['quality']['scores']['s'],.79)
