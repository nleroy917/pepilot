import pytest

from pepilot.config import load_config
from pepilot.plan import PlanError, make_plan


def _exists_none(paths):
    return [False] * len(paths)


def test_renders_each_sample_from_the_pep(project):
    config = load_config(project(command="tool {{ sample.value }} --out {{ sample.out | quote }}\n"))
    plan = make_plan(config, exists=_exists_none)
    assert [r.name for r in plan.runs] == ["s0", "s1", "s2", "s3", "s4"]
    assert plan.runs[3].command == f"tool 3 --out {config.folder}/out/s3.txt\n"


def test_shell_variables_pass_through_untouched(project):
    config = load_config(project(command='echo "${HOME}" $SKYPILOT_JOB_RANK {{ sample.sample_name }}\n'))
    plan = make_plan(config, exists=_exists_none)
    assert plan.runs[0].command == 'echo "${HOME}" $SKYPILOT_JOB_RANK s0\n'


def test_quote_filter_quotes_for_the_shell(project):
    config = load_config(project(command="echo {{ sample.group | quote }} {{ 'a b; rm -rf /' | quote }}\n"))
    plan = make_plan(config, exists=_exists_none)
    assert plan.runs[0].command == "echo a 'a b; rm -rf /'\n"


def test_misspelled_attribute_names_the_sample_and_its_attributes(project):
    config = load_config(project(command="echo {{ sample.vaule }}\n"))
    with pytest.raises(PlanError, match=r"sample 's0'.*vaule.*attributes: group, out, sample_name, value"):
        make_plan(config, exists=_exists_none)


def test_attribute_named_like_a_dict_method(project, tmp_path):
    config = load_config(project(command="echo {{ sample.items }}\n"))
    (tmp_path / "samples.csv").write_text("sample_name,items\nx,3\n")
    plan = make_plan(config, exists=_exists_none)
    assert plan.runs[0].command == "echo 3\n"


def test_deals_samples_round_robin(project):
    config = load_config(project(rows=7))
    plan = make_plan(config, workers=3, exists=_exists_none)
    assert [[r.name for r in job] for job in plan.jobs] == [["s0", "s3", "s6"], ["s1", "s4"], ["s2", "s5"]]


def test_caps_workers_at_samples_to_run(project):
    config = load_config(project(rows=3))
    plan = make_plan(config, workers=1000, exists=_exists_none)
    assert len(plan.jobs) == 3
    assert any("1000" in note for note in plan.notes)


def test_requires_workers(project):
    path = project()
    path.write_text(path.read_text().replace("  workers: 2\n", ""))
    with pytest.raises(PlanError, match="how many jobs"):
        make_plan(load_config(path), exists=_exists_none)


def test_skips_samples_whose_paths_all_exist(project, tmp_path):
    config = load_config(project(pepilot_extra="  skip_if_exists: ['{{ sample.out }}']\n"))
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "s1.txt").write_text("done")
    (tmp_path / "out" / "s3.txt").write_text("done")
    plan = make_plan(config, workers=10)  # the real check, on local files
    assert [r.name for r in plan.done] == ["s1", "s3"]
    assert [r.name for r in plan.todo] == ["s0", "s2", "s4"]
    assert len(plan.jobs) == 3

    forced = make_plan(config, workers=10, force=True)
    assert forced.done == [] and len(forced.todo) == 5


def test_a_sample_needs_every_path_to_be_finished(project, tmp_path):
    config = load_config(project(pepilot_extra="  skip_if_exists: ['{{ sample.out }}', '{{ sample.out }}.done']\n"))
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "s0.txt").write_text("partial")
    plan = make_plan(config)
    assert plan.done == []


def test_nothing_to_do_means_no_jobs(project, tmp_path):
    config = load_config(project(rows=2, pepilot_extra="  skip_if_exists: ['{{ sample.out }}']\n"))
    plan = make_plan(config, exists=lambda paths: [True] * len(paths))
    assert plan.jobs == [] and len(plan.done) == 2


def test_rejects_samples_sharing_an_output(project):
    config = load_config(project(pepilot_extra="  skip_if_exists: ['/out/{{ sample.group }}.txt']\n"))
    with pytest.raises(PlanError, match="would overwrite each other"):
        make_plan(config, exists=_exists_none)


def test_amendment_selects_pilot_samples(project):
    config = load_config(project())
    plan = make_plan(config, amendments=["pilot"], exists=_exists_none)
    assert [r.name for r in plan.runs] == ["s0"]
    with pytest.raises(PlanError, match="no amendment.*nope.*it has: pilot"):
        make_plan(config, amendments=["nope"], exists=_exists_none)


def test_plain_csv_works_as_the_pep(project, tmp_path):
    config = load_config(project())
    plan = make_plan(config, pep=tmp_path / "samples.csv", exists=_exists_none)
    assert len(plan.runs) == 5


def test_rejects_duplicate_sample_names(project, tmp_path):
    config = load_config(project())
    (tmp_path / "samples.csv").write_text("sample_name,group,value\nx,a,1\nx,b,2\n")
    with pytest.raises(PlanError, match="two samples are named 'x'"):
        make_plan(config, exists=_exists_none)


def test_relative_skip_paths_are_relative_to_the_pepilot_file(project, tmp_path, monkeypatch):
    config = load_config(project(rows=2, pepilot_extra="  skip_if_exists: ['done/{{ sample.sample_name }}']\n"))
    (tmp_path / "done").mkdir()
    (tmp_path / "done" / "s1").write_text("")
    monkeypatch.chdir("/")
    plan = make_plan(config)
    assert [r.name for r in plan.done] == ["s1"]
