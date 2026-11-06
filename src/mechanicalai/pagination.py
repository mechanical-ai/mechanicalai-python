"""Paged lists.

Every list answer is ``{object: "list", data: [...], has_more, next_cursor}``. A page holds its ``data``, and
iterating over the page walks every page after it too, fetching each one as needed::

    page = client.projects.list(limit=50)
    page.data                      # this page only
    for project in page: ...       # every project, page after page
    page.next_page()               # the next page, or None

With ``AsyncClient`` the page is an ``AsyncPage``: ``async for item in page`` walks every page; ``page.data`` is
this page. There is no sync iteration over an ``AsyncPage``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any, Generic, TypeVar

from mechanicalai.types._base import BaseModel

T = TypeVar("T", bound=BaseModel)

__all__ = ["AsyncPage", "SyncPage"]


class _Page(Generic[T]):
    data: list[T]
    """The items of this page."""
    has_more: bool
    next_cursor: str | None
    """The value to pass as ``after`` for the next page."""
    request_id: str | None

    def __init__(
        self,
        *,
        data: list[T],
        has_more: bool = False,
        next_cursor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.data = data
        self.has_more = has_more
        self.next_cursor = next_cursor
        self.request_id = request_id

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(data={self.data!r}, has_more={self.has_more!r}, next_cursor={self.next_cursor!r})"
        )

    def _cursor_for_next(self) -> str | None:
        """The cursor to pass as ``after``, or None when this is the last page."""
        if not self.has_more or not self.next_cursor:
            return None
        return self.next_cursor

    @classmethod
    def _parse_body(cls, body: Any, item_type: type[T]) -> tuple[list[T], bool, str | None]:
        if not isinstance(body, dict):
            raise TypeError(f"A list answer is an object; got {type(body).__name__}.")
        raw = body.get("data")
        items = [item_type.model_validate(item) for item in raw] if isinstance(raw, list) else []
        has_more = bool(body.get("has_more", False))
        cursor = body.get("next_cursor")
        return items, has_more, cursor if isinstance(cursor, str) else None


class SyncPage(_Page[T]):
    """One page of a list; iterating over it continues through the following pages."""

    def __init__(
        self,
        *,
        data: list[T],
        has_more: bool = False,
        next_cursor: str | None = None,
        request_id: str | None = None,
        fetch_next: Callable[[str], SyncPage[T]] | None = None,
    ) -> None:
        super().__init__(data=data, has_more=has_more, next_cursor=next_cursor, request_id=request_id)
        self._fetch_next = fetch_next

    @classmethod
    def from_body(
        cls,
        body: Any,
        item_type: type[T],
        *,
        request_id: str | None = None,
        fetch_next: Callable[[str], SyncPage[T]] | None = None,
    ) -> SyncPage[T]:
        items, has_more, cursor = cls._parse_body(body, item_type)
        return cls(data=items, has_more=has_more, next_cursor=cursor, request_id=request_id, fetch_next=fetch_next)

    def next_page(self) -> SyncPage[T] | None:
        cursor = self._cursor_for_next()
        if cursor is None or self._fetch_next is None:
            return None
        return self._fetch_next(cursor)

    def __iter__(self) -> Iterator[T]:
        """Every item, this page's and every following page's (fetched as needed)."""
        page: SyncPage[T] | None = self
        seen: set[str] = set()
        while page is not None:
            yield from page.data
            cursor = page._cursor_for_next()
            if cursor is None or cursor in seen:
                return
            seen.add(cursor)
            page = page.next_page()


class AsyncPage(_Page[T]):
    """One page of a list; ``async for`` over it continues through the following pages."""

    def __init__(
        self,
        *,
        data: list[T],
        has_more: bool = False,
        next_cursor: str | None = None,
        request_id: str | None = None,
        fetch_next: Callable[[str], Awaitable[AsyncPage[T]]] | None = None,
    ) -> None:
        super().__init__(data=data, has_more=has_more, next_cursor=next_cursor, request_id=request_id)
        self._fetch_next = fetch_next

    @classmethod
    def from_body(
        cls,
        body: Any,
        item_type: type[T],
        *,
        request_id: str | None = None,
        fetch_next: Callable[[str], Awaitable[AsyncPage[T]]] | None = None,
    ) -> AsyncPage[T]:
        items, has_more, cursor = cls._parse_body(body, item_type)
        return cls(data=items, has_more=has_more, next_cursor=cursor, request_id=request_id, fetch_next=fetch_next)

    async def next_page(self) -> AsyncPage[T] | None:
        cursor = self._cursor_for_next()
        if cursor is None or self._fetch_next is None:
            return None
        return await self._fetch_next(cursor)

    async def __aiter__(self) -> AsyncIterator[T]:
        """Every item, this page's and every following page's (fetched as needed)."""
        page: AsyncPage[T] | None = self
        seen: set[str] = set()
        while page is not None:
            for item in page.data:
                yield item
            cursor = page._cursor_for_next()
            if cursor is None or cursor in seen:
                return
            seen.add(cursor)
            page = await page.next_page()
