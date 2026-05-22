# hermes-flamepy

Hermes plugin for tools backed by Flame's Python SDK. The first tool is
`flmexec`, which runs Python and shell snippets through Flame's built-in
`flmexec` application.

The tool opens or creates the Flame session with the active Hermes
`session_id`. That keeps execution tied to the current Hermes conversation and
lets repeated calls reuse the same Flame `flmexec` session.

## Install

```bash
hermes plugins install xflops/hermes-flamepy
hermes plugins enable hermes-flamepy
hermes tools enable flamepy
```

The Hermes environment must have `flamepy` installed and configured for the
target Flame endpoint.

## Tool

### flmexec

`flmexec` accepts:

- `language`: `python` or `shell`
- `code`: UTF-8 source code to run

The result is the script's UTF-8 stdout. If the code is not UTF-8, the output is
not UTF-8, the Flame session cannot be opened, or flmexec reports an error, the
tool raises an exception instead of returning an error object.

The Hermes handler is registered as async. It keeps the public tool contract
simple while offloading Flame's blocking Python SDK calls to a worker thread.

## Python Dependencies

For `language: "python"`, flmexec writes the snippet to `main.py` and runs it
with `uv run`. Do not rely on third-party packages already being installed on
the worker image. Put Python dependencies in uv inline script metadata at the
top of the submitted code.

Example tool payload:

```json
{
  "language": "python",
  "code": "# /// script\n# requires-python = \">=3.11\"\n# dependencies = [\n#   \"httpx>=0.27\",\n#   \"beautifulsoup4>=4.12\",\n# ]\n# ///\n\nimport httpx\nfrom bs4 import BeautifulSoup\n\nhtml = httpx.get(\"https://example.com\", timeout=10).text\nsoup = BeautifulSoup(html, \"html.parser\")\nprint(soup.title.string)\n"
}
```

The important part is the `# /// script` block. `uv run` reads it, resolves the
listed packages, and executes the script with those packages available.
