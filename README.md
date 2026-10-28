# hermes-flamepy

Hermes plugin that runs code in [Flame](https://xflops.io) instead of on the local
machine. It adds two tools to the `flamepy` toolset, both served by Flame's
built-in `flmbox` application:

| Tool | Runs | Use it for |
|---|---|---|
| `sandbox` | one snippet at a time in a persistent sandbox (`flamepy.agent.Sandbox`) | multi-step work whose steps share files under `/tmp` |
| `session` | a batch of snippets in parallel, one executor each (`flamepy.agent.Session`) | independent snippets: sweeps, many small computations |

## Install

```bash
hermes plugins install xflops/hermes-flamepy
hermes plugins enable hermes-flamepy
hermes tools enable flamepy
```

Prerequisites:

- A Flame cluster with the `flmbox` application registered and `runsc` on the
  executors. See the [Installation Guide](https://xflops.io/docs/installation/).
  The sandbox must be able to reach the network, which the built-in `flmbox`
  application allows (`--network=host`, flame#613).
- `flamepy` 0.7.0 or later, installed and configured for the cluster in the
  Hermes environment.

## Tools

Both tools take the code first, then `language` (`python` or `shell`, default
`python`), then an optional `timeout` in seconds.

### sandbox

`sandbox` takes `code`, `language` and `timeout`. It returns the script's stdout.
A nonzero exit raises an error that includes the exit code, stderr and stdout.
Calls run one at a time in the same sandbox, so files under `/tmp` persist
between calls.

### session

`session` takes `codes` (a list of 1-64 snippets), `language` (for all of them)
and `timeout` (for each). Each snippet is submitted with `Session.submit_code`, so
they run concurrently on separate executors and do not share `/tmp`; use
`sandbox` when steps need each other's files.

The result is a JSON array with one object per snippet, in the order of `codes`,
always `{"exit_code", "stdout", "stderr"}`:

```json
[
  {"exit_code": 0, "stdout": "4\n", "stderr": ""},
  {"exit_code": 1, "stdout": "", "stderr": "Traceback ..."}
]
```

An `exit_code` other than 0 means that snippet failed. If a snippet could not run
at all, `exit_code` is `-1` and `stderr` holds the error. A failing snippet does
not fail the others.

## Sandbox and session ids

Neither tool takes an id. Each machine has one sandbox and one session, created
on first use as Flame `hermes-sandbox-<mac>` (from `python:3.12-slim`) and
`hermes-session-<mac>`. Calls share them, across Hermes sessions.

`<mac>` is the MAC address (12 hex digits) of the machine's first physical
network interface, so the ids are the same in every Hermes process and across
restarts. Virtual interfaces (loopback, bridges, VPNs, containers) are skipped:

- Linux: the first interface, by name, that has a `/sys/class/net/<name>/device`
  link.
- macOS: the first hardware port in `networksetup -listallhardwareports`.
- Other systems: `uuid.getnode()`, if it found a real MAC.

If there is no physical interface, for example in a container with only virtual
ones, the tools fail with an error instead of inventing an id. The first physical
interface can change when an adapter or dock is plugged in or removed, which
selects a different sandbox and session.

A restarted Hermes reconnects while the sandbox or session is open. A suspended
sandbox is resumed (and starts fresh). A closed one keeps its id on the cluster,
so the plugin opens `...-1`, `-2`, ... and the old files are lost. The plugin
never closes either, so each keeps its executors until something else does; list
and remove them with `flmctl list -s`, `flmctl close -s <id>` and
`flmctl delete -s <id>`.

The session is opened with `flamepy.agent.open_session(ssn_id=...)`, which opens
the session or creates it when missing.

## Python packages

The root filesystem is read-only and only `/tmp` is writable, in the sandbox and
in session executors. The plugin sets `PIP_TARGET` and `PYTHONPATH` to
`/tmp/site-packages` on every call, so a plain install works and the package can
be imported afterwards:

```json
{"language": "shell", "code": "pip install httpx"}
```

In `sandbox`, installed packages stay available to later calls until the sandbox
is recreated. In `session`, each snippet may run on a different executor, so
install and import within the same snippet (a `shell` snippet that runs
`pip install` and then the program, or `python` code that installs first). The
image has no `uv`, so PEP 723 inline metadata is not supported.
