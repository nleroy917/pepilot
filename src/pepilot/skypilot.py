"""Turning a plan into a SkyPilot task, and submitting it as managed jobs.

SkyPilot is imported only here, and only when needed: importing it takes a second or two.
"""

from __future__ import annotations

import contextlib
import copy
import os
import pathlib
from typing import Any, Iterator, Mapping

import yaml

from pepilot.config import Config
from pepilot.plan import Plan
from pepilot.script import run_script


def task_config(
    config: Config,
    plan: Plan,
    *,
    envs: Mapping[str, str] | None = None,
    infra: str | None = None,
    use_spot: bool | None = None,
) -> dict[str, Any]:
    """The SkyPilot task for a plan: the file's task, with `run:` written and overrides applied."""
    task = copy.deepcopy(config.task)
    task["name"] = plan.name
    if envs:
        task["envs"] = {**(task.get("envs") or {}), **envs}
    if infra is not None or use_spot is not None:
        resources = task.get("resources") or {}
        if not isinstance(resources, dict):
            raise ValueError("`resources` must be a mapping to override infra or use_spot")
        if infra is not None:
            resources["infra"] = infra
        if use_spot is not None:
            resources["use_spot"] = use_spot
        task["resources"] = resources
    task["run"] = run_script(plan.jobs)
    return task


def to_yaml(task: Mapping[str, Any], header: str = "") -> str:
    """The task as YAML, with `run:` as a readable block, not one long quoted line."""

    class _Dumper(yaml.SafeDumper):
        pass

    def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
        style = "|" if "\n" in value else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    _Dumper.add_representer(str, _str)
    body = yaml.dump(dict(task), Dumper=_Dumper, sort_keys=False, width=1000, allow_unicode=True)
    return header + body


@contextlib.contextmanager
def _in_folder(folder: pathlib.Path) -> Iterator[None]:
    """Relative paths in the task (workdir, file_mounts) are relative to the pepilot file."""
    before = pathlib.Path.cwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(before)


def check(task: Mapping[str, Any], folder: pathlib.Path) -> None:
    """Has SkyPilot parse the task, which catches schema mistakes without launching anything."""
    import sky

    with _in_folder(folder):
        sky.Task.from_yaml_config(copy.deepcopy(dict(task)))


def launch(task: Mapping[str, Any], folder: pathlib.Path, num_jobs: int) -> list[int]:
    """Submits the task as `num_jobs` managed jobs; returns their IDs once they are queued."""
    import sky

    with _in_folder(folder):
        sky_task = sky.Task.from_yaml_config(copy.deepcopy(dict(task)))
        request = sky.jobs.launch(sky_task, name=task["name"], num_jobs=num_jobs)
        result = sky.stream_and_get(request)
    job_ids = result[0] if isinstance(result, tuple) else result
    if job_ids is None:
        return []
    return list(job_ids) if isinstance(job_ids, (list, tuple)) else [job_ids]
