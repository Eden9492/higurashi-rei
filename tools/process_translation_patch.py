#!/usr/bin/env python3
"""Process one translated JSONL patch end-to-end.

This orchestrates the existing import/apply tools:
1. validate paths,
2. dry-run import,
3. run patch QA,
4. real import into rei_translation.csv,
5. dry-run apply to one Update file,
6. real apply to that same Update file.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PATCH_KEYS = {"row_number", "french_translation", "notes"}
FORBIDDEN_PATCH_KEYS = {"english_text", "japanese_text", "file", "line_number", "status"}
HONORIFIC_RE = re.compile(r"(?i)-(kun|chan|san|sama|sensei)\b")
SPECIFIC_HONORIFICS_RE = re.compile(
    r"\b(Keiichi-kun|Rena-chan|Rika-chan|Satoko-chan)\b"
)
ENGLISH_QUOTE_RE = re.compile(r'["“”]')
COLLAGE_RE = re.compile(r"[.!?][A-Za-zÀ-ÖØ-öø-ÿ]")
COLOR_TAG_RE = re.compile(r"</color>|<color=#[0-9A-Fa-f]{6}>")


@dataclass(frozen=True)
class PatchRecord:
    jsonl_line_number: int
    row_number: int
    french_translation: str
    notes: str


@dataclass(frozen=True)
class QualityProblem:
    row_number: str
    problem: str
    french_translation: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a translated patch and apply it to one Update script."
    )
    parser.add_argument(
        "--patch",
        required=True,
        help="translated JSONL patch, e.g. translations/batches/saikoroshi_0001_translated.jsonl",
    )
    parser.add_argument(
        "--file",
        required=True,
        dest="update_file",
        help="target Update file, e.g. Update/saikoroshi00.txt",
    )
    return parser


def normalize_update_file(value: str) -> str:
    return value.replace("\\", "/")


def resolve_inside_repo(repo_root: Path, user_path: str) -> Path:
    path = Path(user_path)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def validate_target_update_file(repo_root: Path, update_file: str) -> tuple[Path | None, str | None]:
    normalized = normalize_update_file(update_file)
    if not normalized.startswith("Update/") or not normalized.endswith(".txt"):
        return None, "--file must point to an Update/*.txt script"

    update_root = (repo_root / "Update").resolve()
    target_path = resolve_inside_repo(repo_root, normalized)
    if target_path != update_root and update_root not in target_path.parents:
        return None, "--file escapes the Update/ directory"
    if not target_path.exists():
        return None, f"Update file does not exist: {target_path}"
    return target_path, None


def load_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    required = {
        "file",
        "line_number",
        "original_line",
        "japanese_text",
        "english_text",
        "french_translation",
        "status",
        "notes",
    }
    errors = [
        f"CSV is missing expected column: {column}"
        for column in sorted(required - set(fieldnames))
    ]
    return rows, errors


def load_patch_for_qa(patch_path: Path) -> tuple[list[PatchRecord], list[QualityProblem]]:
    records: list[PatchRecord] = []
    problems: list[QualityProblem] = []
    physical_lines = 0
    non_empty_lines = 0

    with patch_path.open("r", encoding="utf-8-sig") as input_file:
        for jsonl_line_number, line in enumerate(input_file, start=1):
            physical_lines += 1
            if line.strip() == "":
                problems.append(
                    QualityProblem(
                        row_number="?",
                        problem=f"empty JSONL line at physical line {jsonl_line_number}",
                        french_translation="",
                    )
                )
                continue

            non_empty_lines += 1
            try:
                raw_record = json.loads(line)
            except json.JSONDecodeError as error:
                problems.append(
                    QualityProblem(
                        row_number="?",
                        problem=(
                            f"invalid JSON on line {jsonl_line_number}: "
                            f"{error.msg}"
                        ),
                        french_translation="",
                    )
                )
                continue

            record, record_problems = parse_patch_record(raw_record, jsonl_line_number)
            problems.extend(record_problems)
            if record is not None:
                records.append(record)

    if physical_lines == 0 or non_empty_lines == 0:
        problems.append(
            QualityProblem(
                row_number="?",
                problem="invalid JSONL line count: patch contains no records",
                french_translation="",
            )
        )
    if records and len(records) != non_empty_lines:
        problems.append(
            QualityProblem(
                row_number="?",
                problem=(
                    "invalid JSONL line count: parsed record count does not match "
                    "non-empty line count"
                ),
                french_translation="",
            )
        )

    return records, problems


def parse_patch_record(
    raw_record: Any,
    jsonl_line_number: int,
) -> tuple[PatchRecord | None, list[QualityProblem]]:
    problems: list[QualityProblem] = []
    row_number_label = "?"
    french_translation = ""

    if not isinstance(raw_record, dict):
        return (
            None,
            [
                QualityProblem(
                    row_number=row_number_label,
                    problem=f"line {jsonl_line_number}: JSON object expected",
                    french_translation=french_translation,
                )
            ],
        )

    raw_row_number = raw_record.get("row_number")
    if isinstance(raw_row_number, int) and not isinstance(raw_row_number, bool):
        row_number_label = str(raw_row_number)

    raw_french = raw_record.get("french_translation")
    if isinstance(raw_french, str):
        french_translation = raw_french

    keys = set(raw_record)
    if keys != PATCH_KEYS:
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem=(
                    "object keys must be exactly: "
                    "row_number, french_translation, notes"
                ),
                french_translation=french_translation,
            )
        )

    for forbidden_key in sorted(keys & FORBIDDEN_PATCH_KEYS):
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem=f"forbidden key in patch: {forbidden_key}",
                french_translation=french_translation,
            )
        )

    if isinstance(raw_row_number, bool) or not isinstance(raw_row_number, int):
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem="row_number must be an integer",
                french_translation=french_translation,
            )
        )
        parsed_row_number = -1
    else:
        parsed_row_number = raw_row_number
        if parsed_row_number < 1:
            problems.append(
                QualityProblem(
                    row_number=row_number_label,
                    problem="row_number must be greater than zero",
                    french_translation=french_translation,
                )
            )

    if not isinstance(raw_french, str):
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem="french_translation must be a string",
                french_translation=french_translation,
            )
        )
    elif raw_french.strip() == "":
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem="french_translation must not be empty",
                french_translation=french_translation,
            )
        )

    raw_notes = raw_record.get("notes")
    if "notes" in raw_record and not isinstance(raw_notes, str):
        problems.append(
            QualityProblem(
                row_number=row_number_label,
                problem="notes must be a string",
                french_translation=french_translation,
            )
        )

    if problems:
        return None, problems

    return (
        PatchRecord(
            jsonl_line_number=jsonl_line_number,
            row_number=parsed_row_number,
            french_translation=french_translation,
            notes=raw_notes,
        ),
        [],
    )


def extract_color_tags(text: str) -> list[str]:
    return COLOR_TAG_RE.findall(text)


def has_broken_color_markup(text: str) -> bool:
    cleaned = COLOR_TAG_RE.sub("", text)
    return "<color" in cleaned or "</color" in cleaned


def check_french_text(record: PatchRecord) -> list[QualityProblem]:
    text = record.french_translation
    row_number = str(record.row_number)
    problems: list[QualityProblem] = []

    for match in HONORIFIC_RE.finditer(text):
        problems.append(
            QualityProblem(row_number, f"Japanese honorific remains: {match.group(0)}", text)
        )
    for match in SPECIFIC_HONORIFICS_RE.finditer(text):
        problems.append(
            QualityProblem(row_number, f"specific honorific remains: {match.group(0)}", text)
        )
    if ENGLISH_QUOTE_RE.search(text):
        problems.append(
            QualityProblem(row_number, "suspicious English quote in french_translation", text)
        )
    for match in COLLAGE_RE.finditer(text):
        problems.append(
            QualityProblem(row_number, f"suspect punctuation collage: {match.group(0)}", text)
        )
    if has_broken_color_markup(text):
        problems.append(QualityProblem(row_number, "broken <color=#...> markup", text))

    return problems


def run_patch_qa(
    patch_path: Path,
    csv_path: Path,
    target_file: str,
) -> tuple[int, list[QualityProblem]]:
    csv_rows, csv_errors = load_csv_rows(csv_path)
    problems = [
        QualityProblem(row_number="?", problem=error, french_translation="")
        for error in csv_errors
    ]

    records, patch_problems = load_patch_for_qa(patch_path)
    problems.extend(patch_problems)

    seen_row_numbers: set[int] = set()
    normalized_target = normalize_update_file(target_file)

    for record in records:
        problems.extend(check_french_text(record))

        if record.row_number in seen_row_numbers:
            problems.append(
                QualityProblem(
                    row_number=str(record.row_number),
                    problem="duplicate row_number in patch",
                    french_translation=record.french_translation,
                )
            )
            continue
        seen_row_numbers.add(record.row_number)

        if record.row_number > len(csv_rows):
            problems.append(
                QualityProblem(
                    row_number=str(record.row_number),
                    problem="row_number is outside CSV data range",
                    french_translation=record.french_translation,
                )
            )
            continue

        csv_row = csv_rows[record.row_number - 1]
        csv_file = normalize_update_file(csv_row.get("file", ""))
        if csv_file != normalized_target:
            problems.append(
                QualityProblem(
                    row_number=str(record.row_number),
                    problem=(
                        f"row belongs to {csv_file}, not target file "
                        f"{normalized_target}"
                    ),
                    french_translation=record.french_translation,
                )
            )

        expected_tags = extract_color_tags(csv_row.get("english_text", ""))
        actual_tags = extract_color_tags(record.french_translation)
        if expected_tags and actual_tags != expected_tags:
            problems.append(
                QualityProblem(
                    row_number=str(record.row_number),
                    problem=(
                        "modified or missing <color=#...> tags "
                        f"(expected {expected_tags}, got {actual_tags})"
                    ),
                    french_translation=record.french_translation,
                )
            )
        elif not expected_tags and actual_tags:
            problems.append(
                QualityProblem(
                    row_number=str(record.row_number),
                    problem=f"unexpected <color=#...> tags: {actual_tags}",
                    french_translation=record.french_translation,
                )
            )

    return len(records), problems


def print_quality_problems(problems: list[QualityProblem]) -> None:
    print(f"Erreurs QA: {len(problems)}")
    for problem in problems:
        print(
            "  "
            f"row_number={problem.row_number} | "
            f"{problem.problem} | "
            f"french_translation={problem.french_translation!r}"
        )


def run_import_tool(patch_path: Path, dry_run: bool) -> int:
    import_translation_batch = import_existing_tool("import_translation_batch")
    args = [str(patch_path)]
    if dry_run:
        args.append("--dry-run")
    return import_translation_batch.main(args)


def run_apply_tool(update_file: str, dry_run: bool) -> int:
    apply_translations_to_update = import_existing_tool("apply_translations_to_update")
    args = ["--file", update_file]
    if dry_run:
        args.insert(0, "--dry-run")
    return apply_translations_to_update.main(args)


def import_existing_tool(module_name: str):
    tools_dir = Path(__file__).resolve().parent
    tools_dir_text = str(tools_dir)
    if tools_dir_text not in sys.path:
        sys.path.insert(0, tools_dir_text)
    return __import__(module_name)


def print_final_summary(
    patch_path: Path,
    update_file: str,
    imported_lines: int,
    qa_errors: int,
) -> None:
    print("Résumé final:")
    print(f"  Patch traité: {patch_path}")
    print(f"  Fichier Update traité: {update_file}")
    print(f"  Lignes importées: {imported_lines}")
    print(f"  Erreurs QA: {qa_errors}")
    print(f"  Fichier prêt à copier dans le jeu: {update_file}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    patch_path = resolve_inside_repo(repo_root, args.patch)
    update_file = normalize_update_file(args.update_file)
    csv_path = repo_root / "translations" / "rei_translation.csv"

    if not patch_path.exists():
        print(f"ERROR: patch file does not exist: {patch_path}", file=sys.stderr)
        return 1

    _, update_error = validate_target_update_file(repo_root, update_file)
    if update_error is not None:
        print(f"ERROR: {update_error}", file=sys.stderr)
        return 1

    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}", file=sys.stderr)
        return 1

    print("Étape 1: dry-run import du patch")
    if run_import_tool(patch_path, dry_run=True) != 0:
        print("Arrêt: le dry-run d'import a échoué.")
        return 1

    print("Étape 2: contrôle qualité du patch")
    importable_lines, qa_problems = run_patch_qa(patch_path, csv_path, update_file)
    if qa_problems:
        print_quality_problems(qa_problems)
        print("Corriger le patch avant import.")
        return 1
    print("Erreurs QA: 0")

    print("Étape 3: import réel du patch dans rei_translation.csv")
    if run_import_tool(patch_path, dry_run=False) != 0:
        print("Arrêt: l'import réel a échoué.")
        return 1

    print("Étape 4: dry-run application vers Update")
    if run_apply_tool(update_file, dry_run=True) != 0:
        print("Arrêt: le dry-run d'application a échoué.")
        return 1

    print("Étape 5: application réelle vers Update")
    if run_apply_tool(update_file, dry_run=False) != 0:
        print("Arrêt: l'application réelle a échoué.")
        return 1

    print_final_summary(
        patch_path=patch_path,
        update_file=update_file,
        imported_lines=importable_lines,
        qa_errors=0,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
