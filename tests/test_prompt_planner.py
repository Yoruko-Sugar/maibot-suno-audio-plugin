"""自然语言提示词优化测试。"""

from types import SimpleNamespace

import pytest

from suno_audio.models import AudioOperation, GenerationRequest
from suno_audio.prompt_planner import PromptPlanningService


class FakeLlm:
    async def generate(self, **kwargs):
        del kwargs
        return {
            "success": True,
            "model": "test-model",
            "response": (
                '{"operation":"custom_song", "prompt":"更精确的风格", "title":"", "style":"流行", '
                '"lyrics":"模型擅自改写", "negative_tags":"", "vocal_gender":"Female", '
                '"sound_type":"one-shot", "bpm":null, "key":""}'
            ),
        }


@pytest.mark.asyncio
async def test_optimizer_preserves_user_lyrics() -> None:
    config = SimpleNamespace(
        prompt_optimizer=SimpleNamespace(
            prompt_optimizer_enabled=True,
            preserve_user_lyrics=True,
            llm_task_name="utils",
            temperature=0.3,
            max_tokens=4096,
            llm_timeout_seconds=90,
            failure_mode="abort",
        ),
        limits=SimpleNamespace(max_prompt_chars=3000, max_lyrics_chars=12000),
    )
    plugin = SimpleNamespace(
        config=config,
        ctx=SimpleNamespace(
            llm=FakeLlm(),
            logger=SimpleNamespace(info=lambda *args: None, warning=lambda *args: None),
        ),
    )
    planner = PromptPlanningService(plugin)
    request = GenerationRequest(
        operation=AudioOperation.CUSTOM_SONG,
        original_prompt="写歌",
        prompt="写歌",
        title="标题",
        style="流行",
        lyrics="用户原始歌词",
    )

    optimized = await planner.optimize(request)

    assert optimized.prompt == "更精确的风格"
    assert optimized.lyrics == "用户原始歌词"
    assert optimized.optimizer_model == "test-model"
