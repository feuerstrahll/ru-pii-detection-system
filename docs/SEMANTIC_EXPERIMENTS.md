# Semantic experiments

`SEM-NER-01` asks: *Which pretrained semantic NER model provides the best quality-resource trade-off for PERSON and ADDRESS detection in Russian unstructured text?*

It uses only `data/dev_set`. The benchmark derives a stable document-level 20% validation split from `document_id`, selects a threshold independently for every model by validation macro exact F1, then reports exact and overlap P/R/F1, latency, RAM, VRAM, snapshot size, raw-score predictions, versions, and hardware on the remaining documents. `pii-bench` is excluded.

Download local snapshots and lock their resolved revision and content hash:

```powershell
python scripts/fetch_semantic_models.py --model ru_pii_ner_gliner2_5 --model gliner_multi_v2_1
python -m evaluation.benchmark_semantic --dataset data/dev_set --config configs/ner_semantic_benchmark_v1.json --models-dir models/ner --out artifacts/sem_ner_01.json
```

The same commands run in Kaggle with `--device cuda`; Kaggle adds no detector or policy code.

The completed Kaggle run is saved in `artifacts/sem_ner_01_kaggle.json`. Its
412-document calibration split selected threshold 0.7 for
`ru_pii_ner_gliner2_5`; on the remaining 1,588 synthetic documents, PERSON
exact F1 was 0.878, while ADDRESS exact F1 was 0.163. Error references are
organized in `artifacts/sem_ner_01_error_review.csv`, with review categories
left blank until a reviewer assigns a cause. The experimental decision enables
only PERSON. ADDRESS remains unavailable for automatic semantic decisions.

For the independent `pii-bench` check, attach `entity.jsonl` and `domain.jsonl`
as a separate private Kaggle input under `data/pii_bench`, refresh the source
dataset to include the frozen config and runner, then fetch the pinned snapshot:

```bash
python scripts/fetch_semantic_models.py --model ru_pii_ner_gliner2_5
```

The snapshot hash covers model files and excludes Hugging Face's local
`.cache` download metadata. The frozen content hash is
`48238a57dd048f6e00b157684fc84c0dd35a8dec22b32a588084d260cd66a293`;
the earlier hash in the dev-set lock included local cache metadata.
On Windows the original `sorted(Path)` order differed from Kaggle Linux for
uppercase and lowercase filenames. The downloader and production preflight now
sort relative POSIX paths; the downloaded revision then reproduced the frozen
hash exactly. The model, tokenizer, threshold, and saved experiment reports were
not changed.

The first `pii-bench` run (`artifacts/pii_bench_semantic_person_v1.json`) used
only `ru_pii_person` at inference. The dev-set calibration had requested both
`ru_pii_person` and `ru_pii_location`, so the first run does not test the same
inference procedure. It remains an exploratory one-label result. Of its PERSON
false positives, 412/429 on `entity` and 222/230 on `domain` exactly match
gold spans of other PII types.

`configs/semantic_person_frozen_v2.json` restores the two-label inference schema
from the dev-set run while retaining its model, revision, threshold 0.7, and
PERSON-only scoring. The runner rejects a different inference schema, model
revision, or snapshot hash and never selects a threshold:

```bash
python -m evaluation.benchmark_semantic_locked --dataset data/pii_bench --config configs/semantic_person_frozen_v2.json --lock models.lock --models-dir models/ner --device cuda --out artifacts/pii_bench_semantic_person_v2.json
```

Its `entity` and `domain` PERSON results are separate. They do not evaluate
ADDRESS, structured identifiers, or the full hybrid system. Do not tune the
frozen policy on either `pii-bench` view.

The corrected run is saved in `artifacts/pii_bench_semantic_person_v2.json`.
At exact-span matching, `entity` has TP 66, FP 40, FN 4, precision 0.623,
recall 0.943, F1 0.750; `domain` has TP 156, FP 10, FN 2, precision 0.940,
recall 0.987, F1 0.963. The dataset hashes, model revision, snapshot hash,
and threshold match the first run. All corrected PERSON predictions are a
subset of the first run's predictions: 389 `entity` and 220 `domain` false
positives disappear, while TP and FN stay the same. This difference is
associated with restoring the two-label inference request; the held-out
benchmark was not used to select a new policy.

