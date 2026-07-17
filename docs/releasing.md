# Maintainer release process

This page is for project maintainers. End users should follow the
[Windows installation guide](windows-installation.md). The GitHub `pypi` environment and PyPI
Trusted Publishing are already configured; account creation and publisher setup do not belong in
the repository instructions.

Releases are tag-driven. The workflow publishes one previously verified wheel and source
distribution to PyPI, then creates the matching GitHub release from the same artifacts. Do not
upload locally rebuilt artifacts and do not store a PyPI API token in GitHub.

## Prepare the release

1. Choose the next stable semantic version and update it consistently in
   `src/krita_codex_mcp/__init__.py`, `server.json`, installation examples and release notes.
2. Run the complete [Krita live checklist](krita-e2e-checklist.md) and require
   `Ready for productive use.` from `krita-codex-install check`.
3. Run the full offline test, lint, build, distribution and STDIO-surface verification.
4. Merge the release-preparation PR into `main` and require a clean Windows CI result.

## Publish

From an up-to-date, clean `main`, replace `X.Y.Z` with the prepared version and run:

```powershell
$Version = "X.Y.Z"
$Tag = "v$Version"
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe scripts\verify_release.py --tag $Tag
git tag $Tag
git push origin "refs/tags/$Tag"
```

The tag workflow rejects a version mismatch and any commit not already contained in `main`. It
then repeats the Windows verification and publishes through short-lived OIDC credentials. If
GitHub pauses at **Review deployments**, approve the `pypi` environment to continue the release.

PyPI distributions and release tags are immutable. Never move or reuse a published version. If
PyPI succeeds but GitHub release creation fails, retain the tag and create the GitHub release from
the workflow's `python-distributions` artifact instead of rebuilding or republishing.

## Verify the public release

After the workflow completes, verify the exact public version without a checkout:

```powershell
uvx --refresh --from "krita-codex-mcp==$Version" krita-codex-install --version
```

Confirm that PyPI displays the repository links, MIT license and README ownership marker
`mcp-name: io.github.cyyprezz/krita-codex-mcp`, and that the GitHub release contains the same wheel
and source distribution. CI performs the bounded 13-tool STDIO smoke before publication.

## Official MCP Registry

Publish Registry metadata only after the exact `server.json` version exists publicly on PyPI:

```powershell
mcp-publisher login github
mcp-publisher publish
```

Verify the result through the Registry API search for
`io.github.cyyprezz/krita-codex-mcp`. Registry versions are immutable; update the package and
`server.json` versions together, and keep the README ownership marker in place for every later
release.
