# pay_kusd under the operator's spending controls: the policy comes from KUSD_SPEND_POLICY (or the server's
# conservative default), the ledger from KUSD_SPEND_LEDGER; refusals come back with their code; kusd_spend_status
# shows the allowance. No chain, no wallet: a stub stands in for the KusdPayer.
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import k402_mcp  # noqa: E402
from k402.spend import SpendPolicy, SpendRefused  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv("KUSD_SPEND_POLICY", raising=False)
    monkeypatch.setenv("KUSD_SPEND_LEDGER", str(tmp_path / "spend.db"))
    monkeypatch.setattr(k402_mcp, "_kusd_payer", None)
    monkeypatch.setattr(k402_mcp, "_policy_seen", None, raising=False)
    yield


def test_spend_controls_default_policy_without_a_policy_file(tmp_path):
    policy, ledger = k402_mcp._spend_controls()
    assert policy == SpendPolicy.from_dict(k402_mcp.DEFAULT_SPEND_POLICY)
    assert policy.max_per_call_cents == 25 and policy.max_window_cents == 200 and policy.max_window_calls == 20
    assert policy.max_window_topup_cents == 100 and policy.max_window_failures == 2 and policy.window_seconds == 3600
    assert ledger.path == str(tmp_path / "spend.db") and (tmp_path / "spend.db").exists()
    ledger.close()


def test_spend_controls_read_the_operators_policy_file(monkeypatch, tmp_path):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"max_per_call_cents": 3, "allow_capabilities": ["summarize"]}))
    monkeypatch.setenv("KUSD_SPEND_POLICY", str(f))
    policy, ledger = k402_mcp._spend_controls()
    assert policy.max_per_call_cents == 3 and policy.allow_capabilities == frozenset({"summarize"})
    assert policy.max_window_cents is None, "the operator's file replaces the default, it does not merge with it"
    ledger.close()


def test_spend_controls_default_ledger_path(monkeypatch):
    monkeypatch.delenv("KUSD_SPEND_LEDGER")
    monkeypatch.setenv("HOME", "/nonexistent-home")
    assert k402_mcp._spend_ledger_path() == "/nonexistent-home/.kusd-market/spend.db"


class StubPayer:
    def __init__(self, status):
        self._status = status
    async def pay_best(self, capability, **kw):
        raise SpendRefused("total", "5¢ would take the lifetime total to 105¢, over the budget of 100¢", self._status)
    def spend_status(self):
        return self._status


def test_pay_kusd_reports_a_spend_refusal_with_its_code(monkeypatch):
    status = {"frozen": False, "total_cents": 100, "remaining": {"total_cents": 0}}
    monkeypatch.setattr(k402_mcp, "_kusd_payer", StubPayer(status))
    out = k402_mcp.pay_kusd("summarize", {"text": "x"})
    assert out["code"] == "total" and out["spend"] == status
    assert "over the budget of 100¢" in out["error"] and "operator" in out["note"].lower()


def test_kusd_spend_status_tool(monkeypatch):
    status = {"frozen": True, "total_cents": 7}
    monkeypatch.setattr(k402_mcp, "_kusd_payer", StubPayer(status))
    assert k402_mcp.kusd_spend_status() == status
    monkeypatch.setattr(k402_mcp, "_kusd_payer", StubPayer(None))
    out = k402_mcp.kusd_spend_status()
    assert out["policy"] is None and "note" in out


class FakeWallet:
    """Stands in for k402.KusdWallet at construction time; never touches a chain."""
    network = "testnet-10"
    def __init__(self, *a, **kw):
        pass


def test_kusd_builds_the_payer_inside_the_policy_and_reloads_edits(monkeypatch, tmp_path):
    import k402
    import os as _os
    monkeypatch.setattr(k402, "KusdWallet", FakeWallet)
    for k, v in {"KUSD_PAY_BIN": "/bin/true", "KUSD_KEY_FILE": str(tmp_path / "k"), "KUSD_AUTHORITY": "06" * 32}.items():
        monkeypatch.setenv(k, v)
    payer = k402_mcp._kusd()
    assert payer.policy == SpendPolicy.from_dict(k402_mcp.DEFAULT_SPEND_POLICY), "pay_kusd is bounded by the default when no file is set"
    assert payer.ledger.path == str(tmp_path / "spend.db")
    # the operator writes a policy file while the server runs: the next call sees it
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"max_per_call_cents": 5}))
    monkeypatch.setenv("KUSD_SPEND_POLICY", str(f))
    assert k402_mcp._kusd() is payer and payer.policy.max_per_call_cents == 5
    f.write_text(json.dumps({"max_per_call_cents": 7}))
    _os.utime(str(f), (1, 1))          # a different mtime, whatever the clock resolution
    assert k402_mcp._kusd().policy.max_per_call_cents == 7
    f.write_text("not json")
    _os.utime(str(f), (2, 2))
    with pytest.raises(RuntimeError, match="policy"):
        k402_mcp._kusd()               # a broken edit refuses to pay rather than silently keeping the old bounds
    assert "policy" in k402_mcp.pay_kusd("summarize", {"text": "x"})["error"]