The remaining PERSON exact-span errors are observable as follows. On `entity`,
34/40 FP exactly match a different gold PII type (27 EMAIL, two PASSPORT, two
SOCIAL_SECURITY_ID, and one each PHONE, BANK_CARD, ORG_REGISTRATION_ID); three
FP overlap a gold PERSON with different boundaries, and three have neither
match. Three of four FN have a corresponding boundary-overlapping PERSON
prediction; one is absent. On `domain`, eight of ten FP exactly match EMAIL,
one has a PERSON boundary overlap, and one has neither match; one of two FN
has a boundary-overlapping PERSON prediction. These are observed span
relationships, not reviewed root-cause labels.

`HybridPipeline` in `app/core/pipeline.py` now composes the unchanged
deterministic detector with the frozen two-label semantic call. It maps only
`ru_pii_person` to PERSON, verifies the returned text and offsets, and retains
model revision and raw score as evidence. Semantic PERSON is routed to review:
the 0.7 detection threshold is not a calibrated auto-accept probability.
The caller must provide the pinned local GLiNER2 adapter; the pipeline does not
download model weights or send text to an external API.

At the time of the combined evaluation the local snapshot was unavailable, so
that saved report replays Kaggle PERSON predictions against the hashed
pii-bench JSONL files and runs the deterministic pipeline live:

```powershell
python -m evaluation.benchmark_hybrid_replay --out artifacts/pii_bench_hybrid_replay_v1.json
```

The replay report scores PERSON and the eight enabled deterministic types;
ADDRESS, CVC, and TOKEN are excluded. As in `evaluation.run`, both `accept`
and `review` count as detections, while `reject` does not. `entity` has 667 TP,
45 FP, 33 FN, micro exact F1 0.9448, macro exact F1 0.9426. `domain` has 616
TP, 41 FP, 3 FN, micro exact F1 0.9655, macro exact F1 0.9696. The report
includes per-type metrics and span-level FP/FN references. Its error-pattern
counts describe exact matches to another gold type and same-type overlaps;
`entity` has 39 exact other-type matches, six same-type overlaps, and 33
unclassified errors; `domain` has 36, two, and six respectively. Root causes
remain unclassified until manual review. This is a deterministic
plus recorded-PERSON replay, not a fresh model run or end-to-end document
evaluation.

A local temporary digital PDF was parsed through `OCRDocumentParser` and the
deterministic pipeline: page and source locator were retained and an EMAIL
span matched the extracted source text at its original offsets. Scanned OCR
and live hybrid inference were not run in that earlier environment because
Tesseract and GLiNER2 with the pinned snapshot were unavailable. In the
current implementation, new-text live inference and an OCR control image have
been exercised separately; these checks do not replace a labelled document
evaluation. `DOC-E2E-01` recall and offset
preservation rate still require a labelled document corpus with source-level
locations; pii-bench JSONL does not supply that evaluation.

`SEM-CONTEXT-01` asks: *Does semantic context evidence reduce false positives for structurally valid numeric PII candidates without materially reducing recall?* It compares deterministic only, keyword context, semantic context, and keyword plus semantic context on `evaluation/semantic_context_cases.json`. Its required `--threshold` is the selected result from `SEM-NER-01`; it is never tuned on the collision cases.

```powershell
python -m evaluation.benchmark_semantic_context --threshold <SEM-NER-01 threshold> --out artifacts/sem_context_01.json
```

Semantic context evidence is experimental: it preserves the original candidate, offsets, checksum and conflict evidence, and does not modify `deterministic_v3` or make a production policy decision.

`NEREL` is an external linguistic check after Tier 0 selection, reported separately. Generic `LOC` is not mapped to `ADDRESS`.

The later `DOC-E2E-01` must separately report clean-text detector recall, recall conditional on successful extraction, end-to-end document recall, and **offset preservation rate**: the proportion of extracted entities for which document, page, original text, and source location can be reconstructed correctly.

## NEREL-PERSON-01

