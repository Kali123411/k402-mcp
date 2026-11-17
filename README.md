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

## Hosted endpoint — zero install

The same server runs hosted at the public gateway (streamable HTTP):

```
https://x402-compute.68cxgfyr0.workers.dev/mcp
```

Claude Code:
```bash
claude mcp add --transport http k402 https://x402-compute.68cxgfyr0.workers.dev/mcp
```

Hosted sessions are **per-connection**: `open_session` binds the payment session to your MCP
connection and returns the key — save it, and re-attach with `use_session(<key>)` after a
reconnect. Nothing is shared between connections.

## Install (local stdio)

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
   `kaspa_fee_estimate`, `kaspa_network`, `generate` (tiers: chat / llama / reason / think / code / kaspa-expert),
   `covenant_compile`/`covenant_address`/`covenant_utxos`/`covenant_build`/`covenant_check`/`covenant_broadcast`,
   `prove_guest_upload`/`prove_preflight`/`prove_submit`/`job_status`/`job_result`/`attest_verify`,
   `payment_options`, `pay_per_call`
4. `session_status` — watch the balance draw down (free)

**New here? Start with `open_session`** — one funded deposit address, and every call meters
against it. That's the whole loop. The other schemes (per-call coins, or trustless payment
channels) are for when you want them; `payment_options` explains each.

### The kUSD market (`pay_kusd`)

Services can also be priced in **kUSD**, the covenant-native dollar on Kaspa. `registry_search(currency="kUSD")`
lists them (`price_cents`, ranked by settled cents); `pay_kusd(capability, request_body, max_price_cents)` pays
the best one from your own kUSD wallet and returns the result; `kusd_balance` shows the wallet. The payment is
a kUSD box of exactly the quoted cents to the provider, on the x402 wire (`PAYMENT-REQUIRED` /
`PAYMENT-SIGNATURE`, kUSD as the asset) or k402's `kusd-utxo` header — the same money either way. Each box
also carries 1.5 KAS (the provider's to recover) plus ~0.1 KAS fee; `pay_kusd` reports the full outlay. Short
on kUSD? It buys the shortfall + 50¢ through the kUSD PSM with your KAS.

Local (stdio) install only — it spends the key behind `KUSD_KEY_FILE`, which the hosted endpoint never
holds. Needs `pip install 'k402-mcp[kusd]'`, the `kusd_pay` binary from
[kusd-framework](https://github.com/gordonmurray-coding/kusd-framework) (`contracts/v2/harness`), and
env `KUSD_PAY_BIN`, `KUSD_KEY_FILE` (mode 600), `KUSD_AUTHORITY` (the kUSD authority covenant id — pins the
family), optional `KUSD_NODE_URL`, `KUSD_RELAY_URL`, `KUSD_NETWORK` (default `testnet-10`), `K402_REGISTRY`.
TN10 only today.

**The whole loop, as tools.** `registry_search(currency="kUSD")` / `registry_provider` / `registry_activity`
(the Kaspa Agent Market at `K402_REGISTRY`, default `https://market.kaspa-402.org`) to discover; `get_quote`
to see exactly what a call costs with nothing paid (the kUSD requirement off the provider's 402, the per-offer
payee key, the outlay, the checks, what your policy has left); `pay_kusd` to pay; `verify_settlement(txid)` to
prove it counted — the registry's node-verified row, and with the offer's payee/cents/payment_id a second check
on your own wallet's node; `list_service` / `delist_service` to sell (a listing signed with your wallet key, the
10¢ kUSD fee paid through k402). These are the machine interfaces behind
[kaspa-app.vercel.app/kusd/market](https://kaspa-app.vercel.app/kusd/market).

**The operator sets the bounds, not the agent.** `pay_kusd` only spends inside a `SpendPolicy` written by
whoever funds the wallet, measured against a sqlite ledger so a restart never resets a budget:
`KUSD_SPEND_POLICY` (JSON file) and `KUSD_SPEND_LEDGER` (default `~/.kusd-market/spend.db`). Without a
policy file the server applies a conservative default — 25¢ per call, 200¢ and 20 calls per hour, 100¢ of
PSM top-ups per hour, stop after 2 unserved payments in an hour. Fields (all optional): `max_per_call_cents`,
`max_total_cents`, `window_seconds`, `max_window_cents`, `max_window_calls`, `max_window_topup_cents` (`0` =
never convert KAS), `max_window_failures`, `allow_payees`, `allow_capabilities`, `require_listed_payee`. The
agent's own `max_price_cents` can only tighten the per-call ceiling. A refused call returns
`{"error", "code", "spend"}` and paid nothing; `kusd_spend_status` (free) shows what is left. The kill
switch and the policy are the operator's alone, from the `k402-spend` CLI in the `k402` package:

```
k402-spend --ledger ~/.kusd-market/spend.db --policy ~/.kusd-market/policy.json status
k402-spend --ledger ~/.kusd-market/spend.db freeze      # every pay_kusd refused until unfreeze
```

### Trustless payment channels (`kaspa-channel`)

Where the gateway offers it, `channel_config`/`channel_status` expose a covenant payment channel:
your KAS sits in a Kaspa L1 covenant (not the merchant's wallet), and you pay per call with signed
vouchers — the merchant can only ever claim what you signed, and you reclaim the rest. Opening and
funding a channel needs your Kaspa key, so that part runs in the [`k402`](https://pypi.org/project/k402/)
Python client (`k402.channel`); these MCP tools cover discovery and status.

Example, in Claude Code after `claude mcp add`:

> "Use the k402 tools to check the current Kaspa network status and summarize https://kaspa.org"

The first call returns how-to-pay instructions; fund the deposit address it gives you and retry.

## Notes & honest caveats

- **Prototype.** Payments settle either custodial-prepaid (sessions) or trustlessly via
  `kaspa-channel` covenant payment channels (live on mainnet, covenant unaudited — channel sizes
  are capped). Keep balances small: a few KAS covers thousands of calls.
- Paid tools return `payment_required` + instructions instead of failing opaquely.
- The gateway rate-limits per IP; prices float with KAS/USD, so `catalog` shows current numbers.
- Need KAS? Any exchange or wallet works — deposits are plain mainnet transfers.

---
`mcp-name: io.github.Kali123411/k402`

MIT © Kaspa Lab
