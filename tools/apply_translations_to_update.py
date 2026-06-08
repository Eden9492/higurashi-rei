#!/usr/bin/env python3
"""Apply drafted CSV translations to Update/*.txt scripts.

This tool is intentionally conservative: it only replaces the English player
text argument inside OutputLine/OutputLineAll calls after confirming the current
source still matches the CSV original_line exactly.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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

OUTPUT_FUNCTIONS = {"OutputLine", "OutputLineAll"}
MAX_PRINTED_ERRORS = 50


@dataclass(frozen=True)
class TranslationRow:
    csv_row_number: int
    file: str
    line_number: int
    original_line: str
    japanese_text: str
    english_text: str
    french_translation: str


@dataclass(frozen=True)
class ArgSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Replacement:
    row: TranslationRow
    start: int
    end: int
    replacement_text: str

    @property
    def changes_text(self) -> bool:
        return self.end > self.start and self.replacement_text is not None


@dataclass(frozen=True)
class ScriptText:
    text: str
    encoding: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply draft translations from rei_translation.csv to Update scripts."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be modified without writing Update files",
    )
    parser.add_argument(
        "--file",
        dest="file_filter",
        help="limit application to one CSV file value, e.g. Update/batsukoishi01.txt",
    )
    return parser


def normalize_csv_file(value: str) -> str:
    return value.replace("\\", "/")


def read_script_text(path: Path) -> ScriptText:
    """Read a script and remember the encoding to use when writing it back."""
    data = path.read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        return ScriptText(data.decode("utf-8-sig"), "utf-8-sig")

    for encoding in ("utf-8", "cp932"):
        try:
            return ScriptText(data.decode(encoding), encoding)
        except UnicodeDecodeError:
            continue

    # If this happens, refuse later rather than silently corrupting a script.
    raise UnicodeDecodeError("utf-8", data, 0, 1, "unsupported script encoding")


def line_starts_for(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def is_identifier_start(char: str) -> bool:
    return char == "_" or char.isalpha()


def is_identifier_char(char: str) -> bool:
    return char == "_" or char.isalnum()


def skip_string(text: str, index: int) -> int:
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    return index


def find_call_end(text: str, open_paren: int) -> int:
    """Find the closing parenthesis for a C-like function call."""
    depth = 0
    index = open_paren

    while index < len(text):
        char = text[index]

        if char == '"':
            index = skip_string(text, index)
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            return len(text) if newline == -1 else newline
        if text.startswith("/*", index):
            end_comment = text.find("*/", index + 2)
            if end_comment == -1:
                return len(text)
            index = end_comment + 2
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index

        index += 1

    return -1


def find_output_call(source_span: str) -> tuple[str, int, int] | None:
    """Return (function_name, args_start, args_end) inside source_span."""
    index = 0
    while index < len(source_span):
        char = source_span[index]

        if char == '"':
            index = skip_string(source_span, index)
            continue
        if source_span.startswith("//", index):
            newline = source_span.find("\n", index)
            if newline == -1:
                return None
            index = newline + 1
            continue
        if source_span.startswith("/*", index):
            end_comment = source_span.find("*/", index + 2)
            if end_comment == -1:
                return None
            index = end_comment + 2
            continue
        if not is_identifier_start(char):
            index += 1
            continue

        name_start = index
        index += 1
        while index < len(source_span) and is_identifier_char(source_span[index]):
            index += 1
        name = source_span[name_start:index]

        if name not in OUTPUT_FUNCTIONS:
            continue

        probe = index
        while probe < len(source_span) and source_span[probe] in " \t\r\n":
            probe += 1
        if probe >= len(source_span) or source_span[probe] != "(":
            continue

        close_paren = find_call_end(source_span, probe)
        if close_paren == -1:
            return None
        return name, probe + 1, close_paren

    return None


def split_top_level_args(args_text: str, base_index: int) -> list[ArgSpan]:
    """Split arguments while preserving positions in the full source span."""
    args: list[ArgSpan] = []
    start = 0
    depth = 0
    index = 0

    while index < len(args_text):
        char = args_text[index]

        if char == '"':
            index = skip_string(args_text, index)
            continue
        if args_text.startswith("//", index):
            newline = args_text.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue
        if args_text.startswith("/*", index):
            end_comment = args_text.find("*/", index + 2)
            if end_comment == -1:
                break
            index = end_comment + 2
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(trim_arg(args_text, base_index, start, index))
            start = index + 1

        index += 1

    args.append(trim_arg(args_text, base_index, start, len(args_text)))
    return args


def trim_arg(args_text: str, base_index: int, start: int, end: int) -> ArgSpan:
    while start < end and args_text[start].isspace():
        start += 1
    while end > start and args_text[end - 1].isspace():
        end -= 1
    return ArgSpan(base_index + start, base_index + end, args_text[start:end])


def decode_c_string_literal(text: str, index: int) -> tuple[str, int]:
    assert text[index] == '"'
    index += 1
    result: list[str] = []
    escape_map = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "\\": "\\",
        '"': '"',
    }

    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                result.append("\\")
                return "".join(result), index + 1
            escaped = text[index + 1]
            result.append(escape_map.get(escaped, "\\" + escaped))
            index += 2
            continue
        if char == '"':
            return "".join(result), index + 1
        result.append(char)
        index += 1

    return "".join(result), index


def parse_c_string_literals(arg_text: str) -> str | None:
    """Decode NULL or pure C-like string literal arguments."""
    arg_text = arg_text.strip()
    if arg_text == "NULL" or arg_text == "":
        return None

    values: list[str] = []
    index = 0
    while index < len(arg_text):
        while index < len(arg_text) and arg_text[index].isspace():
            index += 1
        if index >= len(arg_text):
            break
        if arg_text[index] != '"':
            return None
        value, index = decode_c_string_literal(arg_text, index)
        values.append(value)

    return "".join(values) if values else None


def encode_c_string_literal(value: str) -> str:
    """Encode French text as one safe C-like string literal.

    Leading and trailing spaces are not stripped: they may be meaningful in the
    VN script. Only characters that would break the script string are escaped.
    """
    encoded = ['"']
    for char in value:
        if char == "\\":
            encoded.append("\\\\")
        elif char == '"':
            encoded.append('\\"')
        elif char == "\n":
            encoded.append("\\n")
        elif char == "\r":
            encoded.append("\\r")
        elif char == "\t":
            encoded.append("\\t")
        else:
            encoded.append(char)
    encoded.append('"')
    return "".join(encoded)


def select_english_arg_index(
    function_name: str,
    args: list[ArgSpan],
    row: TranslationRow,
) -> int | None:
    decoded = [parse_c_string_literals(arg.text) for arg in args]

    if function_name == "OutputLine":
        candidates = ((1, 3), (0, 2))
    elif function_name == "OutputLineAll":
        candidates = ((0, 1),)
    else:
        return None

    matches: list[int] = []
    for japanese_index, english_index in candidates:
        if japanese_index >= len(decoded) or english_index >= len(decoded):
            continue
        if (
            decoded[japanese_index] == row.japanese_text
            and decoded[english_index] == row.english_text
        ):
            matches.append(english_index)

    if len(matches) == 1:
        return matches[0]
    return None


def build_replacement_span(row: TranslationRow) -> tuple[str | None, str | None]:
    """Return (replacement_span, error) for one verified original_line span."""
    call = find_output_call(row.original_line)
    if call is None:
        return None, "could not find OutputLine/OutputLineAll in original_line"

    function_name, args_start, args_end = call
    args = split_top_level_args(row.original_line[args_start:args_end], args_start)
    english_index = select_english_arg_index(function_name, args, row)
    if english_index is None:
        return None, "could not identify the English text argument safely"

    english_arg = args[english_index]
    french_literal = encode_c_string_literal(row.french_translation)
    return (
        row.original_line[: english_arg.start]
        + french_literal
        + row.original_line[english_arg.end :],
        None,
    )


def load_csv_rows(
    csv_path: Path,
    file_filter: str | None,
) -> tuple[list[TranslationRow], list[str]]:
    errors: list[str] = []
    selected: list[TranslationRow] = []

    with csv_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for column in REQUIRED_CSV_COLUMNS:
            if column not in fieldnames:
                errors.append(f"CSV is missing expected column: {column}")
        if errors:
            return [], errors

        for csv_row_number, row in enumerate(reader, start=1):
            if row.get("status") != "draft":
                continue

            normalized_file = normalize_csv_file(row.get("file", ""))
            if file_filter is not None and normalized_file != file_filter:
                continue

            if row.get("french_translation", "") == "":
                errors.append(
                    f"CSV row {csv_row_number}: french_translation is empty"
                )
                continue

            try:
                line_number = int(row.get("line_number", ""))
            except ValueError:
                errors.append(f"CSV row {csv_row_number}: invalid line_number")
                continue
            if line_number < 1:
                errors.append(f"CSV row {csv_row_number}: invalid line_number")
                continue

            selected.append(
                TranslationRow(
                    csv_row_number=csv_row_number,
                    file=normalized_file,
                    line_number=line_number,
                    original_line=row.get("original_line", ""),
                    japanese_text=row.get("japanese_text", ""),
                    english_text=row.get("english_text", ""),
                    french_translation=row.get("french_translation", ""),
                )
            )

    return selected, errors


def resolve_update_file(repo_root: Path, relative_file: str) -> Path:
    if not relative_file.startswith("Update/") or not relative_file.endswith(".txt"):
        raise ValueError(f"CSV file is not an Update .txt path: {relative_file}")

    script_path = (repo_root / Path(relative_file)).resolve()
    update_root = (repo_root / "Update").resolve()
    if script_path != update_root and update_root not in script_path.parents:
        raise ValueError(f"CSV file escapes Update/: {relative_file}")
    return script_path


def plan_replacements(
    repo_root: Path,
    rows: list[TranslationRow],
) -> tuple[dict[str, ScriptText], dict[str, list[Replacement]], list[str]]:
    scripts: dict[str, ScriptText] = {}
    replacements_by_file: dict[str, list[Replacement]] = {}
    errors: list[str] = []

    for row in rows:
        try:
            script_path = resolve_update_file(repo_root, row.file)
        except ValueError as error:
            errors.append(f"CSV row {row.csv_row_number}: {error}")
            continue

        if row.file not in scripts:
            try:
                scripts[row.file] = read_script_text(script_path)
            except (OSError, UnicodeDecodeError) as error:
                errors.append(f"{row.file}: could not read script: {error}")
                continue

        script = scripts[row.file]
        line_starts = line_starts_for(script.text)
        if row.line_number > len(line_starts):
            errors.append(
                f"CSV row {row.csv_row_number}: line_number is outside {row.file}"
            )
            continue

        start = line_starts[row.line_number - 1]
        end = start + len(row.original_line)
        current_span = script.text[start:end]
        if current_span != row.original_line:
            errors.append(
                f"CSV row {row.csv_row_number}: original_line mismatch in {row.file}"
            )
            continue

        replacement_span, replacement_error = build_replacement_span(row)
        if replacement_error is not None or replacement_span is None:
            errors.append(f"CSV row {row.csv_row_number}: {replacement_error}")
            continue

        replacements_by_file.setdefault(row.file, []).append(
            Replacement(row=row, start=start, end=end, replacement_text=replacement_span)
        )

    errors.extend(check_overlaps(replacements_by_file))
    return scripts, replacements_by_file, errors


def check_overlaps(replacements_by_file: dict[str, list[Replacement]]) -> list[str]:
    errors: list[str] = []
    for relative_file, replacements in replacements_by_file.items():
        ordered = sorted(replacements, key=lambda item: item.start)
        for previous, current in zip(ordered, ordered[1:]):
            if previous.end > current.start:
                errors.append(
                    f"{relative_file}: overlapping replacements at CSV rows "
                    f"{previous.row.csv_row_number} and {current.row.csv_row_number}"
                )
    return errors


def apply_replacements(text: str, replacements: list[Replacement]) -> str:
    """Apply replacements from the end of the file to keep indexes stable."""
    updated = text
    for replacement in sorted(replacements, key=lambda item: item.start, reverse=True):
        updated = (
            updated[: replacement.start]
            + replacement.replacement_text
            + updated[replacement.end :]
        )
    return updated


def create_backups(
    repo_root: Path,
    files_to_write: list[str],
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = repo_root / "translations" / "update_backups" / timestamp
    for relative_file in files_to_write:
        source_path = resolve_update_file(repo_root, relative_file)
        backup_path = backup_root / relative_file
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, backup_path)
    return backup_root


def write_updated_scripts(
    repo_root: Path,
    scripts: dict[str, ScriptText],
    replacements_by_file: dict[str, list[Replacement]],
) -> list[str]:
    files_written: list[str] = []
    for relative_file, replacements in replacements_by_file.items():
        script = scripts[relative_file]
        updated_text = apply_replacements(script.text, replacements)
        if updated_text == script.text:
            continue

        # Encode before opening for write so failures happen before truncation.
        encoded = updated_text.encode(script.encoding)
        script_path = resolve_update_file(repo_root, relative_file)
        with script_path.open("wb") as output_file:
            output_file.write(encoded)
        files_written.append(relative_file)
    return files_written


def print_summary(
    applicable: int,
    changed_rows: int,
    modified_rows: int,
    errors: list[str],
    files_touched: list[str],
    dry_run: bool,
    backup_root: Path | None,
) -> None:
    print(f"Mode: {'dry-run' if dry_run else 'apply'}")
    print(f"Lines applicable: {applicable}")
    if dry_run:
        print(f"Lines that would change: {changed_rows}")
        print("Lines modified: 0")
    else:
        print(f"Lines modified: {modified_rows}")
    print(f"Errors: {len(errors)}")
    print(f"Files touched: {len(files_touched)}")
    for relative_file in files_touched:
        print(f"  {relative_file}")
    if backup_root is not None:
        print(f"Backup: {backup_root}")
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
    file_filter = normalize_csv_file(args.file_filter) if args.file_filter else None

    if not csv_path.exists():
        print(f"ERROR: CSV does not exist: {csv_path}", file=sys.stderr)
        return 1

    try:
        rows, csv_errors = load_csv_rows(csv_path, file_filter)
        scripts, replacements_by_file, plan_errors = plan_replacements(repo_root, rows)
    except (OSError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    errors = csv_errors + plan_errors
    changed_rows = sum(
        1
        for replacements in replacements_by_file.values()
        for replacement in replacements
        if replacement.replacement_text
        != scripts[replacement.row.file].text[replacement.start : replacement.end]
    )
    files_that_would_change = sorted(
        relative_file
        for relative_file, replacements in replacements_by_file.items()
        if any(
            replacement.replacement_text
            != scripts[replacement.row.file].text[replacement.start : replacement.end]
            for replacement in replacements
        )
    )

    if errors:
        print_summary(
            applicable=sum(len(items) for items in replacements_by_file.values()),
            changed_rows=changed_rows,
            modified_rows=0,
            errors=errors,
            files_touched=files_that_would_change,
            dry_run=args.dry_run,
            backup_root=None,
        )
        return 1

    if args.dry_run:
        print_summary(
            applicable=sum(len(items) for items in replacements_by_file.values()),
            changed_rows=changed_rows,
            modified_rows=0,
            errors=[],
            files_touched=files_that_would_change,
            dry_run=True,
            backup_root=None,
        )
        return 0

    backup_root: Path | None = None
    try:
        if files_that_would_change:
            backup_root = create_backups(repo_root, files_that_would_change)
        files_written = write_updated_scripts(repo_root, scripts, replacements_by_file)
    except (OSError, UnicodeEncodeError, ValueError) as error:
        print_summary(
            applicable=sum(len(items) for items in replacements_by_file.values()),
            changed_rows=changed_rows,
            modified_rows=0,
            errors=[str(error)],
            files_touched=[],
            dry_run=False,
            backup_root=backup_root,
        )
        return 1

    print_summary(
        applicable=sum(len(items) for items in replacements_by_file.values()),
        changed_rows=changed_rows,
        modified_rows=changed_rows,
        errors=[],
        files_touched=files_written,
        dry_run=False,
        backup_root=backup_root,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
