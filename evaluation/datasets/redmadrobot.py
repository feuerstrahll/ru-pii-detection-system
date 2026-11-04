"""Read the pinned redmadrobot test CSV and restore strict character spans."""

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.schemas.models import EntityType
from evaluation.metrics import Annotation

LABELS: dict[str, EntityType | None] = {
    "FIRST_NAME": EntityType.PERSON, "LAST_NAME": EntityType.PERSON,
    "MIDDLE_NAME": EntityType.PERSON, "EMAIL": EntityType.EMAIL,
    "PHONE": EntityType.PHONE, "PASSPORT": EntityType.PASSPORT,
    "INN": EntityType.TAX_ID, "SNILS": EntityType.SOCIAL_SECURITY_ID,
    "CREDIT_CARD": EntityType.BANK_CARD,
    "COUNTRY": None, "REGION": None, "DISTRICT": None, "CITY": None,
    "STREET": None, "HOUSE": None, "URL": None, "IP_ADDRESS": None,
    "OMS": None, "DRIVER_LICENSE": None, "MILITARY_ID": None,
    "BIRTH_CERTIFICATE": None,
}
NAME_LABELS = frozenset(("FIRST_NAME", "LAST_NAME", "MIDDLE_NAME"))
COMPARABLE = frozenset(kind for kind in LABELS.values() if kind is not None)


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    label: str


def token_offsets(text: str, tokens: list[str]) -> list[tuple[int, int]]:
    """Align in sequence; only source whitespace and token case may differ."""
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for index, token in enumerate(tokens):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if not isinstance(token, str):
            raise TypeError(f"invalid token {index}")
        if not token:
            offsets.append((cursor, cursor))
            continue
        end = cursor + len(token)
        if text[cursor:end].casefold() != token.casefold():
            raise ValueError(f"token {index} cannot be aligned at character {cursor}: {token!r}")
        offsets.append((cursor, end))
        cursor = end
    if text[cursor:].strip():
        raise ValueError(f"unmatched source text from character {cursor}")
    return offsets


def bio_spans(text: str, tokens: list[str], tags: list[str]) -> list[SourceSpan]:
    if len(tokens) != len(tags):
        raise ValueError(f"BIO token/tag count differs: {len(tokens)} != {len(tags)}")
    offsets = token_offsets(text, tokens)
    spans: list[SourceSpan] = []
    active: SourceSpan | None = None
    for index, (tag, (start, end)) in enumerate(zip(tags, offsets)):
        if start == end and tag != "O":
            raise ValueError(f"empty entity token {index}")
        if tag == "O":
            if active:
                spans.append(active)
            active = None
            continue
        if not isinstance(tag, str) or len(tag) < 3 or tag[:2] not in ("B-", "I-") or tag[2:] not in LABELS:
            raise ValueError(f"unknown BIO tag at token {index}: {tag!r}")
        label = tag[2:]
        if tag.startswith("I-"):
            if active is None or active.label != label:
                raise ValueError(f"orphan or mismatched I-tag at token {index}: {tag}")
            active = SourceSpan(active.start, end, label)
        else:
            if active:
                spans.append(active)
            active = SourceSpan(start, end, label)
    if active:
        spans.append(active)
    return spans


def merge_person(text: str, spans: list[SourceSpan]) -> list[SourceSpan]:
    merged: list[SourceSpan] = []
    for span in spans:
        if (merged and span.label in NAME_LABELS and merged[-1].label in NAME_LABELS
                and span.start >= merged[-1].end
                and text[merged[-1].end:span.start].isspace()):
            merged[-1] = SourceSpan(merged[-1].start, span.end, "FIRST_NAME")
        else:
            merged.append(span)
    return merged


def load_test(path: Path, dev_dir: Path) -> tuple[list[tuple[str, str, set[Annotation]]], dict[str, object]]:
    dev_hashes = {
        hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()
        for file in dev_dir.glob("*.json")
        for segment in json.loads(file.read_text(encoding="utf-8"))["segments"]
    }
    if not dev_hashes:
        raise ValueError(f"development texts are missing: {dev_dir}")
    rows: list[tuple[str, str, set[Annotation]]] = []
    counts: Counter[str] = Counter()
    duplicate_rows: list[int] = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["text", "tokens", "ner_tags"]:
            raise ValueError(f"unexpected test CSV schema: {reader.fieldnames}")
        for row_number, row in enumerate(reader):
            try:
                text = row["text"]
                tokens, tags = json.loads(row["tokens"]), json.loads(row["ner_tags"])
                if not isinstance(tokens, list) or not isinstance(tags, list):
                    raise TypeError("tokens and ner_tags must be JSON arrays")
                spans = bio_spans(text, tokens, tags)
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError(f"test row {row_number}: {error}") from error
            counts.update(span.label for span in spans)
            if hashlib.sha256(text.encode("utf-8")).hexdigest() in dev_hashes:
                duplicate_rows.append(row_number)
                continue
            document_id = f"redmadrobot-test-{row_number:05d}"
            gold = {
                Annotation(document_id, "body", span.start, span.end, LABELS[span.label])
                for span in merge_person(text, spans) if LABELS[span.label] is not None
            }
            rows.append((document_id, text, gold))
    supported = sum(counts[label] for label, kind in LABELS.items() if kind is not None)
    total = sum(counts.values())
    coverage = {
        "source_label_counts": dict(sorted(counts.items())),
        "source_label_count": len(counts), "source_mentions": total,
        "comparable_source_mentions": supported,
        "comparable_source_fraction": supported / total if total else 0,
        "excluded_source_mentions": total - supported,
        "ambiguous_location_mentions": sum(counts[label] for label in ("COUNTRY", "REGION", "DISTRICT", "CITY", "STREET", "HOUSE")),
        "unsupported_mentions": sum(counts[label] for label in ("URL", "IP_ADDRESS", "OMS", "DRIVER_LICENSE", "MILITARY_ID", "BIRTH_CERTIFICATE")),
        "comparable_gold_spans_after_person_merge": sum(len(gold) for _, _, gold in rows),
        "dev_duplicate_row_count": len(duplicate_rows), "excluded_dev_duplicate_rows": duplicate_rows,
        "evaluated_rows": len(rows),
    }
    return rows, coverage
