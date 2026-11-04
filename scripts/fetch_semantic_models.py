"""Download and lock the local semantic-model snapshots used by benchmark experiments."""

import argparse
import hashlib
import json
from pathlib import Path


def _snapshot_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda entry: entry.relative_to(path).as_posix()):
        if item.is_file():
            relative = item.relative_to(path)
            if relative.parts[0] == ".cache":
                continue
            digest.update(relative.as_posix().encode())
            with item.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def fetch(lock_path: Path, selected_ids: set[str] | None = None) -> None:
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as error:
        raise RuntimeError("huggingface_hub is required; install the ner-benchmark extra") from error
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    api = HfApi()
    for item in raw["candidates"]:
        if selected_ids and item["id"] not in selected_ids:
            continue
        snapshot = Path(item["local_snapshot"])
        info = api.model_info(item["model_name"], revision=item["revision"])
        snapshot_download(item["model_name"], revision=info.sha, local_dir=snapshot)
        item["revision"] = info.sha
        item["tokenizer_revision"] = info.sha
        item["snapshot_sha256"] = _snapshot_hash(snapshot)
        item["status"] = "ready"
        print(f"{item['id']}: {info.sha}")
    lock_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path("models.lock"))
    parser.add_argument("--model", action="append", dest="models")
    args = parser.parse_args()
    fetch(args.lock, set(args.models) if args.models else None)


if __name__ == "__main__":
    main()
