"""MaiBot Suno 音频工坊插件入口。"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Iterable, List

from maibot_sdk import (
    API,
    CONFIG_RELOAD_SCOPE_SELF,
    ON_MODEL_CONFIG_RELOAD,
    Command,
    MaiBotPlugin,
    PluginConfigBase,
    Tool,
)

from .config_models import SunoAudioPluginConfig
from .suno_audio.apimart_client import ApiMartClient
from .suno_audio.commands import AudioCommandRouter, CommandContext
from .suno_audio.delivery import DeliveryService, send_succeeded
from .suno_audio.errors import SunoAudioError
from .suno_audio.jobs import AudioJobManager
from .suno_audio.models import AudioOperation, GenerationRequest
from .suno_audio.prompt_planner import PromptPlanningService
from .suno_audio.service import AudioService
from .suno_audio.storage import AudioRepository


TOOL_DESCRIPTION = (
    "根据当前用户的明确要求生成音乐、纯音乐、指定歌词歌曲、歌词或短音效。"
    "调用后只提交后台任务，完成结果会自动发送到当前聊天。\n\n"
    "适合调用：用户明确说生成、创作、制作、写一首、做一段音乐/歌曲/歌词/音效；"
    "也包括明确指定曲风、乐器、情绪、歌词、BPM 或调性的请求。\n"
    "不要调用：用户只是在讨论或评价音乐；询问插件是否支持；只是描述声音场景；"
    "用户明确说不要生成；引用、转述他人的生成请求；当前消息已经成功创建过音频任务。\n"
    "operation 选择：music=普通音乐，instrumental=纯音乐，custom_song=指定歌词歌曲，"
    "lyrics=只写歌词，sound=短音效。触发消息 ID 必须来自当前提出请求的用户消息。"
)

TOOL_PARAMETERS = {
    "operation": {
        "type": "string",
        "enum": ["music", "instrumental", "custom_song", "lyrics", "sound"],
        "description": "生成类型。",
        "required": True,
    },
    "description": {
        "type": "string",
        "description": "完整保留用户意图的自然语言描述。",
        "required": True,
    },
    "msg_id": {
        "type": "string",
        "description": "触发生成请求的当前用户消息 ID，用于阻止重复计费。",
        "required": True,
    },
    "title": {"type": "string", "description": "自定义歌曲标题，仅 custom_song 使用。"},
    "style": {"type": "string", "description": "自定义歌曲曲风，仅 custom_song 使用。"},
    "lyrics": {"type": "string", "description": "用户提供的完整歌词；必须原样保留。"},
    "sound_type": {
        "type": "string",
        "enum": ["one-shot", "loop"],
        "description": "音效类型，默认 one-shot。",
    },
    "bpm": {"type": "integer", "description": "音效 BPM，范围 1 到 300。"},
    "key": {"type": "string", "description": "音效调性，如 C、C#、Cm、C#m。"},
}


class SunoAudioPlugin(MaiBotPlugin):
    """自然语言与指令双入口的异步音频生成插件。"""

    config_model: ClassVar[type[PluginConfigBase]] = SunoAudioPluginConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = (ON_MODEL_CONFIG_RELOAD,)

    def __init__(self) -> None:
        super().__init__()
        self._available_model_tasks: List[str] = []
        self._repository: AudioRepository | None = None
        self._client: ApiMartClient | None = None
        self._delivery: DeliveryService | None = None
        self._jobs: AudioJobManager | None = None
        self._service: AudioService | None = None
        self._router: AudioCommandRouter | None = None

    async def on_load(self) -> None:
        """初始化独立数据库、供应商客户端和可恢复任务管理器。"""

        await self._refresh_model_tasks(validate=False)
        await self._initialize_runtime()

    async def _initialize_runtime(self) -> None:
        """按照当前配置装配运行时服务。"""

        if not self.config.plugin.enabled:
            self.ctx.logger.warning("Suno 音频工坊已在配置中禁用")
            return
        if not str(self.config.apimart.api_key).strip():
            self.ctx.logger.error("Suno 音频工坊缺少 API Mart API Key，暂不初始化生成服务")
            return
        await self._refresh_model_tasks(validate=bool(self.config.prompt_optimizer.prompt_optimizer_enabled))
        client = ApiMartClient(
            base_url=str(self.config.apimart.base_url),
            api_key=str(self.config.apimart.api_key),
            model=str(self.config.apimart.model),
            connect_timeout_seconds=float(self.config.apimart.connect_timeout_seconds),
            request_timeout_seconds=float(self.config.apimart.request_timeout_seconds),
        )
        try:
            repository = AudioRepository(self.ctx.paths.data_dir / "suno_audio.sqlite3")
        except Exception:
            await client.close()
            raise
        planner = PromptPlanningService(self)
        delivery = DeliveryService(self, repository, client)
        jobs = AudioJobManager(self, repository, client, delivery)
        service = AudioService(self, repository, client, planner, jobs)
        self._repository = repository
        self._client = client
        self._delivery = delivery
        self._jobs = jobs
        self._service = service
        self._router = AudioCommandRouter(self, repository, service, jobs, delivery)
        resumed = jobs.resume()
        self.ctx.logger.info(
            "Suno 音频工坊已加载：data=%s resumed=%s optimizer_model=%s",
            self.ctx.paths.data_dir,
            resumed,
            self.config.prompt_optimizer.llm_task_name,
        )

    async def on_unload(self) -> None:
        """停止本地跟踪并释放 HTTP 与 SQLite 资源。"""

        await self._shutdown_runtime()

    async def _shutdown_runtime(self) -> None:
        """释放当前运行时，配置热更新后可安全重新装配。"""

        if self._jobs is not None:
            await self._jobs.shutdown()
        if self._client is not None:
            await self._client.close()
        if self._repository is not None:
            self._repository.close()
        self._router = None
        self._service = None
        self._jobs = None
        self._delivery = None
        self._client = None
        self._repository = None

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """模型配置变化时刷新配置页下拉选项。"""

        del config_data
        if scope in {CONFIG_RELOAD_SCOPE_SELF, ON_MODEL_CONFIG_RELOAD}:
            await self._refresh_model_tasks(validate=False)
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            await self._shutdown_runtime()
            await self._initialize_runtime()
        self.ctx.logger.info("Suno 音频工坊配置已刷新：scope=%s version=%s", scope, version)

    async def _refresh_model_tasks(self, *, validate: bool) -> None:
        available = sorted(
            {
                str(task_name).strip()
                for task_name in await self.ctx.llm.get_available_models()
                if str(task_name).strip()
            }
        )
        self._available_model_tasks = available
        configured = str(self.config.prompt_optimizer.llm_task_name).strip()
        if validate and configured not in available:
            raise ValueError(
                f"提示词优化模型任务不可用：{configured or '<空>'}；当前可用任务：{', '.join(available) or '<无>'}"
            )

    def get_webui_config_schema(self, **kwargs: Any) -> Dict[str, Any]:
        """把宿主当前模型任务注入提示词优化模型下拉框。"""

        schema = super().get_webui_config_schema(**kwargs)
        sections = schema.get("sections")
        if not isinstance(sections, dict):
            return schema
        section = sections.get("prompt_optimizer")
        if not isinstance(section, dict):
            return schema
        fields = section.get("fields")
        if not isinstance(fields, dict):
            return schema
        field = fields.get("llm_task_name")
        if isinstance(field, dict):
            field["ui_type"] = "select"
            field["choices"] = list(self._available_model_tasks)
        return schema

    @Command(
        "suno_audio",
        description="Suno 音频工坊全部指令入口",
        pattern=r"(?s)^/声音(?:\s.*)?$",
        timeout_ms=90000,
    )
    async def handle_audio_command(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """执行 `/声音` 指令并拦截后续聊天流程。"""

        stream_id = str(kwargs.get("stream_id") or "")
        if self._router is None:
            await self.ctx.send.text("⚠️ 声音插件未就绪，请确认已启用插件并填写 API Mart API Key。", stream_id)
            return False, "插件未初始化", True
        message = kwargs.get("message")
        context = CommandContext(
            stream_id=stream_id,
            platform=str(kwargs.get("platform") or ""),
            group_id=str(kwargs.get("group_id") or ""),
            user_id=str(kwargs.get("user_id") or ""),
            text=str(kwargs.get("text") or "").strip(),
            message=dict(message) if isinstance(message, dict) else {},
        )
        try:
            response = await self._router.execute(context)
            if response and not send_succeeded(await self.ctx.send.text(response, stream_id)):
                raise RuntimeError("指令结果发送失败")
            return True, response or "指令执行完成", True
        except (SunoAudioError, PermissionError, ValueError) as exc:
            self.ctx.logger.warning("声音指令未执行：%s", exc)
            await self.ctx.send.text(f"⚠️ {exc}", stream_id)
            return False, str(exc), True
        except Exception as exc:
            self.ctx.logger.error("声音指令执行失败：%s", exc, exc_info=True)
            await self.ctx.send.text("⚠️ 声音指令执行失败，请稍后重试。", stream_id)
            return False, "指令执行失败", True

    @Tool("generate_audio", description=TOOL_DESCRIPTION, parameters=TOOL_PARAMETERS)
    async def handle_generate_audio(self, **kwargs: Any) -> tuple[bool, str]:
        """自然语言语义识别后的音频任务提交入口。"""

        if not self.config.prompt_optimizer.semantic_tool_enabled:
            return False, "自然语言音频生成功能未启用"
        if self._service is None:
            return False, "声音插件未就绪"
        try:
            request = self._request_from_tool(kwargs)
            stream_id = str(kwargs.get("stream_id") or kwargs.get("chat_id") or "")
            job, created = await self._service.submit(
                request,
                platform=str(kwargs.get("platform") or ""),
                stream_id=stream_id,
                group_id=str(kwargs.get("group_id") or ""),
                requester_id=str(kwargs.get("user_id") or ""),
                triggering_message_id=str(kwargs.get("msg_id") or kwargs.get("triggering_message_id") or ""),
                optimize_prompt=True,
            )
            if not created:
                return True, f"该消息已有音频任务 {job['short_id']}，无需重复创建"
            return True, f"已提交音频任务 {job['short_id']}，完成后会自动发送到当前聊天"
        except (SunoAudioError, PermissionError, ValueError) as exc:
            self.ctx.logger.warning("自然语言音频任务未提交：%s", exc)
            return False, str(exc)
        except Exception as exc:
            self.ctx.logger.error("自然语言音频任务提交失败：%s", exc, exc_info=True)
            return False, "音频任务提交失败"

    def _request_from_tool(self, kwargs: Dict[str, Any]) -> GenerationRequest:
        operation = AudioOperation(str(kwargs.get("operation") or ""))
        description = str(kwargs.get("description") or "").strip()
        config = self.config
        bpm_value = kwargs.get("bpm")
        request = GenerationRequest(
            operation=operation,
            original_prompt=description,
            prompt=description,
            version=(
                str(config.generation.sound_version)
                if operation == AudioOperation.SOUND
                else str(config.apimart.default_version)
            ),
            title=str(kwargs.get("title") or ""),
            style=str(kwargs.get("style") or ""),
            lyrics=str(kwargs.get("lyrics") or ""),
            negative_tags=str(config.generation.negative_tags),
            instrumental=operation == AudioOperation.INSTRUMENTAL,
            vocal_gender=str(config.generation.vocal_gender),
            style_weight=float(config.generation.style_weight),
            weirdness_constraint=float(config.generation.weirdness_constraint),
            audio_weight=float(config.generation.audio_weight),
            auto_lyrics=bool(config.generation.auto_lyrics),
            sound_type=str(kwargs.get("sound_type") or "one-shot"),
            bpm=int(bpm_value) if bpm_value is not None and bpm_value != "" else None,
            key=str(kwargs.get("key") or ""),
            lyrics_model=("" if str(config.apimart.lyrics_model) == "default" else str(config.apimart.lyrics_model)),
        )
        return request

    @API("audio_generate", description="提交 Suno 音频生成任务并返回供应商 task_id。", version="1", public=True)
    async def api_audio_generate(self, operation: str, description: str, **kwargs: Any) -> Dict[str, Any]:
        """供其他插件提交异步音频任务；此 API 不等待生成完成。"""

        if self._client is None:
            return {"success": False, "error": "声音插件未就绪"}
        try:
            parameters = dict(kwargs)
            parameters.update(operation=operation, description=description)
            request = self._request_from_tool(parameters)
            request.validate(
                max_prompt_chars=int(self.config.limits.max_prompt_chars),
                max_lyrics_chars=int(self.config.limits.max_lyrics_chars),
            )
            task_id = await self._client.submit(request)
            return {"success": True, "task_id": task_id}
        except Exception as exc:
            self.ctx.logger.error("外部 API 提交音频任务失败：%s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    @API("audio_task_get", description="查询 Suno 供应商任务状态和结果。", version="1", public=True)
    async def api_audio_task_get(self, task_id: str) -> Dict[str, Any]:
        """供其他插件查询异步音频结果。"""

        if self._client is None:
            return {"success": False, "error": "声音插件未就绪"}
        try:
            snapshot = await self._client.get_task(task_id)
            return {
                "success": True,
                "task_id": snapshot.task_id,
                "status": snapshot.status,
                "progress": snapshot.progress,
                "lyrics": snapshot.lyrics_text,
                "tracks": [track.__dict__ for track in snapshot.tracks],
                "error": snapshot.error_message,
            }
        except Exception as exc:
            self.ctx.logger.error("外部 API 查询音频任务失败：%s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}


def create_plugin() -> SunoAudioPlugin:
    """创建插件实例。"""

    return SunoAudioPlugin()
