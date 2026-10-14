"""Read the pinned engine's real translation template in its isolated runtime."""
import json
import importlib.metadata
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from scripts.babeldoc_assets import configure
    configure()
    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import PROMPT_TEMPLATE
    print(json.dumps({'version': importlib.metadata.version('babeldoc'),
                      'template': PROMPT_TEMPLATE.template}, ensure_ascii=True))


if __name__ == '__main__':
    main()
