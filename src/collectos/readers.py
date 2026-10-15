"""File readers: CSV and XLSX to raw rows. No interpretation here."""

import csv
import zipfile
from pathlib import Path

from openpyxl import load_workbook

# Each row is (1-based row number in the source file, list of cell values).
RawRows = list[tuple[int, list[object]]]


class UnsupportedFile(ValueError):
    pass


def read_rows(path: str | Path) -> RawRows:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    raise UnsupportedFile(f"unsupported file type {suffix!r}; use .csv or .xlsx (save .xls as .xlsx)")


def _read_csv(path: Path) -> RawRows:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [(i, list(row)) for i, row in enumerate(csv.reader(handle), start=1)]


def _check_xlsx(path: Path) -> None:
    """Raise UnsupportedFile with a specific fix if a file named .xlsx is not Excel."""
    with path.open("rb") as handle:
        head = handle.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFile(f"{path.name} is an old Excel (.xls) file renamed to .xlsx; open it in Excel and use Save As > .xlsx")
    if not head.startswith(b"PK"):
        raise UnsupportedFile(f"{path.name} is not a real .xlsx file (it may be CSV, HTML or text); export it as .xlsx or .csv")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        raise UnsupportedFile(f"{path.name} is damaged or incomplete (not a valid zip/.xlsx); re-export it") from None
    if "[Content_Types].xml" not in names:
        if any(n.endswith(".iwa") for n in names):
            raise UnsupportedFile(
                f"{path.name} is an Apple Numbers document, not Excel. In Numbers choose File > Export To > Excel "
                "(or CSV), then import the exported file"
            )
        raise UnsupportedFile(f"{path.name} is a zip file but not an Excel workbook; export it as .xlsx or .csv")


def _read_xlsx(path: Path) -> RawRows:
    _check_xlsx(path)
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        return [(i, list(row)) for i, row in enumerate(sheet.iter_rows(values_only=True), start=1)]
    finally:
        workbook.close()
