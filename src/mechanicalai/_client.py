from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.mechanicalai.com"


class Client:
    """A client bound to one API key. The key comes from ``MECHANICALAI_API_KEY`` unless given."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("MECHANICALAI_API_KEY")
        if not self.api_key:
            raise ValueError("Set MECHANICALAI_API_KEY or pass api_key=")
        self.base_url = (base_url or os.environ.get("MECHANICALAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
