import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.routers.ai import _parse_tool_calls, _parse_proposals, _strip_tool_blocks, _build_proposal
from backend.routers.services import _safe_unit_name, service_of_pid
from fastapi import HTTPException
import pytest


def test_tool_call_parsing():
    text = 'Let me check.\n```tool\n{"tool": "search_processes", "args": {"query": "nginx"}}\n```'
    calls = _parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["tool"] == "search_processes"
    assert calls[0]["args"]["query"] == "nginx"


def test_tool_call_no_args():
    text = '```tool\n{"tool": "get_firewall_rules", "args": {}}\n```'
    assert _parse_tool_calls(text)[0]["tool"] == "get_firewall_rules"


def test_tool_call_malformed_ignored():
    assert _parse_tool_calls('```tool\n{not json}\n```') == []
    assert _parse_tool_calls("plain answer, no tools") == []


def test_tool_blocks_stripped_from_saved_text():
    text = 'Answer part.\n```tool\n{"tool": "x", "args": {}}\n```\nMore answer.'
    cleaned = _strip_tool_blocks(text)
    assert "tool" not in cleaned
    assert "Answer part." in cleaned and "More answer." in cleaned


def test_proposal_parsing():
    text = 'I suggest:\n```proposal\n{"type": "service_action", "action": "restart", "name": "nginx"}\n```'
    props = _parse_proposals(text)
    assert props[0]["type"] == "service_action"
    enriched = _build_proposal(props[0])
    assert enriched["label"] == "Service: restart nginx"


def test_proposal_firewall_rule_validated():
    p = _build_proposal({"type": "firewall_rule", "action": "allow", "direction": "in",
                         "protocol": "tcp", "port": "8080", "comment": "web"})
    assert "ufw allow" in p["command"]


def test_proposal_rejects_injection():
    p = _build_proposal({"type": "firewall_rule", "action": "allow", "port": "22; reboot"})
    assert p["type"] == "error"


def test_proposal_bad_action_rejected():
    assert _build_proposal({"type": "service_action", "action": "rm -rf", "name": "x"})["type"] == "error"
    assert _build_proposal({"type": "package_action", "action": "own", "manager": "apt", "name": "x"})["type"] == "error"
    assert _build_proposal({"type": "process_kill", "pid": "not-a-pid"})["type"] == "error"


def test_unit_name_guard():
    assert _safe_unit_name("nginx") == "nginx.service"
    assert _safe_unit_name("my-app.service") == "my-app.service"
    for bad in ("../../etc/shadow", "/etc/shadow", "a/b", "..", ".hidden", "a b"):
        with pytest.raises(HTTPException):
            _safe_unit_name(bad)


def test_service_of_pid_returns_str():
    # current process: not systemd-managed in test env, must return "" not raise
    assert isinstance(service_of_pid(1), str)
    assert isinstance(service_of_pid(999999), str)
