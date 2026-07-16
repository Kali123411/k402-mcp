#!/usr/bin/env python3
# k402-mcp — MCP server exposing the k402 agent-payable API catalog as tools.
#
# Any MCP-capable agent (Claude Code, Claude Desktop, agent SDKs) gets the whole paid catalog —
# text tools, Kaspa chain data, GPU inference, covenants-as-a-service, and RISC Zero
# attestation-as-a-service — as ordinary tools. Pay either way:
#   • prepaid session — open_session() mints a personal KAS deposit address; fund once and every
#     paid call meters against it (simplest, no per-call step); or
#   • per-call in any coin the gateway accepts (Kaspa, Pearl, BTC, LTC, DOGE, BCH, DASH, and EVM
#     assets ETC/ETH/USDC/USDT) — a 402 returns the coin offers; pay one on-chain and call
#     pay_per_call() to complete it. See payment_options().
# A 402 comes back as readable "how to pay" instructions either way, so an agent can discover the
# service, learn how to pay, and use it — the whole loop without a human account, card, or API key.
#
# Env: K402_GATEWAY (default: the public gateway), K402_SESSION (optional fixed session key),
#      K402_STATE (session persistence, default ~/.k402/session.json)
import json, os, pathlib
import httpx
from mcp.server.fastmcp import FastMCP

GATEWAY = os.environ.get("K402_GATEWAY", "https://x402-compute.68cxgfyr0.workers.dev").rstrip("/")
STATE = pathlib.Path(os.environ.get("K402_STATE", os.path.expanduser("~/.k402/session.json")))

mcp = FastMCP("k402")
http = httpx.Client(timeout=180)

def _session():
    if os.environ.get("K402_SESSION"):
        return os.environ["K402_SESSION"]
    try:
        return json.loads(STATE.read_text())["session"]
    except Exception:
        return None

def _offer_line(o):
    """Human-readable one-liner for a per-call coin offer."""
    s = o.get("scheme")
    if s == "kaspa-utxo":
        return f"KAS {int(o['amount_sompi'])/1e8:g} → {o['pay_to']}"
    if s == "blockbook-utxo":
        return f"{o['coin'].upper()} {int(o['amount'])/10**o['decimals']:g} → {o['pay_to']}"
    if s == "evm":
        return f"{o['asset']} {int(o['amount'])/10**o['decimals']:g} (chain {o['chain_id']}) → {o['pay_to']}"
    return None

def _paid(path, body):
    """POST a paid endpoint with the session; turn 402s into actionable payment instructions —
    both the prepaid-session path and the per-call coin offers the gateway returns."""
    sid = _session()
    headers = {"X-Session": sid} if sid else {}
    r = http.post(f"{GATEWAY}{path}", json=body, headers=headers)
    try:
        out = r.json()
    except Exception:
        return {"error": f"gateway returned non-JSON (HTTP {r.status_code})"}
    if r.status_code == 402:
        coin_offers = [o for o in out.get("accepts", [])
                       if o.get("scheme") in ("kaspa-utxo", "blockbook-utxo", "evm")]
        session_hint = ("Call open_session to get a prepaid deposit address, fund it once, then "
                        "every call meters against it — no per-call step." if not sid else
                        f"Session balance low — top up your deposit address (session_status). "
                        f"This call ~{out.get('neededKas', '?')} KAS.")
        return {"payment_required": True,
                "how_to_pay": "Pick ONE: (A) prepaid session — " + session_hint +
                              "  (B) per-call — pay ONE coin offer below on-chain, then call "
                              "pay_per_call(endpoint, request_body, scheme, payment_id, txid).",
                "endpoint": path, "request_body": body,
                "pay_per_call_offers": [
                    {"pay": _offer_line(o), "scheme": o["scheme"], "payment_id": o["payment_id"],
                     "expires": o.get("expires")}
                    for o in coin_offers if _offer_line(o)],
                **{k: v for k, v in out.items() if k in ("depositAddress", "onboard")}}
    return out

def _free_get(path):
    """GET a free endpoint (job polling); no payment."""
    r = http.get(f"{GATEWAY}{path}")
    try:
        return r.json()
    except Exception:
        return {"error": f"gateway returned non-JSON (HTTP {r.status_code})"}

# ------------------------------------------------------------------ free: discovery + payment
@mcp.tool()
def catalog() -> dict:
    """List every service this gateway sells (text tools, Kaspa chain data, LLM tiers) with live
    prices in KAS and USD. Free. Start here to see what's available and what calls cost."""
    return http.get(f"{GATEWAY}/").json()

