#!/usr/bin/env python3
# k402-mcp — MCP server exposing the k402 agent-payable API catalog as tools.
#
# Any MCP-capable agent (Claude Code, Claude Desktop, agent SDKs) gets the whole paid catalog —
# text tools, Kaspa chain data, GPU inference — as ordinary tools. Payment is a prepaid KAS
# balance: open_session() mints a personal mainnet deposit address; fund it from any wallet and
# every paid tool call meters against it. A 402 comes back as readable "how to pay" instructions,
# so an agent can discover the service, learn how to fund it, and (once funded) use it — the whole
# loop without a human account, card, or API key.
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

def _paid(path, body):
    """POST a paid endpoint with the session; turn 402s into actionable payment instructions."""
    sid = _session()
    headers = {"X-Session": sid} if sid else {}
    r = http.post(f"{GATEWAY}{path}", json=body, headers=headers)
    try:
        out = r.json()
    except Exception:
        return {"error": f"gateway returned non-JSON (HTTP {r.status_code})"}
    if r.status_code == 402:
        if not sid:
            return {"payment_required": True,
                    "how_to_pay": "No session yet. Call the open_session tool to get a personal "
                                  "Kaspa deposit address, fund it with KAS from any wallet, then retry."}
        return {"payment_required": True, "how_to_pay":
                f"Session balance is too low. Send KAS to your deposit address "
                f"{out.get('depositAddress', '(see session_status)')} — this call costs "
                f"~{out.get('neededKas', '?')} KAS. Balance updates within seconds of confirmation.",
                **out}
    return out

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

def main():
    mcp.run()

if __name__ == "__main__":
    main()
