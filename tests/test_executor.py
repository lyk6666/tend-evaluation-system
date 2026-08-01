from types import SimpleNamespace

import pytest

from tend_eval.executor import TendMethodExecutor


@pytest.mark.asyncio
async def test_gpt5_chat_adapter_removes_unsupported_fields() -> None:
    captured = {}

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    completions = Completions()
    runtime = SimpleNamespace(
        ctx=SimpleNamespace(
            llm=SimpleNamespace(
                _client=SimpleNamespace(
                    chat=SimpleNamespace(completions=completions)
                )
            )
        )
    )
    TendMethodExecutor._install_openai_chat_compat(runtime)
    await completions.create(
        model="gpt-5.6-luna",
        messages=[],
        temperature=0.0,
        max_tokens=2048,
        reasoning_effort="medium",
    )
    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == 2048
    assert captured["reasoning_effort"] == "medium"
