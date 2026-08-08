"""可恢复的供应商任务轮询与结果投递。"""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import time

from .apimart_client import ApiMartClient
from .delivery import DeliveryService
from .errors import ApiMartProtocolError, ApiMartRateLimitError, ApiMartServerError
from .storage import AudioRepository


class AudioJobManager:
    """管理后台轮询协程，不在命令或 Tool RPC 中等待完整生成。"""

    def __init__(
        self,
        plugin: Any,
        repository: AudioRepository,
        client: ApiMartClient,
        delivery: DeliveryService,
    ) -> None:
        self.plugin = plugin
        self.repository = repository
        self.client = client
        self.delivery = delivery
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(int(plugin.config.limits.max_concurrent_jobs))
        self._closing = False

    def schedule(self, job_id: str) -> None:
        """为任务创建唯一后台协程。"""

        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        if self._closing:
            raise RuntimeError("任务管理器正在关闭")
        task = asyncio.create_task(self._track(job_id), name=f"suno-audio-{job_id[:8]}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda completed, key=job_id: self._task_done(key, completed))

    def resume(self) -> int:
        """恢复数据库中的未完成任务。"""

        jobs = self.repository.list_resumable_jobs()
        for job in jobs:
            self.schedule(str(job["id"]))
        return len(jobs)

    async def refresh_now(self, job_id: str) -> None:
        """管理员手动查询一次并在完成时投递。"""

        job = self.repository.get_job(job_id)
        if job is None or not job.get("vendor_task_id"):
            raise ValueError("任务没有可查询的供应商 task_id")
        snapshot = await self.client.get_task(str(job["vendor_task_id"]))
        await self._handle_snapshot(job, snapshot)

    async def shutdown(self) -> None:
        """取消本地协程，不改写供应商任务状态。"""

        self._closing = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _track(self, job_id: str) -> None:
        async with self._semaphore:
            job = self.repository.get_job(job_id)
            if job is None:
                return
            vendor_task_id = str(job.get("vendor_task_id") or "")
            if not vendor_task_id:
                self.repository.mark_failed(
                    job_id,
                    status="tracking_error",
                    error_type="missing_vendor_task_id",
                    error_message="本地任务缺少供应商 task_id",
                )
                return
            self.plugin.ctx.logger.info(
                "开始跟踪音频任务：job=%s vendor_task=%s operation=%s",
                job["short_id"],
                vendor_task_id,
                job["operation"],
            )
            started = time.monotonic()
            poll_errors = 0
            last_state = (str(job.get("vendor_status") or ""), int(job.get("progress") or 0))
            while time.monotonic() - started < int(self.plugin.config.apimart.generation_timeout_seconds):
                try:
                    snapshot = await self.client.get_task(vendor_task_id)
                    poll_errors = 0
                    current_state = (snapshot.status, snapshot.progress)
                    if current_state != last_state:
                        self.plugin.ctx.logger.info(
                            "音频任务状态更新：job=%s status=%s progress=%s",
                            job["short_id"],
                            snapshot.status,
                            snapshot.progress,
                        )
                        last_state = current_state
                    finished = await self._handle_snapshot(job, snapshot)
                    if finished:
                        return
                    await asyncio.sleep(int(self.plugin.config.apimart.poll_interval_seconds))
                except ApiMartRateLimitError as exc:
                    poll_errors += 1
                    if poll_errors >= int(self.plugin.config.apimart.max_poll_errors):
                        await self._mark_tracking_timeout(job, str(exc))
                        return
                    await asyncio.sleep(exc.retry_after or min(30, 5 * (2 ** (poll_errors - 1))))
                except ApiMartServerError as exc:
                    poll_errors += 1
                    self.plugin.ctx.logger.warning(
                        "音频任务查询失败：job=%s attempt=%s error=%s",
                        job["short_id"],
                        poll_errors,
                        exc,
                    )
                    if poll_errors >= int(self.plugin.config.apimart.max_poll_errors):
                        await self._mark_tracking_timeout(job, str(exc))
                        return
                    await asyncio.sleep(min(30, 5 * (2 ** (poll_errors - 1))))
                except asyncio.CancelledError:
                    self.plugin.ctx.logger.info("停止本地音频任务跟踪：job=%s", job["short_id"])
                    raise
                except ApiMartProtocolError as exc:
                    self.repository.mark_failed(
                        job_id,
                        status="tracking_error",
                        error_type="protocol_error",
                        error_message=str(exc),
                    )
                    self.plugin.ctx.logger.error("音频任务协议解析失败：job=%s error=%s", job["short_id"], exc)
                    return
                except Exception as exc:
                    self.repository.mark_failed(
                        job_id,
                        status="tracking_error",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    self.plugin.ctx.logger.error(
                        "音频任务跟踪异常：job=%s error=%s",
                        job["short_id"],
                        exc,
                        exc_info=True,
                    )
                    return
            await self._mark_tracking_timeout(job, "超过自动跟踪总时长")

    async def _handle_snapshot(self, job: Dict[str, Any], snapshot: Any) -> bool:
        job_id = str(job["id"])
        if snapshot.status in {"submitted", "pending"}:
            self.repository.update_snapshot(job_id, snapshot)
            return False
        if snapshot.status == "failed":
            message = snapshot.error_message or "供应商任务失败"
            self.repository.mark_failed(
                job_id,
                status="failed",
                error_type="vendor_task_failed",
                error_message=message,
            )
            await self.plugin.ctx.send.text(
                f"⚠️ 音频任务 {job['short_id']} 生成失败\n原因：{message}", str(job["stream_id"])
            )
            return True
        if snapshot.status == "completed":
            if str(job["operation"]) == "lyrics":
                if not snapshot.lyrics_text:
                    raise ApiMartProtocolError("歌词任务完成但结果中没有歌词文本", error_type="protocol_error")
            elif not snapshot.tracks:
                raise ApiMartProtocolError("音频任务完成但结果中没有 music[]", error_type="protocol_error")
            self.repository.complete_job(job_id, snapshot)
            self.plugin.ctx.logger.info("音频任务结果已保存：job=%s tracks=%s", job["short_id"], len(snapshot.tracks))
            try:
                await self.delivery.deliver_job(job_id)
                self.plugin.ctx.logger.info("音频任务发送完成：job=%s", job["short_id"])
            except Exception as exc:
                self.plugin.ctx.logger.error(
                    "音频任务已生成但发送失败：job=%s error=%s",
                    job["short_id"],
                    exc,
                    exc_info=True,
                )
                await self.plugin.ctx.send.text(
                    f"⚠️ 音频已生成，但发送失败\n任务：{job['short_id']}\n可使用：/声音 重发 {job['short_id']}",
                    str(job["stream_id"]),
                )
            return True
        raise ApiMartProtocolError(f"无法处理供应商状态：{snapshot.status}", error_type="protocol_error")

    async def _mark_tracking_timeout(self, job: Dict[str, Any], message: str) -> None:
        job_id = str(job["id"])
        self.repository.mark_failed(
            job_id,
            status="tracking_timeout",
            error_type="tracking_timeout",
            error_message=message,
        )
        self.plugin.ctx.logger.error("音频任务停止自动跟踪：job=%s error=%s", job["short_id"], message)
        await self.plugin.ctx.send.text(
            f"⚠️ 音频任务 {job['short_id']} 已停止自动查询。\n查询：/声音 状态 {job['short_id']}",
            str(job["stream_id"]),
        )

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            exception_info = (type(exception), exception, exception.__traceback__)
            self.plugin.ctx.logger.error("音频后台任务未捕获异常：job=%s", job_id, exc_info=exception_info)
