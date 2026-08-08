# Repository Guidance

- Keep the BLE transport behind interfaces so tests never require nearby hardware.
- Preserve structured JSON error contracts across CLI and MCP surfaces.
- Treat writes as dangerous: require explicit enablement and exact device confirmation.
- Treat `.codex-plugin`, `.mcp.json`, and `skills` at the repository root as the Codex Plugin
  source of truth. Run `uv run python scripts/sync_codex_plugin.py` instead of editing
  `plugins/blea` by hand.
- Keep `plugin.json`, `mcp.json`, `.codex-plugin/plugin.json`, and `.mcp.json` aligned.
- Before committing, run the Plugin sync check, Ruff checks, unit tests, Skill validator, both
  Plugin validators, and `uv run python scripts/check_distribution.py dist` against a clean build.
- Commit cohesive, verified checkpoints without including unrelated files.
