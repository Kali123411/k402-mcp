# k402 protocol — moved

The k402 payment protocol is now maintained as a standalone spec and reference
implementation, separate from this gateway client:

- **Protocol spec:** https://github.com/Kali123411/k402/blob/main/PROTOCOL.md
- **Reference implementation (Python):** https://github.com/Kali123411/k402 —
  `pip install k402` (client, FastAPI server middleware, PNN/node chain
  backends, watch-only xpub derivation)

The protocol is an open convention — HTTP 402 offer body + `X-K402-Payment`
header, schemes `kaspa-utxo` (non-custodial per-call), `kaspa-session`
(prepaid balance), `kaspa-channel` (reserved). The protocol extracts no fee;
services on top quote a transparent `facilitator_fee`.

This package (`k402-mcp`) will migrate to consuming `k402.Client` in 0.3,
which makes any protocol-compliant third-party service usable by MCP agents
with no extra code.
