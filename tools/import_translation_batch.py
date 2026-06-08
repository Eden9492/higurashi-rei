#!/usr/bin/env python3
"""Import a translated JSONL batch into translations/rei_translation.csv.

Safety rules:
- Update/ is never touched.
- rei_translation.csv is changed only after every JSONL entry has been checked.
- A backup is created before a real import, never during --dry-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_CSV_COLUMNS = [
    "file",
    "line_number",
    "original_line",
    "japanese_text",
    "english_text",
    "french_translation",
    "status",
    "notes",
]

REQUIRED_BATCH_KEYS = [
    "row_number",
    "file",
    "line_number",
    "japanese_text",
    "english_text",
    "french_translation",
    "status",
    "notes",
]

MAX_PRINTED_ERRORS = 50


@dataclass(frozen=True)
class BatchEntry:
    """A validated JSONL batch entry ready to compare with the CSV."""

    jsonl_line_number: int
    row_number: int
    file: str
    line_number: str
    french_translation: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a translated JSONL batch into rei_translation.csv."
    )
    parser.add_argument(
        "batch_jsonl",
        help="path to a translated JSONL batch file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report what would be imported without writing files",
    )
    return parser


def load_csv(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load the tracking CSV while preserving its original columns."""
    with csv_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    return fieldnames, rows


def check_csv_columns(fieldnames: list[str]) -> list[str]:
    """Return CSV header errors, if any."""
    missing = [column for column in REQUIRED_CSV_COLUMNS if column not in fieldnames]
    return [f"CSV is missing expected column: {column}" for column in missing]


def require_string(value: Any, key: str, jsonl_line_number: int) -> tuple[str, str | None]:
    """Read a required JSON string field."""
    if not isinstance(value, str):
        return "", f"JSONL line {jsonl_line_number}: {key} must be a string"
    return value, None


def parse_batch_entry(
    raw_entry: Any,
    jsonl_line_number: int,
) -> tuple[BatchEntry | None, list[str]]:
    """Validate one JSON object from the batch file."""
    errors: list[str] = []

    if not isinstance(raw_entry, dict):
        return None, [f"JSONL line {jsonl_line_number}: entry must be an object"]

    for key in REQUIRED_BATCH_KEYS:
        if key not in raw_entry:
            errors.append(f"JSONL line {jsonl_line_number}: missing key: {key}")

    row_number = raw_entry.get("row_number")
    if isinstance(row_number, bool) or not isinstance(row_number, int):
        errors.append(f"JSONL line {jsonl_line_number}: row_number must be an integer")
        parsed_row_number = -1
    else:
        parsed_row_number = row_number
        if parsed_row_number < 1:
            errors.append(
                f"JSONL line {jsonl_line_number}: row_number must be greater than zero"
            )

    file_value, file_error = require_string(
        raw_entry.get("file"), "file", jsonl_line_number
    )
    line_number_value, line_number_error = require_string(
        raw_entry.get("line_number"), "line_number", jsonl_line_number
    )
    french_value, french_error = require_string(
        raw_entry.get("french_translation"),
        "french_translation",
        jsonl_line_number,
    )

    for error in (file_error, line_number_error, french_error):
        if error:
            errors.append(error)

    if french_value.strip() == "":
        errors.append(
            f"JSONL line {jsonl_line_number}: french_translation must not be empty"
        )

    if errors:
        return None, errors

    return (
        BatchEntry(
            jsonl_line_number=jsonl_line_number,
            row_number=parsed_row_number,
            file=file_value,
            line_number=line_number_value,
            french_translation=french_value,
        ),
        [],
    )


def load_batch(batch_path: Path) -> tuple[list[BatchEntry], int, list[str]]:
    """Read and validate the JSONL batch syntax and required fields."""
    entries: list[BatchEntry] = []
    ignored = 0
    errors: list[str] = []

    with batch_path.open("r", encoding="utf-8") as input_file:
        for jsonl_line_number, line in enumerate(input_file, start=1):
            if line.strip() == "":
                ignored += 1
                continue

            try:
                raw_entry = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(
                    f"JSONL line {jsonl_line_number}: invalid JSON: {error.msg}"
                )
                continue

            entry, entry_errors = parse_batch_entry(raw_entry, jsonl_line_number)
            errors.extend(entry_errors)
            if entry is not None:
                entries.append(entry)

    return entries, ignored, errors


