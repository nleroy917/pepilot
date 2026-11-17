"""The `pepilot` command: launch, render and status."""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import click
import yaml

from pepilot import __version__, skypilot, storage
from pepilot.config import Config, ConfigError, load_config
from pepilot.plan import Plan, PlanError, make_plan

_ERRORS = (ConfigError, PlanError, storage.StorageError)


def _plan_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """Options shared by every command that plans."""
    options = [
        click.argument("pepilot_file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path)),
        click.option("--pep", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
                     help="PEP to use instead of the file's `pepilot.pep`."),
        click.option("--amend", "amendments", multiple=True, metavar="NAME",
                     help="Activate a PEP amendment (repeatable); replaces the file's `pepilot.amendments`."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _launch_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """Options that shape the jobs."""
    options = [
        click.option("--force", is_flag=True, help="Run every sample, even those whose skip_if_exists paths all exist."),
        click.option("-w", "--workers", type=click.IntRange(min=1),
                     help="Number of jobs (one instance each); at most one per sample to run. "
                          "Overrides `pepilot.workers`."),
        click.option("-n", "--name", help="Job name; default: the file's `name:`."),
        click.option("--env", "envs", multiple=True, metavar="KEY[=VALUE]",
                     help="Set an env var for the jobs (repeatable); KEY alone takes its value from here."),
        click.option("--infra", help="Override resources.infra, e.g. aws/us-east-1."),
        click.option("--use-spot/--no-use-spot", default=None, help="Override resources.use_spot."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _parse_envs(envs: tuple[str, ...]) -> dict[str, str]:
    parsed = {}
    for item in envs:
        key, sep, value = item.partition("=")
        if not key:
            raise click.BadParameter(f"{item!r}: expected KEY=VALUE or KEY", param_hint="--env")
        if not sep:
            if key not in os.environ:
                raise click.BadParameter(f"{key} is not set here, so give it a value: --env {key}=...",
                                         param_hint="--env")
            value = os.environ[key]
        parsed[key] = value
    return parsed


def _display(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(pathlib.Path.cwd()))
    except ValueError:
        return str(path)


def _plan(
    pepilot_file: pathlib.Path,
    pep: pathlib.Path | None,
    amendments: tuple[str, ...],
    force: bool,
    workers: int | None = None,
    name: str | None = None,
    need_workers: bool = True
) -> tuple[Config, Plan]:
    config = load_config(pepilot_file)
    plan = make_plan(
        config,
        pep=pep.resolve() if pep else None,
        amendments=amendments or None,
        # `status` does not need a worker count; any will do.
        workers=workers if need_workers else (workers or config.workers or 1),
        name=name,
        force=force,
    )
    return config, plan


def _summary(plan: Plan, task: dict[str, Any] | None, force: bool) -> str:
    lines = [f"pepilot: {plan.name}"]
    pep = _display(plan.pep)
    if plan.amendments:
        pep += f" (amendments: {', '.join(plan.amendments)})"
    lines.append(f"  PEP        {pep}")
    lines.append(f"  samples    {len(plan.runs)}")
    if force:
        lines.append("  finished   not checked (--force)")
    elif plan.runs and plan.runs[0].skip_paths:
        lines.append(f"  finished   {len(plan.done)} (skipped: all their skip_if_exists paths exist)")
    lines.append(f"  to run     {len(plan.todo)}")
    if plan.jobs:
        sizes = sorted({len(j) for j in plan.jobs})
        each = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}"
        lines.append(f"  jobs       {len(plan.jobs)}, {each} sample(s) each, one instance per job")
    if task is not None and task.get("resources"):
        flow = yaml.safe_dump(task["resources"], default_flow_style=True, sort_keys=False, width=1000).strip()
        lines.append(f"  resources  {flow}")
    for note in plan.notes:
        lines.append(f"  note: {note}")
    if plan.jobs:
        lines.append("")
        shown = plan.jobs if len(plan.jobs) <= 20 else plan.jobs[:10]
        for k, job in enumerate(shown):
            names = " ".join(r.name for r in job[:8]) + (f" ... (+{len(job) - 8})" if len(job) > 8 else "")
            lines.append(f"  job {k:<3} {names}")
        if len(shown) < len(plan.jobs):
            lines.append(f"  ... and {len(plan.jobs) - len(shown)} more jobs")
    return "\n".join(lines)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="pepilot")
def main() -> None:
    """Run a command over every sample of a PEP, in the cloud, with SkyPilot.

    PEPILOT_FILE is a SkyPilot task YAML with a `pepilot:` section. See
    https://github.com/nleroy917/pepilot
    """


@main.command()
@_plan_options
@_launch_options
@click.option("--dry-run", is_flag=True, help="Plan and check everything, launch nothing.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask before launching.")
def launch(pepilot_file, pep, amendments, force, workers, name, envs, infra, use_spot, dry_run, yes) -> None:
    """Launch managed jobs that run the command for every sample not yet finished."""
    try:
        config, plan = _plan(pepilot_file, pep, amendments, force, workers, name)
        task = skypilot.task_config(config, plan, envs=_parse_envs(envs), infra=infra, use_spot=use_spot) \
            if plan.jobs else None
    except _ERRORS as e:
        raise click.ClickException(str(e)) from None

    click.echo(_summary(plan, task, force))
    if task is None:
        click.echo("\nNothing to run: every sample is finished. --force runs them all again.")
        return

    try:
        skypilot.check(task, config.folder)
    except Exception as e:  # noqa: BLE001 - SkyPilot raises many types for a bad task
        raise click.ClickException(f"SkyPilot does not accept the task: {e}") from None

    if dry_run:
        first = plan.jobs[0][0]
        click.echo(f"\nSample {first.name} would run:\n")
        click.echo("    " + first.command.rstrip("\n").replace("\n", "\n    "))
        click.echo(f"\nDry run: nothing launched. `pepilot render {_display(pepilot_file)}` prints the whole task.")
        return

    if not yes:
        click.confirm(f"\nLaunch {len(plan.jobs)} managed job(s) for {len(plan.todo)} sample(s)?", abort=True)
    job_ids = skypilot.launch(task, config.folder, num_jobs=len(plan.jobs))
    ids = " ".join(str(i) for i in job_ids)
    click.echo(f"\nSubmitted {len(job_ids)} managed job(s): {ids}")
    click.echo("  sky jobs queue                 status of every job")
    if job_ids:
        click.echo(f"  sky jobs logs {job_ids[0]:<16} follow one job's log")
        click.echo(f"  sky jobs cancel {ids}   stop them")
    if plan.runs and plan.runs[0].skip_paths:
        click.echo(f"  pepilot status {_display(pepilot_file)}   which samples are finished")


@main.command()
@_plan_options
@_launch_options
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=pathlib.Path),
              help="Write the task here instead of to stdout.")
def render(pepilot_file, pep, amendments, force, workers, name, envs, infra, use_spot, output) -> None:
    """Print the SkyPilot task a launch would submit, run script and all.

    The result runs with plain SkyPilot, too: `sky jobs launch TASK --num-jobs N`, from the
    pepilot file's folder (relative paths in it are relative to that folder).
    """
    try:
        config, plan = _plan(pepilot_file, pep, amendments, force, workers, name)
        if not plan.jobs:
            raise click.ClickException("nothing to run: every sample is finished (--force runs them all again)")
        task = skypilot.task_config(config, plan, envs=_parse_envs(envs), infra=infra, use_spot=use_spot)
    except _ERRORS as e:
        raise click.ClickException(str(e)) from None
    header = (
        f"# Rendered by pepilot {__version__} from {_display(pepilot_file)}: "
        f"{len(plan.todo)} sample(s) in {len(plan.jobs)} job(s). From {_display(config.folder) or '.'}, run:\n"
        f"#   sky jobs launch <this file> --num-jobs {len(plan.jobs)}\n"
    )
    text = skypilot.to_yaml(task, header)
    if output:
        output.write_text(text)
        click.echo(f"wrote {output}: {len(plan.todo)} sample(s) in {len(plan.jobs)} job(s)", err=True)
    else:
        sys.stdout.write(text)


@main.command()
@_plan_options
@click.option("--todo", "only_todo", is_flag=True, help="List only the samples not finished.")
def status(pepilot_file, pep, amendments, only_todo) -> None:
    """Which samples are finished, judged by their skip_if_exists paths."""
    try:
        config, plan = _plan(pepilot_file, pep, amendments, force=False, need_workers=False)
    except _ERRORS as e:
        raise click.ClickException(str(e)) from None
    if not config.skip_if_exists:
        raise click.ClickException(f"{_display(pepilot_file)} has no `pepilot.skip_if_exists`, so pepilot "
                                   "cannot tell which samples are finished")
    done = {r.name for r in plan.done}
    width = max(len(r.name) for r in plan.runs)
    for r in plan.runs:
        if r.name in done:
            if not only_todo:
                click.echo(f"{r.name:<{width}}  finished")
        else:
            click.echo(f"{r.name:<{width}}  to run")
    click.echo(f"\n{len(done)} of {len(plan.runs)} finished, {len(plan.runs) - len(done)} to run", err=True)


if __name__ == "__main__":
    main()
