import pytest

from pepilot.config import ConfigError, load_config


def test_splits_pepilot_section_from_task(project):
    path = project(task="name: t\nresources:\n  cpus: 2+\nsetup: echo hi\n")
    config = load_config(path)
    assert config.task == {"name": "t", "resources": {"cpus": "2+"}, "setup": "echo hi"}
    assert config.pep == path.parent / "pep.yaml"
    assert config.workers == 2
    assert config.command.startswith("echo {{ sample.sample_name }}")


def test_rejects_run_section(project):
    path = project(task="name: t\nrun: echo hi\n")
    with pytest.raises(ConfigError, match="writes `run:` itself"):
        load_config(path)


def test_rejects_unknown_key(project):
    path = project(pepilot_extra="  comand: oops\n")
    with pytest.raises(ConfigError, match="unknown key.*comand"):
        load_config(path)


def test_requires_pepilot_section(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("name: t\nrun: echo hi\n")
    with pytest.raises(ConfigError, match="needs a `pepilot:` section"):
        load_config(path)


@pytest.mark.parametrize("workers", ["0", "-1", "two", "true"])
def test_rejects_bad_workers(project, workers):
    path = project()
    path.write_text(path.read_text().replace("workers: 2", f"workers: {workers}"))
    with pytest.raises(ConfigError, match="workers"):
        load_config(path)


def test_skip_if_exists_accepts_string_or_list(project):
    one = load_config(project(pepilot_extra="  skip_if_exists: '{{ sample.out }}'\n"))
    assert one.skip_if_exists == ("{{ sample.out }}",)
    two = load_config(project(pepilot_extra="  skip_if_exists: ['a', 'b']\n"))
    assert two.skip_if_exists == ("a", "b")