def validate_entries_against_csv(
    entries: list[BatchEntry],
    rows: list[dict[str, str]],
) -> list[str]:
    """Ensure every batch row points to the expected CSV row."""
    errors: list[str] = []
    seen_row_numbers: set[int] = set()

    for entry in entries:
        label = f"JSONL line {entry.jsonl_line_number}, row_number {entry.row_number}"

        if entry.row_number in seen_row_numbers:
            errors.append(f"{label}: duplicate row_number in batch")
            continue
        seen_row_numbers.add(entry.row_number)

        if entry.row_number > len(rows):
            errors.append(f"{label}: row_number is outside CSV data range")
            continue

        csv_row = rows[entry.row_number - 1]
        if csv_row.get("file") != entry.file:
            errors.append(
                f"{label}: file mismatch "
                f"(CSV {csv_row.get('file')!r}, batch {entry.file!r})"
            )
        if csv_row.get("line_number") != entry.line_number:
            errors.append(
                f"{label}: line_number mismatch "
                f"(CSV {csv_row.get('line_number')!r}, batch {entry.line_number!r})"
            )

    return errors


def create_backup(csv_path: Path, repo_root: Path) -> Path:
    """Copy the CSV before a real import."""
    backup_dir = repo_root / "translations" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"rei_translation_before_import_{timestamp}.csv"
    shutil.copy2(csv_path, backup_path)
    return backup_path


def apply_entries(entries: list[BatchEntry], rows: list[dict[str, str]]) -> None:
    """Update only french_translation and status for validated rows."""
    for entry in entries:
        csv_row = rows[entry.row_number - 1]
        csv_row["french_translation"] = entry.french_translation
        csv_row["status"] = "draft"


def write_csv(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Rewrite the CSV with its original header order."""
    with csv_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(
    imported: int,
    ignored: int,
    errors: list[str],
    dry_run: bool,
    backup_path: Path | None,
) -> None:
    """Print the required import summary."""
    if dry_run:
        print("Mode: dry-run")
        print(f"Lines importable: {imported}")
        print("Lines imported: 0")
    else:
        print("Mode: import")
        print(f"Lines imported: {imported}")

    print(f"Lines ignored: {ignored}")
    print(f"Errors: {len(errors)}")

    if backup_path is not None:
        print(f"Backup: {backup_path}")

    if errors:
        print("Error details:")
        for error in errors[:MAX_PRINTED_ERRORS]:
            print(f"  {error}")
        remaining = len(errors) - MAX_PRINTED_ERRORS
        if remaining > 0:
            print(f"  ... {remaining} more error(s) not shown")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    csv_path = repo_root / "translations" / "rei_translation.csv"
    batch_path = Path(args.batch_jsonl)
    if not batch_path.is_absolute():
        batch_path = repo_root / batch_path

    errors: list[str] = []
    ignored = 0
    entries: list[BatchEntry] = []

    if not csv_path.exists():
        errors.append(f"CSV does not exist: {csv_path}")
    if not batch_path.exists():
        errors.append(f"Batch JSONL does not exist: {batch_path}")

    fieldnames: list[str] = []
    rows: list[dict[str, str]] = []

    if not errors:
        try:
            fieldnames, rows = load_csv(csv_path)
            errors.extend(check_csv_columns(fieldnames))
            entries, ignored, batch_errors = load_batch(batch_path)
            errors.extend(batch_errors)
            if not errors:
                errors.extend(validate_entries_against_csv(entries, rows))
        except (OSError, csv.Error) as error:
            errors.append(str(error))

    if errors:
        print_summary(0, ignored, errors, args.dry_run, backup_path=None)
        return 1

    if args.dry_run:
        print_summary(len(entries), ignored, [], dry_run=True, backup_path=None)
        return 0

    backup_path: Path | None = None
    try:
        backup_path = create_backup(csv_path, repo_root)
        apply_entries(entries, rows)
        write_csv(csv_path, fieldnames, rows)
    except (OSError, csv.Error) as error:
        errors.append(str(error))
        print_summary(0, ignored, errors, args.dry_run, backup_path=backup_path)
        return 1

    print_summary(len(entries), ignored, [], dry_run=False, backup_path=backup_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
