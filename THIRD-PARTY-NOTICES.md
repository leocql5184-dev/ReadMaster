# Third-party components

Distribution scope: the GitHub source snapshot contains ReadMaster source and the
license notices in `licenses/`. It does not include third-party executable
runtimes, model weights, fonts, or upstream source archives. References below to
`tools/`, `source/`, and bundled assets describe the full portable distribution
when those components are actually included; they do not imply that these files
exist in the source-only snapshot. ReadMaster source is at repository root in
the GitHub snapshot and under `source/` in the portable distribution.

- ReadMaster application: AGPL-3.0, source included in `source/`.
- PyMuPDF 1.28.2 and MuPDF: AGPL-3.0 (upstream also offers commercial licensing). https://github.com/pymupdf/PyMuPDF ; https://mupdf.com/ . License and downloaded corresponding upstream source artifacts accompany this package.
- Python 3.12 runtime: Python Software Foundation License. https://www.python.org/ . See licenses/Python.txt.
- PyInstaller 6.22.3: GPL-2.0 with bootloader exception. https://pyinstaller.org/ . See licenses/PyInstaller.txt.
- Codex CLI 0.162.1 Windows x64: Apache-2.0. Official npm platform distribution copied unmodified as a separate executable and resources. https://github.com/openai/codex . See licenses/Codex.txt and tools/codex/codex-resources/voice/licenses/ for bundled voice components, which ReadMaster does not invoke.
- pywebview 6.2.1: BSD-3-Clause. https://github.com/r0x0r/pywebview . See licenses/pywebview.txt.
- Python.NET 3.2.1, clr-loader 0.3.1, Bottle 0.13.4, proxy-tools 0.1.0: MIT. CFFI 2.1.1 and pycparser 3.11: BSD. typing-extensions 4.16.0: PSF. Corresponding license notices are in licenses/.
- Microsoft WebView2 SDK loader and interop assemblies are bundled by pywebview; the Evergreen WebView2 browser runtime is provided by Windows, not bundled in this archive. https://developer.microsoft.com/microsoft-edge/webview2/ . See licenses/WebView2.txt.

The isolated PDF engine, when bundled under `tools/babeldoc/`, includes:

- BabelDOC 0.5.24: AGPL-3.0. https://github.com/funstory-ai/BabelDOC . The installed Python source and its license are included under `tools/babeldoc/Lib/site-packages/`, including `babeldoc-0.5.24.dist-info/licenses/LICENSE`.
- PDFMathTranslate-next (`pdf2zh-next`) 2.8.2: AGPL-3.0. https://github.com/PDFMathTranslate-next/PDFMathTranslate-next . Installed source and `pdf2zh_next-2.8.2.dist-info/licenses/LICENSE` are included in the isolated runtime.
- PyMuPDF 1.25.2 is isolated from the reader's PyMuPDF 1.28.2. Both use the upstream AGPL licensing described above.
- Python 3.12.10 official Windows embedded runtime is kept separate from the desktop application. Its distribution includes `LICENSE.txt`.
- The engine's other Python dependencies retain their installed distribution metadata and license files in `tools/babeldoc/Lib/site-packages/`; the actual versions are listed in `tools/babeldoc/dependencies.json` and `requirements-babeldoc-lock.txt`.
- Font copyright records and embedded license statements are preserved in `tools/babeldoc/licenses/Font-embedded-notices.json`; upstream font notices, OFL texts and the MaruBuri publisher's redistribution notice accompany them. The font files are copied unchanged.
- The DocLayout-YOLO ONNX weight publisher and original weight model card declare Apache-2.0: https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx and https://huggingface.co/juliozhao/DocLayout-YOLO-DocStructBench . The original project's code license is separately preserved; that project code is not bundled as a model inference dependency. RapidOCR/PaddleOCR source notices accompany the OCR detection weight.
- The tokenizer resources use the bundled tiktoken distribution's notices. All 37 downloaded font/model/tokenizer files are listed in `tools/babeldoc/assets-manifest.json` with source URLs, SHA-256 hashes and verified upstream SHA3-256 digests.
- Corresponding BabelDOC, PDFMathTranslate-next, PyMuPDF 1.25.2/1.28.2 and MuPDF 1.25.2/1.28.2 source artifacts are included under `source/upstream/`; source origins and digests accompany the new engine archives.

No login files, access tokens, API keys, user PDFs, task histories or runtime caches are included in the distributable archive.

## COMET evaluation runtime (0.5.0)

- The separate relocatable runtime in `tools/comet` includes Python 3.12.10, PyTorch 2.6.0+cpu and unbabel-comet 2.2.7. Python's distribution license is included at runtime root; package distribution metadata and license files are preserved in `Lib/site-packages`.
- unbabel-comet: Apache-2.0, https://github.com/Unbabel/COMET . PyTorch and NumPy retain their BSD license notices; Transformers, Hugging Face Hub, SentencePiece and PyTorch Lightning retain their package notices.
- COMETKiwi/XCOMET checkpoints are not redistributed in the archive. They are downloaded after the user's Hugging Face access request is accepted. Their model-specific licenses and conditions are distinct from the COMET source-code license: https://github.com/Unbabel/COMET/blob/master/LICENSE.models.md .

The configured `Unbabel/wmt22-cometkiwi-da` and `Unbabel/XCOMET-XL` model cards
both specify CC BY-NC-SA 4.0. Users must accept the respective access conditions;
the XCOMET model card requires authorization from Unbabel for commercial services.
COMET's authors request citation of their work and identification of the model
used. Model links and research references are listed in README.md.
