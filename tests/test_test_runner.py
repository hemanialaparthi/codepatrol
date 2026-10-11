import os
import shlex
import signal
import sys
import time
from pathlib import Path

import pytest

from codepatrol.testing import parse_command, run_test_command


def python_command(script: Path, *arguments: str) -> str:
    return " ".join(shlex.quote(item) for item in (sys.executable, str(script), *arguments))


def test_command_success_and_nonzero_exit(tmp_path):
    script = tmp_path / "result.py"
    script.write_text(
        "import sys\nprint('out')\nprint('err', file=sys.stderr)\nraise SystemExit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )

    passed = run_test_command(
        python_command(script, "0"), working_directory=tmp_path
    )
    failed = run_test_command(
        python_command(script, "7"), working_directory=tmp_path
    )

    assert (passed.outcome, passed.exit_code) == ("PASS", 0)
    assert (failed.outcome, failed.exit_code) == ("FAIL", 7)
    assert passed.stdout_preview == "out\n"
    assert passed.stderr_preview == "err\n"
    assert passed.stdout_log.read_bytes() == b"out\n"


def test_launch_failure_and_invalid_working_directory(tmp_path):
    launch = run_test_command(
        "definitely-not-a-real-executable", working_directory=tmp_path
    )
    bad_cwd = run_test_command(
        f"{shlex.quote(sys.executable)} --version",
        working_directory=tmp_path / "missing",
    )

    assert launch.outcome == "ERROR"
    assert launch.exit_code is None
    assert launch.errors[0].category == "launch"
    assert bad_cwd.outcome == "ERROR"
    assert bad_cwd.errors[0].category == "working_directory"


@pytest.mark.parametrize("command", ["", "   ", "'unterminated"])
def test_invalid_commands_are_structured_errors(tmp_path, command):
    result = run_test_command(command, working_directory=tmp_path)
    assert result.outcome == "ERROR"
    assert result.arguments == []
    assert result.errors[0].category == "command"


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_timeouts_are_structured_errors(tmp_path, timeout):
    result = run_test_command(
        f"{shlex.quote(sys.executable)} --version",
        working_directory=tmp_path,
        timeout_seconds=timeout,
    )
    assert result.outcome == "ERROR"
    assert result.errors[0].category == "timeout"


def test_quoted_arguments_and_paths_with_spaces(tmp_path):
    directory = tmp_path / "space directory"
    directory.mkdir()
    script = directory / "echo argument.py"
    script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")

    arguments = parse_command(python_command(script, "value with spaces"))
    result = run_test_command(
        python_command(script, "value with spaces"), working_directory=directory
    )

    assert arguments[-2:] == [str(script), "value with spaces"]
    assert result.outcome == "PASS"
    assert result.stdout_preview == "value with spaces\n"


def test_runner_is_unrelated_to_pytest_and_inherits_environment(tmp_path, monkeypatch):
    script = tmp_path / "environment.py"
    script.write_text(
        "import os\nprint(os.environ['CODEPATROL_RUNNER_TEST'])\n", encoding="utf-8"
    )
    monkeypatch.setenv("CODEPATROL_RUNNER_TEST", "inherited")

    result = run_test_command(python_command(script), working_directory=tmp_path)

    assert result.outcome == "PASS"
    assert result.stdout_preview == "inherited\n"


def test_stdin_is_closed_and_shell_tokens_are_plain_arguments(tmp_path):
    script = tmp_path / "arguments.py"
    script.write_text(
        "import sys\nprint(repr(sys.stdin.read()))\nprint(repr(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    command = python_command(script, "|", "$HOME", ">", "&&")

    result = run_test_command(command, working_directory=tmp_path)

    assert result.outcome == "PASS"
    assert result.stdout_preview == "''\n['|', '$HOME', '>', '&&']\n"


def test_negative_signal_return_code_is_preserved(tmp_path):
    if os.name != "posix":
        pytest.skip("negative signal status is POSIX-specific")
    script = tmp_path / "terminated.py"
    script.write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n",
        encoding="utf-8",
    )

    result = run_test_command(python_command(script), working_directory=tmp_path)

    assert result.outcome == "FAIL"
    assert result.exit_code == -signal.SIGTERM


def test_timeout_terminates_process_group(tmp_path):
    if os.name != "posix":
        pytest.skip("process-group assertion is POSIX-specific")
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "hang.py"
    script.write_text(
        """import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
""",
        encoding="utf-8",
    )

    result = run_test_command(
        python_command(script, str(child_pid_file)),
        working_directory=tmp_path,
        timeout_seconds=0.3,
    )

    assert result.outcome == "TIMEOUT"
    assert result.duration_seconds < 5
    assert result.exit_code is not None
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        # A killed child can remain briefly as a zombie until init reaps it.
        status = Path(f"/proc/{child_pid}/status")
        if status.exists():
            assert "State:\tZ" in status.read_text()


def test_large_and_non_utf8_output_is_bounded_but_preserved(tmp_path):
    script = tmp_path / "binary.py"
    script.write_text(
        "import os\nos.write(1, b'abc\\xff' + b'x' * 10000)\nos.write(2, b'err\\xfe')\n",
        encoding="utf-8",
    )

    result = run_test_command(
        python_command(script), working_directory=tmp_path, preview_bytes=32
    )

    assert result.outcome == "PASS"
    assert result.stdout_truncated is True
    assert len(result.stdout_preview.encode("utf-8")) <= 34
    assert "\ufffd" in result.stdout_preview
    assert result.stdout_log.read_bytes().startswith(b"abc\xff")
    assert result.stdout_log.stat().st_size == 10004
    assert result.stderr_log.read_bytes() == b"err\xfe"


def test_log_artifact_creation_failure_is_structured(tmp_path):
    artifact_root = tmp_path / "not-a-directory"
    artifact_root.write_text("occupied", encoding="utf-8")

    result = run_test_command(
        f"{shlex.quote(sys.executable)} --version",
        working_directory=tmp_path,
        artifact_root=artifact_root,
    )

    assert result.outcome == "ERROR"
    assert result.errors[0].category == "artifact"
    assert result.exit_code is None


def test_interruption_is_reported_and_process_is_cleaned_up(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 123
        returncode = -signal.SIGTERM

        def wait(self, timeout=None):
            raise KeyboardInterrupt

        def poll(self):
            return None

    fake = FakeProcess()
    cleaned = []
    monkeypatch.setattr("codepatrol.testing.runner.subprocess.Popen", lambda *a, **k: fake)
    monkeypatch.setattr(
        "codepatrol.testing.runner._terminate_process_tree",
        lambda process: cleaned.append(process) or True,
    )

    result = run_test_command("anything", working_directory=tmp_path)

    assert result.outcome == "INTERRUPTED"
    assert result.exit_code == -signal.SIGTERM
    assert cleaned == [fake]

