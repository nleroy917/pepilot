import pathlib
import textwrap

import pytest


@pytest.fixture
def project(tmp_path: pathlib.Path):
    """Writes a PEP and a pepilot file into tmp_path; returns a function that does so."""

    def make(command: str = "echo {{ sample.sample_name }}\n", pepilot_extra: str = "", rows: int = 5,
             task: str = "name: test-run\n") -> pathlib.Path:
        lines = ["sample_name,group,value"] + [f"s{i},{'ab'[i % 2]},{i}" for i in range(rows)]
        (tmp_path / "samples.csv").write_text("\n".join(lines) + "\n")
        (tmp_path / "pilot.csv").write_text("sample_name,group,value\ns0,a,0\n")
        (tmp_path / "pep.yaml").write_text(textwrap.dedent("""\
            pep_version: 2.1.0
            sample_table: samples.csv
            sample_modifiers:
              append:
                out: out_src
              derive:
                attributes: [out]
                sources:
                  out_src: "{root}/out/{sample_name}.txt"
            project_modifiers:
              amend:
                pilot:
                  sample_table: pilot.csv
            """).replace("{root}", str(tmp_path)))
        body = textwrap.indent(command, "    ")
        (tmp_path / "pepilot.yaml").write_text(
            task + "pepilot:\n  pep: pep.yaml\n  workers: 2\n" + pepilot_extra + "  command: |\n" + body
        )
        return tmp_path / "pepilot.yaml"

    return make
