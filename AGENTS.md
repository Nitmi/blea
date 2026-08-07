# Repository Guidance

- Keep the BLE transport behind interfaces so tests never require nearby hardware.
- Preserve structured JSON error contracts across CLI and MCP surfaces.
- Treat writes as dangerous: require explicit enablement and exact device confirmation.
- Keep `plugin.json`, `mcp.json`, `.codex-plugin/plugin.json`, and `.mcp.json` aligned.
- Before committing, run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, the Skill validator, and the Plugin validator.
- Commit cohesive, verified checkpoints without including unrelated files.

