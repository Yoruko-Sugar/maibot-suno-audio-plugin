"""API Mart HTTP 协议测试。"""

import httpx
import pytest

from suno_audio.apimart_client import ApiMartClient
from suno_audio.errors import ApiMartSubmitUnknownError
from suno_audio.models import AudioOperation, GenerationRequest


@pytest.mark.asyncio
async def test_submit_and_query_completed_music() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        if request.method == "POST":
            assert request.url.path == "/v1/music/generations"
            return httpx.Response(200, json={"data": [{"task_id": "task-1"}]})
        assert request.url.path == "/v1/music/tasks/task-1"
        return httpx.Response(
            200,
            json={
                "task_id": "task-1",
                "status": "completed",
                "progress": 100,
                "data": {
                    "result": {
                        "music": [
                            {
                                "audio_id": "audio-1",
                                "title": "测试歌曲",
                                "duration": 95,
                                "audio_url": "https://files.example/test.mp3",
                            }
                        ]
                    }
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ApiMartClient(base_url="https://api.example", api_key="secret", client=http_client)
    request = GenerationRequest(
        operation=AudioOperation.MUSIC,
        original_prompt="测试",
        prompt="测试",
    )

    task_id = await client.submit(request)
    snapshot = await client.get_task(task_id)

    assert task_id == "task-1"
    assert snapshot.status == "completed"
    assert snapshot.tracks[0].title == "测试歌曲"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_submit_timeout_is_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ApiMartClient(base_url="https://api.example", api_key="secret", client=http_client)
    request = GenerationRequest(
        operation=AudioOperation.MUSIC,
        original_prompt="测试",
        prompt="测试",
    )

    with pytest.raises(ApiMartSubmitUnknownError):
        await client.submit(request)

    assert calls == 1
    await http_client.aclose()
