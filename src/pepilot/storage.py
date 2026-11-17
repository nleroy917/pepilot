"""Whether paths exist, wherever they are: local, s3://, gs://, az://, ... (through fsspec).

Each object store needs its fsspec backend installed: s3fs, gcsfs or adlfs (the pepilot extras
`aws`, `gcp` and `azure` include them).
"""

from __future__ import annotations

import concurrent.futures
from typing import Callable, Sequence

ExistsFn = Callable[[Sequence[str]], list[bool]]


class StorageError(RuntimeError):
    """A path could not be checked."""


def _exists(path: str) -> bool:
    import fsspec

    try:
        fs, local_path = fsspec.core.url_to_fs(path)
    except (ImportError, ValueError) as e:
        raise StorageError(f"cannot check {path}: {e}") from None
    try:
        return fs.exists(local_path)
    except Exception as e:  # noqa: BLE001 - backends raise their own types
        raise StorageError(f"cannot check {path}: {type(e).__name__}: {e}") from None


def exists(paths: Sequence[str], threads: int = 32) -> list[bool]:
    """Whether each path exists, in order, checking many at once."""
    if not paths:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(threads, len(paths))) as pool:
        return list(pool.map(_exists, paths))
