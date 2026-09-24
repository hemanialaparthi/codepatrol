"""Command-line entry point for CodePatrol."""

import argparse
import sys
from pathlib import Path

from codepatrol import __version__
from codepatrol.errors import CodePatrolError
from codepatrol.languages import default_registry
from codepatrol.policy import compile_policies, load_policies
from codepatrol.reporting import render_terminal_report
from codepatrol.scanner import ProjectScanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codepatrol",
        description="Scan source code for configurable structural policy violations.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan a project directory")
    scan_parser.add_argument("project", type=Path, help="project directory to scan")
    scan_parser.add_argument(
        "--policies",
        required=True,
        type=Path,
        help="YAML policy file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_scan(args.project, args.policies)
    except CodePatrolError as exc:
        print(f"CodePatrol error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"CodePatrol error: {exc}", file=sys.stderr)
        return 2


def _run_scan(project: Path, policy_file: Path) -> int:
    if not project.exists():
        raise CodePatrolError(f"Project does not exist: '{project}'.")
    if not project.is_dir():
        raise CodePatrolError(f"Project is not a directory: '{project}'.")
    if not policy_file.exists():
        raise CodePatrolError(f"Policy file does not exist: '{policy_file}'.")
    if not policy_file.is_file():
        raise CodePatrolError(f"Policy path is not a file: '{policy_file}'.")

    registry = default_registry()
    policies = load_policies(policy_file, registry)
    compiled_policies = compile_policies(policies, registry)
    result = ProjectScanner(registry).scan(project, compiled_policies)
    print(render_terminal_report(project, result))
    return 1 if result.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
