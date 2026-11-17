"""From a pepilot file and its PEP to a plan: which samples run, with what command, in which job.

Everything is decided here, on the machine that launches: each sample's command is filled in,
finished samples are left out, and the rest are dealt to the jobs. The instances only run the
shell they are handed (see `script.py`).
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Sequence

from pepilot import render, storage
from pepilot.config import Config


class PlanError(ValueError):
    """The samples cannot be planned: bad PEP, template, or settings."""


@dataclasses.dataclass(frozen=True)
class SampleRun:
    """One sample, with its command filled in."""

    name: str
    command: str
    skip_paths: tuple[str, ...]


@dataclasses.dataclass
class Plan:
    """What a launch would do."""

    name: str
    """The SkyPilot job name."""
    pep: pathlib.Path
    amendments: tuple[str, ...]
    runs: list[SampleRun]
    """Every sample, in the PEP's order."""
    done: list[SampleRun]
    """Samples skipped because all their `skip_if_exists` paths exist."""
    jobs: list[list[SampleRun]]
    """The samples to run, dealt round-robin: job k runs samples k, k+N, k+2N, ..."""
    requested_workers: int
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def todo(self) -> list[SampleRun]:
        """
        The samples that will run, in the PEP's order.
        """
        done = {r.name for r in self.done}
        return [r for r in self.runs if r.name not in done]


def load_samples(pep: pathlib.Path, amendments: Sequence[str] = ()) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """
    Reads a PEP: its samples as (name, attributes) in order, and the project config.
    """
    import peprs

    if not pep.exists():
        raise PlanError(f"{pep}: no such file")
    try:
        project = peprs.Project(str(pep))
    except Exception as e:  # noqa: BLE001 - peprs raises RuntimeError and friends
        raise PlanError(f"{pep}: cannot be read as a PEP: {e}") from None
    if amendments:
        available = list(project.list_amendments() or [])
        missing = [a for a in amendments if a not in available]
        if missing:
            raise PlanError(f"{pep}: no amendment(s) {', '.join(missing)}; it has: {', '.join(available) or 'none'}")
        try:
            project.activate_amendments(list(amendments))
        except Exception as e:  # noqa: BLE001
            raise PlanError(f"{pep}: cannot activate {', '.join(amendments)}: {e}") from None

    index = project.sample_name_colname
    samples = []
    seen: set[str] = set()
    for attributes in project.to_dict(by_sample=True)["samples"]:
        name = attributes.get(index)
        if name is None or str(name) == "":
            raise PlanError(f"{pep}: a sample has no {index!r}")
        name = str(name)
        if name in seen:
            raise PlanError(f"{pep}: two samples are named {name!r}")
        seen.add(name)
        samples.append((name, attributes))
    return samples, dict(project.config or {})


def make_plan(
    config: Config,
    *,
    pep: pathlib.Path | None = None,
    amendments: Sequence[str] | None = None,
    workers: int | None = None,
    name: str | None = None,
    force: bool = False,
    exists: storage.ExistsFn = storage.exists,
) -> Plan:
    """
    Plans a launch. Arguments override the file's settings.

    `force` runs every sample, finished or not. `exists` checks paths; tests pass their own.
    """
    pep = pep or config.pep
    if pep is None:
        raise PlanError(f"{config.path}: no PEP: set `pepilot.pep`, or pass one")
    amendments = tuple(config.amendments if amendments is None else amendments)
    workers = workers if workers is not None else config.workers
    if workers is None:
        raise PlanError(
            f"{config.path}: how many jobs? Set `pepilot.workers`, or pass --workers. Each job is "
            "one instance, which runs its share of the samples one after another."
        )
    if workers < 1:
        raise PlanError(f"workers must be at least 1, not {workers}")

    samples, project = load_samples(pep, amendments)
    if not samples:
        raise PlanError(f"{pep}: has no samples")

    try:
        command = render.compile_template(config.command, "pepilot.command")
        skip_templates = [
            render.compile_template(t, f"pepilot.skip_if_exists[{i}]") for i, t in enumerate(config.skip_if_exists)
        ]
        runs = []
        for sample_name, attributes in samples:
            runs.append(
                SampleRun(
                    name=sample_name,
                    command=render.fill(command, "pepilot.command", sample_name, attributes, project),
                    skip_paths=tuple(
                        render.fill(t, f"pepilot.skip_if_exists[{i}]", sample_name, attributes, project).strip()
                        for i, t in enumerate(skip_templates)
                    ),
                )
            )
    except render.TemplateError as e:
        raise PlanError(str(e)) from None

    _check_distinct_paths(runs)

    plan = Plan(
        name=name or config.task.get("name") or "pepilot",
        pep=pep,
        amendments=amendments,
        runs=runs,
        done=[],
        jobs=[],
        requested_workers=workers,
    )

    if config.skip_if_exists and not force:
        paths = [p for r in runs for p in r.skip_paths]
        try:
            found = dict(zip(paths, exists([_against(config.folder, p) for p in paths])))
        except storage.StorageError as e:
            raise PlanError(f"checking skip_if_exists: {e}") from None
        plan.done = [r for r in runs if all(found[p] for p in r.skip_paths)]

    todo = plan.todo
    if todo:
        n = min(workers, len(todo))
        if n < workers:
            plan.notes.append(f"asked for {workers} workers, but only {len(todo)} samples need running: using {n}")
        plan.jobs = [todo[k::n] for k in range(n)]
    return plan


def _against(folder: pathlib.Path, path: str) -> str:
    """A relative local path is relative to the pepilot file, like every path in it."""
    if "://" in path or pathlib.PurePath(path).is_absolute():
        return path
    return str(folder / path)


def _check_distinct_paths(runs: Sequence[SampleRun]) -> None:
    """Two samples with the same `skip_if_exists` path would overwrite each other's results."""
    owner: dict[str, str] = {}
    for r in runs:
        for p in r.skip_paths:
            if not p:
                raise PlanError(f"sample {r.name!r}: a skip_if_exists path is empty")
            if p in owner and owner[p] != r.name:
                raise PlanError(
                    f"samples {owner[p]!r} and {r.name!r} both have skip_if_exists path {p}: "
                    "their results would overwrite each other"
                )
            owner[p] = r.name
