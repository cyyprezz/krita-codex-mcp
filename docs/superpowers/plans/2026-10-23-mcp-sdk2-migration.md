# MCP SDK 2 migration implementation plan

> **For agentic workers:** Execute this plan inline with `superpowers:executing-plans`; use one independent whole-branch review after verification.

**Goal:** Run the existing 13-tool Krita server on stable MCP SDK 2.3, supporting modern and legacy clients.

**Architecture:** Preserve the local STDIO process, authenticated loopback bridge, async tool handlers and Krita Qt-thread dispatcher. Change only the SDK boundary, development clients, tests and compatibility documentation.

**Tech stack:** Python >=3.11, MCP >=2.3,<3, uv, unittest, existing Windows/Ubuntu CI.

**Spec:** The user-approved discussion on 2026-10-23: migrate SDK/API, preserve architecture, verify tool results/images/errors/concurrency and Codex/Claude compatibility in a separate PR.

## Global constraints

- Start from the green Claude PR #18 at `f0bf9a346ef382ef4f76882d5e387e7fd8810f8c`; submit the SDK delta as a separate stacked PR.
- Preserve public tool names, argument bounds, read/write annotations, allowed roots, token handling and bridge protocol.
- Keep async handlers async; the SDK's sync-worker change must not introduce a new execution model.
- Pin the lockfile to stable SDK 2.3.0 and bound the dependency below 3.
- Do not publish a release or move tags; distinguish development SDK support from published 0.2.2.
- Keep model-free Codex and loopback-stub Claude discovery; do not claim a live Krita GUI test.

## Review focus

- Modern `server/discover` and the legacy `initialize` handshake must both reach the real built server.
- PNG content must remain a direct image block with the original bytes and MIME type.
- Invalid/path-escaping requests must not reach the bridge; busy/timeout errors retain structured details.
- Concurrent async calls must preserve bridge request identities and cancellation behavior.
- Client-facing schemas and tool approval annotations must survive the SDK's field-name changes.

## Task 1: Server and boundary contracts

**Files:** pyproject.toml, uv.lock, src/krita_codex_mcp/server*.py, tests/test_server_surface.py, new tests/test_stdio_contract.py.

**Interface:** build_server(config, client=None) returns the SDK 2 MCPServer with the same tools and bridge behavior.

- [ ] Verify the unchanged baseline: `uv run --locked python -m unittest discover -s tests -v` and `uv run --locked ruff check .`.
- [ ] Add a real STDIO modern-discovery regression and observe its expected failure with SDK 1.
- [ ] Inspect SDK 2's real public API; migrate imports, constructors, result fields and annotations without compatibility shims.
- [ ] Preserve existing business assertions while adapting direct SDK result access. Add meaningful wire tests for images, bounded arguments, errors and async calls.
- [ ] Verify the full suite and lint under the updated lockfile before committing.

## Task 2: Candidate distributions and client verification

**Files:** scripts/stdio_surface_smoke.py, scripts/live_mcp_e2e.py, docs/codex-compatibility.md, README.md and CI where required.

**Interface:** SDK smoke exercises both protocol eras against the candidate wheel; existing actual Codex and Claude discovery continues to consume that wheel.

- [ ] Migrate the SDK smoke and live-test client to supported SDK 2 APIs; keep all live behavioral checks.
- [ ] Build wheel and sdist, run strict metadata/payload checks, rebuild from the sdist and compare payloads.
- [ ] Run modern/legacy wheel discovery and run full Windows/Ubuntu CI including actual Codex and Claude discovery.
- [ ] Document the candidate SDK/protocol support accurately, with explicit GUI-test limits and published-version boundaries.
- [ ] Obtain one independent review, fix material findings with regression tests, and present a reviewable stacked PR.
