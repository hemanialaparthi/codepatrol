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
from codepatrol.evaluation import EvaluationError, EvaluationResult, evaluate_scan
from codepatrol.errors import CodePatrolError, PolicyError
from codepatrol.languages import default_registry
from codepatrol.policy import compile_policies, load_policies
from codepatrol.reporting import (
    render_evaluation_json,
    render_evaluation_terminal,
    render_json_report,
    render_saved_report_message,
    render_terminal_report,
    save_report_atomic,
    validate_report_destination,
)
from codepatrol.scanner import CheckError, ProjectScanner, ScanResult
from codepatrol.testing import parse_command

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
                option_value_error,
                json_output="--json" in raw_arguments,
                evaluation="--test-command" in raw_arguments,
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
                result = _render_argument_error(
                    str(exc),
                    json_output=True,
                    evaluation="--test-command" in raw_arguments,
                )

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
    test_command: Annotated[
        str | None,
        typer.Option(
            "--test-command",
            metavar="COMMAND",
            help="Run this exact command after policy scanning (without a shell).",
        ),
    ] = None,
    test_cwd: Annotated[
        Path | None,
        typer.Option(
            "--test-cwd",
            metavar="PATH",
            help="Test working directory (default: invocation directory).",
        ),
    ] = None,
    test_timeout: Annotated[
        float | None,
        typer.Option(
            "--test-timeout",
            metavar="SECONDS",
            help="Positive test timeout in seconds (default: 60).",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            metavar="PATH",
            help="Atomically save the JSON report, independently of --json.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show all findings, exact ranges, paths, arguments, and output details.",
        ),
    ] = False,
) -> int:
    """Scan Python files and directories against structural policies."""
    return _execute_scan_command(
        paths=paths,
        policy_file=policy_file,
        exclusions=exclusions or [],
        json_output=json_output,
        test_command=test_command,
        test_cwd=test_cwd,
        test_timeout=test_timeout,
        output=output,
        verbose=verbose,
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
    test_command: str | None = None,
    test_cwd: Path | None = None,
    test_timeout: float | None = None,
    output: Path | None = None,
    verbose: bool = False,
) -> int:
    invocation_directory = Path.cwd().resolve()
    if test_command is None and (test_cwd is not None or test_timeout is not None):
        supplied = "--test-cwd" if test_cwd is not None else "--test-timeout"
        return _render_argument_error(
            f"Option '{supplied}' requires --test-command.",
            json_output=json_output,
            evaluation=False,
        )
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

    if test_command is None:
        return _present_scan_result(
            result,
            paths=paths,
            policy_file=policy_file,
            json_output=json_output,
            output=output,
            verbose=verbose,
        )

    working_directory = (
        test_cwd.expanduser().resolve(strict=False)
        if test_cwd is not None
        else invocation_directory
    )
    destination_error = _validate_output_destination(
        output,
        paths=paths,
        policy_file=policy_file,
        test_command=test_command,
        test_cwd=working_directory,
    )
    if destination_error is not None:
        evaluation = EvaluationResult(
            policy_scan=result,
            test_command=None,
            test_skip_reason="Test execution was skipped because the report destination is invalid.",
            errors=[EvaluationError("reporting", destination_error)],
        )
    else:
        evaluation = evaluate_scan(
            result,
            test_command,
            test_working_directory=working_directory,
            test_timeout_seconds=test_timeout if test_timeout is not None else 60.0,
            artifact_root=invocation_directory / ".codepatrol" / "artifacts",
        )
    serialized = render_evaluation_json(evaluation)
    saved_report: Path | None = None
    if output is not None and destination_error is None:
        try:
            destination = validate_report_destination(
                output,
                source_inputs=paths,
                policy_file=policy_file,
                command_arguments=_safe_parse_command(test_command),
                command_working_directory=working_directory,
            )
            save_report_atomic(serialized, destination)
            saved_report = destination
        except (OSError, ValueError) as exc:
            evaluation.errors.append(
                EvaluationError("reporting", f"Could not save report: {exc}")
            )
            serialized = render_evaluation_json(evaluation)

    if json_output:
        typer.echo(serialized)
        if saved_report is not None:
            typer.echo(render_saved_report_message(saved_report), err=True)
    else:
        typer.echo(
            render_evaluation_terminal(
                evaluation,
                verbose=verbose,
                saved_report=saved_report,
            )
        )
    return evaluation.exit_code


def _present_scan_result(
    result: ScanResult,
    *,
    paths: list[Path],
    policy_file: Path,
    json_output: bool,
    output: Path | None,
    verbose: bool,
) -> int:
    serialized = render_json_report(result)
    save_error: str | None = None
    saved_report: Path | None = None
    if output is not None:
        try:
            destination = validate_report_destination(
                output, source_inputs=paths, policy_file=policy_file
            )
            save_report_atomic(serialized, destination)
            saved_report = destination
        except (OSError, ValueError) as exc:
            save_error = f"Could not save report: {exc}"
    if json_output:
        typer.echo(serialized)
        if saved_report is not None:
            typer.echo(render_saved_report_message(saved_report), err=True)
    else:
        typer.echo(
            render_terminal_report(
                result.root,
                result,
                verbose=verbose,
                saved_report=saved_report,
                report_error=save_error,
            )
        )
    if save_error is not None:
        if json_output:
            typer.echo(f"CodePatrol error: {save_error}", err=True)
        return 2
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result.status]


def _validate_output_destination(
    output: Path | None,
    *,
    paths: list[Path],
    policy_file: Path,
    test_command: str,
    test_cwd: Path,
) -> str | None:
    if output is None:
        return None
    try:
        validate_report_destination(
            output,
            source_inputs=paths,
            policy_file=policy_file,
            command_arguments=_safe_parse_command(test_command),
            command_working_directory=test_cwd,
        )
    except ValueError as exc:
        return str(exc)
    return None


def _safe_parse_command(command: str) -> list[str]:
    try:
        return parse_command(command)
    except ValueError:
        return []


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
    value_options = {
        "--exclude",
        "--output",
        "--policies",
        "--test-command",
        "--test-cwd",
        "--test-timeout",
    }
    for index in range(scan_index + 1, len(arguments)):
        option = arguments[index]
        if option not in value_options:
            continue
        if index + 1 == len(arguments) or arguments[index + 1].startswith("-"):
            return f"Option '{option}' requires an argument."
    return None


def _render_argument_error(
    message: str, *, json_output: bool, evaluation: bool = False
) -> int:
    if json_output:
        report = _error_result(Path.cwd().resolve(), "configuration", message)
        if evaluation:
            result = EvaluationResult(
                policy_scan=report,
                test_command=None,
                test_skip_reason="Test execution was skipped because CLI configuration is invalid.",
            )
            typer.echo(render_evaluation_json(result))
        else:
            typer.echo(render_json_report(report))
    else:
        typer.echo(f"CodePatrol error: {message}", err=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