@mcp.tool()
def open_session() -> dict:
    """Open a prepaid payment session: returns a session key and a personal Kaspa MAINNET deposit
    address. Send KAS to the address from any wallet (min 0.25 KAS); confirmed deposits become
    spendable balance within seconds and every paid tool call meters against it. Free. The session
    is saved locally so subsequent calls use it automatically."""
    r = http.post(f"{GATEWAY}/onboard/request", json={}).json()
    if "session" in r:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"session": r["session"], "depositAddress": r["depositAddress"],
                                     "gateway": GATEWAY}))
        STATE.chmod(0o600)
        r["note"] = f"session saved to {STATE} — fund the deposit address, then call any paid tool"
    return r

@mcp.tool()
def session_status() -> dict:
    """Check the current payment session: deposit address, deposited / spent / remaining KAS.
    Free."""
    sid = _session()
    if not sid:
        return {"session": None, "note": "no session yet — call open_session first"}
    return http.get(f"{GATEWAY}/session/{sid}").json()

@mcp.tool()
def payment_options() -> dict:
    """Which coins and schemes this gateway accepts for payment. Free. Two ways to pay any paid
    tool: a prepaid KAS session (open_session — simplest, no per-call step) OR per-call in any
    listed coin (pay the offer a 402 returns, then call pay_per_call). Coins may include Kaspa,
    Pearl, BTC, LTC, DOGE, BCH, DASH, and EVM assets (ETC, ETH, USDC, USDT)."""
    k = http.get(f"{GATEWAY}/").json().get("k402", {})
    return {"schemes": k.get("schemes", []), "coins": k.get("coins", []),
            "how": "Prepaid session: open_session (fund once). Per-call: any paid tool returns "
                   "coin offers in its 402; pay one on-chain, then pay_per_call(...).",
            "facilitator": k.get("facilitator")}

@mcp.tool()
def pay_per_call(endpoint: str, request_body: dict, scheme: str, payment_id: str, txid: str) -> dict:
    """Complete a PER-CALL coin payment. After a paid tool returns a 402 with pay_per_call_offers,
    pay one offer on-chain from your own wallet, then call this with the 402's `endpoint` and
    `request_body`, the chosen offer's `scheme` and `payment_id`, and your payment `txid`. The
    gateway verifies the payment landed and returns the tool's real result. (Alternative to a
    prepaid session — use this when you'd rather pay each call directly in a specific coin.)"""
    hdr = {"X-K402-Payment": f"{scheme} {txid} {payment_id}"}
    r = http.post(f"{GATEWAY}{endpoint}", json=request_body, headers=hdr)
    try:
        out = r.json()
    except Exception:
        return {"error": f"gateway returned non-JSON (HTTP {r.status_code})"}
    if r.status_code == 402:
        return {"payment_required": True,
                "reason": out.get("reason", "payment not verified yet"),
                "note": "If you just paid, the tx may not be confirmed — retry pay_per_call in a moment."}
    return out

# ------------------------------------------------------------------ paid: text tools
@mcp.tool()
def summarize(text: str = "", url: str = "", max_words: int = 150) -> dict:
    """Summarize text or a public URL in at most max_words. Paid (~$0.001 in KAS)."""
    body = {"max_words": max_words, **({"url": url} if url else {"text": text})}
    return _paid("/summarize", body)

@mcp.tool()
def extract(text: str, schema: dict, instruction: str = "") -> dict:
    """Extract structured data from text as JSON guaranteed to match your JSON schema
    (schema-constrained decoding, not best-effort). Paid (~$0.0015 in KAS)."""
    body = {"text": text, "schema": schema}
    if instruction:
        body["instruction"] = instruction
    return _paid("/extract", body)

@mcp.tool()
def rewrite(text: str, instruction: str) -> dict:
    """Rewrite text per an instruction (tone, format, length...). Paid (~$0.001 in KAS)."""
    return _paid("/rewrite", {"text": text, "instruction": instruction})

@mcp.tool()
def classify(text: str, labels: list[str], multi: bool = False) -> dict:
    """Classify text into one (or multiple, if multi=true) of 2-32 labels; the result is
    constrained to your label set. Paid (~$0.0003 in KAS)."""
    return _paid("/classify", {"text": text, "labels": labels, "multi": multi})

@mcp.tool()
def read_url(url: str, distill: bool = False) -> dict:
    """Fetch a public web page and return its title + clean markdown; distill=true adds key-fact
    bullets. Useful when you have no web access of your own. Paid (~$0.0012 in KAS)."""
    return _paid("/read", {"url": url, "distill": distill})

