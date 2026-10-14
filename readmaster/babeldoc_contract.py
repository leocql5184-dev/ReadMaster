"""Lossless boundary between fixed reader IDs and BabelDOC's indexed batches.

This contract does not segment source text or guess correspondence from output
order. It must be used on the frozen reader units before invoking translation.
"""
import json
import re
from collections import Counter

from .pipeline import validate_translations


_MARKERS = re.compile(r'</?(?:style|b|code)\b[^>]*>|\{v\d+\}|\{\{\d+\}\}')


class BabelBatch:
    def __init__(self, sentences):
        self.sentences = [dict(s) for s in sentences]
        ids = [s.get('id') for s in self.sentences]
        if (not ids or any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('BabelDOC 请求必须包含唯一、非空的原句编号')
        if any(not isinstance(s.get('source'), str) or not s['source'].strip()
               for s in self.sentences):
            raise ValueError('BabelDOC 请求原文不能为空')
        self.inputs = [{'id': i, 'input': s['source'], 'layout_label': 'text'}
                       for i, s in enumerate(self.sentences)]

    def input_json(self):
        return json.dumps(self.inputs, ensure_ascii=False)

    def decode(self, raw):
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            raise ValueError('BabelDOC 译文不是有效的 JSON') from None
        if not isinstance(items, list):
            raise ValueError('BabelDOC 必须返回逐编号的译文列表')
        found = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('BabelDOC 译文条目格式错误')
            index, text = item.get('id'), item.get('output')
            # bool is an int subclass; never accept it as a paragraph index.
            if (type(index) is not int or not 0 <= index < len(self.sentences)
                    or index in found):
                raise ValueError('BabelDOC 译文编号重复或越界')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('BabelDOC 返回了空译文')
            if Counter(_MARKERS.findall(self.sentences[index]['source'])) != Counter(_MARKERS.findall(text)):
                raise ValueError('BabelDOC 译文改变了公式或样式占位符')
            found[index] = text
        if len(found) != len(self.sentences):
            raise ValueError('BabelDOC 译文未覆盖全部原句编号')
        value = {'translations': [{'id': s['id'], 'text': found[i]}
                                  for i, s in enumerate(self.sentences)]}
        # Includes decimal fragments, limits and exact reader ID coverage.
        validate_translations(value, self.sentences)
        return value

