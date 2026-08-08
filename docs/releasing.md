# BLEA Release Runbook

This runbook separates repeatable repository work from account-owned publication. A release is not
complete until the package is installed again from its public distribution path.

## Release gates

1. Select a Semantic Version and align it in `pyproject.toml`, `src/blea/__init__.py`, `plugin.json`,
   and the base portion of `.codex-plugin/plugin.json`.
2. Update `CHANGELOG.md`, README installation text, platform status, and known limitations.
3. Run the unit suite, Ruff checks, Skill validator, Plugin validator, lock check, and diff check.
4. Build from a clean output directory and validate both artifacts:

   ```shell
   uv build --clear --no-create-gitignore
   uvx --from twine twine check dist/*
   ```

5. Install the wheel in isolated Python 3.10, 3.11, 3.12, and 3.13 environments. In each, verify
   `ble --version` and the adapter-free replay Workflow.
6. Verify the source archive contains the portable plugin directory, license, changelog, docs, and
   evidence fixture. Verify the wheel contains only the runtime package and metadata.
7. Confirm the worktree is clean and the release commit is present on the public default branch.

## Account-owned prerequisites

Before the first release:

- Create `github.com/Nitmi/blea`, set it as `origin`, and push the default branch.
- Create a protected GitHub environment named `pypi` with a required reviewer.
- Configure a pending PyPI Trusted Publisher for owner `Nitmi`, repository `blea`, workflow
  `release.yml`, and environment `pypi`.
- Protect `v*` tags and require the normal CI checks before publishing a GitHub Release.
- Decide and publish the Codex Git marketplace source separately from the portable repository-root
  plugin. Do not imply that PyPI installs the Agent Plugin.

These operations change public account state and require the repository owner's explicit action or
authorization. Do not substitute a long-lived PyPI API token.

## Automated publication

`.github/workflows/release.yml` runs only when a GitHub Release is published. It checks that the
release tag exactly matches `v<project-version>`, reruns software validation, builds from a cleared
`dist/`, validates metadata, transfers the artifacts to a minimal OIDC-enabled publish job, and
uploads them through PyPI Trusted Publishing.

The publish job is the only job with `id-token: write`. It uses the protected `pypi` environment and
does not receive a stored PyPI password or token. Published PyPI files are immutable; fixing a bad
release requires a new version.

## Release sequence

1. Complete every release gate locally and merge the release commit.
2. Wait for the default-branch CI matrix to pass.
3. Create an annotated `v<version>` tag from the verified release commit and push that tag.
4. Draft GitHub Release notes from `CHANGELOG.md`, explicitly retaining the platform limitations and
   write-safety policy.
5. Publish the GitHub Release and approve the protected `pypi` environment deployment.
6. Verify the PyPI project, file hashes, Trusted Publisher identity, and publish attestations.
7. In a fresh environment, run:

   ```shell
   uv tool install "blea==<version>"
   ble --version
   ble doctor --scan-timeout 2 --json
   ble replay <downloaded-fixture> run <downloaded-workflow> --json
   ```

8. Install or refresh the public Agent Plugin path, start a new Agent task, verify MCP initialization,
   and confirm the server tool count and version.
9. Record the GitHub Release URL, PyPI URL, artifact SHA-256 values, CI run, Plugin install result,
   and hardware/replay smoke results in the project TODO.

## First release status

Version 0.6.0 is the selected first public release candidate. Local wheel and source builds can be
accepted before the GitHub repository and PyPI publisher exist, but the release remains unpublished
until all post-publication checks above pass.
