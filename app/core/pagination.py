"""Shared pagination envelope: {items, total, page, page_size} (API §2)."""
from typing import Generic, TypeVar, Sequence
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: Sequence[T]
    total: int
    page: int
    page_size: int


def paginate_params(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)  # cap at 100
    return page, page_size
