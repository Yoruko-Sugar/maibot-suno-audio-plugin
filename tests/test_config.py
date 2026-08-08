"""WebUI 配置模型测试。"""

from importlib import util
from pathlib import Path
import sys

from config_models import GenerationConfig, SunoAudioPluginConfig


def test_vocal_gender_select_has_no_empty_item() -> None:
    schema = SunoAudioPluginConfig.model_json_schema()
    field = schema["$defs"]["GenerationConfig"]["properties"]["vocal_gender"]

    assert field["default"] == "auto"
    assert field["choices"] == ["auto", "Male", "Female"]
    assert "" not in field["choices"]
    assert "enum" not in field


def test_legacy_empty_vocal_gender_remains_readable() -> None:
    config = GenerationConfig(vocal_gender="")

    assert config.vocal_gender == ""


def test_final_webui_schema_injects_non_empty_select_choices() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    package_name = "suno_audio_plugin_schema_test"
    package_spec = util.spec_from_file_location(
        package_name,
        plugin_root / "__init__.py",
        submodule_search_locations=[str(plugin_root)],
    )
    assert package_spec is not None and package_spec.loader is not None
    package = util.module_from_spec(package_spec)
    sys.modules[package_name] = package
    package_spec.loader.exec_module(package)
    plugin_spec = util.find_spec(f"{package_name}.plugin")
    assert plugin_spec is not None and plugin_spec.loader is not None
    plugin_module = util.module_from_spec(plugin_spec)
    sys.modules[f"{package_name}.plugin"] = plugin_module
    plugin_spec.loader.exec_module(plugin_module)

    schema = plugin_module.SunoAudioPlugin().get_webui_config_schema()
    field = schema["sections"]["generation"]["fields"]["vocal_gender"]

    assert field["ui_type"] == "select"
    assert field["choices"] == ["auto", "Male", "Female"]
    assert "" not in field["choices"]
