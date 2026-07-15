# k402-mcp — pay-per-call APIs for AI agents, settled in Kaspa

An MCP server that gives any agent (Claude Code, Claude Desktop, agent SDKs) a catalog of
**agent-payable APIs**: text tools (summarize, schema-guaranteed extraction, classify, rewrite),
web reading, embeddings + semantic search, live Kaspa mainnet chain data, GPU model tiers,
covenants-as-a-service (compile/derive/inspect/build/check/broadcast Silverscript covenants), and
RISC Zero attestation-as-a-service (upload a guest, preflight for a cycle-exact quote, prove, verify).
**No account, no card, no API key** — the agent opens a session, gets a personal KAS deposit
address, and every call meters against the prepaid balance. Prices are USD-pegged, fractions of a
cent per call, settled on Kaspa L1 (sub-second, ~zero fees).

The payment loop is agent-native: an unfunded call returns *instructions* ("send KAS to this
address, this call costs X"), so an agent can discover the service, learn how to pay, and use it
autonomously — the [x402](https://www.x402.org/) idea, on Kaspa.

## Install

```bash
pip install k402-mcp
```

Claude Code:
```bash
claude mcp add k402 -- k402-mcp
```

Claude Desktop / generic MCP config:
```json
{ "mcpServers": { "k402": { "command": "k402-mcp" } } }
```

Env (all optional): `K402_GATEWAY` (defaults to the public gateway), `K402_SESSION` (pin a funded
session key), `K402_STATE` (where the auto-opened session persists, default `~/.k402/session.json`).

## Use

1. `catalog` — see every service and its live KAS/USD price (free)
2. `open_session` — get a session + personal Kaspa mainnet deposit address (free); fund it from
   any wallet (min 0.25 KAS — confirmed in seconds)
3. Call anything: `summarize`, `extract`, `classify`, `rewrite`, `read_url`, `embed_text`,
   `search_index`/`search_query`, `kaspa_balance`, `kaspa_utxos`, `kaspa_tx_status`,
   `kaspa_fee_estimate`, `kaspa_network`, `generate` (tiers: chat / reason / code / kaspa-expert),
   `covenant_compile`/`covenant_address`/`covenant_utxos`/`covenant_build`/`covenant_check`/`covenant_broadcast`,
   `prove_guest_upload`/`prove_preflight`/`prove_submit`/`job_status`/`job_result`/`attest_verify`
4. `session_status` — watch the balance draw down (free)

Example, in Claude Code after `claude mcp add`:

> "Use the k402 tools to check the current Kaspa network status and summarize https://kaspa.org"

The first call returns how-to-pay instructions; fund the deposit address it gives you and retry.

## Notes & honest caveats

- **Prototype.** The payment rail is custodial-prepaid today (a trustless covenant-based
  settlement layer — the k402 protocol on Kaspa L1 — is in the works). Keep balances small:
  a few KAS covers thousands of calls.
- Paid tools return `payment_required` + instructions instead of failing opaquely.
- The gateway rate-limits per IP; prices float with KAS/USD, so `catalog` shows current numbers.
- Need KAS? Any exchange or wallet works — deposits are plain mainnet transfers.

---
`mcp-name: io.github.Kali123411/k402`

MIT © Kaspa Lab