@mcp.tool()
def embed_text(texts: list[str]) -> dict:
    """Embed up to 64 texts (768-dim vectors, nomic-embed-text). Paid (~$0.0003 in KAS)."""
    return _paid("/embed", {"input": texts})

@mcp.tool()
def search_index(collection: str, documents: list[dict], replace: bool = False) -> dict:
    """Store documents [{id, text, meta?}] in a named server-side collection for semantic search.
    Use an unguessable collection name and keep it secret. Paid (~$0.0015 in KAS)."""
    return _paid("/search/index", {"collection": collection, "documents": documents, "replace": replace})

@mcp.tool()
def search_query(collection: str, q: str, top_k: int = 5) -> dict:
    """Semantic-search a collection you indexed earlier; returns top-k matches with scores.
    Paid (~$0.0003 in KAS)."""
    return _paid("/search/query", {"collection": collection, "q": q, "top_k": top_k})

# ------------------------------------------------------------------ paid: Kaspa chain data
@mcp.tool()
def kaspa_balance(address: str) -> dict:
    """Balance of any Kaspa MAINNET address, straight from a node (no indexer, no API key).
    Paid (~$0.00015 in KAS)."""
    return _paid("/kaspa/balance", {"address": address})

@mcp.tool()
def kaspa_utxos(address: str) -> dict:
    """UTXO set of a Kaspa mainnet address, including scriptPublicKey and covenant_id where set.
    Paid (~$0.0003 in KAS)."""
    return _paid("/kaspa/utxos", {"address": address})

@mcp.tool()
def kaspa_tx_status(txid: str) -> dict:
    """Mempool status of a Kaspa transaction. Absent from mempool means accepted-or-unknown —
    confirm acceptance by checking one of its outputs with kaspa_utxos. Paid (~$0.00015)."""
    return _paid("/kaspa/tx", {"txid": txid})

@mcp.tool()
def kaspa_fee_estimate() -> dict:
    """Current Kaspa mainnet feerate buckets (sompi per gram of tx mass). Paid (~$0.00015)."""
    return _paid("/kaspa/fee-estimate", {})

@mcp.tool()
def kaspa_network() -> dict:
    """Kaspa mainnet status: DAA score, block count, difficulty, node sync state.
    Paid (~$0.00015 in KAS)."""
    return _paid("/kaspa/network", {})

# ------------------------------------------------------------------ paid: LLM tiers
@mcp.tool()
def generate(prompt: str, tier: str = "chat", system: str = "", max_tokens: int = 512) -> dict:
    """Run a prompt on a GPU model tier: 'chat' (fast 7B, ~$0.0015), 'reason' (35B, ~$0.004),
    'code' (coder model, ~$0.0025), or 'kaspa-expert' (RAG-grounded, current Kaspa knowledge,
    ~$0.0015). Returns the completion text."""
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    out = _paid("/v1/chat/completions", {"model": tier, "messages": msgs, "max_tokens": max_tokens})
    if isinstance(out, dict) and out.get("choices"):
        return {"tier": tier, "text": out["choices"][0]["message"]["content"],
                "usage": out.get("usage")}
    return out

# ------------------------------------------------------------------ paid: covenants-as-a-service
# Compile Silverscript covenants, derive addresses, inspect state, build/verify/broadcast spends on
# Kaspa mainnet (or testnet-10). The service never signs — you sign locally, keys never leave you.
@mcp.tool()
def covenant_compile(source: str, constructor_args: list = None) -> dict:
    """Compile a Silverscript covenant to script hex, ABI, hardened template hash, and its P2SH
    address on each network. constructor_args is the silverc ctor JSON (e.g.
    [{"kind":"int","data":7}]). Paid (~$0.002). NOTE: 'compiles' ≠ proven on-chain."""
    body = {"source": source}
    if constructor_args is not None:
        body["constructor_args"] = constructor_args
    return _paid("/covenant/compile", body)

@mcp.tool()
def covenant_address(script_hex: str, network: str = "mainnet") -> dict:
    """Derive the covenant P2SH address for a compiled script on 'mainnet' or 'testnet-10'.
    Paid (~$0.0003)."""
    return _paid("/covenant/address", {"script_hex": script_hex, "network": network})

