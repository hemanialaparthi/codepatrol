"""Validation and byte-preserving application of deterministic edits."""

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile

from codepatrol.errors import CodePatrolError
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.models import ScanResult
from codepatrol.fixing.replacement import render_replacement


@dataclass(frozen=True)
class SourceEdit:
    file: Path
    start_byte: int
    end_byte: int
    replacement: str
    rule_id: str


class EditError(CodePatrolError):
    """A candidate edit cannot be safely planned or applied."""


def plan_edits(
    result: ScanResult, policies: list[CompiledPolicy], allowed_files: set[Path]
) -> list[SourceEdit]:
    by_id = {policy.policy.id: policy for policy in policies}
    planned: list[SourceEdit] = []
    for violation in result.violations:
        policy = by_id[violation.rule_id]
        if policy.fix is None:
            continue
        if violation.file.resolve(strict=False) not in allowed_files:
            raise EditError(f"Fix target is outside the permitted source files: '{violation.file}'.")
        if violation.start_byte is None or violation.end_byte is None:
            raise EditError(f"Rule '{violation.rule_id}' did not provide a byte range.")
        source = violation.file.read_bytes()
        replacement = (
            render_replacement(
                policy.fix,
                result.match_captures.get(
                    (violation.rule_id, violation.file, violation.start_byte, violation.end_byte),
                    {},
                ),
                source,
            )
            if policy.pattern is not None
            else policy.fix.replacement
        )
        replacement.encode("utf-8")
        planned.append(
            SourceEdit(
                violation.file,
                violation.start_byte,
                violation.end_byte,
                replacement,
                violation.rule_id,
            )
        )
    return validate_edits(planned)


def validate_edits(edits: list[SourceEdit]) -> list[SourceEdit]:
    unique: dict[tuple[Path, int, int, str], SourceEdit] = {}
    for edit in edits:
        if edit.start_byte < 0 or edit.end_byte < edit.start_byte:
            raise EditError(f"Invalid edit range for rule '{edit.rule_id}'.")
        key = (edit.file, edit.start_byte, edit.end_byte, edit.replacement)
        unique[key] = edit
    ordered = sorted(unique.values(), key=lambda item: (item.file, item.start_byte, item.end_byte))
    by_file: dict[Path, list[SourceEdit]] = {}
    for edit in ordered:
        by_file.setdefault(edit.file, []).append(edit)
    for file_edits in by_file.values():
        for left, right in zip(file_edits, file_edits[1:]):
            if right.start_byte < left.end_byte:
                if (
                    left.start_byte == right.start_byte
                    and left.end_byte == right.end_byte
                    and left.replacement != right.replacement
                ):
                    raise EditError(f"Conflicting fixes target the same range in '{left.file}'.")
                raise EditError(f"Overlapping fixes are not supported in '{left.file}'.")
    return ordered


def apply_edits(source: bytes, edits: list[SourceEdit]) -> bytes:
    result = source
    for edit in sorted(edits, key=lambda item: item.start_byte, reverse=True):
        if edit.end_byte > len(result):
            raise EditError(f"Edit range exceeds source length for '{edit.file}'.")
        result = result[: edit.start_byte] + edit.replacement.encode("utf-8") + result[edit.end_byte :]
    return result


def replace_one_file(edit_file: Path, original: bytes, candidate: bytes) -> None:
    current = edit_file.read_bytes()
    if current != original:
        raise EditError(f"Source changed before application: '{edit_file}'.")
    mode = edit_file.stat().st_mode
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=f".{edit_file.name}.codepatrol-", dir=edit_file.parent)
        temporary = Path(name)
        with os.fdopen(handle, "wb") as output:
            output.write(candidate)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, edit_file)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
