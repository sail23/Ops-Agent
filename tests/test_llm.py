from unittest.mock import MagicMock, patch

from power_aiops.agents import OpsAgent
from power_aiops.config import Settings
from power_aiops.llm.client import OpenAICompatibleClient, chat_completion_stub
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.models import IncidentContext


def test_chat_completion_stub():
    t = chat_completion_stub(system="s", user="hello")
    assert "[LLM stub]" in t
    assert "hello" in t


def test_client_stub_when_no_key():
    c = OpenAICompatibleClient(settings=Settings(openai_api_key=""))
    assert c.is_configured() is False
    out = c.chat(system="sys", user="u")
    assert "[LLM stub]" in out


def test_ops_agent_default_no_llm():
    board = SharedBoard()
    r = OpsAgent(board).run(IncidentContext(incident_id="i", trace_id="t"))
    assert "占位" in r.content
    assert r.meta == {}


def test_ops_agent_use_llm_stub_path():
    board = SharedBoard()
    r = OpsAgent(board, use_llm=True, llm=OpenAICompatibleClient(settings=Settings(openai_api_key=""))).run(
        IncidentContext(incident_id="i", trace_id="t")
    )
    assert "[LLM stub]" in r.content
    assert r.meta.get("llm") == "stub"


@patch("power_aiops.llm.client.httpx.Client")
def test_client_calls_openai_when_key_set(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "analysis done"}}]}
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client_cls.return_value = mock_client

    c = OpenAICompatibleClient(
        settings=Settings(
            openai_api_key="sk-test",
            openai_api_base="https://api.openai.com/v1",
            openai_chat_model="gpt-4o-mini",
        )
    )
    assert c.is_configured() is True
    out = c.chat(system="sys", user="user text")
    assert out == "analysis done"
    mock_client.post.assert_called_once()


@patch("power_aiops.llm.client.httpx.Client")
def test_ops_agent_use_llm_with_mocked_http(mock_client_cls):
    with patch("power_aiops.llm.client.httpx.Client") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ops ok"}}]}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        llm = OpenAICompatibleClient(
            settings=Settings(openai_api_key="sk-x", openai_api_base="https://api.openai.com/v1")
        )
        board = SharedBoard()
        r = OpsAgent(board, use_llm=True, llm=llm).run(IncidentContext(incident_id="i", trace_id="t"))
        assert r.content == "ops ok"
        assert r.meta.get("llm") == "openai-compatible"
