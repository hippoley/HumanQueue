from __future__ import annotations

from .humanize import URI_TO_KIND

HUMAN_SCHEME = "human://"
SUPPORTED_URIS = tuple(URI_TO_KIND)


def is_human_uri(value: str) -> bool:
    return value in URI_TO_KIND
