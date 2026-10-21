from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ArtifactRecord:
    timestamp: str | None
    source: str
    output: str | None
    status: str
    size_bytes: int | None = None
    message: str | None = None


@dataclass
class RunManifest:
    provider: str
    request: dict[str, Any]
    region: dict[str, Any]
    started_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_utc: str | None = None
    artifacts: list[ArtifactRecord] = field(default_factory=list)

    def add(self, record: ArtifactRecord) -> None:
        self.artifacts.append(record)

    def finish(self) -> None:
        self.finished_utc = datetime.now(timezone.utc).isoformat()

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.artifacts:
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["summary"] = self.summary()
        return result

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)
        return target

