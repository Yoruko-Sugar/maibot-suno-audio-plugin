"""平台发送结果兼容测试。"""

from suno_audio.delivery import send_succeeded


def test_structured_send_result_requires_success() -> None:
    assert send_succeeded({"success": True}) is True
    assert send_succeeded({"success": False}) is False
    assert send_succeeded({"message": "missing success"}) is False


def test_boolean_send_result_is_supported() -> None:
    assert send_succeeded(True) is True
    assert send_succeeded(False) is False
