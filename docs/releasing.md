# Release process

Releases are tag-driven and publish one previously verified wheel and source distribution to PyPI
before creating the matching GitHub release. Do not upload local artifacts manually and do not
store a PyPI API token in GitHub.

## One-time setup

1. Create a GitHub environment named `pypi` in `cyyprezz/krita-codex-mcp`. Configure a required
   reviewer when the repository plan supports it.
2. Sign in to PyPI with two-factor authentication enabled.
3. Under the PyPI account's **Publishing** page, create a pending GitHub Trusted Publisher with:

   | Field | Value |
   |---|---|
   | PyPI project name | `krita-codex-mcp` |
   | Owner | `cyyprezz` |
   | Repository | `krita-codex-mcp` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

A pending publisher does not reserve the package name. Complete the first release directly after
the setup. The workflow receives only a short-lived OIDC credential through `id-token: write`.

## Release checklist

1. Update `src/krita_codex_mcp/__init__.py`, `server.json`, installation examples and release notes
   to the same stable semantic version.
2. Run the complete Krita live checklist and require `Ready for productive use.` from
   `krita-codex-install check`.
3. Merge the release-preparation PR into `main` and require a clean Windows CI result.
4. From an up-to-date, clean `main`, run:

   ```powershell
   $env:PYTHONPATH = "src"
   .\.venv\Scripts\python.exe scripts\verify_release.py --tag v0.1.0
   git tag v0.1.0
   git push origin v0.1.0
   ```

The tag workflow rejects a version mismatch and any commit not already contained in `main`. It
then repeats unit tests, release contracts, Ruff, wheel/sdist builds, payload comparison and the
13-tool STDIO smoke on Windows. Only those stored artifacts are downloaded by the OIDC-authorized
PyPI job. The GitHub release is created from the same artifacts after PyPI accepts them.

PyPI distributions and release tags are immutable. Never move or reuse a published version. If
PyPI succeeds but GitHub release creation fails, retain the tag and create the GitHub release from
the workflow's `python-distributions` artifact instead of rebuilding or republishing.

## Public verification

After the workflow completes, verify the public package without a checkout:

```powershell
uvx --from krita-codex-mcp==0.1.0 krita-codex-install --version
uvx --from krita-codex-mcp==0.1.0 krita-codex-mcp
```

The second command is a STDIO server and waits for MCP input; use `scripts/stdio_surface_smoke.py`
for the bounded 13-tool check. Confirm that PyPI displays the repository links, MIT license and the
README ownership marker `mcp-name: io.github.cyyprezz/krita-codex-mcp`.

## Official MCP Registry

Publish Registry metadata only after the exact `server.json` version exists publicly on PyPI:

```powershell
mcp-publisher login github
mcp-publisher publish
```

Verify the result through the Registry API search for
`io.github.cyyprezz/krita-codex-mcp`. Registry versions are immutable; update the package,
`server.json` and README marker together before publishing any later version.
