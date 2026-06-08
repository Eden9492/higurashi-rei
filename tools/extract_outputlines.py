#!/usr/bin/env python3
"""Extract player-facing OutputLine text into a translation tracking CSV.

This tool is intentionally read-only for Update/: it only scans .txt scripts and
writes translations/rei_translation.csv.
"""

from __future__ import annotations

import bisect
import csv
from pathlib import Path


OUTPUT_CSV_COLUMNS = [
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


def read_script_text(path: Path) -> str:
    """Read a script while preserving its original line endings.

    The current Update/ files are UTF-8. A CP932 fallback is kept because some
    Higurashi-era scripts may come from Japanese Windows tooling. If neither
    decoding succeeds, the final UTF-8 replacement pass prevents the extractor
    from crashing, but any replacement characters should be reviewed manually.
    """
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def line_starts_for(text: str) -> list[int]:
    """Return absolute indexes for each line start in text."""
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def line_number_for(line_starts: list[int], index: int) -> int:
    """Convert a character index to a 1-based source line number."""
    return bisect.bisect_right(line_starts, index)


def source_line_span(text: str, start: int, end: int) -> str:
    """Keep the complete source line span that contains a function call.

    For multi-line OutputLine calls, this preserves the original indentation and
    internal line breaks. Only the final physical newline is omitted so the CSV
    cell does not gain an extra blank line.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def is_identifier_start(char: str) -> bool:
    return char == "_" or char.isalpha()


def is_identifier_char(char: str) -> bool:
    return char == "_" or char.isalnum()


def skip_string(text: str, index: int) -> int:
    """Skip a double-quoted C-like string starting at index."""
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


def find_call_end(text: str, open_paren: int) -> tuple[int, int]:
    """Return (close_paren_index, end_index) for a function call.

    Parentheses inside strings and comments are ignored. end_index includes a
    following semicolon when present.
    """
    depth = 0
    index = open_paren
    while index < len(text):
        char = text[index]

        if char == '"':
            index = skip_string(text, index)
            continue

        if text.startswith("//", index):
            newline = text.find("\n", index)
            if newline == -1:
                return len(text) - 1, len(text)
            index = newline + 1
            continue

        if text.startswith("/*", index):
            end_comment = text.find("*/", index + 2)
            if end_comment == -1:
                return len(text) - 1, len(text)
            index = end_comment + 2
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end_index = index + 1
                probe = end_index
                while probe < len(text) and text[probe] in " \t":
                    probe += 1
                if probe < len(text) and text[probe] == ";":
                    end_index = probe + 1
                return index, end_index

        index += 1

    return len(text) - 1, len(text)


def iter_output_calls(text: str):
    """Yield OutputLine/OutputLineAll calls found outside comments and strings.

    This deliberately does not try to extract unrelated player text such as
    Select() choice arrays. Those may need a separate extractor once their
    formats are reviewed.
    """
    starts = line_starts_for(text)
    index = 0
    while index < len(text):
        char = text[index]

        if char == '"':
            index = skip_string(text, index)
            continue

        if text.startswith("//", index):
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue

        if text.startswith("/*", index):
            end_comment = text.find("*/", index + 2)
            if end_comment == -1:
                break
            index = end_comment + 2
            continue

        if not is_identifier_start(char):
            index += 1
            continue

        name_start = index
        index += 1
        while index < len(text) and is_identifier_char(text[index]):
            index += 1
        name = text[name_start:index]

        if name not in OUTPUT_FUNCTIONS:
            continue

        probe = index
        while probe < len(text) and text[probe] in " \t\r\n":
            probe += 1
        if probe >= len(text) or text[probe] != "(":
            continue

        close_paren, end_index = find_call_end(text, probe)
        yield {
            "name": name,
            "line_number": line_number_for(starts, name_start),
            "call_text": text[name_start:end_index],
            "source_span": source_line_span(text, name_start, end_index),
            "args_text": text[probe + 1 : close_paren],
        }
        index = end_index


def split_top_level_args(args_text: str) -> list[str]:
    """Split a function argument list without splitting inside strings/calls."""
    args = []
    start = 0
    depth = 0
    index = 0

    while index < len(args_text):
        char = args_text[index]

        if char == '"':
            index = skip_string(args_text, index)
            continue

        if text_startswith(args_text, "//", index):
            newline = args_text.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue

        if text_startswith(args_text, "/*", index):
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
            args.append(args_text[start:index].strip())
            start = index + 1

        index += 1

    args.append(args_text[start:].strip())
    return args


def text_startswith(text: str, prefix: str, index: int) -> bool:
    """Tiny wrapper used to keep the argument splitter readable."""
    return text.startswith(prefix, index)


def parse_c_string_literals(arg: str) -> str | None:
    """Return decoded text for a NULL or pure C-like string literal argument."""
    arg = arg.strip()
    if arg == "NULL":
        return None
    if not arg:
        return None

    values = []
    index = 0
    while index < len(arg):
        while index < len(arg) and arg[index].isspace():
            index += 1
        if index >= len(arg):
            break
        if arg[index] != '"':
            return None

        value, index = decode_c_string_literal(arg, index)
        values.append(value)

    if not values:
        return None
    return "".join(values)


def decode_c_string_literal(text: str, index: int) -> tuple[str, int]:
    """Decode one double-quoted C-like string literal."""
    assert text[index] == '"'
    index += 1
    result = []

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


def is_visible_text(value: str | None) -> bool:
    """Reject empty strings and pure control whitespace such as '\\n'."""
    return value is not None and bool(value.strip())


def text_pair_for_call(function_name: str, args: list[str]) -> tuple[str, str] | None:
    """Extract the Japanese/English text pair from a supported output call."""
    decoded = [parse_c_string_literals(arg) for arg in args]

    if function_name == "OutputLine":
        # Common forms:
        # OutputLine(NULL, "jp text", NULL, "en text", mode)
        # OutputLine("jp speaker", NULL, "en speaker", NULL, mode)
        candidates = ((1, 3), (0, 2))
    elif function_name == "OutputLineAll":
        # OutputLineAll usually has (jp, en, mode). Most calls are empty or
        # newline-only technical spacing and are filtered out below.
        candidates = ((0, 1),)
    else:
        return None

    for japanese_index, english_index in candidates:
        japanese = decoded[japanese_index] if japanese_index < len(decoded) else None
        english = decoded[english_index] if english_index < len(decoded) else None
        if is_visible_text(japanese) or is_visible_text(english):
            return japanese or "", english or ""

    return None


def build_rows(repo_root: Path) -> list[dict[str, str]]:
    update_dir = repo_root / "Update"
    rows = []

    for script_path in sorted(update_dir.rglob("*.txt")):
        text = read_script_text(script_path)
        relative_file = script_path.relative_to(repo_root).as_posix()

        for call in iter_output_calls(text):
            args = split_top_level_args(call["args_text"])
            text_pair = text_pair_for_call(call["name"], args)
            if text_pair is None:
                continue

            japanese_text, english_text = text_pair
            rows.append(
                {
                    "file": relative_file,
                    "line_number": str(call["line_number"]),
                    "original_line": call["source_span"],
                    "japanese_text": japanese_text,
                    "english_text": english_text,
                    "french_translation": "",
                    "status": "todo",
                    "notes": "",
                }
            )

    return rows


def write_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=OUTPUT_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    csv_path = repo_root / "translations" / "rei_translation.csv"
    rows = build_rows(repo_root)
    write_csv(csv_path, rows)
    print(f"Wrote {len(rows)} rows to {csv_path}")


if __name__ == "__main__":
    main()
