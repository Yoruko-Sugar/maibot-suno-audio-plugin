"""音频任务持久化测试。"""

from pathlib import Path

from suno_audio.models import AudioOperation, AudioTrack, GenerationRequest, VendorTaskSnapshot
from suno_audio.storage import AudioRepository


def make_request(prompt: str = "雨夜爵士") -> GenerationRequest:
    return GenerationRequest(
        operation=AudioOperation.INSTRUMENTAL,
        original_prompt=prompt,
        prompt=prompt,
        instrumental=True,
    )


def test_same_trigger_message_creates_only_one_job(tmp_path: Path) -> None:
    repository = AudioRepository(tmp_path / "audio.sqlite3")

    first, first_created = repository.create_job(
        request=make_request(),
        platform="qq",
        stream_id="stream-1",
        group_id="group-1",
        requester_id="user-1",
        triggering_message_id="message-1",
    )
    second, second_created = repository.create_job(
        request=make_request("完全不同的描述"),
        platform="qq",
        stream_id="stream-1",
        group_id="group-1",
        requester_id="user-1",
        triggering_message_id="message-1",
    )

    assert first_created is True
    assert second_created is False
    assert second["id"] == first["id"]
    assert repository.get_job_by_trigger("stream-1", "message-1")["id"] == first["id"]
    repository.close()


def test_completed_tracks_survive_repository_reopen(tmp_path: Path) -> None:
    database = tmp_path / "audio.sqlite3"
    repository = AudioRepository(database)
    job, _ = repository.create_job(
        request=make_request(),
        platform="qq",
        stream_id="stream-1",
        group_id="group-1",
        requester_id="user-1",
    )
    repository.mark_submitted(str(job["id"]), "task-1")
    snapshot = VendorTaskSnapshot(
        task_id="task-1",
        status="completed",
        progress=100,
        tracks=[AudioTrack(audio_index=1, title="雨夜", audio_url="https://files.example/rain.mp3")],
    )
    repository.complete_job(str(job["id"]), snapshot)
    assert repository.list_resumable_jobs()[0]["id"] == job["id"]
    repository.set_delivery_status(str(job["id"]), "delivered")
    assert repository.list_resumable_jobs() == []
    repository.close()

    reopened = AudioRepository(database)

    assert reopened.get_job(str(job["id"]))["status"] == "completed"
    assert reopened.get_tracks(str(job["id"]))[0]["title"] == "雨夜"
    reopened.close()
