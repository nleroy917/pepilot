"""Reading a pepilot file: a SkyPilot task YAML with one extra section, `pepilot:`.

Everything outside `pepilot:` is SkyPilot's and is passed to it unchanged, except `run:`,
which pepilot writes from `pepilot.command`.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import yaml


class ConfigError(ValueError):
    """The pepilot file is missing something or has something it should not."""


# What `pepilot:` may contain, and what each is for (also used in error messages).
FIELDS = {
    "pep": "the PEP: a project config (.yaml) or a sample table (.csv)",
    "command": "shell commands run once per sample (a Jinja template)",
    "skip_if_exists": "paths (Jinja templates); a sample whose paths all exist is skipped",
    "workers": "how many jobs (one instance each) to split the samples across",
    "amendments": "PEP amendments to activate",
}


@dataclasses.dataclass(frozen=True)
class Config:
    """A parsed pepilot file."""

    path: pathlib.Path
    """The file itself (absolute)."""
    pep: pathlib.Path | None
    """The PEP, resolved against the file's folder; None if the file names none."""
    command: str
    skip_if_exists: tuple[str, ...]
    workers: int | None
    amendments: tuple[str, ...]
    task: dict[str, Any]
    """The SkyPilot task: the file without `pepilot:`."""

    @property
    def folder(self) -> pathlib.Path:
        """Relative paths in the file (the PEP, `workdir`, `file_mounts`) are relative to this."""
        return self.path.parent


def load_config(path: str | pathlib.Path) -> Config:
    """
    Reads and checks a pepilot file.
    """
    path = pathlib.Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path}: no such file") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a SkyPilot task (a mapping) with a `pepilot:` section")

    block = raw.pop("pepilot", None)
    if not isinstance(block, dict):
        raise ConfigError(f"{path}: needs a `pepilot:` section with at least `command:`")
    unknown = sorted(set(block) - set(FIELDS))
    if unknown:
        known = "\n".join(f"  {k}: {v}" for k, v in FIELDS.items())
        raise ConfigError(f"{path}: unknown key(s) in `pepilot:`: {', '.join(unknown)}. Known:\n{known}")
    if "run" in raw:
        raise ConfigError(
            f"{path}: has a `run:` section, but pepilot writes `run:` itself. "
            "Put the per-sample commands in `pepilot.command`, and anything that should run "
            "once per instance in `setup:`."
        )

    command = block.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ConfigError(f"{path}: `pepilot.command` must be the shell commands to run per sample")

    workers = block.get("workers")
    if workers is not None and (isinstance(workers, bool) or not isinstance(workers, int) or workers < 1):
        raise ConfigError(f"{path}: `pepilot.workers` must be a whole number of at least 1, not {workers!r}")

    pep = block.get("pep")
    if pep is not None and not isinstance(pep, str):
        raise ConfigError(f"{path}: `pepilot.pep` must be a path, not {pep!r}")

    return Config(
        path=path,
        pep=(path.parent / pep).resolve() if pep else None,
        command=command,
        skip_if_exists=_strings(block.get("skip_if_exists"), "skip_if_exists", path),
        workers=workers,
        amendments=_strings(block.get("amendments"), "amendments", path),
        task=raw,
    )


def _strings(value: Any, key: str, path: pathlib.Path) -> tuple[str, ...]:
    """A string or a list of strings, as a tuple; None as an empty one."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"{path}: `pepilot.{key}` must be a string or a list of strings")
