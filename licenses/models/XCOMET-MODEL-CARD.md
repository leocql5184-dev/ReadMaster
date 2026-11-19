---
pipeline_tag: translation
language:
- multilingual
- af
- am
- ar
- as
- az
- be
- bg
- bn
- br
- bs
- ca
- cs
- cy
- da
- de
- el
- en
- eo
- es
- et
- eu
- fa
- fi
- fr
- fy
- ga
- gd
- gl
- gu
- ha
- he
- hi
- hr
- hu
- hy
- id
- is
- it
- ja
- jv
- ka
- kk
- km
- kn
- ko
- ku
- ky
- la
- lo
- lt
- lv
- mg
- mk
- ml
- mn
- mr
- ms
- my
- ne
- nl
- 'no'
- om
- or
- pa
- pl
- ps
- pt
- ro
- ru
- sa
- sd
- si
- sk
- sl
- so
- sq
- sr
- su
- sv
- sw
- ta
- te
- th
- tl
- tr
- ug
- uk
- ur
- uz
- vi
- xh
- yi
- zh
license: cc-by-nc-sa-4.0
library_name: transformers
base_model:
- facebook/xlm-roberta-xl
---

xCOMET stands for eXplainable COMET. This is an evaluation model that is trained to identify errors in sentences along with a final quality score and thus leading to an explainable neural metric. This is the XL version with ~3.5B parameters. 

# Paper

- [xCOMET: Transparent Machine Translation Evaluation through Fine-grained Error Detection](https://arxiv.org/pdf/2310.10482.pdf)

# Usage (unbabel-comet)

This model requires unbabel-comet (>=2.2.0) to be installed:

```bash
pip install --upgrade pip  # ensures that pip is current 
pip install "unbabel-comet>=2.2.0"
```

Then you can use it through comet CLI:

```bash
comet-score -s {source-inputs}.txt -t {translation-outputs}.txt -r {references}.txt --model Unbabel/XCOMET-XL
```

and if used with the `--to_json` flag you can also export the error spans detected by the model:

```bash
comet-score -s {source-inputs}.txt -t {translation-outputs}.txt -r {references}.txt --model Unbabel/XCOMET-XL --to_json {output}.json
```

Or using Python:

```python
from comet import download_model, load_from_checkpoint

model_path = download_model("Unbabel/XCOMET-XL")
model = load_from_checkpoint(model_path)
data = [
    {
        "src": "Boris Johnson teeters on edge of favour with Tory MPs", 
        "mt": "Boris Johnson ist bei Tory-Abgeordneten völlig in der Gunst", 
        "ref": "Boris Johnsons Beliebtheit bei Tory-MPs steht auf der Kippe"
    }
]
model_output = model.predict(data, batch_size=8, gpus=1)
# Segment-level scores
print (model_output.scores)

# System-level score
print (model_output.system_score)

# Score explanation (error spans)
print (model_output.metadata.error_spans)
```

# License

cc-by-nc-sa-4.0

# Usage Permissions:

**Evaluation:** You are encouraged to use this model for non-commercial evaluation purposes. Feel free to test and assess its performance in machine translation and various generative tasks.

# Limitations:

**Commercial Services:** If you intend to utilize this model to build a commercial service, such as for profit, you are required to contact Unbabel to obtain proper authorization. This requirement is in place to ensure that any commercial use of the model for evaluation services is done in collaboration with Unbabel. This helps maintain the quality and consistency of the model's use in commercial contexts.

# Contact Information:

For inquiries regarding commercial use authorization or any other questions, please contact us at [ai-research@unbabel.com](ai-research@unbabel.com).

We believe in the power of open-source and collaborative efforts, and we're excited to contribute to the community's advancements in the field of natural language processing. Please respect the terms of the CC-BY-NC-SA-4.0 license when using XCOMET-XL.

# Languages Covered:

This model builds on top of XLM-R XL which cover the following languages:

Afrikaans, Albanian, Amharic, Arabic, Armenian, Assamese, Azerbaijani, Basque, Belarusian, Bengali, Bengali Romanized, Bosnian, Breton, Bulgarian, Burmese, Burmese, Catalan, Chinese (Simplified), Chinese (Traditional), Croatian, Czech, Danish, Dutch, English, Esperanto, Estonian, Filipino, Finnish, French, Galician, Georgian, German, Greek, Gujarati, Hausa, Hebrew, Hindi, Hindi Romanized, Hungarian, Icelandic, Indonesian, Irish, Italian, Japanese, Javanese, Kannada, Kazakh, Khmer, Korean, Kurdish (Kurmanji), Kyrgyz, Lao, Latin, Latvian, Lithuanian, Macedonian, Malagasy, Malay, Malayalam, Marathi, Mongolian, Nepali, Norwegian, Oriya, Oromo, Pashto, Persian, Polish, Portuguese, Punjabi, Romanian, Russian, Sanskri, Scottish, Gaelic, Serbian, Sindhi, Sinhala, Slovak, Slovenian, Somali, Spanish, Sundanese, Swahili, Swedish, Tamil, Tamil Romanized, Telugu, Telugu Romanized, Thai, Turkish, Ukrainian, Urdu, Urdu Romanized, Uyghur, Uzbek, Vietnamese, Welsh, Western, Frisian, Xhosa, Yiddish.

Thus, results for language pairs containing uncovered languages are unreliable!