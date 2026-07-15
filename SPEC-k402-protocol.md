# k402 protocol v0.1 — generic HTTP payments on Kaspa

Status: draft sketch (2026-07-15). Positioning: what x402 is for EVM/stablecoin
HTTP payments, k402 is for Kaspa — an open convention any HTTP service can
implement to charge KAS per call, with no accounts or API keys. Our gateway
becomes reference implementation #1, not the protocol itself.

---

## 1. Core flow (scheme: `kaspa-utxo`, non-custodial per-call)

1. Client calls a paid endpoint with no payment attached.
2. Server replies **HTTP 402** with a machine-readable offer:

```json
{
  "k402": "0.1",
  "accepts": [
    {
      "scheme": "kaspa-utxo",
      "network": "mainnet",
      "amount_sompi": "1500000",
      "pay_to": "kaspa:qr…unique-per-payment",
      "payment_id": "p_8f3ab2c4",
      "expires": 1784074500,
      "description": "summarize, ~150 words"
    },
    { "scheme": "kaspa-session", "open": "/onboard/request" }
  ]
}
```

3. Client pays `amount_sompi` to `pay_to` (a **fresh HD-derived address per
   payment_id** — see §4) and retries with:

```
X-K402-Payment: kaspa-utxo <txid> <payment_id>
```

4. Server verifies against a node — tx accepted, output pays `pay_to` ≥
   `amount_sompi`, `payment_id` unspent-by-us — marks the payment_id used
   (replay protection), serves the request. At 10 bps this adds ~1–3 s to the
   first call; nothing custodial anywhere.

Overpayment is kept (dust) or credited as a session balance if the client
also holds a session. Expired quotes re-402 with a fresh offer.

## 2. Scheme: `kaspa-session` (prepaid, low-latency — what we ship today)

Existing model, now written down as a protocol scheme rather than a gateway
quirk: `open` endpoint mints `{session, depositAddress}`; deposits become
meterable balance; calls carry `X-Session`. Zero added latency per call,
mild custody (the merchant holds the float). Servers SHOULD offer both
schemes in `accepts`; clients pick.

## 3. Scheme: `kaspa-channel` (future — ties into covenants-as-a-service)

KIP-10 introspection covenants enable unidirectional payment channels:
client locks N KAS in a covenant, pays per call with off-chain signed
balance increments, either side settles on-chain. Best of both — per-call
granularity, zero per-call chain latency, non-custodial. Spec later;
the covenant service (SPEC-v0.2) is the substrate. This is the piece that
makes "k402 = payment infra for Kaspa" more than a slogan.

## 4. Design decisions

- **Fresh address per payment, not payload matching.** HD-derive `pay_to`
  from an xpub per payment_id. Verification = "does this address hold ≥
  amount", which any utxoindex node answers; no dependence on clients being
  able to set tx payloads (wallet support is uneven). Payload carrying the
  payment_id is an OPTIONAL optimization, not required for compliance.
- **Amounts in sompi as strings** — no float KAS anywhere on the wire.
- **USD pricing is the merchant's problem**: servers MAY price in USD
  internally (oracle) but the offer always quotes exact sompi with an expiry.
- **Verification depth**: default = accepted tx (1 confirmation ≈ 1 s).
  Merchants selling expensive calls MAY require `finality: N` DAA score depth
  in the offer.

## 5. Package restructure

New PyPI package `k402` (core protocol); `k402-mcp` becomes a thin consumer
depending on it.

```
k402/
  client.py    # k402.Client — httpx wrapper: catches 402s, picks a scheme,
               # pays (wallet or session), retries. Agents' entry point.
  server.py    # FastAPI/ASGI: @k402.paid(sompi=..., usd=...) decorator +
               # verifier (node-backed) + payment_id store (sqlite/DO)
  wallet.py    # optional hot wallet via kaspa Python SDK for kaspa-utxo;
               # session-only clients skip it (no keys, fund manually)
  schemes.py   # offer/verify logic per scheme; registry for kaspa-channel later
```

Server config mirrors the covenant app: `KASPA_BACKEND=grpc://…` own node in
prod, `resolver://mainnet` (PNN) for dev/test.

## 6. Facilitator (optional service, sold via k402 itself)

Merchants who won't run a utxoindex node can outsource verification:
`POST /facilitate/verify {offer, txid}` → signed verdict. Our chain-data
upstream already does 90% of this; it's a new cheap endpoint (~$0.0002).
Non-custodial — the facilitator never touches funds, only answers queries.
This is the "payment infra" business: every Kaspa-paid API that doesn't run
its own node pays us a fraction of a cent per verification.

## 7. Fees (decided 2026-07-15)

**The protocol extracts no fee.** No fee output, no routed settlement, no
percentage. Rationale: in `kaspa-utxo` the payment is client → merchant
directly on-chain — we are not in the money flow, so a baked-in fee is
enforceable only by the merchant's own (open-source) verifier and survives
exactly until the first fork removes it. Worse, a rail fee reintroduces the
thing k402 exists to eliminate; micropayments only work at ~zero cost.
(Precedent: x402 charges no protocol fee; Coinbase monetizes facilitation.)

**Fees are supported, transparent, and belong to services.** The offer schema
gains an OPTIONAL field so any commercial layer on top (facilitator, hosted
checkout, channel operator) can quote its cut as a visible line item:

```json
{
  "scheme": "kaspa-utxo",
  "amount_sompi": "1500000",
  "facilitator_fee": { "sompi": "2000", "to": "kaspa:qr…", "by": "k402.dev" }
}
```

Clients display it; merchants chose the facilitator knowing its price.
Revenue = facilitator verifications, our own catalog, and (later) channel
settlement/watchtower services — sell work on the rail, never tolls on it.

## 8. Adoption path

1. Ship `k402` core with both schemes + the FastAPI decorator.
2. Convert our own gateway to speak protocol-compliant 402s (it's ~already
   the session scheme; add kaspa-utxo offers).
3. `k402-mcp` 0.3 consumes `k402.Client` — any compliant third-party service
   becomes usable by agents with zero extra code.
4. Facilitator endpoint.
5. `kaspa-channel` scheme on top of covenants-as-a-service.