# ------------------------------------------------------------------ Phase 3: the tools the north-star demo needs
class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
    def json(self):
        return self._body


class FakeHttp:
    """Stands in for the module's httpx client: scripted answers by URL suffix."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []
    def _answer(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for suffix, ans in self.routes.items():
            if url.endswith(suffix) or suffix in url:
                return FakeResp(*ans)
        return FakeResp(404, {"error": "no route"})
    def get(self, url, **kw):
        return self._answer("GET", url, **kw)
    def post(self, url, **kw):
        return self._answer("POST", url, **kw)
    def request(self, method, url, **kw):
        return self._answer(method, url, **kw)


def test_registry_tools_use_the_kaspa_agent_market_registry_by_default(monkeypatch):
    monkeypatch.delenv("K402_REGISTRY", raising=False)
    assert k402_mcp.registry_base() == "https://market.kaspa-402.org"
    monkeypatch.setenv("K402_REGISTRY", "https://r.test/")
    assert k402_mcp.registry_base() == "https://r.test"
    fh = FakeHttp({"/registry/search": (200, {"providers": [{"capability": "summarize"}], "count": 1}),
                   "/registry/activity?limit=3": (200, {"settlements": [{"cents": 5}], "total_cents": 5})})
    monkeypatch.setattr(k402_mcp, "http", fh)
    assert k402_mcp.registry_search(capability="summarize", currency="kUSD")["count"] == 1
    assert fh.calls[-1][1].startswith("https://r.test/registry/search")
    assert k402_mcp.registry_activity(limit=3)["total_cents"] == 5


class QuotePayer:
    """A payer whose quote()/discover() are scripted; nothing is ever paid."""
    def __init__(self, quote=None, providers=(), error=None):
        self._quote, self._providers, self._error, self.paid = quote, list(providers), error, []
        self.policy = SpendPolicy.from_dict({"max_per_call_cents": 25})
        self.wallet = type("W", (), {"payee_xonly": lambda s: "ea" * 32})()
    async def discover(self, capability="", max_price_cents=None, limit=20):
        return self._providers
    def rank(self, providers, policy="registry"):
        return providers
    async def quote(self, provider, path="", json_body=None, max_price_cents=None):
        if self._error:
            raise self._error
        return self._quote
    def spend_status(self):
        return {"remaining": {"total_cents": 40}}


def _quote():
    from k402.kusd_client import Outlay, Quote
    return Quote(cents=5, pay_to="cd" * 32, authority="06" * 32, payment_id="p_1", network="kaspa:testnet-10", wire="x402",
                 accepted={"scheme": "exact"}, url="https://p.test/summarize", outlay=Outlay(5))


def test_get_quote_checks_and_pays_nothing(monkeypatch):
    prov = {"endpoint": "https://p.test/summarize", "payee_pubkey": "ab" * 32, "capability": "summarize", "price_cents": 5}
    monkeypatch.setattr(k402_mcp, "_kusd_payer", QuotePayer(quote=_quote(), providers=[prov]))
    out = k402_mcp.get_quote(capability="summarize", request_body={"text": "x"})
    assert out["cents"] == 5 and out["pay_to"] == "cd" * 32 and out["payment_id"] == "p_1" and out["wire"] == "x402"
    assert out["provider"] == "ab" * 32 and out["endpoint"] == "https://p.test/summarize"
    assert out["outlay"]["box_kas_sompi"] == 150_000_000 and out["checks"]["payee_is_listed_key_tweaked_by_offer"] is True
    assert out["paid"] is False and out["spend"]["remaining"]["total_cents"] == 40
    # by endpoint, no discovery
    out = k402_mcp.get_quote(endpoint="https://p.test/summarize", payee_pubkey="ab" * 32, request_body={})
    assert out["cents"] == 5
    # a refused quote comes back with its code, nothing paid
    monkeypatch.setattr(k402_mcp, "_kusd_payer", QuotePayer(error=SpendRefused("per_call", "too dear", {}), providers=[prov]))
    out = k402_mcp.get_quote(capability="summarize")
    assert out["code"] == "per_call" and "error" in out
    monkeypatch.setattr(k402_mcp, "_kusd_payer", QuotePayer(providers=[]))
    assert "error" in k402_mcp.get_quote(capability="nothing-listed")


def test_verify_settlement_asks_the_registry_then_the_node(monkeypatch):
    row = {"txid": "d3" * 32, "verified": True, "settlements": [{"idx": 0, "payee_pubkey": "ab" * 32, "cents": 5, "t": 1, "capability": "summarize"}]}
    monkeypatch.setattr(k402_mcp, "http", FakeHttp({"/registry/settled/" + "d3" * 32: (200, row), "/registry/settled/" + "ee" * 32: (404, {"error": "none"})}))
    out = k402_mcp.verify_settlement("D3" * 32)
    assert out["registry"]["verified"] is True and out["registry"]["settlements"][0]["cents"] == 5 and out["txid"] == "d3" * 32
    assert out["node"] is None, "no node check without payee/cents/payment_id"
    # with the offer's details and a local wallet, the node is asked too
    class W:
        def verify(self, txid, to, cents, authority, since):
            from k402.kusd import VerifyResult
            return VerifyResult(ok=True, reason="", index=0, daa=624_000_100, retryable=False)
        def family(self):
            from k402.kusd import Family
            return Family(authority="06" * 32, borrow_guard="00" * 32, ext="ac" * 32, network="testnet-10")
    monkeypatch.setattr(k402_mcp, "_kusd_payer", type("P", (), {"wallet": W()})())
    out = k402_mcp.verify_settlement("d3" * 32, payee_pubkey="ab" * 32, cents=5, payment_id="p_1")
    assert out["node"]["ok"] is True and out["node"]["index"] == 0 and out["node"]["box_owner"] != "ab" * 32
    # unknown to the registry: say so, do not invent
    out = k402_mcp.verify_settlement("ee" * 32)
    assert out["registry"]["verified"] is False
    assert k402_mcp.verify_settlement("zz")["error"]


def test_list_and_delist_service_sign_with_the_wallet_key_and_pay_the_fee(monkeypatch, tmp_path):
    from k402.channel import payer_pubkey_from_privkey
    secret = "11" * 32
    key = tmp_path / "k"
    key.write_text(secret + "\n")
    monkeypatch.setenv("KUSD_KEY_FILE", str(key))
    pub = payer_pubkey_from_privkey(secret)
    paid = []
    class Payer:
        policy = None
        network = "testnet-10"
        wallet = type("W", (), {"payee_xonly": lambda s: pub})()
        async def pay(self, provider, path="", json_body=None, max_price_cents=None):
            paid.append((provider, json_body, max_price_cents))
            class R:
                status_code = 200
                def json(self):
                    return {"ok": True, "listed": json_body["capability"]}
            from k402.kusd_client import Outlay, PayResult
            return PayResult(response=R(), txid="f1" * 32, cents=10, wire="x402", outlay=Outlay(10))
    monkeypatch.setattr(k402_mcp, "_kusd_payer", Payer())
    out = k402_mcp.list_service(capability="summarize", endpoint="https://me.test/summarize", price_cents=5, description="mine")
    assert out["listed"]["capability"] == "summarize" and out["listed"]["currency"] == "kUSD" and out["listed"]["price_cents"] == 5
    assert out["listed"]["payee_pubkey"] == pub and out["listed"]["schemes"] == ["kusd-utxo"] and out["listed"]["network"] == "testnet-10"
    from k402.registry import Listing
    assert Listing.from_dict(out["listed"]).verify(), "signed by the wallet key"
    assert out["fee"]["txid"] == "f1" * 32 and out["fee"]["cents"] == 10
    assert paid[0][0]["endpoint"].endswith("/registry/list") and paid[0][2] == 25
    # delist: signed DELETE, free
    fh = FakeHttp({"/registry/list": (200, {"ok": True, "delisted": "summarize"})})
    monkeypatch.setattr(k402_mcp, "http", fh)
    out = k402_mcp.delist_service(capability="summarize", endpoint="https://me.test/summarize", price_cents=5)
    assert out["ok"] is True and fh.calls[-1][0] == "DELETE"
    # refusals: bad price, bad endpoint — nothing signed, nothing paid
    assert "error" in k402_mcp.list_service(capability="summarize", endpoint="ftp://x", price_cents=5)
    assert "error" in k402_mcp.list_service(capability="summarize", endpoint="https://me.test/s", price_cents=0)
    assert len(paid) == 1
