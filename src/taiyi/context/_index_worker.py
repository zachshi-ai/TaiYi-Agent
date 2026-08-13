"""Child entry point for one durable repository index generation."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from taiyi.context.index_jobs import (
    INDEX_JOB_RECEIPT_SCHEMA,
    INDEX_JOB_REQUEST_SCHEMA,
)
from taiyi.context.repository import RepositoryContextIndex


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    if request.get("schema_version") != INDEX_JOB_REQUEST_SCHEMA:
        raise ValueError("unsupported repository index request schema")
    operation_id = str(request["operation_id"])
    progress_path = Path(request["progress_path"])
    receipt_path = Path(request["receipt_path"])
    delay = max(0.0, float(request.get("worker_progress_delay", 0.0)))
    index = RepositoryContextIndex(
        request["repository_root"],
        db_path=request["db_path"],
        max_files=int(request["max_files"]),
        max_file_bytes=int(request["max_file_bytes"]),
        chunk_lines=int(request["chunk_lines"]),
    )

    def progress(observed) -> None:
        _atomic_json(progress_path, {
            "schema_version": INDEX_JOB_RECEIPT_SCHEMA,
            "operation_id": operation_id,
            "progress": observed.to_dict(),
        })
        if delay:
            time.sleep(delay)

    result = index.refresh(progress=progress)
    _atomic_json(receipt_path, {
        "schema_version": INDEX_JOB_RECEIPT_SCHEMA,
        "operation_id": operation_id,
        "result": result.to_dict(),
    })
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
