"""Filling in the per-sample templates (`command`, `skip_if_exists`) with Jinja.

Templates use `{{ sample.<attribute> }}`, never `${...}`, so they cannot be confused with the
shell's own variables, which pass through untouched. A misspelled attribute is an error at
launch, not an empty string on some instance. `None` (an empty cell) renders as an empty string,
and the `quote` filter shell-quotes a value: `{{ sample.path | quote }}`.
"""

from __future__ import annotations

import shlex
from typing import Any, Iterator, Mapping

import jinja2


class TemplateError(ValueError):
    """A template could not be filled in for a sample."""


class Attributes:
    """A sample's (or the project's) attributes, for templates: `sample.x` and `sample['x']`.

    Deliberately has no methods of its own (not even a dict's), so that an attribute named
    `items`, `values`, `keys`, `get`, ... still means the attribute.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]):
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, key: str) -> Any:
        try:
            return self._values[key]
        except KeyError:
            raise AttributeError(key) from None

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._values

    def __repr__(self) -> str:
        return f"Attributes({self._values!r})"


def _finalize(value: Any) -> Any:
    return "" if value is None else value


def _quote(value: Any) -> str:
    return shlex.quote("" if value is None else str(value))


_ENV = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
    finalize=_finalize,
)
_ENV.filters["quote"] = _quote


def compile_template(source: str, what: str) -> jinja2.Template:
    """Parses a template once, so syntax errors surface before any sample is filled in."""
    try:
        return _ENV.from_string(source)
    except jinja2.TemplateSyntaxError as e:
        raise TemplateError(f"{what}, line {e.lineno}: {e.message}") from None


def fill(template: jinja2.Template, what: str, sample_name: str, sample: Mapping[str, Any],
         project: Mapping[str, Any]) -> str:
    """Fills in a template for one sample."""
    try:
        return template.render(sample=Attributes(sample), project=Attributes(project))
    except jinja2.UndefinedError as e:
        known = ", ".join(sorted(sample))
        raise TemplateError(f"{what} for sample {sample_name!r}: {e.message}. Its attributes: {known}") from None
    except jinja2.TemplateError as e:
        raise TemplateError(f"{what} for sample {sample_name!r}: {e}") from None