@mcp.tool()
def covenant_utxos(address: str, network: str = "mainnet", covenant_id: str = "") -> dict:
    """Live UTXO set locked under a covenant address (covenant state inspection), from the node.
    Optionally filter by covenant_id. Paid (~$0.0003)."""
    body = {"address": address, "network": network}
    if covenant_id:
        body["covenant_id"] = covenant_id
    return _paid("/covenant/utxos", body)

@mcp.tool()
def covenant_build(inputs: list, outputs: list, network: str = "mainnet",
                   lock_time: int = 0, payload_hex: str = "") -> dict:
    """Assemble a covenant spend tx. Each input: {txid, index, entry, compute_budget?, and either
    sigscript:{source,decl,args,is_leader?} to build a covenant sigscript server-side, or omit
    sigscript for a P2PK input you sign locally}. Each output: {amount, address|script_public_key_hex,
    covenant?:{authorizing_input,covenant_id}}. Returns the tx, a broadcast-ready rpc_transaction,
    fee accounting, and (if fully authorized) a pre-verify. We never sign. Paid (~$0.001)."""
    return _paid("/covenant/build", {"inputs": inputs, "outputs": outputs,
                                     "network": network, "lock_time": lock_time,
                                     "payload_hex": payload_hex})

@mcp.tool()
def covenant_check(transaction: dict, entries: list) -> dict:
    """Local txscript pre-verify of a signed covenant tx BEFORE broadcast — catches script failures
    that would otherwise cost you a broadcast. 'entries' are the spent UTXOs (covenant_utxos output
    plugs in directly). Returns {all_ok, inputs:[{index, ok, error?}]}. Paid (~$0.0005)."""
    return _paid("/covenant/check", {"transaction": transaction, "entries": entries})

@mcp.tool()
def covenant_broadcast(transaction: dict, network: str = "mainnet") -> dict:
    """Broadcast a signed covenant tx (RpcTransaction JSON from covenant_build). MAINNET moves real
    KAS — run covenant_check first. Paid (~$0.0003)."""
    return _paid("/covenant/broadcast", {"transaction": transaction, "network": network})

# ------------------------------------------------------------------ paid: attestation-as-a-service
# RISC Zero proving behind an async job model: upload a guest once, preflight for a cycle-exact
# quote, submit a proof (dynamic price), poll for the receipt, verify it. The receipt is a portable
# attestation anyone can check with attest_verify.
@mcp.tool()
def prove_guest_upload(program_b64: str) -> dict:
    """Upload a RISC Zero program binary (the .bin risc0-build emits, base64) once; returns its
    content-addressed image_id (also the verification key), reusable across proofs. Paid (~$0.01)."""
    return _paid("/prove/guests", {"program_b64": program_b64})

@mcp.tool()
def prove_preflight(image_id: str, input_b64: str = "") -> dict:
    """Execute a guest WITHOUT proving: exact cycle count + a signed price quote
    {total_cycles, price_kas, quote_id, quote_expires}. Run this first — proving cost scales with
    cycles by orders of magnitude. Paid (~$0.002)."""
    return _paid("/prove/preflight", {"image_id": image_id, "input_b64": input_b64})

@mcp.tool()
def prove_submit(image_id: str, input_b64: str = "", quote_id: str = "", max_kas: float = 0) -> dict:
    """Queue a proving job -> {job_id, cost_kas}. Pass quote_id (from prove_preflight; charged the
    quote) or max_kas (skip preflight; priced on actual cycles, refuses if over the cap). Then poll
    job_status and fetch job_result. Paid: the proof's cycle-based price (dynamic)."""
    body = {"image_id": image_id, "input_b64": input_b64}
    if quote_id:
        body["quote_id"] = quote_id
    else:
        body["max_kas"] = max_kas
    return _paid("/jobs/prove", body)

@mcp.tool()
def job_status(job_id: str) -> dict:
    """Poll a proving job: queued/running/done/failed, queue position, cost. Free."""
    return _free_get(f"/jobs/{job_id}")

@mcp.tool()
def job_result(job_id: str) -> dict:
    """Fetch a finished proving job's result {image_id, total_cycles, journal_b64, receipt_b64}.
    404 until done; results kept 24h. Free."""
    return _free_get(f"/jobs/{job_id}/result")

@mcp.tool()
def attest_verify(receipt_b64: str, image_id: str) -> dict:
    """Verify a RISC Zero receipt against an image_id -> {valid, journal_b64}. Anyone can check an
    attestation without trusting the prover. Paid (~$0.0002)."""
    return _paid("/attest/verify", {"receipt_b64": receipt_b64, "image_id": image_id})

def main():
    mcp.run()

if __name__ == "__main__":
    main()
