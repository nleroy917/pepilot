import yaml
from click.testing import CliRunner

from pepilot.cli import main


def test_dry_run_plans_and_checks_without_launching(project, monkeypatch):
    path = project(task="name: t\nresources:\n  cpus: 2+\n",
                   pepilot_extra="  skip_if_exists: ['{{ sample.out }}']\n")
    (path.parent / "out").mkdir()
    (path.parent / "out" / "s2.txt").write_text("done")

    def no_launch(*args, **kwargs):
        raise AssertionError("a dry run launched")

    monkeypatch.setattr("pepilot.skypilot.launch", no_launch)
    result = CliRunner().invoke(main, ["launch", str(path), "--dry-run", "--workers", "3"])
    assert result.exit_code == 0, result.output
    assert "finished   1" in result.output
    assert "to run     4" in result.output
    assert "jobs       3" in result.output
    assert "Sample s0 would run:" in result.output
    assert "Dry run: nothing launched" in result.output


def test_render_writes_a_task_skypilot_accepts(project, tmp_path):
    import sky

    path = project(task="name: t\nresources:\n  cpus: 2+\nenvs:\n  A: '1'\n")
    out = tmp_path / "task.yaml"
    result = CliRunner().invoke(
        main, ["render", str(path), "-o", str(out), "--env", "B=2", "--infra", "aws/us-east-1", "--use-spot"]
    )
    assert result.exit_code == 0, result.output
    task = yaml.safe_load(out.read_text())
    assert task["envs"] == {"A": "1", "B": "2"}
    assert task["resources"] == {"cpus": "2+", "infra": "aws/us-east-1", "use_spot": True}
    assert "pepilot_add s0 <<'__PEPILOT_EOF__'" in task["run"]
    assert "sky jobs launch <this file> --num-jobs 2" in out.read_text()
    sky.Task.from_yaml_config(task)


def test_launch_submits_one_job_per_worker(project, monkeypatch):
    path = project(rows=3)
    calls = []
    monkeypatch.setattr("pepilot.skypilot.launch",
                        lambda task, folder, num_jobs: calls.append((task["name"], num_jobs)) or [11, 12])
    result = CliRunner().invoke(main, ["launch", str(path), "-y", "--name", "renamed"])
    assert result.exit_code == 0, result.output
    assert calls == [("renamed", 2)]
    assert "Submitted 2 managed job(s): 11 12" in result.output


def test_status_lists_finished_samples(project):
    path = project(rows=3, pepilot_extra="  skip_if_exists: ['{{ sample.out }}']\n")
    (path.parent / "out").mkdir()
    (path.parent / "out" / "s1.txt").write_text("done")
    result = CliRunner().invoke(main, ["status", str(path)])
    assert result.exit_code == 0, result.output
    assert "s0  to run" in result.output
    assert "s1  finished" in result.output
    assert "1 of 3 finished, 2 to run" in result.output


def test_errors_are_one_line_not_a_traceback(project):
    path = project(command="echo {{ sample.nope }}\n")
    result = CliRunner().invoke(main, ["launch", str(path), "--dry-run"])
    assert result.exit_code == 1
    assert "Error:" in result.output and "nope" in result.output
    assert "Traceback" not in result.output
