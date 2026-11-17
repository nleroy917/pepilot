"""Runs generated scripts in real bash, once per job rank, the way the instances would."""

import os
import pathlib
import subprocess

import pytest

from pepilot.plan import SampleRun
from pepilot.script import HEREDOC_END, run_script


def _run(script: str, rank: int, n: int, tmp_path: pathlib.Path, **env: str) -> subprocess.CompletedProcess:
    path = tmp_path / "run.sh"
    path.write_text(script)
    return subprocess.run(
        ["bash", str(path)],
        cwd=tmp_path,
        env={**os.environ, "SKYPILOT_JOB_RANK": str(rank), "SKYPILOT_NUM_JOBS": str(n), **env},
        capture_output=True,
        text=True,
    )


def _runs(*commands: str) -> list[SampleRun]:
    return [SampleRun(name=f"s{i}", command=c, skip_paths=()) for i, c in enumerate(commands)]


def test_each_job_runs_exactly_its_own_samples(tmp_path):
    runs = _runs(*[f"echo s{i} >> ran.txt\n" for i in range(7)])
    jobs = [runs[k::3] for k in range(3)]
    script = run_script(jobs)
    for rank in range(3):
        result = _run(script, rank, 3, tmp_path)
        assert result.returncode == 0, result.stderr
    assert sorted((tmp_path / "ran.txt").read_text().split()) == [f"s{i}" for i in range(7)]


def test_commands_arrive_exactly_as_rendered(tmp_path):
    command = (
        "echo \"$GREETING\" '$NOT_EXPANDED' \"$PEPILOT_SAMPLE\" > out.txt\n"
        "cat >> out.txt <<'INNER'\n"
        "a heredoc of the user's own, with `backticks` and $(subshells)\n"
        "INNER\n"
    )
    result = _run(run_script([_runs(command)]), 0, 1, tmp_path, GREETING="hi there")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out.txt").read_text() == (
        "hi there $NOT_EXPANDED s0\n"
        "a heredoc of the user's own, with `backticks` and $(subshells)\n"
    )


def test_a_failed_sample_does_not_stop_the_others_but_fails_the_job(tmp_path):
    runs = _runs("echo s0 >> ran.txt\n", "false\necho never >> ran.txt\n", "echo s2 >> ran.txt\n")
    result = _run(run_script([runs]), 0, 1, tmp_path)
    assert result.returncode == 1
    assert (tmp_path / "ran.txt").read_text().split() == ["s0", "s2"]
    assert "s1: FAILED with exit code 1" in result.stderr
    assert "1 of 3 sample(s) failed: s1" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "echo $UNSET_VARIABLE_XYZ > out.txt\n",  # -u
        "false | true\necho reached > out.txt\n",  # pipefail
        "exit 3\n",
    ],
)
def test_samples_run_strictly(tmp_path, command):
    result = _run(run_script([_runs(command)]), 0, 1, tmp_path)
    assert result.returncode == 1
    assert not (tmp_path / "out.txt").exists() or (tmp_path / "out.txt").read_text() == ""


def test_exit_code_is_reported(tmp_path):
    result = _run(run_script([_runs("exit 7\n")]), 0, 1, tmp_path)
    assert "FAILED with exit code 7" in result.stderr


def test_a_rank_with_no_arm_fails(tmp_path):
    result = _run(run_script([_runs("true\n")]), 5, 6, tmp_path)
    assert result.returncode == 1
    assert "job 5 has no samples" in result.stderr


def test_odd_sample_names_survive(tmp_path):
    runs = [SampleRun(name="it's a $sample (1)", command='echo "$PEPILOT_SAMPLE" > out.txt\n', skip_paths=())]
    result = _run(run_script([runs]), 0, 1, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out.txt").read_text() == "it's a $sample (1)\n"


def test_refuses_a_command_containing_the_heredoc_end(tmp_path):
    with pytest.raises(ValueError, match=HEREDOC_END):
        run_script([_runs(f"echo hi\n{HEREDOC_END}\n")])
