# Repository Guidance

- Keep the BLE transport behind interfaces so tests never require nearby hardware.
- Preserve structured JSON error contracts across CLI and MCP surfaces.
- Treat writes as dangerous: require explicit enablement and exact device confirmation.
- Treat `.codex-plugin`, `.mcp.json`, and `skills` at the repository root as the Codex Plugin
  source of truth. Run `uv run python scripts/sync_codex_plugin.py` instead of editing
  `plugins/blea` by hand.
- Keep `plugin.json`, `mcp.json`, `.codex-plugin/plugin.json`, and `.mcp.json` aligned.
- Before committing, run the Plugin sync check, `uv run python scripts/check_agent_package.py`,
  Ruff checks, unit tests, and `uv run python scripts/check_distribution.py dist` against a clean
  build. Before a release, also run the current official Skill validator and both Plugin validators.
- Commit cohesive, verified checkpoints without including unrelated files.
