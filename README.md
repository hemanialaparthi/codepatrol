# CodePatrol

CodePatrol is a focused Python structural policy checker. It parses policy
patterns and source files with Tree-sitter and reports matching syntax subtrees.
It is designed to provide deterministic feedback for controlled experiments; it
does not prove functional correctness.

## Installation

CodePatrol requires Python 3.11 or newer.

From the project directory, create a virtual environment and install CodePatrol
with its development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
```

After changing CodePatrol's source code, run the final installation command
again to update the installed CLI. A normal installation is recommended when
the project is stored in a cloud-managed macOS Desktop folder: macOS can mark
the `.pth` link created by an editable installation as hidden, which causes
Python 3.14 to ignore the project package.

Runtime dependencies are PyYAML, Tree-sitter, the Tree-sitter Python grammar,
and Typer for the command-line interface. Their supported version ranges are
listed in `pyproject.toml`.

### Running CodePatrol

Each time you open a new terminal, change to the project directory and activate
the existing virtual environment:

```bash
cd /path/to/codepatrol
source .venv/bin/activate
```

You can then run CodePatrol, inspect its help, or run the test suite:

```bash
codepatrol scan demo/project --policies demo/policies.yaml
codepatrol --help
pytest
```

When you are finished, leave the virtual environment with `deactivate`. You can
also run the tool without activating the environment:

```bash
.venv/bin/codepatrol scan demo/project --policies demo/policies.yaml
```

If `.venv` is deleted, repeat the installation commands above to recreate it.
An exit status of `1` from a scan means CodePatrol found policy violations; it
does not mean the tool crashed.

## Policy format and validation

A policy file is a YAML mapping with one non-empty `rules` list:

```yaml
rules:
  - id: no-print
    language: python
    pattern: "print($MSG)"
    message: "Use logging instead."
