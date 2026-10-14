"""Candidate BabelDOC prompt adapter; retains reader-owned review and ID validation.

Native PDF parsing/rendering is a separate integration step. This adapter uses
the installed BabelDOC translation template, not a copied approximation.
"""
import json
from pathlib import Path
from string import Template
import subprocess
import threading

from .agents import InvalidApiOutput
from .babeldoc_contract import BabelBatch
from .babeldoc_runtime import command

_templates = {}
_lock = threading.Lock()


def load_template(root):
    root = Path(root)
    args = command(root, 'template')
    key = tuple(args)
    with _lock:
        if key in _templates:
            return _templates[key]
        result = subprocess.run(args, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('BabelDOC 模板加载失败，请先运行独立环境检查')
        value = json.loads(result.stdout.strip().splitlines()[-1])
        if value['version'] != '0.5.24':
            raise RuntimeError('BabelDOC 版本不符合已验证的接入版本')
        _templates[key] = Template(value['template'])
        return _templates[key]


class BabelDOCTranslationAgent:
    def __init__(self, inner, root):
        self.inner = inner
        self.template = load_template(root)
        self.backend = 'babeldoc-0.5.24-template-v3'

    def call(self, role, payload, schema):
        if role not in ('translate', 'revise'):
            return self.inner.call(role, payload, schema)
        batch = BabelBatch(payload['sentences'])
        glossary = '\n'.join(f"{t['source']} → {t['target']}" for t in payload.get('terms', [])
                             if isinstance(t, dict) and t.get('source') and t.get('target'))
        hints = '## Context\n' + str(payload.get('context', ''))
        if payload.get('candidate_sources'):
            hints += ('\nThis is an alternative wording request. For repeated source sentences, '
                      'produce distinct faithful wording according to reader_data.candidate_sources style. '
                      'Do not simply repeat the current translation or another candidate. '
                      'Keep facts, numbers, negation, proper names and frozen source ownership unchanged.')
        prompt = self.template.substitute(
            role_block='You are a professional Chinese translator. Follow all rules strictly.',
            glossary_usage_rules_block=('Use the glossary for consistent technical meanings. '
                'Do not insert glossary definitions or parenthetical explanations absent from the source sentence. '
                'Translate ordinary nouns and verbs into natural Chinese, including scenario objects; '
                'do not mistake them for code identifiers or untranslatable proper names. '
                'Preserve genuine proper names, acronyms, equations and code/file/task identifiers. '
                'Contextual fidelity takes precedence over literal glossary targets.') if glossary else '',
            contextual_hints_block=hints,
            json_input_str=batch.input_json(), glossary_tables_block=glossary, lang_out='Chinese')
        # Preserve original review feedback. Index mapping is explicit; never
        # substitute a guessed sentence order for the reader's frozen IDs.
        request = {'native_prompt': prompt, 'role': role,
                   'reader_id_map': {str(i): s['id'] for i, s in enumerate(batch.sentences)},
                   'reader_data': payload}
        items_schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {
            'type': 'object', 'properties': {'id': {'type': 'integer', 'enum': list(range(len(batch.inputs)))},
                                           'output': {'type': 'string'}},
            'required': ['id', 'output'], 'additionalProperties': False}}},
            'required': ['items'], 'additionalProperties': False}
        value = self.inner.call('babeldoc_translate', request, items_schema)
        try:
            return batch.decode(value.get('items') if isinstance(value, dict) else value)
        except ValueError as exc:
            raise InvalidApiOutput(str(exc)) from exc

