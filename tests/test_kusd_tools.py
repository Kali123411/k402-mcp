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
