#!/usr/bin/env python3
"""Small workflow helper for Higurashi Rei translation batches."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


TRACKED_STATUSES = ["todo", "draft", "reviewed", "approved", "injected"]
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


def normalize_file(value: str) -> str:
    return value.replace("\\", "/")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--limit must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("--limit must be greater than zero")
    return parsed


def load_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        missing_columns = [column for column in CSV_COLUMNS if column not in fieldnames]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ValueError(f"CSV is missing expected column(s): {missing}")

        rows = []
        for row_number, row in enumerate(reader, start=1):
            row["_row_number"] = str(row_number)
            row["file"] = normalize_file(row.get("file", ""))
            rows.append(row)
    return rows


def rows_for_file(rows: list[dict[str, str]], file_filter: str) -> list[dict[str, str]]:
    normalized = normalize_file(file_filter)
    return [row for row in rows if row.get("file") == normalized]


def infer_arc_from_file(file_filter: str) -> str:
    stem = Path(normalize_file(file_filter)).stem
    inferred = re.sub(r"[_-]?\d+$", "", stem)
    return inferred or stem or "batch"


def next_batch_number(batch_dir: Path, arc: str) -> int:
    pattern = re.compile(rf"^{re.escape(arc)}_(\d{{4}})(?:_translated)?\.jsonl$")
    prompt_pattern = re.compile(rf"^{re.escape(arc)}_(\d{{4}})_prompt\.txt$")
    used_numbers: set[int] = set()

    if batch_dir.exists():
        for path in batch_dir.iterdir():
            match = pattern.match(path.name) or prompt_pattern.match(path.name)
            if match:
                used_numbers.add(int(match.group(1)))

    number = 1
    while number in used_numbers:
        number += 1
    return number


def batch_name_for(arc: str, number: int) -> str:
    return f"{arc}_{number:04d}.jsonl"


def prompt_name_for(arc: str, number: int) -> str:
    return f"{arc}_{number:04d}_prompt.txt"


def status_summary(rows: list[dict[str, str]], file_filter: str, batch_dir: Path) -> str:
    file_rows = rows_for_file(rows, file_filter)
    counts = Counter(row.get("status", "") for row in file_rows)
    todo_rows = [row for row in file_rows if row.get("status") == "todo"]
    non_todo_rows = [row for row in file_rows if row.get("status") != "todo"]

    total = len(file_rows)
    done = total - len(todo_rows)
    progress = (done / total * 100) if total else 0.0

    first_todo = todo_rows[0] if todo_rows else None
    last_non_todo = non_todo_rows[-1] if non_todo_rows else None
    arc = infer_arc_from_file(file_filter)
    suggested_number = next_batch_number(batch_dir, arc)

    if first_todo is None:
        suggested = "aucun batch nécessaire"
    else:
        suggested = f"translations/batches/{batch_name_for(arc, suggested_number)}"

    lines = [
        f"Fichier ciblé: {normalize_file(file_filter)}",
        f"Total lignes: {total}",
        f"todo: {counts['todo']}",
        f"draft: {counts['draft']}",
        f"reviewed: {counts['reviewed']}",
        f"approved: {counts['approved']}",
        f"injected: {counts['injected']}",
        "Premier row_number todo: "
        + (first_todo["_row_number"] if first_todo else "aucun"),
        "Première line_number todo: "
        + (first_todo.get("line_number", "") if first_todo else "aucune"),
        "Dernier row_number non-todo: "
        + (last_non_todo["_row_number"] if last_non_todo else "aucun"),
        f"Progression: {progress:.2f}%",
        f"Prochain batch suggéré: {suggested}",
    ]
    return "\n".join(lines)


def import_export_tool():
    tools_dir = Path(__file__).resolve().parent
    tools_dir_text = str(tools_dir)
    if tools_dir_text not in sys.path:
        sys.path.insert(0, tools_dir_text)
    import export_translation_batch

    return export_translation_batch


def generate_prompt(batch_path: Path, file_filter: str, arc: str) -> str:
    batch_content = batch_path.read_text(encoding="utf-8")
    return f"""Tu traduis un batch de Higurashi Rei en français.

Réponds uniquement avec du JSONL patch-only. Aucun commentaire, aucun Markdown.

Format obligatoire, un objet JSON par ligne :
{{"row_number":1,"french_translation":"...","notes":""}}

