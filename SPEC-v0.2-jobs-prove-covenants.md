# k402 v0.2 spec — job model, attestation-as-a-service, covenants-as-a-service

Status: draft sketch (2026-07-14). Client tools land in `k402_mcp.py` (0.2.0);
endpoints land in the gateway worker; proving runs on a dedicated prover box
(GPU now, Xilinx FPGA backend later — same API either way).

---

## 1. Job model (new gateway primitive)

Everything in the catalog today is synchronous. Proving is not (seconds–minutes),
so the gateway grows a generic async-job layer that any future slow service can use.

### Endpoints

| Method | Path | Cost | Notes |
|---|---|---|---|
| POST | `/jobs/{service}` | service-priced | submit; debits session, returns `job_id` |
| GET | `/jobs/{id}` | free | status poll |
| GET | `/jobs/{id}/result` | free | result once `done`; 404 before, 410 after TTL |
| DELETE | `/jobs/{id}` | free | cancel; full refund if still `queued` |

### Job object

```json
{
  "job_id": "j_8f3ab2…",
  "service": "prove",
  "status": "queued | running | done | failed | cancelled",
  "queue_position": 2,
  "cost_kas": 0.42,
  "created": 1784073600, "started": null, "finished": null,
  "error": null,
  "result_ttl": 86400
}
```

### Billing & refunds

- Debit the session at **submit** time (same 402 flow as today if underfunded).
- `failed` → automatic full refund to the session.
- `cancelled` while `queued` → full refund; while `running` → no refund.
- Results are retained 24 h (`result_ttl`), then 410.

### Prover-box transport

The gateway is a Cloudflare worker; the prover box should **poll outbound**
(no inbound ports on the FPGA/GPU machine — same posture as the miners):

- `GET /internal/jobs/next?worker=prover-1` (long-poll, bearer token)
- `POST /internal/jobs/{id}/result` / `POST /internal/jobs/{id}/fail`
- Worker heartbeats; a job with no heartbeat for 120 s goes back to `queued`.

Queue state lives in a Durable Object (per-service FIFO, one DO per service).

---

## 2. Attestation-as-a-service (zk proving)

RISC Zero receipts: customer supplies a guest program + input, gets back
`{journal, seal}` — a portable attestation anyone can verify without trusting us.

### Two-phase pricing (cycles vary by orders of magnitude)

1. **Preflight** (sync, flat ~$0.002): execute-only run, no proof.
   Returns exact cycle count and a signed quote.

   `POST /prove/preflight {image_id, input_b64}` →
   ```json
   {"cycles": 18700000, "segments": 12,
    "quote_id": "q_…", "quote_kas": 0.38, "quote_expires": 1784074200}
   ```
2. **Submit** with `quote_id` → debits the quoted amount, enqueues.

   Escape hatch for one-shot callers: submit without a quote but with a
   `max_kas` cap — billed on actual cycles, job fails (refunded) if the cap
   would be exceeded.

### Guest program delivery

ELFs are megabytes; don't push them through tool args per call.

- `POST /prove/guests` (paid, ~$0.01): upload ELF (b64 or URL), returns
  content-addressed `image_id` (the risc0 image ID, so it doubles as the
  verification key). Stored server-side, reusable across jobs.
- Gateway also ships a **registered-guest catalog** (e.g. `median-attest`,
  `sig-check`) usable by name — the kas-oracle relayer becomes customer #1.

### Limits (day one, arbitrary code is arbitrary)

- Cycle cap per job (e.g. 2^32), input ≤ 8 MiB, queue depth per session,
  one concurrent `running` job per session.

### Verification endpoint

`POST /attest/verify {receipt_b64, expected_image_id?}` — sync, ~$0.0002 or
free. Cheap on purpose: it lets our customers' counterparties verify receipts
against us, which is the whole attestation story.

---

## 3. Covenants-as-a-service (MAINNET-first)

Targets Kaspa MAINNET using the post-Crescendo KIP-10 transaction-introspection
opcodes — i.e. standard-opcode covenants only. The zk-precompile path (zkgate)
exists only on the TN10 fork and stays behind a `network="testnet-10"`
extension flag until precompiles land on mainnet; the compiler must reject
zk constructs when targeting mainnet rather than emitting scripts that
can never validate.

All sync, all fit the existing `_paid` pattern. Keys never leave the customer:
we compile, derive, inspect, build and broadcast — we never sign. Every tool
takes `network` (default `"mainnet"`).

Mainnet broadcast moves real KAS: `covenant_check` (dry-run against the node)
should be strongly recommended in the `covenant_broadcast` docstring, and the
gateway should refuse to broadcast a tx it can statically tell fails script
evaluation.

| Tool / endpoint | Cost (~) | Does |
|---|---|---|
| `covenant_compile` `POST /covenant/compile` | $0.002 | silverscript/template → `{script_hex, covenant_id, abi}` |
| `covenant_address` `POST /covenant/address` | $0.0003 | covenant_id + params → address on the target network |
| `covenant_utxos` `POST /covenant/utxos` | $0.0003 | live UTXO set filtered by covenant_id (state inspection) |
| `covenant_build_spend` `POST /covenant/build` | $0.001 | unsigned spend tx + per-input sighashes |
| `covenant_broadcast` `POST /covenant/broadcast` | $0.0003 | submit signed tx to the node (mainnet = real KAS; run covenant_check first) |
| `covenant_check` `POST /covenant/check` | $0.0005 | dry-run validate a signed tx against the node (catches "false stack entry" before broadcast) |

### Upstreams & environments

A new FastAPI app (`covenant_app`) next to `chain_app`, plus the silverscript
compiler. Node backend is env-selected:

| Env | Backend | Protocol | Notes |
|---|---|---|---|
| production | own mainnet node | gRPC 16110 | utxoindex required |
| testing / CI | PNN via Kaspa Resolver (least-loaded public node) | wRPC (Borsh 17110 / JSON 18110) | zero infra; PNN is explicitly dev/test-grade — never point production at it |
| zk extension | TN10 fork node | gRPC | zkgate / precompile covenants |

Config: `KASPA_BACKEND=grpc://host:port` or `KASPA_BACKEND=resolver://mainnet`.
The app needs a wRPC client for the resolver path (kaspa Python SDK /
`kaspa-wrpc-client`) alongside the existing gRPC client — PNN nodes do not
serve gRPC.

Test-money note: PNN endpoints are mainnet, so integration tests that
broadcast spend real (tiny) KAS. Read-path tests (compile/address/utxos/build/
check) are free of that; keep broadcast tests on TN10 or behind an explicit
flag.

---

## 4. Client tool surface (k402_mcp.py 0.2.0)

New generic job tools: `job_status(job_id)`, `job_result(job_id)`,
`job_cancel(job_id)` — free, shared by every async service.

Proving: `prove_guest_upload`, `prove_preflight`, `prove_submit`
(quote_id or max_kas), plus `attest_verify` (sync).

Covenants: the six tools above, 1:1 with endpoints; each takes
`network: str = "mainnet"` (`"testnet-10"` enables the zk-precompile
extension against the fork node).

Agents poll: submit → do other work → `job_status` → `job_result`.
No blocking waits inside MCP tools (a tool call that sleeps for minutes
starves the agent loop and gateway timeouts kill it anyway).
