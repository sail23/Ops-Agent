import pytest

from power_aiops.prompts import (
    SYSTEM_PROMPT_OPS_AGENT,
    cot_step_labels,
    format_cot_prompt,
)


def test_cot_step_labels_five():
    assert len(cot_step_labels()) == 5
    assert "现象确认" in cot_step_labels()


def test_format_cot_contains_fault_name():
    text = format_cot_prompt("数据库查询延迟过高")
    assert "数据库查询延迟过高" in text
    assert "Step1" in text and "Step5" in text


def test_ops_system_prompt_conservative():
    assert "保守型" in SYSTEM_PROMPT_OPS_AGENT
    assert "写操作" in SYSTEM_PROMPT_OPS_AGENT or "生产" in SYSTEM_PROMPT_OPS_AGENT


@pytest.mark.parametrize("name", ["Ops-Agent", "SRE-Agent", "Code-Agent", "Report-Agent"])
def test_all_roles_present(name):
    from power_aiops.prompts import all_role_system_prompts

    prompts = all_role_system_prompts()
    assert name in prompts
    assert len(prompts[name]) > 100