```

Every rule requires exactly these four non-blank string fields. `language` is
required because CodePatrol has no default language; `python` is currently the
only supported value. Rule IDs must be unique. Unknown fields, duplicate YAML
mapping keys, malformed YAML, malformed metavariables, invalid patterns, and
identifiers beginning with the reserved `__CODEPATROL_META_` prefix are errors.
All policies are loaded and compiled before source files are checked. An invalid
policy is never skipped.

Metavariable names use `$` followed by an uppercase letter, then uppercase
letters, digits, or underscores, for example `$X` or `$ARG_2`. Dollar-prefixed
text inside a string or comment is literal and is not rewritten as a
metavariable.

## Matching contract

- A metavariable matches exactly one named AST node, which may be a whole
  expression subtree. It never means zero nodes or a sequence of arguments or
  statements.
- Repeated occurrences bind consistently using structural equivalence:
  `$X + $X` matches `a + a` and `(a * b) + (a * b)`, but not `a + b`.
- Literal identifiers, operators, literal values, node types, field
  relationships, and child structure must match.
- Whitespace, source positions, and comments do not affect a match. Identifier
  names and literal values are not normalized.
- Positional, keyword, and splatted arguments remain distinct. Thus
  `call($X)` does not match `call(value=1)`.
- An expression-statement wrapper is removed from a pattern, so
  `print($MSG)` matches the call subtree inside an assignment or nested
  expression rather than requiring a complete module.
- A pattern must contain exactly one top-level statement or expression. A bare
  metavariable is rejected because it has no literal structure to anchor the
  match. Multi-statement and otherwise unparsable patterns are also rejected.

`print($MSG)` is specifically a one-argument call pattern. It matches
`print("hello")` and `print(make_message())`, but not `print("hello", name)` or
`logger.print("hello")`.

CodePatrol walks the complete valid syntax tree, including functions, async
functions, classes, loops, conditionals, exception handlers, comprehensions,
and nested expressions. Each matching node is reported once per rule; distinct
nested matches are retained. Text inside comments or string literals is not
parsed as executable Python.

### Structural limitations

Matching is syntactic only. CodePatrol does not resolve imports, aliases,
bindings, types, control flow, or data flow. Semantically equivalent code with a
different AST does not necessarily match. Metavariables cannot capture argument
or statement sequences, and there are no automatic fixes.

Both source files and patterns are checked for Tree-sitter error and missing
nodes. This catches Tree-sitter parsing failures, but it does not establish
runtime correctness or every restriction enforced by the Python compiler. For
example, a construct accepted by the grammar may still fail later compilation
or execution.

## Selecting files

Pass one or more explicit Python files or directories. Directories are searched
recursively; overlapping inputs and file symlink aliases are deduplicated by
their resolved path, and results are ordered by normalized path. Directory
symlinks are never recursively followed, preventing loops.

```bash
codepatrol scan src tests/unit/test_api.py --policies policies.yaml
codepatrol scan . --policies policies.yaml --exclude 'generated/**' --exclude vendor
```

Custom `--exclude` values are repeatable, case-sensitive globs matched against a
root-relative POSIX path or basename. Exclusions also accept a directory prefix
such as `vendor`. CodePatrol does not read `.gitignore` implicitly.

Default excluded directories are `.git`, `.hg`, `.svn`, `.venv`, `venv`, `env`,
`__pycache__`, `node_modules`, `build`, `dist`, `.tox`, `.nox`, `.mypy_cache`,
`.pytest_cache`, and `.ruff_cache`; directories ending in `.egg-info` are also
excluded. An excluded file count is the number of eligible Python files found
under these pruned paths or matched directly. Directory symlinks are not opened
and therefore their target files are not included in that count.

Nonexistent inputs, explicitly supplied non-Python files, discovery failures,
unreadable files, non-UTF-8 files, and invalid Python are structured errors. A
failure in one source file does not prevent other selected files from being
checked where practical. `selected` counts deduplicated eligible files,
`scanned` counts files successfully decoded, parsed, and fully checked, and
`excluded` uses the definition above. Selecting no eligible files is an error.

## Findings and locations

Each finding contains its rule ID, policy message, normalized file path,
start and end locations, and the exact UTF-8 source snippet for the matched
subtree. Report paths are relative to the common invocation root when possible
and always use `/` separators.

Lines and columns are 1-based Unicode character positions. The start is
inclusive and the end is exclusive. Tree-sitter exposes byte columns, so
CodePatrol explicitly converts them before reporting; a multibyte character
earlier on a line therefore counts as one column.

## Output, status, and exit codes

Human-readable output is the default. `--json` writes exactly one JSON document
to stdout, including for expected errors:

```bash
codepatrol scan demo/project --policies demo/policies.yaml --json
```

Default terminal output begins with one overall `PASS`, `FAIL`, or `ERROR` and
a plain-language reason. A compact summary follows with policy status,
violation count, functional-command outcome when requested, files scanned,
policies checked, and checking-error count when nonzero. There is no separate
policy-only `FAILED` banner in combined evaluations.

Policy violations are numbered in deterministic report order. Each includes
its project-relative start location, rule ID, policy message, and a
syntax-highlighted, line-numbered copy of the exact matched source. The default
view shows at most 10 violations and reports how many were omitted. Use
`--verbose` or `-v` to show every violation, exact start/end ranges,
selection/exclusion counts, project and test working directories, executed
arguments, captured previews, and artifact paths:

```bash
codepatrol scan demo/project --policies demo/policies.yaml --verbose
```

Successful functional commands show only their outcome, raw exit code, and
duration by default; CodePatrol does not claim how many tests ran. Failed,
timed-out, interrupted, or unstartable commands also show the command,
structured execution errors, bounded stdout/stderr previews, truncation state,
and full-log paths. Policy and execution errors are displayed in sections
separate from violations.

Rich styling is used on interactive terminals. Status words remain present
without color, long content wraps on narrow terminals, and redirected output
is plain text. Set the conventional `NO_COLOR` environment variable to disable
ANSI styling. User-controlled paths, messages, commands, snippets, and output
are treated as literal text; terminal control characters are displayed in
escaped form rather than executed.

The CLI retains an explicit `scan` subcommand and is ready for additional
commands. Use these commands to inspect its interface:

```bash
codepatrol --help
codepatrol scan --help
codepatrol --version
```

Typer argument errors—including missing arguments, unknown options, and missing
option values—are converted at the command boundary into the same structured
JSON error report when `--json` appears anywhere in the invocation. Rich error
formatting and progress output are disabled, so they cannot contaminate JSON
stdout. Explicit `--help`, `-h`, and `--version` requests are the intentional
exception: they retain ordinary text output and exit `0` even when combined
with `--json`.

JSON reports use schema version `1.0` and this shape (irrelevant optional error
context fields are omitted):

```json
{
  "schema_version": "1.0",
  "status": "FAIL",
  "summary": {
    "files": {"selected": 3, "scanned": 3, "excluded": 0},
    "policies": 1,
    "findings": 2,
    "errors": 0
  },
  "findings": [
    {
      "rule_id": "no-print",
      "message": "Use logging instead.",
      "file": "app.py",
      "start": {"line": 2, "column": 5},
      "end": {"line": 2, "column": 19},
      "matched_source": "print(message)"
    }
  ],
  "errors": []
}
```

Errors contain `category` and `message`, plus `file`, `policy_id`, `start`, and
`end` when relevant. Reports contain no timestamps, durations, or random IDs.
Files, findings, and errors are sorted, so repeated identical invocations
produce identical JSON.

Statuses and process exit codes are:

- `PASS` / `0`: every selected file was checked and no findings occurred.
- `FAIL` / `1`: checking completed without errors and findings occurred.
- `ERROR` / `2`: configuration, discovery, reading, decoding, parsing, or
  checking errors prevented a complete reliable check. `ERROR` takes precedence
  over findings, while findings from successfully checked files remain present.

Using the included policy, these commands demonstrate all three outcomes:

```bash
# PASS / exit 0
codepatrol scan demo/project/calculator.py --policies demo/policies.yaml

