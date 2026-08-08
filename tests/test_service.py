"""统一业务入口的去重测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from suno_audio.models import AudioOperation, GenerationRequest
from suno_audio.service import AudioService
from suno_audio.storage import AudioRepository


class FakePlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def optimize(self, request, *, enabled=True):
        del enabled
        self.calls += 1
        return request


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def submit(self, request):
        del request
        self.calls += 1
        return "vendor-task-1"


class FakeJobs:
    def __init__(self) -> None:
        self.scheduled = []

    def schedule(self, job_id: str) -> None:
        self.scheduled.append(job_id)


@pytest.mark.asyncio
async def test_duplicate_trigger_skips_optimizer_and_supplier(tmp_path: Path) -> None:
    repository = AudioRepository(tmp_path / "audio.sqlite3")
    planner = FakePlanner()
    client = FakeClient()
    jobs = FakeJobs()
    plugin = SimpleNamespace(
        config=SimpleNamespace(
            permissions=SimpleNamespace(
                global_admin_ids=[], group_whitelist=[], access_mode="public", user_whitelist=[]
            ),
            plugin=SimpleNamespace(allow_group_chat=True, allow_private_chat=True),
            limits=SimpleNamespace(
                max_prompt_chars=3000,
                max_lyrics_chars=12000,
                max_active_jobs_per_stream=1,
                user_cooldown_seconds=0,
                daily_jobs_per_user=0,
            ),
        ),
        ctx=SimpleNamespace(logger=SimpleNamespace(info=lambda *args: None)),
    )
    service = AudioService(plugin, repository, client, planner, jobs)
    request = GenerationRequest(
        operation=AudioOperation.MUSIC,
        original_prompt="夏日流行歌",
        prompt="夏日流行歌",
    )

    first, first_created = await service.submit(
        request,
        platform="qq",
        stream_id="stream-1",
        group_id="group-1",
        requester_id="user-1",
        triggering_message_id="message-1",
    )
    second, second_created = await service.submit(
        request,
        platform="qq",
        stream_id="stream-1",
        group_id="group-1",
        requester_id="user-1",
        triggering_message_id="message-1",
    )

    assert first_created is True
    assert second_created is False
    assert second["id"] == first["id"]
    assert planner.calls == 1
    assert client.calls == 1
    assert jobs.scheduled == [first["id"]]
    repository.close()