Règles JSONL patch-only :
- Ne recopie pas english_text, japanese_text, file, line_number, status.
- Chaque objet contient exactement row_number, french_translation, notes.
- row_number doit rester identique au batch source.
- french_translation est obligatoire et non vide.
- notes vaut "" sauf incertitude réelle à signaler.

Règles Saikoroshi sérieux :
- Ton sobre, tendu, introspectif quand le contexte est grave.
- Ne modernise pas le style et n'ajoute pas de familiarité gratuite.
- Préserve les ambiguïtés et les silences du texte source.
- Ne rends pas Rika idiote ; garde son double niveau enfantin/mystérieux.

Règles anti-honorifiques japonais :
- Ne garde pas -kun, -chan, -san, -sama, -sensei.
- Ne garde pas Keiichi-kun, Rena-chan, Rika-chan, Satoko-chan sauf preuve VF explicite.
- Si une convention VF est incertaine, indique l'incertitude dans notes.

Règles de continuation :
- Si english_text commence par un espace initial, garde normalement un espace initial dans french_translation.
- Si english_text finit par un espace voulu, conserve une séparation équivalente.
- Ne colle pas les phrases : éviter .A, !A, ?A.

Fichier ciblé : {normalize_file(file_filter)}
Arc : {arc}

Batch JSONL source :
{batch_content}"""


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(
            ["clip"],
            input=text,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def run_status(args: argparse.Namespace, repo_root: Path) -> int:
    csv_path = repo_root / "translations" / "rei_translation.csv"
    batch_dir = repo_root / "translations" / "batches"
    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}", file=sys.stderr)
        return 1

    try:
        rows = load_csv_rows(csv_path)
    except (OSError, csv.Error, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(status_summary(rows, args.file_filter, batch_dir))
    return 0


def run_next(args: argparse.Namespace, repo_root: Path) -> int:
    csv_path = repo_root / "translations" / "rei_translation.csv"
    batch_dir = repo_root / "translations" / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}", file=sys.stderr)
        return 1

    normalized_file = normalize_file(args.file_filter)
    try:
        rows = load_csv_rows(csv_path)
    except (OSError, csv.Error, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    todo_count = sum(
        1
        for row in rows
        if row.get("file") == normalized_file and row.get("status") == "todo"
    )
    if todo_count == 0:
        print(f"ERROR: no todo rows found for {normalized_file}", file=sys.stderr)
        return 1

    number = next_batch_number(batch_dir, args.arc)
    batch_name = batch_name_for(args.arc, number)
    prompt_name = prompt_name_for(args.arc, number)
    batch_path = batch_dir / batch_name
    prompt_path = batch_dir / prompt_name

    if batch_path.exists() or prompt_path.exists():
        print("ERROR: computed batch or prompt already exists", file=sys.stderr)
        return 1

    export_tool = import_export_tool()
    try:
        written = export_tool.export_batch(
            csv_path=csv_path,
            output_path=batch_path,
            limit=args.limit,
            file_filter=normalized_file,
        )
        export_tool.validate_jsonl(batch_path)
    except (OSError, ValueError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if written == 0:
        print(f"ERROR: exported zero rows for {normalized_file}", file=sys.stderr)
        return 1

    prompt_text = generate_prompt(batch_path, normalized_file, args.arc)
    prompt_path.write_text(prompt_text, encoding="utf-8")

    print(f"Batch créé: {batch_path}")
    print(f"Lignes exportées: {written}")
    print(f"Prompt créé: {prompt_path}")

    if args.copy_prompt:
        if copy_to_clipboard(prompt_text):
            print("Prompt copié dans le presse-papiers.")
        else:
            print(f"Impossible de copier avec clip. Prompt disponible ici: {prompt_path}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow helper for Rei translation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="show CSV progress for a file")
    status_parser.add_argument("--file", required=True, dest="file_filter")
    status_parser.set_defaults(func=run_status)

    next_parser = subparsers.add_parser("next", help="export the next todo batch")
    next_parser.add_argument("--file", required=True, dest="file_filter")
    next_parser.add_argument("--limit", type=positive_int, default=80)
    next_parser.add_argument("--arc", required=True)
    next_parser.add_argument("--copy-prompt", action="store_true")
    next_parser.set_defaults(func=run_next)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    return args.func(args, repo_root)


if __name__ == "__main__":
    sys.exit(main())