# FAIL / exit 1
codepatrol scan demo/project/users.py --policies demo/policies.yaml

# ERROR / exit 2
codepatrol scan demo/project/does-not-exist.py --policies demo/policies.yaml
```

## Running functional tests

CodePatrol can run one user-supplied command after the policy scan and collect
its result for later tooling. The runner is framework-independent: it neither
requires pytest nor recognizes a fixed list of test frameworks.

```bash
codepatrol scan src \
  --policies policies.yaml \
  --test-command "python -m pytest tests -q" \
  --test-cwd . \
  --test-timeout 60 \
  --json \
  --output reports/evaluation.json

codepatrol scan src --policies policies.yaml \
  --test-command "python -m unittest discover"

codepatrol scan src --policies policies.yaml --test-command "npm test"
codepatrol scan src --policies policies.yaml --test-command "cargo test"
codepatrol scan src --policies policies.yaml --test-command "go test ./..."
codepatrol scan src --policies policies.yaml \
  --test-command "./scripts/test.sh"
```

The included demo has a framework-neutral `unittest` suite that pytest can
also collect. From the repository root, try either command:

```bash
codepatrol scan demo/project --policies demo/policies.yaml \
  --test-command "python -m unittest discover -s demo/tests" --json

codepatrol scan demo/project --policies demo/policies.yaml \
  --test-command "python -m pytest demo/tests -q" --json
