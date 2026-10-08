# CodePatrol

CodePatrol is a focused Python structural policy checker. It parses policy
patterns and source files with Tree-sitter and reports matching syntax subtrees.
It is designed to provide deterministic feedback for controlled experiments; it
does not prove functional correctness.

## Installation

CodePatrol requires Python 3.11 or newer.

```bash
python -m pip install -e '.[dev]'
```

Runtime dependencies are PyYAML, Tree-sitter, the Tree-sitter Python grammar,
and Typer for the command-line interface. Their supported version ranges are
listed in `pyproject.toml`.

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
- `codepatrol/reporting/` renders terminal and JSON output; `codepatrol/cli.py`
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
codepatrol scan demo/project --policies demo/policies.yaml --json
```

The repository implements the checker and demonstrations only; it does not
include agent integration or the complete experimental automation system.
