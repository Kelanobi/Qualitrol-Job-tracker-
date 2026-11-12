from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock


class ExtractionCache:
    """Cache parsed documents while invalidating immediately when a file changes."""

    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple, object] = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _key(extractor, path: Path, args: tuple) -> tuple:
        path = Path(path)
        stat = path.stat()
        return (
            extractor.__module__, extractor.__qualname__, str(path.resolve()).casefold(),
            stat.st_size, stat.st_mtime_ns, args,
        )

    def extract(self, extractor, path: Path, *args):
        key = self._key(extractor, path, args)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return deepcopy(cached)

        result = extractor(Path(path), *args)
        with self._lock:
            self._entries[key] = deepcopy(result)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return result


DOCUMENT_EXTRACTION_CACHE = ExtractionCache()