```

`--test-cwd` is resolved from the directory where CodePatrol was invoked and
defaults to that invocation directory. The command inherits CodePatrol's
environment and uses the executable selected by the user. CodePatrol never
installs a dependency, substitutes a test command, or lets a later analysis
stage alter it. `--test-timeout` must be greater than zero and defaults to 60
seconds. Supplying `--test-cwd` or `--test-timeout` without `--test-command` is
an error. Without `--test-command`, no subprocess is started and the original
scan report schema and exit behavior are unchanged.

### Command syntax and process behavior

The command string is split with POSIX-style shell quoting so quoted paths and
arguments containing spaces remain single arguments. It is then executed with
`shell=False` and closed standard input. Shell syntax is deliberately not
interpreted: pipes, redirection, glob expansion, environment-variable
expansion, and `&&`/`;` command chaining are passed as ordinary arguments. Put
complex behavior in a script, or explicitly invoke a shell, for example
`--test-command "sh -c 'first && second'"`. Quoting follows POSIX rules on all
platforms and therefore is not identical to `cmd.exe` or PowerShell parsing on
Windows.

The runner records the exact argument list, absolute working directory, raw
exit code, elapsed monotonic duration, and stdout/stderr separately. A
subprocess boundary is not a security sandbox: the command has the invoking
user's filesystem and environment permissions.

On POSIX systems CodePatrol places the command in a new process group, sends a
termination signal to the group on timeout or interruption, and escalates to a
kill signal after a short grace period. This normally cleans up descendants.
On Windows, process-group creation and direct-process termination are used, but
complete descendant cleanup is not guaranteed without an OS job object.
Signals on POSIX may appear as negative raw exit codes.

### Test outcomes and logs

- `PASS`: the supplied test command exited successfully (exit code 0). This
  does not establish that the framework discovered or executed any tests.
- `FAIL`: the command completed with a nonzero exit code. Generic execution
  does not guess whether that means assertion, collection, or configuration
  failure.
- `ERROR`: the command could not start, its working directory or inputs were
  invalid, or its result could not be captured reliably.
- `TIMEOUT`: it exceeded the configured time limit.
- `INTERRUPTED`: execution was interrupted and cleanup was attempted.

Reports contain bounded, UTF-8-with-replacement previews and explicit
`truncated` flags. Complete stdout and stderr bytes are written to separate
`stdout.log` and `stderr.log` artifacts, so non-UTF-8 output is retained.
Each run uses a unique directory under `.codepatrol/artifacts/` in the
invocation directory. These logs are ignored by Git, are not Python source
files, and are retained until the user or their workspace cleanup removes
them. Artifact creation or capture failure is an `ERROR`, with the available
paths and structured execution errors preserved in the report.

### Combined evaluation reports

Functional runs produce a separately versioned `evaluation-1.0` envelope. It
keeps the unchanged policy scan v1 report under `policy_scan`, the generic
command result under `test_command`, and evaluation errors and a skip reason in
their own fields. Durations and unique artifact paths live only in this
envelope, so evaluation JSON is intentionally not byte-for-byte deterministic.

Tests run after policy `PASS` or `FAIL`. They also run when all scan errors are
source parsing errors, because runtime diagnostics may still be useful. Invalid
policies, discovery/read/decoding failures, and internal checking failures
block execution and produce a reason in `test_skip_reason`. Policy findings
and captured test output remain separate.

Combined exit codes are:

- `0`: policy `PASS` and command `PASS`.
- `1`: policy violations or command `FAIL`, with no blocking error.
- `2`: policy `ERROR`, command `ERROR`/`TIMEOUT`/`INTERRUPTED`, a blocked test,
  or report/artifact persistence failure. Errors take precedence over failures.

Overall success therefore means both that the policies passed and that the
supplied command exited successfully. The quality and coverage of functional
verification depend entirely on the command and tests supplied by the user.

`--json` emits exactly one JSON document; child output never streams into that
stdout. `--output` works with or without `--json`, creates parent directories,
and atomically replaces the destination with the same JSON document. Known
source inputs, the policy file, test targets, and project configuration files
are protected from accidental report overwrites. Expected failures are
reported without tracebacks. `--verbose` never changes JSON schemas or data.
When `--json` and `--output` are combined, the saved-path confirmation is sent
to stderr so JSON stdout remains undecorated. A saved path is only shown after
persistence succeeds.

## Architecture and maintenance

- `codepatrol/policy/loader.py` validates YAML shape and rule data.
- `codepatrol/policy/compiler.py` delegates validated patterns to a language
  adapter; add policy-level compilation behavior here.
- `codepatrol/languages/python.py` owns Python parsing, metavariable-safe pattern
  transformation, and supported pattern shapes.
- `codepatrol/scanner/matcher.py` defines structural equivalence, traversal, and
  metavariable binding. Modify matching behavior here.
- `codepatrol/scanner/project.py` owns input discovery, exclusions, source
  validation, location conversion, and policy application.
- `codepatrol/scanner/models.py` defines findings, errors, status, deterministic
  ordering, and the report schema.
- `codepatrol/testing/runner.py` exposes `parse_command()` and
  `run_test_command()` as the framework-neutral process boundary;
  `codepatrol/testing/models.py` defines `TestCommandResult` and the
  `FrameworkResultAdapter` protocol. A future pytest, Jest, or other adapter
  attaches through `FrameworkResultAdapter.interpret()` without changing
  generic execution. No adapter or framework plugin is required today.
- `codepatrol/evaluation.py` exposes `evaluate_scan()`, `test_skip_reason()`,
  and `evaluation_exit_code()` for future repair-controller integration while
  keeping policy and command results separate.
- `codepatrol/reporting/` renders terminal and JSON output and owns protected,
  atomic persistence; `codepatrol/cli.py`
  defines the Typer application, converts parsing failures to reports, and
  handles exit codes. Add future commands to its `typer.Typer` application with
  another `@cli.command()` function while keeping checker logic in the modules
  above.

To add a new policy feature, begin with the policy model/loader and compiler,
then make the smallest corresponding matcher or adapter change. To change what
two syntax trees mean by “equivalent,” start in `scanner/matcher.py` and add
focused tests in `tests/test_pattern_and_matcher.py`.

## Verification

```bash
python -m pytest -q
python -m compileall -q codepatrol
codepatrol --help
codepatrol scan --help
codepatrol --version
codepatrol scan demo/project --policies demo/policies.yaml
codepatrol scan demo/project --policies demo/policies.yaml --verbose
codepatrol scan demo/project --policies demo/policies.yaml --json
codepatrol scan demo/project --policies demo/policies.yaml \
  --test-command "python -m pytest demo/tests -q" --json
```

The repository does not yet make AI calls, repair code, or implement `--fix`.
The generic result and adapter boundary are intended to support those later
experiments without giving a future AI control over command selection.
