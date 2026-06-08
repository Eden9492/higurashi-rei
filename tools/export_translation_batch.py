#!/usr/bin/env python3
"""Export small todo translation batches from translations/rei_translation.csv.

The script never writes to Update/ and never modifies rei_translation.csv. Its
only write target is translations/batches/<output>.jsonl.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path


CSV_COLUMNS = [
    "file",
    "line_number",
    "original_line",
    "japanese_text",
    "english_text",
    "french_translation",
    "status",
    "notes",
]

JSONL_COLUMNS = [
    "row_number",
    "file",
    "line_number",
    "japanese_text",
    "english_text",
    "french_translation",
    "status",
    "notes",
]


def positive_int(value: str) -> int:
    """argparse type for --limit."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--limit must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("--limit must be greater than zero")
    return parsed


def normalize_file_filter(value: str | None) -> str | None:
    """Normalize path separators for comparisons against CSV file values."""
    if value is None:
        return None
    return value.replace("\\", "/")


def default_output_name() -> str:
    """Build a timestamped default name to avoid accidental batch overwrites."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"todo_batch_{timestamp}.jsonl"


def resolve_output_path(batch_dir: Path, output_name: str | None) -> Path:
    """Return the JSONL output path, always inside translations/batches/.

    --output is treated as a simple file name, not as an arbitrary path. This
    keeps batch exports in the expected folder and avoids surprising writes
    elsewhere in the project.
    """
    chosen_name = output_name or default_output_name()
    candidate = Path(chosen_name)

    if candidate.is_absolute() or candidate.name != chosen_name:
        raise ValueError("--output must be a file name inside translations/batches/")

    if candidate.suffix.lower() != ".jsonl":
        candidate = candidate.with_suffix(".jsonl")

    return batch_dir / candidate


def check_expected_columns(fieldnames: list[str] | None) -> list[str]:
    """Return any required CSV columns missing from the header."""
    available = fieldnames or []
    return [column for column in CSV_COLUMNS if column not in available]


def iter_todo_rows(csv_path: Path, file_filter: str | None):
    """Yield todo rows, preserving their 1-based data-row number.

    row_number is the CSV data row number excluding the header: the first data
    row is 1, even though it appears on physical CSV line 2.
    """
    with csv_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        missing_columns = check_expected_columns(reader.fieldnames)
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ValueError(f"CSV is missing expected column(s): {missing}")

        for row_number, row in enumerate(reader, start=1):
            if row.get("status") != "todo":
                continue

            row_file = normalize_file_filter(row.get("file", ""))
            if file_filter is not None and row_file != file_filter:
                continue

            yield row_number, row


def export_batch(
    csv_path: Path,
    output_path: Path,
    limit: int,
    file_filter: str | None,
) -> int:
    """Write up to limit todo rows to a JSONL batch file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    with output_path.open("w", encoding="utf-8", newline="\n") as output_file:
        for row_number, row in iter_todo_rows(csv_path, file_filter):
            item = {
                "row_number": row_number,
                "file": row.get("file", ""),
                "line_number": row.get("line_number", ""),
                "japanese_text": row.get("japanese_text", ""),
                "english_text": row.get("english_text", ""),
                "french_translation": row.get("french_translation", ""),
                "status": row.get("status", ""),
                "notes": row.get("notes", ""),
            }
            output_file.write(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            written += 1

            if written >= limit:
                break

    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export todo rows from translations/rei_translation.csv as JSONL."
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=50,
        help="maximum number of todo rows to export (default: 50)",
    )
    parser.add_argument(
        "--file",
        dest="file_filter",
        help="only export todo rows whose CSV file column matches this value",
    )
    parser.add_argument(
        "--output",
        help="output file name inside translations/batches/ (default: timestamped)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    csv_path = repo_root / "translations" / "rei_translation.csv"
    batch_dir = repo_root / "translations" / "batches"

    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}", file=sys.stderr)
        return 1

    file_filter = normalize_file_filter(args.file_filter)

    try:
        output_path = resolve_output_path(batch_dir, args.output)
        written = export_batch(csv_path, output_path, args.limit, file_filter)
    except (OSError, ValueError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {written} row(s) to {output_path}")
    if file_filter is not None:
        print(f"File filter: {file_filter}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
