# hermes-flamepy

A [Hermes terminal backend](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/terminal-environment-plugin.md)
that runs shell commands and file operations in persistent
[Flame](https://xflops.io) sandboxes through `flamepy.agent.Sandbox`.

Select `terminal.backend: flamepy` and keep using Hermes's normal terminal and
file tools. The plugin registers a backend only; it adds no model-facing tools.

## Install and configure

Prerequisites:

- Python 3.11 or newer.
- A recent Hermes with `TerminalEnvironmentProvider`,
  `register_terminal_environment_provider`, and `PluginContext.state` support.
  Older Hermes versions must be updated; the plugin does not patch Hermes core.
- `flamepy>=0.7.0` and `filelock>=3.16,<4` installed in the Python environment
  running Hermes. The manifest declares these requirements; make sure your
  Hermes installation has installed them.
- A Flame cluster with the `flmbox` application and `runsc` on its executors,
  including [Flame #724](https://github.com/xflops/flame/issues/724) for a writable
  sandbox root filesystem.
  Configure the controller's SDK with `~/.flame/flame.yaml` or Flame's supported
  environment variables. See the [Flame installation guide](https://xflops.io/docs/installation/).

```bash
hermes plugins install xflops/hermes-flamepy
hermes plugins enable hermes-flamepy
hermes config set terminal.backend flamepy
hermes config set terminal.cwd "$PWD"
```

This uses the current Hermes project path inside Flame. The backend creates that
directory in the sandbox, even when it resembles a controller path such as
`/Users/klaus/project`. It does not copy the controller's files. Some Hermes TUI
versions also validate `terminal.cwd` locally before creating a session, so use
an existing local directory when starting through those clients.

The relevant `~/.hermes/config.yaml` configuration is:

```yaml
terminal:
  backend: flamepy
  cwd: /Users/klaus/project  # Set to your project's absolute path.
  container_persistent: true

plugins:
  isolation: in_process
  entries:
    hermes-flamepy:
      enabled: true
      settings:
        image: python:3.12-slim
```

Terminal environment objects run inside the Hermes process, so
`plugins.isolation: host` cannot serve this backend. Use Hermes's normal terminal
and file toolsets; there is no `flamepy` toolset to enable.

Every eligible executor needs `runsc` on the service's `PATH`, even when image
pulls already work. On Debian workers, install it with `sudo apt-get install
runsc`. A missing runtime makes `flmbox --daemon` exit before its shim socket is
ready; repeated worker readiness retries can then appear as a pending command
and a Hermes bootstrap timeout. Check the executor manager logs on the assigned
worker for the underlying startup failure.

## Execution and files

Each command runs in a fresh Bash process in the same Flame sandbox. Hermes's
`BaseEnvironment` preserves the working directory and exported shell variables
between calls while the sandbox stays open. Reconnecting to an open sandbox
also restores its shell snapshot. Cleanup suspends the session; resuming it
currently starts with fresh files and shell state. Normal nonzero command exits are returned with their output and status;
infrastructure failures are reported separately and commands are never replayed
automatically.

Flame's root filesystem is writable through a private sandbox overlay. The
backend creates and honors the working directory supplied by Hermes. With no
configured working directory, the provider defaults to the Hermes controller
process's current directory (`/workspace` if that directory was deleted).
`~` and `~/project` refer to the sandbox home, not the controller's home.

| Setting | Sandbox path |
|---|---|
| Home | `/root` |
| Working directory | Hermes's supplied path, created in the sandbox |
| Python packages | Normal image/interpreter/virtualenv installation paths |
| Application cache | `/root/.cache` |

A plain `python3 -m pip install <package>` uses the interpreter's normal package
directory; the backend does not set `PIP_TARGET` or `PYTHONPATH`. Virtualenvs and
system package installation can use the writable root, subject to image tooling,
user permissions, and the cluster's configured network access.
Custom images need `/bin/sh`, `bash`, `python3`, and standard shell utilities.
Add tools such as Git and ripgrep to the image when your workloads need them.
The `image` setting above is the default; Hermes's per-task `image=` override
takes precedence.

Hermes reads, writes, patches, and searches sandbox files through remote command
execution. Stdin is transported as bytes, including embedded NULs and exact
line endings. The local current directory is not mounted or uploaded. Explicit
artifact downloads can use Hermes's inherited `fetch_file()` support; bulk
upload/download and automatic project synchronization are not implemented.

## Reuse, multiple environments, and cleanup

Hermes caches its environment object across tool calls. When Hermes creates a
new object, this provider uses **Hermes's supplied environment identity
(`task_id`)**, namespaced by the profile's plugin-data directory and image, to
find the existing Flame sandbox. It does not use a MAC address or generate a new
persistent identity on each call. The actual Flame ID is a deterministic hash of
those inputs, with a generation suffix when a closed sandbox must be replaced.

- **Same identity, profile, and image:** reconnect to the same open sandbox,
  or resume the same session ID if suspended, including after Hermes restarts.
  Files, installed packages, exported variables, and the last recorded working
  directory remain available only while the sandbox stays open.
- **Different identity, profile, or image:** a separate Flame sandbox is created
  when that environment is first requested. Hermes decides environment identity
  and sharing; delegated tasks may share their parent's environment. This plugin
  does not add an agent tool for creating arbitrary sandboxes.
- **`container_persistent: true` (default):** normal cleanup suspends the Flame
  session and releases its executor after running work drains. The next use
  resumes the same session ID instead of creating a numbered replacement.
  Cleanup is normally triggered by Hermes's idle reaper or session teardown,
  rather than after each command.
- **`container_persistent: false`:** each environment creation gets a fresh
  sandbox; cleanup closes it. Hermes supplies session isolation in this mode.
- **Closed or missing sandbox:** create a replacement and report
  `environment_recreated` when prior state was lost. Closed Flame IDs are retained
  by the cluster, so replacements use a numeric generation suffix.
- **Suspended sandbox:** resume it and report `environment_recreated`.
  **Current Flame suspend/resume does not preserve files or processes.**
  If suspension is still draining a running command, reconnect waits up to
  60 seconds; a wait timeout reports an error without creating another session.

Generation records are kept under the current profile's plugin-data directory.
A file lock serializes connect/create decisions across local Hermes processes.
Keep that directory and the Hermes identity stable for reconnects. Concurrent
clients intentionally using the same identity share the workspace and shell
snapshot; shell changes follow last-writer-wins behavior.

Inspect and release retained sandboxes with Flame's CLI:

```bash
flmctl list -s
flmctl close -s <sandbox-id>
flmctl delete -s <sandbox-id>
```

## Limits and security

- Flame returns complete command results. Output is available on completion,
  rather than streamed live, and large results are buffered by the SDK.
- Command timeouts are forwarded to Flame and enforced by Hermes as well.
  Interrupting or forcibly cancelling a command requests suspension for a
  persistent session and stops waiting locally. Flame lets running work finish
  before releasing its executor; suspension is not an immediate process kill.
  Queued work is retained and can run after resume, even if Hermes stopped
  waiting for it. Use disposable sessions when cancellation must close the
  environment instead.
  A later call resumes the same session with fresh runtime state. Disposable
  sessions are closed on cancellation. The backend never resubmits commands automatically.
- Remote files are never accessed through Hermes's host-filesystem fast path.
  Controller TLS configuration is not forwarded to the sandbox. The backend
  retains Hermes's normal dangerous-command approval guards.
- Sandbox isolation and network access are governed by the Flame deployment.
  The plugin does not add network egress filtering or credential injection.
- Interactive stdin/stdout streams, desktop/browser placement, hosted tenant
  scopes, and durable filesystem snapshots are not supported.
- Hermes's generic container CPU, memory, disk, and mount options are not
  translated by this version; resource allocation follows the Flame deployment.

## Migrating from 0.3.1

Version 0.3.2 removes the read-only-root workarounds: `/tmp/hermes` as home and
the forced pip target/import path. Update `terminal.cwd` to your project path
with the command above if it still points to `/tmp/hermes/workspace`. Already
open Hermes environments retain their current configuration until recreated.
An old sandbox created with a read-only root needs to be suspended and resumed
on an updated executor before it can use the new paths. Files and packages in
the old `/tmp/hermes` layout are not copied to the native layout.

## Migrating from 0.2

The `sandbox` and `session` tools and the `flamepy` toolset have been removed.
Remove the old toolset selection and configure `terminal.backend: flamepy`.
Existing machine-wide `hermes-sandbox-*` and `hermes-session-*` sessions are not
reused or deleted; close them with `flmctl` if no longer needed.

## Development

Tests use the real Hermes `BaseEnvironment` with a local subprocess transport in
place of the Flame cluster. CI pins Hermes to
`31ce88f86e6cf4a66ef6aa315e4657a31bcf9915` and runs Python 3.11–3.13.

```bash
uv sync --extra test
HERMES_AGENT_PATH=/path/to/hermes-agent uv run pytest -q
uv run pylint hermes_flamepy
uv build
```

These checks cover shell state and files across reconnects, stdin fidelity,
artifact extraction, large output, errors, cancellation, cleanup, identity
isolation, and concurrent creation. They do not replace testing against a live
Flame cluster.
