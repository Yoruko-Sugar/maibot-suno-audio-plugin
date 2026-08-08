"""生成请求模型测试。"""

import pytest

from suno_audio.models import AudioOperation, GenerationRequest


def test_custom_song_payload_preserves_lyrics() -> None:
    request = GenerationRequest(
        operation=AudioOperation.CUSTOM_SONG,
        original_prompt="写一首歌",
        prompt="写一首歌",
        title="星海",
        style="中文摇滚",
        lyrics="第一行\n第二行",
    )

    request.validate()
    payload = request.to_vendor_payload()

    assert payload["custom"] is True
    assert payload["prompt"] == "第一行\n第二行"
    assert payload["title"] == "星海"
    assert payload["style"] == "中文摇滚"


def test_sound_validates_key_and_bpm() -> None:
    request = GenerationRequest(
        operation=AudioOperation.SOUND,
        original_prompt="循环鼓点",
        prompt="循环鼓点",
        sound_type="loop",
        bpm=120,
        key="C#m",
    )

    request.validate()
    payload = request.to_vendor_payload()

    assert payload["type"] == "loop"
    assert payload["bpm"] == 120
    assert payload["key"] == "C#m"


@pytest.mark.parametrize("key", ["Db", "H", "c", "C##"])
def test_sound_rejects_unsupported_key_notation(key: str) -> None:
    request = GenerationRequest(
        operation=AudioOperation.SOUND,
        original_prompt="提示音",
        prompt="提示音",
        key=key,
    )

    with pytest.raises(ValueError, match="调性格式无效"):
        request.validate()
