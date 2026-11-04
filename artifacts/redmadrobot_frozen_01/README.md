# REDMADROBOT-FROZEN-01

Frozen Kaggle result.

The four files in this directory are byte-for-byte copies of the Kaggle outputs
provided on 2026-11-02. They were not recomputed or edited locally.

## File hashes (SHA-256)

```text
  results.json   9377c7d35d7783cbd0db5ff5700306b1c2fddaf7f256c508164465f0f0bf7aee
  metrics.csv    28b7b680fc18c9bebe92dce955e85d9e6cd56d00491ac64456a57ead86163cd6
  coverage.json  6cc6e0030268f642732dec75c1da6796b250d73747ca9672efc02763bb8fe32f
  errors.json    d4c8890e697aa989136d47f8767031a4580fe22568feffcd28dcfa4e899a4f1d
```

Comparable-scope micro exact F1 = 0.541247; precision = 0.704965;
recall = 0.439240; macro F1 = 0.501766. The scope covers seven project
classes and 2,841 / 5,614 source mentions (50.61%), counted before PERSON
component merging. It is text-only and says nothing about ADDRESS, OCR,
file ingestion, or organization documents.

Read-only error audit against the pinned test CSV:
  PASSPORT: 472 FN, all without a full-span regex match. 435 are separately
  annotated four-digit series or six-digit number fragments. Of 186 FP, 108
  overlap gold INN and 33 overlap gold DRIVER_LICENSE; all 186 are review
  decisions counted as detections by the frozen protocol.
  SOCIAL_SECURITY_ID: 174 / 222 FN have no exact regex candidate; 48 have
  an exact candidate rejected by the checksum validator.
  BANK_CARD: 75 / 141 FN have an exact candidate rejected by the checksum
  validator, 64 have no regex candidate, and two overlap a candidate.
  PERSON: 138 FN have overlapping predictions with different boundaries.
  For each FN paired with its maximally overlapping FP, 101 predictions
  contain the gold span, 36 are inside it, and one is shifted. In 82 of the
  101 wider predictions the added text matches a strict initials pattern.
  PERSON overlap F1 = 0.910415 is diagnostic only; exact F1 = 0.713765.

The passport fragment mismatch is a benchmark annotation limitation for
full-identifier detection, not grounds to remove gold mentions after seeing
the test result. No threshold, validator, mapping, or model parameter was
changed on this test set. All cause labels above are read-only diagnostics;
the original FP/FN files retain their original categories.
