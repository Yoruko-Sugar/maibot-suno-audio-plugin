from pathlib import Path
import ast


def _constant(name: str) -> object:
    tree = ast.parse(Path("plugin.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"找不到常量 {name}")


def test_audio_tool_declares_cross_media_boundaries() -> None:
    tool_description = str(_constant("TOOL_DESCRIPTION"))
    assert "【唯一产物】可独立发送" in tool_description
    assert "专辑封面" in tool_description
    assert "猫唱歌的视频" in tool_description
    assert "默认一条消息只调用一个付费媒体生成工具" in tool_description
    assert "未说明最终媒介" in tool_description


def test_audio_operation_excludes_video_soundtrack() -> None:
    tool_parameters = _constant("TOOL_PARAMETERS")
    assert isinstance(tool_parameters, dict)
    assert "不得用于视频内声音轨道" in tool_parameters["operation"]["description"]
