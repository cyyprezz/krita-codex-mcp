# Releasing

Keep the Python version, `server.json`, Codex plugin version/package pin, shipped TOML example
and install documentation aligned. Submit those changes through a reviewed pull request.

To request publication without a local tag push, update `.github/release-request.json` to exactly
`{"version": "<package-version>"}` in that pull request. Merging the request into `main` starts
the existing `release.yml` workflow. Other branches, missing requests and mismatched versions
are rejected. Normal pushes of version-matched `vX.Y.Z` tags remain supported.

The workflow verifies both Windows and Ubuntu, including the built distributions and actual
Codex tool discovery. Only after both pass does a main request create its tag at that exact
verified commit. Tag creation uses a short-lived GitHub Actions token and never updates an
existing tag. Publication then continues in the same workflow because a tag created with the
Actions token does not start another push workflow.

PyPI Trusted Publishing still uses the `release.yml` identity and the `pypi` environment; existing
environment approvals and branch policies remain enforced. Public PyPI file hashes must match
the verified artifacts before the workflow creates or completes the GitHub release.

An ordinary code merge does not request publication. Each new release needs a matching request
file change or an explicit tag push. A repeated request cannot overwrite an existing tag or
existing PyPI files. If a job fails, inspect its logs and rerun only the failed jobs after fixing
the cause; do not move release tags.
