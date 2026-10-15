import stat

import pytest

from collectos.uploads import UploadError, UploadStore


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def uploads():
    u = UploadStore(ttl=100, max_bytes=1000, clock=FakeClock())
    yield u
    u.close()


def test_saves_under_a_random_name_never_the_users(uploads):
    item = uploads.save(b"a,b\n1,2\n", "../../etc/Invoices final.CSV")
    assert item.path.parent == uploads.directory and item.path.name == item.id + ".csv"
    assert item.name == "Invoices final.CSV"          # directory parts of the supplied name are dropped
    assert item.path.read_bytes() == b"a,b\n1,2\n"
    assert stat.S_IMODE(item.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(uploads.directory.stat().st_mode) == 0o700


@pytest.mark.parametrize("name", ["x.exe", "x.xls", "x", "x.csv.php", "", ".csv.txt"])
def test_only_csv_and_xlsx_are_accepted(uploads, name):
    with pytest.raises(UploadError):
        uploads.save(b"data", name)


def test_size_and_empty_limits(uploads):
    with pytest.raises(UploadError, match="empty"):
        uploads.save(b"", "a.csv")
    with pytest.raises(UploadError, match="larger than"):
        uploads.save(b"x" * 1001, "a.csv")
    assert uploads.save(b"x" * 1000, "a.csv")


def test_get_rejects_unknown_malformed_and_path_like_ids(uploads):
    item = uploads.save(b"x", "a.csv")
    assert uploads.get(item.id) == item
    for bad in ("nope", "../../etc/passwd", "", None, 5, item.id + "/..", "a" * 70):
        with pytest.raises(UploadError):
            uploads.get(bad)


def test_uploads_expire_and_their_files_are_deleted():
    clock = FakeClock()
    u = UploadStore(ttl=100, clock=clock)
    try:
        item = u.save(b"x", "a.csv")
        clock.t = 101
        with pytest.raises(UploadError, match="expired"):
            u.get(item.id)
        assert not item.path.exists()
    finally:
        u.close()


def test_discard_and_close_remove_files(uploads):
    item = uploads.save(b"x", "a.csv")
    uploads.discard(item.id)
    assert not item.path.exists()
    other = uploads.save(b"y", "b.csv")
    directory = uploads.directory
    uploads.close()
    assert not directory.exists() and not other.path.exists()
