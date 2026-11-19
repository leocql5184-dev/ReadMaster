"""Run only with the Python shipped inside a newly installed ReadMaster guest."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    home, work = args.home.resolve(), args.work.resolve()
    assert str(home).lower().endswith('programs\\readmaster')
    assert work == Path('C:/ReadMasterVMTest')
    report = {'tests': {}, 'synthetic_inputs_only': True}
    output = work / 'guest-checks.json'

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def run(values, name, timeout=1200):
        with (work / (name + '.stdout.log')).open('w', encoding='utf-8') as stdout, \
                (work / (name + '.stderr.log')).open('w', encoding='utf-8') as stderr:
            result = subprocess.run([str(value) for value in values], stdout=stdout,
                                    stderr=stderr, timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, (name, result.returncode)

    for key in list(os.environ):
        if key.startswith('READMASTER_') or key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'PYTHONPATH', 'PYTHONHOME'):
            os.environ.pop(key, None)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_TOKEN='',
                      HF_HOME=str(work / 'empty-hf'), HF_HUB_CACHE=str(work / 'empty-hf/hub'),
                      TORCH_HOME=str(work / 'empty-torch'), READMASTER_COMET_MODE='quantized')
    try:
        run([home / 'ReadMaster.exe', '--check-models', work / 'routing.json'], 'routing')
        routing = json.loads((work / 'routing.json').read_text(encoding='utf-8'))
        assert routing['passed'] and routing['frozen'] and routing['mode'] == 'quantized'
        assert all(Path(path).resolve().is_relative_to(home) for path in routing['models'].values())
        report['tests']['frozen_quantized_routing'] = True
        save()

        samples = [{'src': 'The model does not require labeled data.', 'mt': '该模型不需要标注数据。'},
                   {'src': 'The experiment used 24 participants.', 'mt': '实验有 24 名参与者。'}]
        report['scores'] = {}
        # Separate runs also check that both bundles can reload without any pre-existing cache.
        for kind in ('kiwi', 'xcomet'):
            inp, out = work / f'{kind}-input.json', work / f'{kind}-output.json'
            inp.write_text(json.dumps({'model': routing['models'][kind], 'device': 'cpu',
                                      'cache_root': str(work / 'empty-hf'), 'data': samples}), encoding='utf-8')
            run([home / 'tools/comet/python.exe', home / 'tools/comet/app/comet_worker.py', inp, out],
                kind, timeout=1800)
            result = json.loads(out.read_text(encoding='utf-8'))
            assert 'error' not in result, result
            assert len(result['results']) == len(samples)
            assert all(math.isfinite(row['score']) for row in result['results'])
            assert result['worker']['model_loads'] == 1
            report['scores'][kind] = result['results']
            report['tests'][kind + '_offline_inference'] = True
            save()

        run([home / 'tools/babeldoc/python.exe', home / 'tools/babeldoc/app/scripts/babeldoc_template.py'], 'babeldoc')
        run([home / 'tools/codex/bin/codex.exe', '--version'], 'codex-version')
        report['tests']['pdf_runtime_and_codex_cli'] = True
        # This is a synthetic local document, never sent to a translation service.
        fixture = work / 'synthetic-installation-test.pdf'
        maker = work / 'make_pdf.py'
        maker.write_text("import pymupdf\nfrom pathlib import Path\ndoc=pymupdf.open()\n"
                         "page=doc.new_page()\n"
                         "paragraph='This synthetic document verifies that ReadMaster can import a PDF on a clean Windows installation. '"
                         "+ 'The reader should extract this paragraph and keep the original file after an update.'\n"
                         "assert page.insert_textbox((72,72,520,180), paragraph, fontsize=11) >= 0\n"
                         "doc.save(str(Path(__file__).parent/'synthetic-installation-test.pdf'))\n", encoding='utf-8')
        run([home / 'tools/babeldoc/python.exe', maker], 'pdf-create')
        app = subprocess.Popen([str(home / 'ReadMaster.exe')], cwd=home,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            url = None
            for _ in range(240):
                assert app.poll() is None, 'Desktop exited before health check'
                address = home / 'data/address.txt'
                if address.is_file():
                    candidate = address.read_text(encoding='utf-8').strip()
                    try:
                        with urllib.request.urlopen(candidate + '/api/health', timeout=2) as response:
                            if json.load(response)['ok']:
                                url = candidate
                                break
                    except (OSError, ValueError):
                        pass
                time.sleep(.5)
            assert url, 'Desktop did not become healthy'
            with urllib.request.urlopen(url, timeout=10) as response:
                assert 'ReadMaster' in response.read().decode('utf-8')
            request = urllib.request.Request(url + '/api/import?name=clean-vm-test.pdf', fixture.read_bytes(),
                                            {'X-ReadMaster': '1', 'Content-Type': 'application/pdf'}, method='POST')
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    imported = json.load(response)
            except urllib.error.HTTPError as error:
                raise RuntimeError(f'PDF import returned {error.code}: {error.read().decode("utf-8", errors="replace")}') from error
            report['synthetic_document_id'] = imported['id']
            report['tests']['local_pdf_import'] = True
            report['tests']['desktop_started'] = True
            save()
            # A marker lets the host observe the actual desktop before closing it.
            (work / 'desktop-visible.txt').write_text(url, encoding='utf-8')
            time.sleep(30)
            request = urllib.request.Request(url + '/api/shutdown', b'{}', {'X-ReadMaster': '1'}, method='POST')
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
            assert app.wait(timeout=30) == 0
            report['tests']['desktop_clean_exit'] = True
        finally:
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=15)
        report['passed'] = True
    except Exception as error:
        report['passed'] = False
        report['error'] = repr(error)
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
