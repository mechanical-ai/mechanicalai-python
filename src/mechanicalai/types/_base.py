from __future__ import annotations

from typing import Any

import pydantic
from pydantic import ConfigDict, PrivateAttr


class BaseModel(pydantic.BaseModel):
    """The base of every API object.

    Unknown fields are kept (``extra="allow"``), so a field the API adds later never breaks an older library.
    ``_request_id`` carries the ``X-Request-Id`` of the answer the object was parsed from; quote it in support.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    _request_id: str | None = PrivateAttr(default=None)

    @property
    def request_id(self) -> str | None:
        return self._request_id

    def to_dict(self) -> dict[str, Any]:
        """The object as plain JSON-compatible data (dates as ISO 8601 strings)."""
        return self.model_dump(mode="json", by_alias=True)
