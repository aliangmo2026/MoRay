#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Absolute-path enforcement utility for MoRay.

Every filesystem operation in this module is anchored to an absolute root
constant; no path in this file is relative, and callers are pushed toward
absolute output as well.

Usage:
    python D:\\MoRay\\scripts\\path_fix.py D:\\MoRay\\scripts
    python D:\\MoRay\\scripts\\path_fix.py --check D:\\MoRay\\server\\app\\api.py
    python D:\\MoRay\\scripts\\path_fix.py --expand "%MoRay%\\work"
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

# Absolute anchors. Never derive these from the current working directory.
MORAY_ROOT: Path = Path("D:/MoRay").resolve()
DEFAULT_BASE: Path = MORAY_ROOT
SCRIPTS_DIR: Path = Path("D:/MoRay/scripts").resolve()
WORKSPACE_DIR: Path = Path("D:/MoRay/workspace").resolve()


class PathFixError(Exception):
    """Raised when a path cannot be anchored to an absolute location."""


def to_absolute(raw_path: str, base: Optional[Path] = None) -> Path:
    """Return an absolute, normalized Path.

    Relative input is resolved against ``base`` (default: MORAY_ROOT), never
    against the process working directory. ``~`` and environment variables are
    expanded first, and the result must be synthetic-absolute after normpath.
    """
    if raw_path is None or str(raw_path).strip() == "":
        raise PathFixError("empty path is not allowed")

    if base is None:
        base = DEFAULT_BASE
    base_path = Path(base)
    if not base_path.is_absolute():
        raise PathFixError("base must be absolute: {0}".format(base_path))

    expanded = os.path.expanduser(os.path.expandvars(str(raw_path).strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = base_path / candidate

    # normpath collapses ".." without touching the filesystem;
    # resolve() would also hit symlinks and can fail on missing files.
    absolute = Path(os.path.normpath(str(candidate)))
    if not absolute.is_absolute():
        raise PathFixError("failed to produce an absolute path: {0}".format(raw_path))

    return absolute


def ensure_absolute(raw_path: str, base: Optional[Path] = None) -> str:
    """String form of :func:`to_absolute`, using the platform separator."""
    return str(to_absolute(raw_path, base))


def normalize_many(paths: Iterable[str], base: Optional[Path] = None) -> List[str]:
    """Normalize every path to absolute form, preserving input order."""
    return [ensure_absolute(item, base) for item in paths]


def is_absolute_text(raw_path: str) -> bool:
    """True when the input is already absolute after expansion."""
    if raw_path is None:
        return False
    expanded = os.path.expanduser(os.path.expandvars(str(raw_path).strip()))
    return os.path.isabs(expanded)


def is_within(child: str, parent: str) -> bool:
    """True when ``child`` lives under ``parent``; both must be absolute."""
    child_path = to_absolute(child)
    parent_path = to_absolute(parent)
    try:
        child_path.relative_to(parent_path)
    except ValueError:
        return False
    return True


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="path_fix.py",
        description="Normalize paths to absolute form anchored at D:\\MoRay.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="One or more paths to normalize (relative input is anchored at --base).",
    )
    parser.add_argument(
        "--base",
        default=str(DEFAULT_BASE),
        help="Absolute base directory for relative input (default: %(default)s).",
    )
    parser.add_argument(
        "--expand",
        action="store_true",
        help="Print expanded absolute paths only (default output mode).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 if any input was not already absolute.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_cli().parse_args(argv)

    try:
        base = to_absolute(args.base)
    except PathFixError as exc:
        sys.stderr.write("error: {0}\n".format(exc))
        return 2

    if args.check:
        offenders = [item for item in args.paths if not is_absolute_text(item)]
        for offender in offenders:
            sys.stderr.write("not absolute: {0}\n".format(offender))
        return 1 if offenders else 0

    exit_code = 0
    for item in args.paths:
        try:
            print(ensure_absolute(item, base))
        except PathFixError as exc:
            sys.stderr.write("error: {0}\n".format(exc))
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
