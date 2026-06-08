#!/usr/bin/env python3
"""Validate translations/rei_translation.csv without modifying any files."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


EXPECTED_COLUMNS = [
    "file",
    "line_number",
    "original_line",
    "japanese_text",
    "english_text",
    "french_translation",
    "status",
    "notes",
]

VALID_STATUSES = {
    "todo",
    "draft",
    "reviewed",
    "approved",
    "injected",
    "issue",
}

MAX_PRINTED_PROBLEMS = 50


def has_control_character(value: str) -> bool:
    """Return True for raw control characters that do not belong in a CSV cell."""
    return any(ord(char) < 32 for char in value)


def check_french_translation(value: str) -> list[str]:
    """Validate a French translation cell.

    Empty is valid because untranslated rows are expected. A filled translation
    is considered clean if it is not whitespace-only, has no leading/trailing
    whitespace, and contains no raw control characters. If line breaks are ever
    needed in game text, they should be represented intentionally in the script
    reinjection step rather than as accidental CSV cell breaks.
    """
    problems = []

    if value == "":
        return problems

    if value.strip() == "":
        problems.append("french_translation is whitespace-only")
    if value != value.strip():
        problems.append("french_translation has leading/trailing whitespace")
    if has_control_character(value):
        problems.append("french_translation contains a control character")

    return problems


def check_required_text(value: str | None, column: str) -> list[str]:
    if value is None or value == "":
        return [f"{column} is empty"]
    return []


def validate_csv(csv_path: Path) -> int:
    problems: list[tuple[str, str]] = []
    problematic_rows: set[int] = set()
    status_counts: Counter[str] = Counter()
    total_rows = 0

    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}")
        return 1

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            fieldnames = reader.fieldnames or []
            missing_columns = [
                column for column in EXPECTED_COLUMNS if column not in fieldnames
            ]

            for column in missing_columns:
                problems.append(("header", f"missing expected column: {column}"))

            for data_row_number, row in enumerate(reader, start=1):
                total_rows += 1
                csv_row_number = data_row_number + 1
                row_problems: list[str] = []

                file_value = row.get("file")
                line_number_value = row.get("line_number")
                original_line_value = row.get("original_line")
                status_value = row.get("status", "")
                french_value = row.get("french_translation", "")

                row_problems.extend(check_required_text(file_value, "file"))
                row_problems.extend(
                    check_required_text(line_number_value, "line_number")
                )
                row_problems.extend(
                    check_required_text(original_line_value, "original_line")
                )

                if status_value not in VALID_STATUSES:
                    row_problems.append(f"invalid status: {status_value!r}")
                else:
                    status_counts[status_value] += 1

                if french_value is None:
                    row_problems.append("french_translation column is missing")
                else:
                    row_problems.extend(check_french_translation(french_value))

                if row_problems:
                    problematic_rows.add(csv_row_number)
                    row_label = f"row {csv_row_number}"
                    for problem in row_problems:
                        problems.append((row_label, problem))
    except csv.Error as error:
        print(f"ERROR: Could not parse CSV: {error}")
        return 1
    except OSError as error:
        print(f"ERROR: Could not read CSV: {error}")
        return 1

    print(f"CSV: {csv_path}")
    print(f"Total rows: {total_rows}")
    print("Rows by status:")
    for status in sorted(VALID_STATUSES):
        print(f"  {status}: {status_counts[status]}")
    print(f"Problematic rows: {len(problematic_rows)}")

    header_problem_count = sum(1 for row_label, _ in problems if row_label == "header")
    if header_problem_count:
        print(f"Header problems: {header_problem_count}")

    if problems:
        print("Problems:")
        for row_label, problem in problems[:MAX_PRINTED_PROBLEMS]:
            print(f"  {row_label}: {problem}")
        remaining = len(problems) - MAX_PRINTED_PROBLEMS
        if remaining > 0:
            print(f"  ... {remaining} more problem(s) not shown")
        return 1

    print("Problems: 0")
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    csv_path = repo_root / "translations" / "rei_translation.csv"
    return validate_csv(csv_path)


if __name__ == "__main__":
    sys.exit(main())
