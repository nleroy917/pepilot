# pepilot

Run a command over every sample of a [PEP](https://pep.databio.org), in the cloud, with
[SkyPilot](https://docs.skypilot.ai).

You describe your samples once, as a PEP (a sample table, plus attributes derived from it), and
write the per-sample command once, as a template. pepilot fills the template in for every
sample, leaves out the ones already finished, splits the rest across N instances, and submits
them as SkyPilot managed jobs: provisioned, recovered from spot preemptions, and torn down for
you.

```console
$ pepilot launch pepilot.yaml --dry-run
pepilot: flagstat
  PEP        project_config.yaml
  samples    100
  finished   13 (skipped: all their skip_if_exists paths exist)
  to run     87
  jobs       25, 3-4 sample(s) each, one instance per job
  resources  {infra: aws/us-east-1, cpus: 4+, disk_size: 200}
  ...
```

## Install

```bash
pip install "pepilot[aws] @ git+https://github.com/nleroy917/pepilot"   # or [gcp]
```

The extra pulls in SkyPilot's support for that cloud and the [fsspec](https://filesystem-spec.readthedocs.io)
backend that checks for finished samples in its object store (s3fs, gcsfs). For Azure, install
`skypilot[azure]` and `adlfs` yourself. Then set up SkyPilot as usual: `sky check`.

## A pepilot file

A pepilot file is a [SkyPilot task YAML](https://docs.skypilot.ai/en/latest/reference/yaml-spec.html)
with one more section, `pepilot:`. Everything else is SkyPilot's and is passed to it as is, so
anything SkyPilot accepts works: `resources` (`any_of`, `use_spot`, `job_recovery`, ...), `envs`,
`setup`, `workdir`, `file_mounts`. The one thing you leave out is `run:`; pepilot writes it.

```yaml
name: flagstat

resources:
  infra: aws/us-east-1
  cpus: 4+
  disk_size: 200

setup: |                                    # once per instance
  sudo apt-get install -y -q samtools

pepilot:
  pep: project_config.yaml                  # the samples
  workers: 25                               # instances to spread them over
  command: |                                # once per sample
    aws s3 cp {{ sample.bam | quote }} sample.bam
    samtools flagstat sample.bam > flagstat.txt
    aws s3 cp flagstat.txt {{ sample.flagstat | quote }}
  skip_if_exists:                           # a sample is finished when all of these exist
    - "{{ sample.flagstat }}"
```

| `pepilot:` key | |
| --- | --- |
| `pep` | The PEP: a project config (`.yaml`) or a bare sample table (`.csv` with a `sample_name` column). Relative to this file. |
| `command` | Shell commands run once per sample: a template (below). |
| `workers` | How many jobs, each on its own instance. Capped at the number of samples to run. |
| `skip_if_exists` | Optional. Path templates; a sample whose paths all exist is finished and left out. |
| `amendments` | Optional. [PEP amendments](https://pep.databio.org/spec/specification/#project_modifiersamend) to activate. |

Relative paths in the file (`pep`, `workdir`, `file_mounts`, local `skip_if_exists` paths)
are relative to the file, wherever you run pepilot from.

[`examples/`](examples) has a minimal example (`hello`) and a realistic one (`s3-flagstat`).

### Templates

`command` and `skip_if_exists` are [Jinja](https://jinja.palletsprojects.com) templates, filled
in from each sample's attributes in the PEP, after any `append`, `derive`, `imply` or amendments:

- `{{ sample.bam }}` is the sample's `bam` attribute; `{{ project.name }}` reads the project config.
- `{{ sample.bam | quote }}` shell-quotes it. Use it for anything that could hold a space.
- `{% if sample.paired %}--paired{% endif %}` and the rest of Jinja work too.
- An empty cell is an empty string. A misspelled attribute is an error at launch, naming the
  sample and the attributes it does have.
- Shell syntax passes through untouched: `$HOME`, `${VAR}`, `$(cmd)`. Use `envs:` and `$VAR`
  for anything that is the same for every sample.

## Commands

```bash
pepilot launch pepilot.yaml --dry-run     # what would run, checked by SkyPilot; nothing launched
pepilot launch pepilot.yaml               # submit the jobs (asks first; -y to skip)
pepilot status pepilot.yaml               # which samples are finished (needs skip_if_exists)
pepilot render pepilot.yaml -o task.yaml  # the SkyPilot task a launch would submit
```

Options for `launch` and `render`:

| | |
| --- | --- |
| `-w, --workers N` | Number of jobs (instances), instead of `pepilot.workers`. |
| `--amend NAME` | Activate a PEP amendment, e.g. a `pilot` that lists two samples. Repeatable. |
| `--pep PATH` | Use another PEP. |
| `--env KEY=VALUE` | Set an env var for the jobs. Repeatable; `KEY` alone takes its value from your shell. |
| `--infra`, `--use-spot` | Override those resources. |
| `--force` | Run every sample, finished or not. |
| `-n, --name` | Job name, instead of the file's `name:`. |

After a launch, it's all SkyPilot: `sky jobs queue`, `sky jobs logs <id>`, `sky jobs cancel <id>`.

## How it runs

- **Everything is decided where you launch.** pepilot reads the PEP, fills in every sample's
  command and checks which are finished, on your machine. The instances get plain shell, and
  need neither pepilot nor the PEP. `--dry-run` shows the commands that will actually run, and
  `render` the whole task.
- **One managed job per worker.** The samples to run are dealt round-robin to N jobs: job k
  gets samples k, k+N, k+2N, ... The N jobs are one `sky jobs launch --num-jobs N`; each finds
  its share by `$SKYPILOT_JOB_RANK`.
- **Each instance runs `setup:` once, then its samples one after another.** Each sample runs
  in its own `bash -euo pipefail`: any failing command, an unset variable or a failure inside a
  pipe fails the sample, and the commands after it don't run. `$PEPILOT_SAMPLE` holds the
  sample's name.
- **A failed sample doesn't stop the others.** Each job runs all its samples, then fails if any
  of them failed, listing them in its log.

## Re-running, and `skip_if_exists`

At launch, pepilot checks every sample's `skip_if_exists` paths (local, `s3://`, `gs://`,
`az://`; anything fsspec reads). A sample with all of them present is finished and left out,
and the number of jobs is worked out from what's left. So re-running the same command is the
retry: after a launch where 6 of 100 samples failed, the next one runs those 6.

- **Point it at something that only exists once the sample is complete.** A single object
  uploaded as the last step works; for several outputs, finish the command with a marker, e.g.
  `touch done && aws s3 cp done {{ sample.out_dir }}/_SUCCESS`, and check for that.
- **Two samples with the same path are an error at launch**, since one would overwrite the
  other's results.
- Leave `skip_if_exists` out to run every sample every time; `--force` does so once.

A job that SkyPilot restarts (spot preemption, `job_recovery`) runs its whole share again,
finished samples included. To skip them there too, have the command check for its own output
first.

## Tips

- **Tools.** Install them in `setup:`, however suits you: a package manager, a container, a
  binary from a bucket. Pass versions as env vars (`envs:` / `--env`) so a launch says
  exactly what it ran: `--env TOOL_VERSION=$(git -C ../tool rev-parse HEAD)`.
- **Try a pilot first.** An amendment with one or two samples, `--amend pilot`, shows you a run's
  times and outputs before you pay for a hundred.
- **Keep instances next to the data.** Pin `infra:` to your bucket's region; transfer between
  an instance and a bucket in the same region is usually free, and across regions it is not.

## Limits

- Every sample's command is written into the one `run:` script, so it grows with the number of
  samples: with 1,000 samples it is about 360 KB, which SkyPilot's task parser accepts. Much
  larger runs haven't been tried against a SkyPilot API server.
- Samples are dealt by count, not size, so a job that draws the biggest inputs finishes last.
- All samples share one set of resources.

## Development

```bash
uv sync
uv run pytest
```
