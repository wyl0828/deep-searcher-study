"""Reject hard-coded remote IPv4 addresses in tracked deployment scripts."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
ALLOWED_LOCAL_ADDRESSES = {"127.0.0.1", "0.0.0.0"}
SCRIPT_SUFFIXES = {".ps1", ".sh"}


def tracked_deployment_scripts(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", "deploy/server"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    paths = []
    for value in result.stdout.splitlines():
        path = (root / value).resolve()
        if path.suffix.lower() in SCRIPT_SUFFIXES and path.is_file():
            paths.append(path)
    return tuple(paths)


def forbidden_addresses(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {value for value in IPV4_PATTERN.findall(text) if value not in ALLOWED_LOCAL_ADDRESSES}
        )
    )


def find_violations(root: Path) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for path in tracked_deployment_scripts(root):
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            for address in forbidden_addresses(line):
                violations.append((path, line_number, address))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    violations = find_violations(args.root.resolve())
    if violations:
        for path, line_number, address in violations:
            print(f"{path}:{line_number}: hard-coded remote IPv4 address: {address}")
        return 1
    print("deployment config IPv4 check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
