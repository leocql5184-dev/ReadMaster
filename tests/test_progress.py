import unittest
from readmaster.progress import begin, activate, advance, preview, snapshot


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.blocks = [{"page": 2, "source": "x"*100, "sentences": [{"id": "a"}, {"id": "b"}]},
                       {"page": 3, "source": "y"*300, "sentences": [{"id": "c"}, {"id": "d"}]}]

    def test_selected_work_and_initial_estimate(self):
        job = begin(self.blocks, 2, 3, "model", now=100)
        self.assertEqual(job["total_units"], 4)
        self.assertIsNone(snapshot(job, "processing", 110)["eta_seconds"])
        self.assertEqual(snapshot(job, "processing", 110)["percent"], 0)

    def test_real_speed_weighted_estimate_and_active_elapsed(self):
        job = begin(self.blocks, 2, 3, "model", now=100)
        activate(job, self.blocks[:1], now=110)
        advance(job, self.blocks[:1], live_calls=3, now=130)
        activate(job, self.blocks[1:], now=130)
        p = snapshot(job, "processing", now=140)
        self.assertEqual(p["percent"], 50)
        self.assertEqual(p["eta_seconds"], 50)
        self.assertEqual(job["done_units"], 2)

    def test_cached_batch_does_not_predict_uncached_speed(self):
        job = begin(self.blocks, 2, 3, "model", now=100)
        activate(job, self.blocks[:1], now=100)
        advance(job, self.blocks[:1], live_calls=0, now=101)
        self.assertIsNone(snapshot(job, "processing", 102)["eta_seconds"])

    def test_pause_failure_and_overrun_never_show_false_zero(self):
        job = begin(self.blocks, 2, 3, "model", now=100)
        activate(job, self.blocks[:1], now=100)
        advance(job, self.blocks[:1], live_calls=3, now=120)
        activate(job, self.blocks[1:], now=120)
        self.assertTrue(snapshot(job, "processing", 200)["overrun"])
        for state in ("paused", "failed"):
            p = snapshot(job, state, 200)
            self.assertIsNone(p["eta_seconds"])
            self.assertEqual(p["percent"], 50)

    def test_finished_translation_waits_for_full_document_quality(self):
        job = begin(self.blocks, 2, 3, "model", now=100)
        activate(job, self.blocks, now=100)
        advance(job, self.blocks, live_calls=3, now=130)
        job["finished_at"] = 130
        p = snapshot(job, "partial", 140)
        self.assertEqual(p["percent"], 100)
        self.assertEqual(p["state"], "partial")
        self.assertEqual(snapshot(job, 'needs_review', 140)['state'], 'needs_review')

    def test_resume_estimates_only_remaining_work_and_excludes_pause(self):
        old = begin(self.blocks, 2, 3, 'model', now=100, speed_key='same-model')
        activate(old, self.blocks[:1], now=100)
        advance(old, self.blocks[:1], live_calls=3, now=120)
        resumed = begin(self.blocks[1:], 3, 3, 'model', now=10000, previous=old, speed_key='same-model')
        self.assertEqual(resumed['total_units'], 2)
        self.assertEqual(resumed['done_units'], 0)
        self.assertEqual(snapshot(resumed, 'processing', 10000)['eta_seconds'], 60)
        activate(resumed, self.blocks[1:], now=10000)
        self.assertEqual(snapshot(resumed, 'processing', 10010)['eta_seconds'], 50)

    def test_new_model_does_not_reuse_previous_speed(self):
        old = begin(self.blocks, 2, 3, 'old', now=100, speed_key='old')
        activate(old, self.blocks[:1], now=100)
        advance(old, self.blocks[:1], live_calls=3, now=120)
        resumed = begin(self.blocks[1:], 3, 3, 'new', now=10000, previous=old, speed_key='new')
        self.assertIsNone(snapshot(resumed, 'processing', 10000)['eta_seconds'])

    def test_saved_progress_survives_restart_and_new_selected_range(self):
        self.blocks[0]['translations']={'a':'甲','b':'乙'}
        for state in ('processing','paused','failed'):
            self.assertEqual(snapshot(None,state,blocks=self.blocks)['percent'],50)
        resumed=begin(self.blocks[1:],3,3,'model',now=10000)
        p=snapshot(resumed,'processing',10001,blocks=self.blocks)
        self.assertEqual((p['done_units'],p['total_units'],p['percent']),(2,4,50))

    def test_complete_draft_shows_complete_during_background_review(self):
        for b in self.blocks:
            b['translations']={s['id']:'译文' for s in b['sentences']}
        job=begin([],2,3,'model',now=100)
        job['phase']='evaluate'
        p=snapshot(job,'processing',110,blocks=self.blocks)
        self.assertEqual(p['percent'],100)
        self.assertTrue(p['draft_complete'])
        self.assertEqual(p['state'],'complete')
        self.assertIsNone(p['eta_seconds'])

    def test_first_committed_batch_estimates_before_wave_finishes(self):
        job=begin(self.blocks,2,3,'model',now=100)
        activate(job,self.blocks,now=100)
        self.blocks[0]['translations']={'a':'甲','b':'乙'}
        preview(job,self.blocks[:1],live_calls=1,now=120)
        p=snapshot(job,'processing',120,blocks=self.blocks)
        self.assertEqual(p['percent'],50)
        self.assertEqual(p['eta_seconds'],60)
        self.assertEqual(job['sample_weight'],0)

    def test_resume_resets_samples_and_replaces_prior_with_current_speed(self):
        old=begin(self.blocks,2,3,'model',now=100,speed_key='same')
        activate(old,self.blocks[:1],now=100)
        advance(old,self.blocks[:1],1,now=120)
        job=begin(self.blocks,2,3,'model',now=10000,previous=old,speed_key='same')
        self.assertEqual(job['sample_seconds'],0)
        self.assertEqual(snapshot(job,'processing',10000)['eta_seconds'],80)
        activate(job,self.blocks,now=10000)
        self.blocks[0]['translations']={'a':'甲','b':'乙'}
        preview(job,self.blocks[:1],1,now=10010)
        self.assertEqual(snapshot(job,'processing',10010,blocks=self.blocks)['eta_seconds'],30)

    def test_draft_speed_does_not_invent_remaining_quality_review_time(self):
        job=begin(self.blocks,2,3,'model',now=100)
        job['estimate_scope']='initial_translation'
        activate(job,self.blocks[:1],now=100)
        advance(job,self.blocks[:1],live_calls=1,now=120)
        job['phase']='translate'
        self.assertEqual(snapshot(job,'processing',120)['eta_seconds'],60)
        for phase in ('evaluate','revise','accept'):
            job['phase']=phase
            self.assertIsNone(snapshot(job,'processing',120)['eta_seconds'])
