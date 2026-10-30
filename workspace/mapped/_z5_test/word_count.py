#!/usr/bin/env python3
"""word_count.py - report line, word and character counts for text files.

A small dependency-free take on the classic ``wc`` utility.  Counts are
written to stdout, one line per input file, in the order the files were
given.  Problems with individual files are reported on stderr and do not
stop the remaining files from being counted.

Exit codes:
    0   every file was counted successfully
    1   at least one file could not be read
    2   the command line itself was invalid (bad option, no file given)
"""

import argparse
import codecs
import os
import sys
from typing import NamedTuple, Optional, Sequence

PROG = "word_count.py"
VERSION = "1.0.0"

EXIT_OK = 0
EXIT_FILE_ERROR = 1
EXIT_USAGE_ERROR = 2


class Counts(NamedTuple):
    """The three numbers reported for a single file."""

    lines: int
    words: int
    chars: int


class FileError(Exception):
    """A problem with one input file that is worth showing to the user."""


def count_text(text: str) -> Counts:
    """Count logical lines, whitespace-separated words and characters.

    Lines are counted the way ``str.splitlines()`` splits them, so a final
    line without a trailing newline is still counted as a line.  Words are
    runs of non-whitespace characters.  Characters are Unicode code points,
    not bytes.
    """
    return Counts(
        lines=len(text.splitlines()),
        words=len(text.split()),
        chars=len(text),
    )


def read_bytes(path: str) -> bytes:
    """Read *path* in binary mode, translating OS errors into FileError."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        raise FileError("no such file or directory")
    except IsADirectoryError:
        raise FileError("is a directory, not a regular file")
    except PermissionError:
        raise FileError("permission denied")
    except OSError as exc:
        raise FileError(exc.strerror or str(exc))


def count_file(path: str, encoding: str = "utf-8", count_bytes: bool = False) -> Counts:
    """Return the counts for *path*.

    Raises FileError with a human-readable reason for anything that stops
    the file from being counted.  When *count_bytes* is true the third
    number is the size of the file in bytes rather than its character
    count.
    """
    # Windows reports directories as "permission denied" when opened, and
    # on other platforms the message is nicer this way round too.
    if os.path.isdir(path):
        raise FileError("is a directory, not a regular file")

    raw = read_bytes(path)

    try:
        text = raw.decode(encoding)
    except LookupError:
        raise FileError("unknown encoding {!r}".format(encoding))
    except UnicodeDecodeError:
        raise FileError(
            "cannot decode as {}; pass a different --encoding".format(encoding)
        )

    counts = count_text(text)
    if count_bytes:
        counts = counts._replace(chars=len(raw))
    return counts


def format_counts(path: str, counts: Counts, unit: str) -> str:
    """Render one report line for *path*."""
    return "{}: {} lines, {} words, {} {}".format(
        path, counts.lines, counts.words, counts.chars, unit
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Count lines, words and characters in one or more text files. "
            "One line of output is printed per file, in the order given."
        ),
        epilog=(
            "exit codes:\n"
            "  0  all files counted successfully\n"
            "  1  at least one file could not be read\n"
            "  2  invalid command line (bad option, no file given)\n"
            "\n"
            "example:\n"
            "  %(prog)s notes.txt README.md\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        metavar="FILE",
        nargs="+",
        help="file(s) to count; at least one is required",
    )
    parser.add_argument(
        "-e",
        "--encoding",
        default="utf-8",
        metavar="NAME",
        help="text encoding used to read the files (default: utf-8)",
    )
    parser.add_argument(
        "-b",
        "--bytes",
        action="store_true",
        dest="count_bytes",
        help="report the file size in bytes instead of a character count",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s {}".format(VERSION),
        help="show the program version and exit",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command line tool and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        codecs.lookup(args.encoding)
    except LookupError:
        parser.error("unknown encoding: {!r}".format(args.encoding))

    unit = "bytes" if args.count_bytes else "chars"
    failures = 0

    for path in args.paths:
        try:
            counts = count_file(path, args.encoding, args.count_bytes)
        except FileError as exc:
            failures += 1
            print("{}: error: {}: {}".format(PROG, path, exc), file=sys.stderr)
            continue
        print(format_counts(path, counts, unit))

    return EXIT_FILE_ERROR if failures else EXIT_OK


def run() -> None:
    """Entry point that turns main()'s return value into an exit status."""
    sys.exit(main())


if __name__ == "__main__":
    run()
