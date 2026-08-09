# BLEA Release Runbook

This runbook separates repeatable repository work from account-owned publication. A release is not
complete until the package is installed again from its public distribution path.

## Release gates

1. Select a Semantic Version and align it in `pyproject.toml`, `src/blea/__init__.py`, `plugin.json`,
   `server.json`, and the base portions of both Codex Plugin manifests.
2. Update `CHANGELOG.md`, README installation text, platform status, and known limitations. Public
   marketplace install commands must use the release's immutable `v<version>` tag, never `main`.
3. Synchronize the root Codex package into `plugins/blea`, verify there is no drift, then run the
   unit suite, Ruff checks, Skill validator, both Plugin validators, lock check, and diff check:

   ```shell
   uv run python scripts/sync_codex_plugin.py
   uv run python scripts/sync_codex_plugin.py --check
   uv run python scripts/check_agent_package.py
   uv run python scripts/check_mcp_registry.py
   ```

   The repository-local checker is the CI gate. Also run the current official Skill validator and
   both official Plugin validators before publishing to catch upstream contract changes.

4. Build from a clean output directory and validate both artifacts:

   ```shell
   uv build --clear --no-create-gitignore
   uv run python scripts/check_distribution.py dist
   uvx --from twine twine check dist/*
   ```

   The Linux CI and release jobs also build the root registry sandbox image from the checkout. Its
   `BLEA_VERSION` argument and OCI version label must match the project version. The image is for MCP
   introspection and does not replace native host installation for physical adapters.

   Build the deterministic OpenAI skills-only upload bundle separately from the Python artifacts:

   ```shell
   uv run python scripts/build_openai_skill_bundle.py --output-directory openai-dist
   ```

5. Install the wheel in isolated Python 3.10, 3.11, 3.12, and 3.13 environments. In each, verify
   `ble --version` and the adapter-free replay Workflow.
6. Confirm the distribution checker reports that the source archive contains the portable plugin
   package, Git marketplace, Codex distribution, license, changelog, docs, and evidence fixture,
   while the wheel contains only the runtime package and metadata.
7. Confirm the worktree is clean and the release commit is present on the public default branch.

## Account-owned prerequisites

Before the first release:

- Create `github.com/Nitmi/blea`, set it as `origin`, and push the default branch.
- Create a protected GitHub environment named `pypi` with a required reviewer.
- Configure a pending PyPI Trusted Publisher for owner `Nitmi`, repository `blea`, workflow
  `release.yml`, and environment `pypi`.
- Protect `v*` tags and require the normal CI checks before publishing a GitHub Release.
- Keep the public Codex Git marketplace source separate from the portable repository-root plugin.
  Do not imply that PyPI installs the Agent Plugin.
- Confirm the public GitHub repository and PyPI package satisfy ownership verification for the
  `io.github.nitmi/blea` namespace. The published PyPI README must contain the exact hidden
  `mcp-name: io.github.nitmi/blea` marker.

These operations change public account state and require the repository owner's explicit action or
authorization. Do not substitute a long-lived PyPI API token.

## Automated publication

`.github/workflows/release.yml` runs only when a GitHub Release is published. It checks that the
release tag exactly matches `v<project-version>`, reruns software validation, builds from a cleared
`dist/`, validates metadata, transfers the artifacts to a minimal OIDC-enabled publish job, uploads
them through PyPI Trusted Publishing, and then publishes `server.json` to the official MCP Registry
with the pinned `mcp-publisher` binary and GitHub OIDC.

Only the PyPI and Registry jobs receive `id-token: write`, and each uses a short-lived audience-bound
OIDC credential. The PyPI job uses the protected `pypi` environment; neither job receives a stored
publication password or token. The Registry job depends on successful PyPI publication because the
Registry verifies the package ownership marker from the public PyPI page. Published PyPI files and
Registry versions are immutable; fixing a bad release requires a new version.

## Release sequence

1. Complete every release gate locally and merge the release commit.
2. Wait for the default-branch CI matrix to pass.
3. Create an annotated `v<version>` tag from the verified release commit and push that tag. Do not
   move or replace an existing release tag.
4. Confirm the protected tag resolves to the verified commit and contains the installable
   marketplace before publishing:

   ```shell
   git show "v<version>:.agents/plugins/marketplace.json"
   git show "v<version>:plugins/blea/.codex-plugin/plugin.json"
   ```

5. Draft GitHub Release notes from `CHANGELOG.md`, explicitly retaining the platform limitations and
   write-safety policy.
6. Publish the GitHub Release and approve the protected `pypi` environment deployment.
7. Verify the PyPI project, file hashes, Trusted Publisher identity, and publish attestations.
   Confirm the rendered PyPI description contains the hidden
   `mcp-name: io.github.nitmi/blea` ownership marker. Confirm the dependent Registry job succeeded,
   then query the official Registry for the exact server name and version.
8. In a fresh environment, run:

   ```shell
   uv tool install "blea==<version>"
   ble --version
   ble doctor --scan-timeout 2 --json
   ble replay <downloaded-fixture> run <downloaded-workflow> --json
   ```

9. Install the public Git marketplace from the same immutable release tag, install `blea@blea`,
   start a new Agent task, verify MCP initialization, and confirm the server tool count and version:

   ```shell
   codex plugin marketplace add Nitmi/blea --ref v<version>
   codex plugin add blea@blea
   ```

   If `blea` is already configured at an older ref, remove that marketplace first and add it again
   at the new tag. `marketplace upgrade` only refreshes the configured ref; it does not select a new
   release.
10. Record the GitHub Release URL, PyPI URL, MCP Registry entry, artifact SHA-256 values, CI run,
   Plugin install result, and hardware/replay smoke results in the project TODO.

## OpenAI Plugins Directory

OpenAI review is intentionally separate from the release workflow because developer identity,
legal URLs, availability, and attestations belong to the maintainer account.

1. Download the `openai-skill-bundle` artifact from the successful release run and verify its hash.
2. Use `docs/openai-plugin-submission.md` as the source for listing copy, starter prompts, and the
   five positive plus three negative test cases.
3. Submit as **Skills only**. Do not add BLEA's local stdio MCP server as a public MCP URL.
4. Complete identity verification, logo rights, privacy policy, terms, availability, and platform
   attestations in `https://platform.openai.com/plugins` as the account owner.
5. Submit for review. After approval, choose the publication time and verify the public Plugins
   Directory listing separately in ChatGPT and Codex.

## First release

Version 0.6.0 is the first public release, but its tag predates the Git marketplace package. The
documented `0.6.0` marketplace bridge therefore uses a verified full commit SHA without changing
that tag. Version 0.6.1 is the first release required to support version-aligned marketplace
installation directly from its release tag. Version 0.6.2 is the first release prepared for the
official MCP Registry and OpenAI skills-only review. A release is complete only after the GitHub
workflow, PyPI installation, Registry entry, public replay, and documented post-publication checks
above pass; OpenAI review may complete later on the marketplace's own schedule.
