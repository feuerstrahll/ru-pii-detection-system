"""Load NEREL v1.1 BRAT test annotations without changing source offsets."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.schemas.models import DocumentSegment, EntityType
from evaluation.metrics import Annotation


def manifest_sha256(root: Path) -> str:
    """Hash annotation taxonomy and every test file in stable path order."""
    digest = hashlib.sha256()
    for path in [root / "annotation.conf", *sorted((root / "test").iterdir())]:
        if not path.is_file():
            raise ValueError(f"unexpected NEREL test entry: {path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _labels(path: Path) -> set[str]:
    labels: set[str] = set()
    in_entities = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            in_entities = line == "[entities]"
        elif in_entities and line.strip():
            labels.add(line.split("\t", 1)[0])
    if "PERSON" not in labels:
        raise ValueError("NEREL annotation.conf does not define PERSON")
    return labels


def _text_hashes(paths: list[Path]) -> set[str]:
    return {hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _read_annotations(path: Path, text: str, labels: set[str], document_id: str
                      ) -> tuple[set[Annotation], list[tuple[int, int, str]], Counter[str], Counter[str]]:
    person: set[Annotation] = set()
    all_spans: list[tuple[int, int, str]] = []
    label_counts: Counter[str] = Counter()
    discontinuous: Counter[str] = Counter()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not re.match(r"^T\d+\t", line):
            if line.startswith("T"):
                raise ValueError(f"invalid NEREL text annotation: {path.name}:{line_number}")
            continue  # Relations, attributes, events, and links are outside NER.
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"invalid NEREL text annotation: {path.name}:{line_number}")
        head = fields[1].split(" ", 1)
        if len(head) != 2 or head[0] not in labels:
            raise ValueError(f"unknown NEREL label or offsets: {path.name}:{line_number}")
        label, offsets = head
        label_counts[label] += 1
        ranges = offsets.split(";")
        parsed: list[tuple[int, int]] = []
        for item in ranges:
            if not re.fullmatch(r"\d+ \d+", item):
                raise ValueError(f"invalid NEREL offsets: {path.name}:{line_number}")
            start, end = map(int, item.split())
            if not 0 <= start < end <= len(text):
                raise ValueError(f"NEREL offsets outside text: {path.name}:{line_number}")
            parsed.append((start, end))
        if len(parsed) > 1:
            if label == "PERSON":
                raise ValueError(f"discontinuous PERSON requires a new fixed policy: {path.name}:{line_number}")
            discontinuous[label] += 1
            continue
        start, end = parsed[0]
        if text[start:end] != fields[2]:
            raise ValueError(f"NEREL source text differs from annotation: {path.name}:{line_number}")
        all_spans.append((start, end, label))
        if label == "PERSON":
            person.add(Annotation(document_id, "body", start, end, EntityType.PERSON))
    return person, all_spans, label_counts, discontinuous


def load_test(root: Path, dev_dir: Path, *, expected_manifest: str) -> tuple[list[tuple[DocumentSegment, set[Annotation]]], dict[str, object]]:
    if manifest_sha256(root) != expected_manifest:
        raise ValueError("NEREL v1.1 test manifest does not match the pinned revision")
    labels = _labels(root / "annotation.conf")
    split_files = {name: sorted((root / name).glob("*.txt")) for name in ("train", "dev", "test")}
    if {name: len(files) for name, files in split_files.items()} != {"train": 746, "dev": 94, "test": 93}:
        raise ValueError("NEREL published split file counts changed")
    test_hashes = _text_hashes(split_files["test"])
    development_hashes = _text_hashes(split_files["train"] + split_files["dev"])
    synthetic_hashes = {
        hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()
        for path in dev_dir.glob("*.json")
        for segment in json.loads(path.read_text(encoding="utf-8"))["segments"]
    }
    if not synthetic_hashes or test_hashes & (development_hashes | synthetic_hashes):
        raise ValueError("NEREL test has exact-text leakage or development data is missing")

    documents: list[tuple[DocumentSegment, set[Annotation]]] = []
    label_counts: Counter[str] = Counter()
    discontinuous: Counter[str] = Counter()
    person_overlap_person = 0
    person_overlap_other = 0
    for path in split_files["test"]:
        annotation_path = path.with_suffix(".ann")
        if not annotation_path.is_file():
            raise ValueError(f"NEREL annotation is missing: {annotation_path}")
        text = path.read_text(encoding="utf-8")
        document_id = f"nerel-test-{path.stem}"
        segment = DocumentSegment(document_id=document_id, segment_id="body", original_text=text,
                                  source_locator={"dataset": "nerel-ds/NEREL", "version": "1.1",
                                                  "split": "test", "file": path.name})
        person, all_spans, counts, skipped = _read_annotations(annotation_path, text, labels, document_id)
        label_counts.update(counts)
        discontinuous.update(skipped)
        person_spans = sorted((item.start, item.end) for item in person)
        for index, (start, end) in enumerate(person_spans):
            person_overlap_person += sum(start < other_end and other_start < end
                                         for other_start, other_end in person_spans[index + 1:])
            person_overlap_other += any(start < other_end and other_start < end
                                        for other_start, other_end, label in all_spans if label != "PERSON")
        documents.append((segment, person))
    if len(list((root / "test").glob("*.ann"))) != len(documents):
        raise ValueError("NEREL test contains orphan annotation files")
    total_entities = sum(label_counts.values())
    coverage = {"documents": len(documents), "all_entity_annotations": total_entities,
                "person_annotations": label_counts["PERSON"],
                "supported_entity_annotations": label_counts["PERSON"],
                "excluded_entity_annotations": total_entities - label_counts["PERSON"],
                "entity_coverage_fraction": label_counts["PERSON"] / total_entities,
                "supported_classes": ["PERSON"], "excluded_classes": sorted(labels - {"PERSON"}),
                "labels": dict(sorted(label_counts.items())),
                "discontinuous_non_person": dict(sorted(discontinuous.items())),
                "person_person_overlap_pairs": person_overlap_person,
                "person_spans_overlapping_other_labels": person_overlap_other,
                "train_documents": len(split_files["train"]), "dev_documents": len(split_files["dev"]),
                "test_documents": len(split_files["test"]), "exact_text_leakage_count": 0}
    return documents, coverage
