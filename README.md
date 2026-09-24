# CodePatrol

CodePatrol is a small static-analysis CLI that lets developers define custom
structural coding policies in YAML and scan a repository for violations.

## Why structural matching?

Literal text searches are easily defeated by spaces and line breaks. CodePatrol
parses both a policy pattern and the source with Tree-sitter, then compares their
syntax trees. As a result, `print($MSG)` matches both `print("hello")` and a
multiline, unusually spaced call.

## Installation

CodePatrol requires Python 3.11 or newer.

```bash
pip install -e .
```

To install the test dependency too:

```bash
pip install -e '.[dev]'
```

## Example policy

```yaml
rules:
  - id: no-print
    language: python
    pattern: "print($MSG)"
    message: "Use logging instead."
```

`$MSG` is a metavariable: it matches exactly one syntax-tree node or subtree.

## Running

```bash
codepatrol scan demo/project --policies demo/policies.yaml
```

Exit codes are `0` for a clean scan, `1` when violations are found, and `2`
when the scan cannot run because of invalid configuration, parsing, or I/O.

## Architecture

```text
Policy Loader
    ↓
Pattern Compiler
    ↓
Language Adapter
    ↓
Project Scanner
    ↓
Structural Matcher
    ↓
Reporter
```

The language registry is the extension point. It currently contains only the
Python adapter, while policy loading, scanning, matching, and reporting remain
language-neutral.

## V1 limitations

- Python only
- `$NAME` matches one node/subtree, not a sequence of nodes
- no multi-node wildcards
- structural/syntactic analysis only
- no semantic, type, or data-flow analysis
- no automatic fixes

## Future work

Natural follow-on work includes additional language adapters, richer
metavariables, path and context constraints, JSON reports, CI integration, and
automated refactoring.

