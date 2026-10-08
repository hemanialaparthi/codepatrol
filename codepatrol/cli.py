"""Typer command-line entry point for CodePatrol."""

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup
from typer.exceptions import TyperException

from codepatrol import __version__
from codepatrol.errors import CodePatrolError, PolicyError
from codepatrol.languages import default_registry
from codepatrol.policy import compile_policies, load_policies
from codepatrol.reporting import render_json_report, render_terminal_report
from codepatrol.scanner import CheckError, ProjectScanner, ScanResult

HELP_OPTIONS = {"-h", "--help"}


class _JsonAwareTyperGroup(TyperGroup):
    """Convert Typer argument failures to reports when JSON was requested."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_arguments = list(args) if args is not None else sys.argv[1:]
        information_requested = _information_requested(raw_arguments)
        if information_requested:
            # Help and version are ordinary text even with --json. Removing the
            # marker also makes top-level `--help --json` unambiguous to Typer.
            raw_arguments = [item for item in raw_arguments if item != "--json"]

        option_value_error = (
            None if information_requested else _missing_option_value(raw_arguments)
        )
        if option_value_error is not None:
            result = _render_argument_error(
                option_value_error, json_output="--json" in raw_arguments
            )
            if standalone_mode:
                raise SystemExit(result)
            return result

        try:
            result = super().main(
                args=raw_arguments,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except TyperException as exc:
            exit_code = int(getattr(exc, "exit_code", 2))
            if "--json" not in raw_arguments:
                show_error = getattr(exc, "show", None)
                if callable(show_error):
                    show_error()
                else:
                    typer.echo(f"CodePatrol error: {exc}", err=True)
                result = exit_code
            else:
                result = _render_argument_error(str(exc), json_output=True)

        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result


cli = typer.Typer(
    cls=_JsonAwareTyperGroup,
    help=(
        "Scan source code for configurable structural policy violations. "
        "Run 'codepatrol scan --help' for scan options."
    ),
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"codepatrol {__version__}")
        raise typer.Exit()


@cli.callback()
def application(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the CodePatrol version and exit.",
        ),
    ] = False,
) -> None:
    """Provide top-level options while retaining explicit subcommands."""


@cli.command("scan")
def scan_command(
    paths: Annotated[
        list[Path],
        typer.Argument(
            metavar="PATH...",
            help="One or more Python files or directories to scan.",
        ),
    ],
    policy_file: Annotated[
        Path,
        typer.Option(
            "--policies",
            metavar="PATH",
            help="YAML policy file.",
        ),
    ],
    exclusions: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            metavar="GLOB",
            help="Exclude a root-relative path glob; repeat for multiple globs.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit exactly one JSON report on stdout.",
        ),
    ] = False,
) -> int:
    """Scan Python files and directories against structural policies."""
    return _execute_scan_command(
        paths=paths,
        policy_file=policy_file,
        exclusions=exclusions or [],
        json_output=json_output,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""
    result = cli(
        args=list(argv) if argv is not None else None,
        prog_name="codepatrol",
        standalone_mode=False,
    )
    return result if isinstance(result, int) else 0


def _execute_scan_command(
    *,
    paths: list[Path],
    policy_file: Path,
    exclusions: list[str],
    json_output: bool,
) -> int:
    root = _report_root(paths)
    try:
        result = _run_scan(paths, policy_file, exclusions)
    except CodePatrolError as exc:
        result = _error_result(
            root,
            "configuration",
            str(exc),
            file=policy_file,
            policy_id=exc.policy_id if isinstance(exc, PolicyError) else None,
        )
    except OSError as exc:
        result = _error_result(root, "reading", str(exc))
    except Exception as exc:
        # This is the process boundary: unexpected failures must never become
        # successful checks or leak a traceback into machine-readable output.
        result = _error_result(
            root,
            "internal",
            f"Unexpected {type(exc).__name__}: {exc}",
        )

    if json_output:
        typer.echo(render_json_report(result))
    else:
        typer.echo(render_terminal_report(result.root, result))
        for error in result.errors:
            typer.echo(f"CodePatrol error: {error.message}", err=True)
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result.status]


def _run_scan(
    paths: list[Path], policy_file: Path, exclusions: list[str]
) -> ScanResult:
    # Retain the original single-project diagnostic while multi-input discovery
    # remains able to report a missing path alongside successfully checked files.
    if len(paths) == 1 and not paths[0].exists():
        result = ScanResult(
            errors=[
                CheckError(
                    category="discovery",
                    message=f"Project does not exist: '{paths[0]}'.",
                    file=paths[0],
                )
            ],
            root=_report_root(paths),
        )
        result.sort()
        return result
    registry = default_registry()
    policies = load_policies(policy_file, registry)
    compiled_policies = compile_policies(policies, registry)
    return ProjectScanner(registry).scan(paths, compiled_policies, exclusions)


def _error_result(
    root: Path,
    category: str,
    message: str,
    *,
    file: Path | None = None,
    policy_id: str | None = None,
) -> ScanResult:
    result = ScanResult(
        errors=[
            CheckError(
                category=category,
                message=message,
                file=file,
                policy_id=policy_id,
            )
        ],
        root=root,
    )
    result.sort()
    return result


def _report_root(paths: list[Path]) -> Path:
    if len(paths) == 1:
        path = paths[0].expanduser().resolve(strict=False)
        return path if path.is_dir() else path.parent
    parents = [
        path.expanduser().resolve(strict=False)
        if path.is_dir()
        else path.expanduser().resolve(strict=False).parent
        for path in paths
    ]
    try:
        return Path(os.path.commonpath([str(path) for path in parents]))
    except ValueError:
        return Path.cwd().resolve()


def _information_requested(arguments: Sequence[str]) -> bool:
    return bool(HELP_OPTIONS.intersection(arguments)) or "--version" in arguments


def _missing_option_value(arguments: Sequence[str]) -> str | None:
    """Detect option markers Typer would otherwise consume as string values."""
    try:
        scan_index = arguments.index("scan")
    except ValueError:
        return None
    value_options = {"--exclude", "--policies"}
    for index in range(scan_index + 1, len(arguments)):
        option = arguments[index]
        if option not in value_options:
            continue
        if index + 1 == len(arguments) or arguments[index + 1].startswith("-"):
            return f"Option '{option}' requires an argument."
    return None


def _render_argument_error(message: str, *, json_output: bool) -> int:
    if json_output:
        report = _error_result(Path.cwd().resolve(), "configuration", message)
        typer.echo(render_json_report(report))
    else:
        typer.echo(f"CodePatrol error: {message}", err=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
