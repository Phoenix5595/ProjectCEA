"""Public immutable archive-manifest value returned by the codec."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """Validated manifest plus the verified archive's member inventory."""
    data: Mapping[str, Any]
    sha256: str
    byte_size: int

    @property
    def dataset_id(self) -> str:
        return str(self.data["dataset_id"])

    @property
    def revision(self) -> int:
        return int(self.data["revision"])
