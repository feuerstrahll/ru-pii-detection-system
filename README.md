# Russian PII Detection and Data Ecology

[![Tests](https://github.com/feuerstrahll/ru-pii-detection-system/actions/workflows/tests.yml/badge.svg)](https://github.com/feuerstrahll/ru-pii-detection-system/actions/workflows/tests.yml)

A Python coursework project for finding personal data in documents and examining how an organization stores those documents. The repository contains a deterministic PII baseline, an optional local semantic PERSON detector, document ingestion, a local catalog and review interface, and separate evaluation code. The default detection path does not require an external LLM service.

## Current scope

| Area | Implemented here | Boundary |
| --- | --- | --- |
| PII detection | Configured rules, format checks, context signals, scoring, and source offsets | Rule scores are not calibrated probabilities. The deterministic configuration does not detect PERSON or ADDRESS. |
| Semantic detection | A frozen local PERSON model can extend the rule pipeline | Requires a separately downloaded, hash-checked model snapshot. ADDRESS remains disabled. |
| Ingestion | UTF-8 TXT, DOCX, PDF, and images | Scanned pages and images require Tesseract and OCR language data. |
| Application | Local FastAPI catalog, review UI, roles, audit log, lifecycle actions, and backups | Intended for local use on `127.0.0.1`; deployment outside the host needs additional transport and access controls. |
| Evaluation | Exact-span metrics, experiment scripts, and frozen result artifacts | Development data and external benchmark results measure different populations; they are not production accuracy estimates. |

## Repository map

```text
app/          ingestion, detection, API, schemas, and storage
configs/      versioned detector and experiment settings
data/         frozen development documents and small benchmark inputs
evaluation/   dataset loaders, metrics, and benchmark entry points
artifacts/    recorded experiment outputs and their provenance notes
tests/        unit and integration checks
docs/         detailed setup, experiment notes, and stage reports
scripts/      reusable model-fetch command
```

Detection and evaluation have separate modules. Predictions retain document and segment identifiers, original text spans, detector evidence, decisions, and scores. Validators check candidates after they are generated; a regex match alone is not a validated identifier.

## Run locally

Requirements: Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/). Run commands from the repository root.

```powershell
uv sync --extra test --extra test-data
$env:PII_SOURCE_ROOT = 'C:\path\to\test-documents'
uv run python -m app.api.main init-db
uv run python -m app.api.main serve
```

`init-db` prompts for an administrator login and a password of at least 12 characters. Open `http://127.0.0.1:8765` after starting the server. The default SQLite catalog and analysis cache are created under `var/`, which is excluded from Git. Use synthetic test documents for a public demonstration. See [the system guide](docs/SYSTEM_START.md) for the UI, roles, and optional OCR and model setup; [the lifecycle walkthrough](docs/LIFECYCLE_BACKUP_DEMO.txt) covers backups. Both guides are in Russian.

The model weights are not included. If the frozen PERSON snapshot is absent, the application reports a model error and can still run deterministic detection. PDF pages without a text layer and image files need the optional `ocr` dependencies, Tesseract, and language data.

## Verify the project

Run the test suite:

```powershell
uv run --extra test --extra test-data python -m pytest -q
```

The checked-in `data/dev_set` is a frozen synthetic development corpus. Run the deterministic exact-span evaluation against those exact files:

```powershell
New-Item -ItemType Directory -Force var | Out-Null
uv run python -m evaluation.run --dataset data/dev_set --config configs/deterministic_v3.json --errors-out var/dev_errors.json
```

The evaluator prints per-entity TP, FP, FN, precision, recall, and F1. Its JSON error output is local because it contains source spans. `data/dev_set` is a **development** corpus, not a held-out estimate of deployment quality. The [`deterministic_v3` report](artifacts/tier0_deterministic_v3.json) records an earlier run on that corpus. The [experiment notes](docs/SEMANTIC_EXPERIMENTS.md) explain the separate semantic benchmarks and their limits. Frozen Kaggle outputs in `artifacts/nerel_person_01/` and `artifacts/redmadrobot_frozen_01/` include hashes and provenance; their original test corpora and model weights are not bundled, so those runs cannot be fully repeated from this checkout alone.

## Data and publication boundaries

The repository excludes local credentials, runtime databases, model weights, the coursework manuscript, and reference PDFs. Do not commit organizational documents or raw PII. Some benchmark artifacts contain examples or predictions from research datasets; check their provenance and redistribution terms before reusing them outside this project. The application stores catalog metadata and aggregate PII counts; protected analysis responses can contain source text and detected values.

Source code is under the [MIT License](LICENSE). Dataset and model licenses are separate.
