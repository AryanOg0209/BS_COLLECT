"""Short-lived storage for files uploaded through the dashboard.

Files go into a private temp directory under a random name (the user's file name is never used
on disk), are size-limited, expire after a while, and are deleted when the server stops.
"""

import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xlsm")
MAX_UPLOAD = 10 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class UploadError(ValueError):
    pass


@dataclass(frozen=True)
class Upload:
    id: str
    path: Path
    name: str       # display name only; sanitised
    created: float


class UploadStore:
    def __init__(self, ttl: float = 900.0, max_bytes: int = MAX_UPLOAD, clock=time.monotonic):
        self._dir = Path(tempfile.mkdtemp(prefix="collectos-uploads-"))  # mode 0700
        self._ttl, self._max, self._clock = ttl, max_bytes, clock
        self._items: dict[str, Upload] = {}

    @property
    def directory(self) -> Path:
        return self._dir

    def save(self, data: bytes, filename: str) -> Upload:
        self.purge()
        if not data:
            raise UploadError("the file is empty")
        if len(data) > self._max:
            raise UploadError(f"the file is larger than {self._max // (1024 * 1024)} MB")
        clean = re.sub(r"[\x00-\x1f\x7f/\\]", "", Path(filename or "").name).strip()[:120] or "upload"
        suffix = Path(clean).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise UploadError("use a .csv or .xlsx file (save older .xls files as .xlsx)")
        upload_id = secrets.token_urlsafe(16)
        path = self._dir / f"{upload_id}{suffix}"
        path.write_bytes(data)
        path.chmod(0o600)
        item = Upload(upload_id, path, clean, self._clock())
        self._items[upload_id] = item
        return item

    def get(self, upload_id: str) -> Upload:
        self.purge()
        if not isinstance(upload_id, str) or not _ID.match(upload_id) or upload_id not in self._items:
            raise UploadError("that upload has expired or does not exist; choose the file again")
        return self._items[upload_id]

    def discard(self, upload_id: str) -> None:
        item = self._items.pop(upload_id, None)
        if item is not None:
            item.path.unlink(missing_ok=True)

    def purge(self) -> None:
        now = self._clock()
        for upload_id in [i for i, u in self._items.items() if now - u.created > self._ttl]:
            self.discard(upload_id)

    def close(self) -> None:
        self._items.clear()
        shutil.rmtree(self._dir, ignore_errors=True)
