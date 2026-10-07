"""Unit tests for the Exgentic adapter and the block-4 domain mapping (E6)."""
import json

from src.adapters.exgentic import _tail_after_last_assistant, customer_ids, link_groups, parse_step
from src.e6_external import domain_inputs, struct_features, tool_kind


def test_tail_after_last_assistant():
    msgs = [{"role": "user", "parts": [{"type": "text", "content": 'say {"role": "assistant"} please'}]},
            {"role": "assistant", "parts": [{"type": "text", "content": "ok"}]},
            {"role": "tool", "parts": [{"type": "tool_call_response", "id": "1", "result": "done"}]}]
    assert _tail_after_last_assistant(json.dumps(msgs)) == msgs[2:]
    assert _tail_after_last_assistant(json.dumps(msgs[:1])) == msgs[:1]


def test_parse_step_formats():
    dsml = ('Let me look.<｜DSML｜function_calls>\n<｜DSML｜invoke name="gmail__show_threads">\n'
            '<｜DSML｜parameter name="page" string="false">2</｜DSML｜parameter>\n</｜DSML｜invoke>\n</｜DSML｜function_calls>')
    st = parse_step([{"role": "assistant", "parts": [{"type": "text", "content": dsml}]}], "tool_calling", set())
    assert st["tool_calls"] == [{"name": "gmail__show_threads", "args": {"page": "2"}}] and st["nl"] == "Let me look."
    smol = json.dumps({"thought": "Ask for the id.", "code": "message('Your user id, please?')\nx = get_user_details("
                                                               "user_id='a_b_1')"})
    st = parse_step([{"role": "assistant", "parts": [{"type": "text", "content": smol}]}], "smolagents_code",
                    {"message", "get_user_details"})
    assert [c["name"] for c in st["tool_calls"]] == ["message", "get_user_details"]
    assert "Your user id, please?" in st["nl"] and "Ask for the id." in st["nl"]
    st = parse_step([{"role": "assistant", "parts": [{"type": "tool_call", "name": "mcp__environment__message",
                                                      "arguments": {"content": "Done, your order is cancelled."}}]}],
                    "claude_code", set())
    assert st["tool_calls"][0]["name"] == "message" and st["nl"] == "Done, your order is cancelled."


def test_tool_kind():
    assert tool_kind("appworld", "spotify__show_song") == ("read", "spotify")
    assert tool_kind("appworld", "venmo__approve_payment_request") == ("edit", "venmo")
    assert tool_kind("appworld", "venmo__login")[0] is None and tool_kind("appworld", "TodoWrite")[0] is None
    assert tool_kind("appworld", "supervisor__complete_task")[0] is None
    assert tool_kind("tau2_retail", "cancel_pending_order") == ("edit", "tau2_retail")
    assert tool_kind("tau2_retail", "get_order_details")[0] == "read" and tool_kind("tau2_retail", "message")[0] is None


def test_link_groups_and_ids():
    steps = [{"role": "assistant", "step_idx": 0, "tool_calls": [{"name": "find_user_id_by_email",
                                                                  "args": {"email": "A@x.com"}}]},
             {"role": "tool", "step_idx": 0, "text": "mia_li_3668", "tool_calls": []}]
    ids = customer_ids("tau2_retail", steps)
    assert "tau2_retail/email=a@x.com" in ids and "tau2_retail/user_id=mia_li_3668" in ids
    g = link_groups({"s1": ["u=1", "e=1"], "s2": ["e=1"], "s3": ["u=2"], "s4": []})
    assert g["s1"] == g["s2"] != g["s3"] and g["s4"] == "noid/s4"


def test_domain_phases():
    def a(i, *calls):
        return {"role": "assistant", "step_idx": i, "text": "x", "nl": "", "tool_calls": [
            {"name": n, "args": {"order_id": "#W1234567"}} for n in calls]}
    rec = {"benchmark": "tau2_retail", "steps": [
        a(0, "get_order_details"), a(1, "cancel_pending_order"),
        {"role": "tool", "step_idx": 1, "text": "Error: not pending", "error": True, "tool_calls": []},
        a(2, "get_order_details"), a(3, "message")]}
    f = struct_features(**domain_inputs(rec, None))
    assert f["n_edits"] == 1 and f["n_verifications"] == 1 and f["verif_after_last_edit"] == 1
    assert f["share_edit"] == 0.25 and f["share_before_first_edit"] == 0.25 and f["nonzero_rc_share"] == 1.0
