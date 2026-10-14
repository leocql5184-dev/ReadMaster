"""Real, bounded candidate-translation checks. Never prints API credentials."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from readmaster.agents import ApiAgent, CodexAgent
from readmaster.babeldoc_agent import BabelDOCTranslationAgent
from readmaster.models import ModelStore
from readmaster.pipeline import run_batch, split_periods
from readmaster.server import CachedAgent, codex_binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', required=True, choices=['deepseek', 'codex'])
    parser.add_argument('--revision-check', action='store_true',
                        help='Inject one faulty initial translation; exercise live evaluation and targeted repair.')
    parser.add_argument('--profiles-dir', type=Path,
                        default=ROOT / 'output/ReadMaster-Client-Windows-x64/data')
    args = parser.parse_args()
    report_dir = ROOT / '.build/babel-validation' / args.provider
    report_dir.mkdir(parents=True, exist_ok=True)
    if args.provider == 'codex':
        config = {'provider': 'codex', 'model': '', 'timeout': 240}
        inner = CodexAgent(report_dir / 'runs', config, codex_binary())
    else:
        # Do not create or modify a user's store when it does not exist.
        if not (args.profiles_dir / 'models.json').exists():
            raise SystemExit('No saved model connections at the selected directory.')
        store = ModelStore(args.profiles_dir)
        profile = next((p for p in store.public()['profiles']
                        if p['provider'] == 'api'
                        and p.get('endpoint', '').rstrip('/') == 'https://api.deepseek.com/v1'
                        and p['has_key']), None)
        if profile is None:
            raise SystemExit('Configure a DeepSeek connection in Models first.')
        config, key = store.snapshot(profile['id'])
        config = {**config, 'timeout': 180, 'retry_attempts': 2}
        inner = ApiAgent(config, key)
    candidate = BabelDOCTranslationAgent(inner, ROOT)
    config = {**config, 'backend': candidate.backend}
    agent = CachedAgent(candidate, report_dir / 'cache', config)
    sentences = split_periods('The method may help. Keep the value 1.5 unchanged.', 'test')
    runner = agent
    if args.revision_check:
        sentences = split_periods('The treatment did not improve survival. The sample contains twenty patients.', 'repair')
        class FaultInjectedInitial:
            def call(self, role, payload, schema):
                if role == 'translate':
                    return {'translations': [
                        {'id': sentences[0]['id'], 'text': '该治疗提高了生存率。'},
                        {'id': sentences[1]['id'], 'text': '样本包含二十名患者。'}]}
                return agent.call(role, payload, schema)
        runner = FaultInjectedInitial()
    started = time.monotonic()
    report = {'provider': args.provider, 'stage': 'babeldoc_native_template',
              'pdf_layout_tested': False, 'sentences': sentences}
    report['fault_injected_initial_translation'] = args.revision_check
    try:
        result = run_batch(runner, sentences,
                           'Synthetic integration check. Preserve uncertainty and the exact numeric value.',
                           [], 1, emit=lambda role, _: print(role, flush=True))
        report.update(result)
        report['passed'] = result['status'] == 'accepted'
        if args.revision_check:
            revisions = [h for h in result['history'] if h['role'] == 'revise']
            report['targeted_revision_verified'] = (
                len(revisions) >= 1
                and all({t['id'] for t in h['result']['translations']} == {sentences[0]['id']}
                        for h in revisions)
                and result['translations'][sentences[1]['id']] == '样本包含二十名患者。')
            report['passed'] &= report['targeted_revision_verified']
    except Exception as exc:
        report.update(passed=False, error=str(exc)[:240])
    report.update(elapsed_seconds=round(time.monotonic()-started, 2), live_calls=agent.live_calls)
    (report_dir / ('revision-report.json' if args.revision_check else 'report.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('provider', 'stage', 'passed', 'elapsed_seconds', 'live_calls')}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
