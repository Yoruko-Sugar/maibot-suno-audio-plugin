"""音频生成业务编排、权限和额度控制。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import asyncio

from .apimart_client import ApiMartClient
from .errors import (
    AccessDeniedError,
    ApiMartError,
    ApiMartSubmitUnknownError,
    JobNotFoundError,
    UsageLimitError,
)
from .jobs import AudioJobManager
from .models import GenerationRequest
from .prompt_planner import PromptPlanningService
from .storage import AudioRepository


class AudioService:
    """所有 Command、Tool 与 API 共用的业务入口。"""

    def __init__(
        self,
        plugin: Any,
        repository: AudioRepository,
        client: ApiMartClient,
        planner: PromptPlanningService,
        jobs: AudioJobManager,
    ) -> None:
        self.plugin = plugin
        self.repository = repository
        self.client = client
        self.planner = planner
        self.jobs = jobs
        self._submit_lock = asyncio.Lock()

    def is_admin(self, user_id: str) -> bool:
        admins = {str(value) for value in self.plugin.config.permissions.global_admin_ids}
        return user_id in admins

    def ensure_access(self, *, user_id: str, group_id: str, is_group: bool) -> None:
        config = self.plugin.config
        if is_group and not config.plugin.allow_group_chat:
            raise AccessDeniedError("本插件当前不允许在群聊中使用")
        if not is_group and not config.plugin.allow_private_chat:
            raise AccessDeniedError("本插件当前不允许在私聊中使用")
        groups = {str(value) for value in config.permissions.group_whitelist}
        if is_group and groups and group_id not in groups:
            raise AccessDeniedError("当前群不在声音插件允许列表中")
        if self.is_admin(user_id):
            return
        mode = str(config.permissions.access_mode)
        if mode == "admin_only":
            raise AccessDeniedError("声音插件当前仅限全局管理员使用")
        if mode == "whitelist":
            users = {str(value) for value in config.permissions.user_whitelist}
            if user_id not in users:
                raise AccessDeniedError("当前账号不在声音插件白名单中")

    async def submit(
        self,
        request: GenerationRequest,
        *,
        platform: str,
        stream_id: str,
        group_id: str,
        requester_id: str,
        triggering_message_id: str = "",
        optimize_prompt: bool = True,
    ) -> tuple[Dict[str, Any], bool]:
        """优化、校验、持久化并提交供应商任务。"""

        self.ensure_access(user_id=requester_id, group_id=group_id, is_group=bool(group_id))
        existing = self.repository.get_job_by_trigger(stream_id, triggering_message_id)
        if existing is not None:
            return existing, False
        request = await self.planner.optimize(request, enabled=optimize_prompt)
        request.validate(
            max_prompt_chars=int(self.plugin.config.limits.max_prompt_chars),
            max_lyrics_chars=int(self.plugin.config.limits.max_lyrics_chars),
        )
        async with self._submit_lock:
            self._check_usage_limits(stream_id, requester_id)
            job, created = self.repository.create_job(
                request=request,
                platform=platform,
                stream_id=stream_id,
                group_id=group_id,
                requester_id=requester_id,
                triggering_message_id=triggering_message_id,
            )
            if not created:
                return job, False
            self.plugin.ctx.logger.info(
                "开始提交音频任务：job=%s operation=%s stream=%s user=%s",
                job["short_id"],
                request.operation.value,
                stream_id,
                requester_id,
            )
            try:
                vendor_task_id = await self.client.submit(request)
            except ApiMartSubmitUnknownError as exc:
                self.repository.mark_failed(
                    str(job["id"]),
                    status="submit_unknown",
                    error_type="submit_unknown",
                    error_message=str(exc),
                )
                raise
            except ApiMartError as exc:
                self.repository.mark_failed(
                    str(job["id"]),
                    status="failed",
                    error_type=exc.error_type or type(exc).__name__,
                    error_message=str(exc),
                )
                raise
            self.repository.mark_submitted(str(job["id"]), vendor_task_id)
            submitted = self.repository.get_job(str(job["id"]))
            if submitted is None:
                raise RuntimeError("提交成功后无法读取本地任务")
            self.plugin.ctx.logger.info(
                "供应商音频任务已创建：job=%s vendor_task=%s",
                job["short_id"],
                vendor_task_id,
            )
            self.jobs.schedule(str(job["id"]))
            return submitted, True

    def get_job(self, identifier: str) -> Dict[str, Any]:
        job = self.repository.get_job(identifier)
        if job is None:
            raise JobNotFoundError("找不到指定音频任务")
        return job

    def _check_usage_limits(self, stream_id: str, requester_id: str) -> None:
        limits = self.plugin.config.limits
        active = self.repository.count_active_stream_jobs(stream_id)
        if active >= int(limits.max_active_jobs_per_stream):
            raise UsageLimitError("当前聊天流已有音频任务正在生成，请等待完成")
        if self.is_admin(requester_id):
            return
        cooldown = int(limits.user_cooldown_seconds)
        latest = self.repository.latest_user_created_at(requester_id)
        if cooldown > 0 and latest:
            latest_time = datetime.fromisoformat(latest)
            remaining = cooldown - int((datetime.now(timezone.utc) - latest_time).total_seconds())
            if remaining > 0:
                raise UsageLimitError(f"请等待 {remaining} 秒后再提交音频任务")
        daily_limit = int(limits.daily_jobs_per_user)
        if daily_limit > 0:
            now = datetime.now(timezone.utc)
            day_start = (
                now - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second, microseconds=now.microsecond)
            ).isoformat()
            used = self.repository.count_user_jobs_since(requester_id, day_start)
            if used >= daily_limit:
                raise UsageLimitError(f"今天的音频任务额度已用完（{used}/{daily_limit}）")
