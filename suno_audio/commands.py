"""`/声音` 指令解析与响应。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import json

from .delivery import DeliveryService, send_succeeded
from .jobs import AudioJobManager
from .models import AudioOperation, GenerationRequest
from .service import AudioService
from .storage import AudioRepository


STATUS_LABELS = {
    "submitting": "正在提交",
    "submitted": "已提交",
    "pending": "生成中",
    "completed": "已完成",
    "failed": "生成失败",
    "submit_unknown": "提交结果未知",
    "tracking_error": "跟踪异常",
    "tracking_timeout": "跟踪超时",
    "delivering": "正在发送",
}

HELP_RECORDS = [
    "声音插件帮助大全\n所有生成任务提交后会在后台运行，完成后自动发回当前聊天。",
    "/声音 音乐 <描述>\n根据自然语言生成带人声音乐。\n示例：/声音 音乐 温柔女声演唱的夏日城市流行歌",
    "/声音 纯音乐 <描述>\n生成不含人声的纯音乐。\n示例：/声音 纯音乐 雨夜咖啡馆里的爵士钢琴",
    "/声音 歌曲 <标题> | <风格> | <歌词>\n使用指定标题、风格和歌词生成歌曲。歌词也可来自被引用消息。",
    "/声音 歌词 <主题>\n只生成歌词。\n示例：/声音 歌词 关于星际旅行与故乡的中文摇滚歌词",
    "/声音 音效 <描述>\n生成一次性短音效。\n示例：/声音 音效 清脆的游戏升级提示音",
    "/声音 音效 循环 <描述>\n生成可循环音效。\n示例：/声音 音效 循环 壁炉燃烧与轻微木柴爆裂声",
    "/声音 音效 高级 <描述> | <BPM> | <调性>\n生成带速度和调性约束的循环音效。调性使用 C、C#、Cm、C#m 等格式。",
    "/声音 状态 [任务ID]\n查看指定任务；省略 ID 时查看自己在当前聊天的最近任务。",
    "/声音 历史 [页码]\n以聊天记录形式显示当前聊天最近的音频任务。",
    "/声音 结果 <任务ID> [序号]\n显示已生成结果的详细信息。",
    "/声音 重发 <任务ID> [序号]\n重新发送已完成的结果；序号从 1 开始。",
    "/声音 管理 队列\n/声音 管理 重查 <任务ID>\n仅全局管理员可查看未完成队列或立即向供应商查询一次。",
    "自然语言触发\n开启后可直接说“帮我生成一段雨夜爵士乐”或“做一个游戏升级音效”。仅明确要求生成时触发；讨论音乐、询问能力、否定生成或转述他人请求时不应触发。",
]


@dataclass
class CommandContext:
    """执行一条声音指令所需的宿主上下文。"""

    stream_id: str
    platform: str
    group_id: str
    user_id: str
    text: str
    message: Dict[str, Any]


class AudioCommandRouter:
    """把中文指令路由到统一 AudioService。"""

    def __init__(
        self,
        plugin: Any,
        repository: AudioRepository,
        service: AudioService,
        jobs: AudioJobManager,
        delivery: DeliveryService,
    ) -> None:
        self.plugin = plugin
        self.repository = repository
        self.service = service
        self.jobs = jobs
        self.delivery = delivery

    async def execute(self, context: CommandContext) -> str:
        argument = context.text.removeprefix("/声音").strip()
        if not argument or argument == "帮助":
            await self._send_forward(context.stream_id, HELP_RECORDS, "声音插件帮助")
            return ""
        if argument.startswith("音乐 "):
            return await self._submit_simple(context, AudioOperation.MUSIC, argument[3:].strip())
        if argument.startswith("纯音乐 "):
            return await self._submit_simple(context, AudioOperation.INSTRUMENTAL, argument[4:].strip())
        if argument.startswith("歌曲 "):
            return await self._submit_song(context, argument[3:].strip())
        if argument.startswith("歌词 "):
            return await self._submit_simple(context, AudioOperation.LYRICS, argument[3:].strip())
        if argument.startswith("音效 高级 "):
            return await self._submit_advanced_sound(context, argument[6:].strip())
        if argument.startswith("音效 循环 "):
            return await self._submit_sound(context, argument[6:].strip(), "loop")
        if argument.startswith("音效 "):
            return await self._submit_sound(context, argument[3:].strip(), "one-shot")
        if argument == "状态" or argument.startswith("状态 "):
            return self._status(context, argument.removeprefix("状态").strip())
        if argument == "历史" or argument.startswith("历史 "):
            await self._history(context, argument.removeprefix("历史").strip())
            return ""
        if argument.startswith("结果 "):
            return self._result(context, argument[3:].strip())
        if argument.startswith("重发 "):
            return await self._resend(context, argument[3:].strip())
        if argument == "管理 队列":
            await self._admin_queue(context)
            return ""
        if argument.startswith("管理 重查 "):
            return await self._admin_refresh(context, argument[6:].strip())
        raise ValueError("未知声音指令，请使用 /声音 帮助 查看完整用法")

    async def _submit_simple(
        self,
        context: CommandContext,
        operation: AudioOperation,
        prompt: str,
    ) -> str:
        if not prompt:
            raise ValueError("生成描述不能为空")
        request = self._base_request(operation, prompt)
        if operation == AudioOperation.INSTRUMENTAL:
            request.instrumental = True
        return await self._submit(context, request)

    async def _submit_song(self, context: CommandContext, argument: str) -> str:
        parts = [part.strip() for part in argument.split("|", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("歌曲格式：/声音 歌曲 <标题> | <风格> | <歌词>，歌词也可放在引用消息中")
        lyrics = parts[2] if len(parts) == 3 else await self._quoted_text(context)
        if not lyrics:
            raise ValueError("没有找到歌词，请在第三段填写歌词或引用一条歌词消息")
        request = self._base_request(AudioOperation.CUSTOM_SONG, f"{parts[0]}，{parts[1]}")
        request.title = parts[0]
        request.style = parts[1]
        request.lyrics = lyrics
        return await self._submit(context, request)

    async def _submit_sound(self, context: CommandContext, prompt: str, sound_type: str) -> str:
        if not prompt:
            raise ValueError("音效描述不能为空")
        request = self._base_request(AudioOperation.SOUND, prompt)
        request.version = str(self.plugin.config.generation.sound_version)
        request.sound_type = sound_type
        return await self._submit(context, request)

    async def _submit_advanced_sound(self, context: CommandContext, argument: str) -> str:
        parts = [part.strip() for part in argument.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError("高级音效格式：/声音 音效 高级 <描述> | <BPM> | <调性>")
        try:
            bpm = int(parts[1])
        except ValueError as exc:
            raise ValueError("BPM 必须是 1 到 300 的整数") from exc
        request = self._base_request(AudioOperation.SOUND, parts[0])
        request.version = str(self.plugin.config.generation.sound_version)
        request.sound_type = "loop"
        request.bpm = bpm
        request.key = parts[2]
        return await self._submit(context, request)

    async def _submit(self, context: CommandContext, request: GenerationRequest) -> str:
        job, created = await self.service.submit(
            request,
            platform=context.platform,
            stream_id=context.stream_id,
            group_id=context.group_id,
            requester_id=context.user_id,
            triggering_message_id=self._message_id(context.message),
            optimize_prompt=bool(self.plugin.config.prompt_optimizer.optimize_command_prompts),
        )
        if not created:
            return f"这条消息已经创建过任务：{job['short_id']}（{self._status_label(job)}）"
        return f"🎵 音频任务已提交：{job['short_id']}\n生成将在后台进行，完成后会自动发送。\n查询：/声音 状态 {job['short_id']}"

    def _base_request(self, operation: AudioOperation, prompt: str) -> GenerationRequest:
        config = self.plugin.config
        lyrics_model = "" if str(config.apimart.lyrics_model) == "default" else str(config.apimart.lyrics_model)
        return GenerationRequest(
            operation=operation,
            original_prompt=prompt,
            prompt=prompt,
            version=str(config.apimart.default_version),
            negative_tags=str(config.generation.negative_tags),
            vocal_gender=str(config.generation.vocal_gender),
            style_weight=float(config.generation.style_weight),
            weirdness_constraint=float(config.generation.weirdness_constraint),
            audio_weight=float(config.generation.audio_weight),
            auto_lyrics=bool(config.generation.auto_lyrics),
            lyrics_model=lyrics_model,
        )

    def _status(self, context: CommandContext, identifier: str) -> str:
        job = self._resolve_job(context, identifier)
        lines = [
            f"🎵 音频任务 {job['short_id']}",
            f"类型：{job['operation']}",
            f"状态：{self._status_label(job)}",
            f"进度：{job['progress']}%",
            f"创建：{job['created_at']}",
            f"发送：{job['delivery_status']}",
        ]
        if job.get("error_message"):
            lines.append(f"原因：{job['error_message']}")
        return "\n".join(lines)

    async def _history(self, context: CommandContext, page_text: str) -> None:
        try:
            page = max(1, int(page_text or "1"))
        except ValueError as exc:
            raise ValueError("历史页码必须是整数") from exc
        jobs = self.repository.list_stream_jobs(context.stream_id, limit=10, offset=(page - 1) * 10)
        records = [
            f"{job['short_id']}\n类型：{job['operation']}\n状态：{self._status_label(job)}\n创建：{job['created_at']}"
            for job in jobs
        ]
        await self._send_forward(context.stream_id, records or ["当前聊天暂无音频任务。"], f"声音历史｜第 {page} 页")

    def _result(self, context: CommandContext, argument: str) -> str:
        identifier, audio_index = self._parse_identifier_index(argument)
        job = self._resolve_job(context, identifier)
        if str(job["status"]) != "completed":
            raise ValueError(f"任务尚未完成，当前状态：{self._status_label(job)}")
        tracks = self.repository.get_tracks(str(job["id"]))
        if audio_index is not None:
            tracks = [track for track in tracks if int(track["audio_index"]) == audio_index]
        if str(job["operation"]) == "lyrics":
            return f"📝 {job['short_id']}\n{job.get('lyrics_text') or '供应商未返回歌词文本'}"
        if not tracks:
            raise ValueError("任务没有匹配的音频结果")
        return "\n\n".join(
            f"结果 {track['audio_index']}《{track['title'] or '未命名'}》\n"
            f"时长：{track['duration_seconds'] or '未返回'} 秒\n风格：{track['tags'] or '未返回'}\n"
            f"音频地址：{track['audio_url']}"
            for track in tracks
        )

    async def _resend(self, context: CommandContext, argument: str) -> str:
        identifier, audio_index = self._parse_identifier_index(argument)
        job = self._resolve_job(context, identifier)
        if str(job["status"]) != "completed":
            raise ValueError("只有已完成的任务可以重发")
        await self.delivery.deliver_job(str(job["id"]), audio_index=audio_index, stream_id=context.stream_id)
        return f"✅ 已重发任务 {job['short_id']}" + (f" 的第 {audio_index} 个结果" if audio_index else "")

    async def _admin_queue(self, context: CommandContext) -> None:
        self._require_admin(context.user_id)
        jobs = self.repository.list_resumable_jobs()
        records = [
            f"{job['short_id']}\n聊天流：{job['stream_id']}\n用户：{job['requester_id']}\n"
            f"状态：{self._status_label(job)}\n供应商任务：{job['vendor_task_id']}"
            for job in jobs
        ]
        await self._send_forward(context.stream_id, records or ["当前没有待跟踪任务。"], "声音任务队列")

    async def _admin_refresh(self, context: CommandContext, identifier: str) -> str:
        self._require_admin(context.user_id)
        job = self.service.get_job(identifier)
        await self.jobs.refresh_now(str(job["id"]))
        refreshed = self.service.get_job(str(job["id"]))
        return f"已重新查询 {job['short_id']}：{self._status_label(refreshed)}"

    def _resolve_job(self, context: CommandContext, identifier: str) -> Dict[str, Any]:
        job = (
            self.service.get_job(identifier)
            if identifier
            else self.repository.latest_user_job(
                context.stream_id,
                context.user_id,
            )
        )
        if job is None:
            raise ValueError("当前聊天没有可查询的音频任务")
        if str(job["stream_id"]) != context.stream_id and not self.service.is_admin(context.user_id):
            raise PermissionError("不能访问其他聊天流的音频任务")
        return job

    def _require_admin(self, user_id: str) -> None:
        if not self.service.is_admin(user_id):
            raise PermissionError("此操作仅限全局管理员")

    async def _quoted_text(self, context: CommandContext) -> str:
        target_id = str(context.message.get("reply_to") or "").strip()
        if not target_id:
            for segment in context.message.get("raw_message") or []:
                if not isinstance(segment, dict) or str(segment.get("type") or "").lower() != "reply":
                    continue
                data = segment.get("data")
                if isinstance(data, dict):
                    target_id = str(data.get("target_message_id") or data.get("id") or "").strip()
                if target_id:
                    break
        if not target_id:
            return ""
        message = await self.plugin.ctx.message.get_by_id(
            message_id=target_id,
            chat_id=context.stream_id,
            stream_id=context.stream_id,
        )
        if not isinstance(message, dict):
            raise ValueError("无法读取被引用的歌词消息")
        return str(
            message.get("processed_plain_text") or message.get("plain_text") or message.get("text") or ""
        ).strip()

    async def _send_forward(self, stream_id: str, records: List[str], title: str) -> None:
        messages = [
            {
                "user_id": "0",
                "nickname": f"{title}｜{index}/{len(records)}",
                "segments": [{"type": "text", "content": record}],
            }
            for index, record in enumerate(records, 1)
        ]
        if not send_succeeded(await self.plugin.ctx.send.forward(messages, stream_id)):
            raise RuntimeError("聊天记录消息发送失败")

    @staticmethod
    def _message_id(message: Dict[str, Any]) -> str:
        message_info = message.get("message_info")
        if isinstance(message_info, dict):
            return str(message_info.get("message_id") or message.get("message_id") or "").strip()
        return str(message.get("message_id") or "").strip()

    @staticmethod
    def _parse_identifier_index(argument: str) -> tuple[str, int | None]:
        parts = argument.split()
        if not parts:
            raise ValueError("请填写任务 ID")
        if len(parts) == 1:
            return parts[0], None
        if len(parts) != 2:
            raise ValueError("格式：<任务ID> [结果序号]")
        try:
            audio_index = int(parts[1])
        except ValueError as exc:
            raise ValueError("结果序号必须是正整数") from exc
        if audio_index < 1:
            raise ValueError("结果序号必须是正整数")
        return parts[0], audio_index

    @staticmethod
    def _status_label(job: Dict[str, Any]) -> str:
        status = str(job.get("status") or "")
        return STATUS_LABELS.get(status, status or "未知")

    @staticmethod
    def request_from_json(request_json: str) -> GenerationRequest:
        """保留给后续重做功能使用的严格请求反序列化入口。"""

        data = json.loads(request_json)
        data["operation"] = AudioOperation(str(data["operation"]))
        return GenerationRequest(**data)
