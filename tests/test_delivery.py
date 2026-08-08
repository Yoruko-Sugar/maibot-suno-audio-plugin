"""平台发送结果兼容测试。"""

from suno_audio.delivery import DeliveryService, send_succeeded


def test_structured_send_result_requires_success() -> None:
    assert send_succeeded({"success": True}) is True
    assert send_succeeded({"success": False}) is False
    assert send_succeeded({"message": "missing success"}) is False


def test_boolean_send_result_is_supported() -> None:
    assert send_succeeded(True) is True
    assert send_succeeded(False) is False


def test_song_tracks_share_one_concise_report() -> None:
    report = DeliveryService._build_track_report(
        {"operation": "music", "short_id": "snd_UNUSED"},
        [
            {"title": "同一首歌", "duration_seconds": 20, "tags": "phonk"},
            {"title": "同一首歌", "duration_seconds": 30, "tags": "phonk"},
        ],
    )

    assert report == "✅ 音频生成完成\n歌名：《同一首歌》\n风格：phonk"
    assert "任务" not in report
    assert "时长" not in report
    assert "结果" not in report


def test_instrumental_report_uses_music_name() -> None:
    report = DeliveryService._build_track_report(
        {"operation": "instrumental"},
        [{"title": "雨夜钢琴", "tags": "jazz"}],
    )

    assert "音乐名：《雨夜钢琴》" in report
