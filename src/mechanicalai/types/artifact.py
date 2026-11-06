from __future__ import annotations

from datetime import datetime
from typing import Literal

from mechanicalai.types._base import BaseModel

ArtifactKind = Literal["report", "document", "model", "table", "data", "image"]


class Artifact(BaseModel):
    """One deliverable a run produced, in one or more formats.

    Formats by kind: report md, pdf; document md, pdf, docx; model step, stl, glb, native; table csv, xlsx;
    data vtu, csv, json; image png.
    """

    id: str
    object: Literal["artifact"] = "artifact"
    project_id: str
    run_id: str
    kind: ArtifactKind | str
    """One of the six known kinds; an unknown kind is passed through."""
    name: str
    """The engine's name for it, stable across follow-ups when it is the same thing."""
    formats: list[str] = []
    """What ``download`` accepts."""
    default_format: str
    size: int | None = None
    """Bytes of the default format, when known."""
    group: str | None = None
    """The variant it belongs to, when the engine made variants."""
    created_at: datetime