Независимая оценка замороженного `semantic_person_frozen_v2` на официальном
`NEREL-v1.1/test` из [NEREL](https://github.com/nerel-ds/NEREL). Оценивается
только PERSON. Модель, порог 0.7, обе исходные inference-метки и веса не
меняются; разметка не создаётся вручную. Это отдельный тест PERSON, а не
пересчёт результата Redmadrobot.

Вход: опубликованный архив NEREL на коммите из
`configs/nerel_person_frozen_v1.json`, `data/dev_set`, исходный
`configs/semantic_person_frozen_v2.json` и его локальный model snapshot.
Скрипт проверяет хеши конфигурации, snapshot и полного тестового manifest,
число файлов официальных train/dev/test, точные дубликаты текстов между test
и development, BRAT offsets и соответствие исходному тексту. В тесте 93
документа и 961 PERSON-аннотация. Три пары PERSON пересекаются; все они
сохраняются. 17 разрывных аннотаций других классов учитываются в coverage,
но не оцениваются. Разрывный PERSON остановит запуск до инференса.

Длинные документы делятся на окна по 384 слова с перекрытием 64 слова;
предсказания переводятся в исходные символьные координаты и совпадающие
интервалы дедуплицируются. Протокол окон зафиксирован до прогона. Основная
метрика — exact span PERSON. Overlap F1 — только диагностика границ и не
заменяет exact F1. Поскольку класс один, micro/macro F1 равны PERSON F1.

### Kaggle с GPU T4

Включите Internet и GPU в Notebook. Создайте Kaggle Dataset из локального
`artifacts/nerel_person_01_kaggle_source.zip` и добавьте его как Input.
Архив содержит актуальные исходники, конфигурацию и `data/dev_set`.
Editable-install проекта не требуется; запускайте `python -m` из корня
распакованного кода.

```python
from pathlib import Path
from zipfile import ZipFile

archives = list(Path("/kaggle/input").rglob("nerel_person_01_kaggle_source.zip"))
assert len(archives) == 1, f"Ожидался один архив исходников, найдено: {archives}"
work = Path("/kaggle/working/pii")
work.mkdir(parents=True, exist_ok=True)
with ZipFile(archives[0]) as source:
    source.extractall(work)
assert (work / "evaluation/benchmark_nerel.py").is_file()
%cd /kaggle/working/pii
!python -m pip install -q "pydantic>=2.11,<3" "psutil>=5.9" "gliner2[local]==2.1.0" "transformers==5.0.0" huggingface_hub
```

Загрузите закреплённые архив и модель в следующей Python-ячейке:

```python
import json
import urllib.request
import zipfile
from pathlib import Path
from huggingface_hub import snapshot_download
from evaluation.datasets.nerel import manifest_sha256
from scripts.fetch_semantic_models import _snapshot_hash

run = json.loads(Path("configs/nerel_person_frozen_v1.json").read_text(encoding="utf-8"))
model = json.loads(Path("configs/semantic_person_frozen_v2.json").read_text(encoding="utf-8"))
archive = Path("/kaggle/working/nerel-pinned.zip")
url = f"https://codeload.github.com/nerel-ds/NEREL/zip/{run['dataset_commit']}"
urllib.request.urlretrieve(url, archive)
with zipfile.ZipFile(archive) as source:
    source.extractall("/kaggle/working")
dataset_root = Path("/kaggle/working") / f"NEREL-{run['dataset_commit']}" / "NEREL-v1.1"
assert manifest_sha256(dataset_root) == run["manifest_sha256"]
model_path = Path("models/ner") / model["candidate_id"]
snapshot_download(repo_id=model["model_ref"], revision=model["revision"], local_dir=model_path)
assert _snapshot_hash(model_path) == model["snapshot_sha256"]
print("Закреплённые NEREL test и модель проверены")
```

```bash
python -m evaluation.benchmark_nerel \
  --dataset-root /kaggle/working/NEREL-8f07d3e410f322d5dbf6b68b13fbf4a4aa077fe9/NEREL-v1.1 \
  --models-dir models/ner \
  --device cuda \
  --out-dir /kaggle/working/nerel-person-01
```

Выход: `results.json`, `metrics.csv`, `coverage.json`, `errors.json`.
JSON содержит хеши, параметры, exact и overlap метрики, FP/FN с координатами,
признак ошибки границ и предсказания без сырого PII. Результат записывается
после завершения полного прогона. Если Kaggle не видит CUDA или модель не
совпадает с закреплённым хешем, запуск завершится ошибкой.

Быстрая локальная проверка без модели:

```bash
python -B -m pytest tests/unit/test_nerel.py tests/unit/test_benchmark_nerel.py -q
```

## REDMADROBOT-FROZEN-01

Оценка **замороженного** `HybridPipeline` на официальном `test.csv` набора
[`redmadrobot-rnd/pii_benchmark`](https://huggingface.co/datasets/redmadrobot-rnd/pii_benchmark).
Обучения, ручной разметки и подбора порога нет. Протокол сопоставления типов —
`dataset_strategy.md`. Основная метрика: exact `(document_id, start, end, type)`
для `PERSON`, `EMAIL`, `PHONE`, `PASSPORT`, `TAX_ID`,
`SOCIAL_SECURITY_ID`, `BANK_CARD`. Overlap для PERSON — только диагностика границ.

### Входы и фиксация

- `test.csv` с полями `text`, `tokens`, `ner_tags` из ревизии
  `f77ea831274daf980cc45c61a93c226be9d978d6`, SHA-256
  `6bf544a380a3ee5bec94b946124bea3afaecce49e734679ad0f0c0e7c12977bb`.
- Исходные `configs/deterministic_v3.json` и
  `configs/semantic_person_frozen_v2.json`; их SHA-256 проверяются по
  `configs/redmadrobot_frozen_v1.json` до инференса.
- Локальный snapshot `models/ner/ru_pii_ner_gliner2_5` с ревизией и хешем,
  указанными в семантической конфигурации. Скрипт проверяет содержимое
  snapshot до загрузки. `models.lock` в этом checkout ещё имеет статус
  `pending_snapshot`; эксперимент опирается на уже зафиксированный хеш в
  `semantic_person_frozen_v2.json` и не изменяет `models.lock`.
- `data/dev_set/*.json` для поиска точных дубликатов текста. Совпавшие строки
  заранее исключаются из test, число и индексы сохраняются в coverage.

### Kaggle

Добавьте исходный код проекта (включая новые файлы эксперимента), официальный
`test.csv` указанной ревизии, `data/dev_set` и frozen snapshot как Kaggle
Inputs. Скопируйте код в записываемую область и запускайте из его корня:

```bash
cp -a /kaggle/input/pii-source/. /kaggle/working/pii/
cd /kaggle/working/pii
python -m pip install 'pydantic>=2.11,<3' 'psutil>=5.9' 'gliner2[local]==2.1.0' 'transformers==5.0.0' huggingface_hub
python -m evaluation.benchmark_redmadrobot \
  --dataset /kaggle/input/redmadrobot-pii-benchmark/test.csv \
  --models-dir /kaggle/input/frozen-pii-model/models/ner \
  --device cpu \
  --out-dir /kaggle/working/redmadrobot-frozen-01
```

Имена Kaggle Inputs в командах замените на фактические. Для GPU укажите
`--device cuda` и Kaggle GPU runtime. Установка должна содержать совместимый
`torch`/`gliner2` и иметь доступ к пакетам или заранее добавленным wheels;
полный прогон и модельный runtime локально не проверялись. CSV и snapshot
нужно предоставить до запуска: их можно приложить как Kaggle Inputs или
скачать в Notebook по закреплённым ревизиям. Скрипт сам их не скачивает.
Пакет проекта устанавливать в editable-режиме не требуется: команды
`python -m` выполняются из корня скопированного кода.

Выходные файлы: `results.json` (метрики, версия и хеши входов, FP/FN с
координатами), `metrics.csv` (семь классов и MICRO), `coverage.json`
(все исходные BIO-типы и исключения), `errors.json` (сводка и FP/FN без
сырого PII). `entity_boundary` ставится только при перекрытии PERSON FP/FN;
остальные ошибки остаются `unclassified`, без выдуманной причины. Файлы
пишутся после завершения всего прогона; частичный результат не считается
метрикой. Поддерживаемые предсказания на участках исключённых gold-типов
остаются FP согласно протоколу.

Быстрая локальная проверка подготовки без модели:

```bash
python -m pytest tests/unit/test_redmadrobot.py -q
```
