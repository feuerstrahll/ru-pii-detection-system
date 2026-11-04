# NEREL-PERSON-01

Frozen Kaggle result received on 2026-11-02.

The four files here are byte-for-byte copies of the user-provided Kaggle outputs.
They were not recomputed or edited locally.

## File hashes (SHA-256)

```text
  results.json   325ec99235899eb490c5d771bd3b73f54aae6d848cff4e8ecfb72c2711dad227
  metrics.csv    a9355713575dd7583421aa9ac2d82c4f966f61d259db6ffd089fbb38d89b07c6
  errors.json   346df00f36378dac1d3a86803387435caef44707afd1a78f222b55232da2b451
  coverage.json 24eab85a94f3f56543768269ba87104f699e3cbf14aef9ffc9fa13e1580c9373
```

Frozen input hashes in results.json match the current benchmark script, NEREL
loader, experiment config, and semantic model config. The reported model
snapshot hash matches semantic_person_frozen_v2.json. The run reports Kaggle
Tesla T4, CUDA, GLiNER2 2.1.0, 100 chunks across 93 test documents.

PERSON exact: TP 740, FP 43, FN 221; precision 0.945083, recall 0.770031,
F1 0.848624. One scored class makes micro and macro F1 equal to PERSON F1.
Overlap diagnostic: TP 767, FP 16, FN 194, F1 0.879587. Exact remains the
primary metric.

The NEREL test contains 961 PERSON annotations among 5,827 entity annotations
(16.49% entity coverage; 1 of 29 labels evaluated). Other labels are excluded
from this PERSON-only benchmark. The loader reported no exact-text overlap
between test and train/dev or data/dev_set.

Read-only error audit against pinned NEREL v1.1 source text:
  194 FN have no overlapping PERSON prediction; 27 FN overlap a prediction
  with different boundaries. Among the latter, 18 predicted spans are narrower
  than gold and nine are wider. The FP file has 28 boundary and 15 non-overlap
  items. Boundary FP/FN item counts need not match because nested or multiple
  spans can overlap one another.
  133 of the 194 non-overlap FN are one-word gold spans. Across all gold PERSON,
  exact recall is 188/321 (58.57%) for one-word spans and 552/640 (86.25%) for
  spans of at least two words. These are descriptive slices, not assigned root
  causes or a basis for changing the frozen detector on this test set.

NEREL is a separate news-domain PERSON sanity check. Its F1 is not directly
comparable to the Redmadrobot hybrid result and is not a measure of other PII
classes, OCR, or organization documents. No threshold, model, or weights were
changed after inspecting this test output.
