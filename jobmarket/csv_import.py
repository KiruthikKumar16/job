"""Robust CSV reading helpers for user-selected Analytics data sources."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class CSVImportError(ValueError):
    """An input problem that can be explained directly to an Analytics user."""


def read_csv_frame(path: str | Path) -> pd.DataFrame:
    """Read a CSV with common text encodings and turn parser failures into guidance."""
    csv_path = Path(path)
    try:
        # utf-8-sig accepts ordinary UTF-8 too, while removing an optional BOM.
        try:
            frame = pd.read_csv(csv_path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            frame = pd.read_csv(csv_path, encoding="latin-1")
    except pd.errors.EmptyDataError as exc:
        raise CSVImportError("This CSV is empty or does not contain a header row.") from exc
    except pd.errors.ParserError as exc:
        raise CSVImportError(
            "The CSV rows could not be parsed. Check that the file uses consistent columns, delimiters, and quotes."
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise CSVImportError(f"Unable to read '{csv_path.name}': {exc}") from exc

    if frame.shape[1] == 0:
        raise CSVImportError("This CSV does not contain any columns.")
    # pandas keeps repeated header names by suffixing them (.1, .2); make that behavior explicit.
    frame.columns = [str(column).strip() for column in frame.columns]
    if frame.empty:
        raise CSVImportError("This CSV has a header but no job rows.")
    return frame
