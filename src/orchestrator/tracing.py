"""Writes down everything that happens in a run, one JSON line per event, so a run can be
debugged, measured, or replayed in the office afterwards."""
import json
from datetime import UTC, datetime
from pathlib import Path


class Tracer:
    def __init__(self, run_id: str, runs_dir: Path = Path("runs")) -> None:
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = runs_dir / f"{run_id}.jsonl"

    def log(self, event: str, **data) -> None:
        record = {"time": datetime.now(UTC).isoformat(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")