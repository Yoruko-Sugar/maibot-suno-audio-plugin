"""音频、封面与歌词结果发送。"""

from __future__ import annotations

from base64 import b64encode
from pathlib import PurePosixPath
from typing import Any, Dict, List
from urllib.parse import urlparse
import mimetypes
import re

from .apimart_client import ApiMartClient
from .storage import AudioRepository


INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def send_succeeded(result: Any) -> bool:
    """兼容 SDK bool 与结构化发送结果。"""

    if isinstance(result, dict):
        return bool(result.get("success", False))
    return bool(result)


class DeliveryService:
    """把已持久化结果发送到原聊天流。"""

    def __init__(self, plugin: Any, repository: AudioRepository, client: ApiMartClient) -> None:
        self.plugin = plugin
        self.repository = repository
        self.client = client

    async def deliver_job(self, job_id: str, *, audio_index: int | None = None, stream_id: str = "") -> None:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("音频任务不存在")
        target_stream = stream_id.strip() or str(job["stream_id"])
        tracks = self.repository.get_tracks(str(job["id"]))
        if audio_index is not None:
            tracks = [track for track in tracks if int(track["audio_index"]) == audio_index]
            if not tracks:
                raise ValueError(f"任务没有第 {audio_index} 个结果")

        self.repository.set_delivery_status(str(job["id"]), "delivering")
        try:
            if str(job["operation"]) == "lyrics" and not tracks:
                lyrics = str(job.get("lyrics_text") or "").strip()
                if not lyrics:
                    raise RuntimeError("歌词任务已完成，但供应商结果中没有歌词文本")
                await self._send_lyrics(target_stream, str(job["short_id"]), lyrics)
            else:
                selected_tracks: List[Dict[str, Any]] = tracks
                if not self.plugin.config.delivery.auto_send_all_tracks and audio_index is None:
                    selected_tracks = tracks[:1]
                report = self._build_track_report(job, selected_tracks)
                if not send_succeeded(await self.plugin.ctx.send.text(report, target_stream)):
                    raise RuntimeError("音频结果信息发送失败")
                for track in selected_tracks:
                    await self._deliver_track(job, track, target_stream)
            self.repository.set_delivery_status(str(job["id"]), "delivered")
        except Exception:
            self.repository.set_delivery_status(str(job["id"]), "failed")
            raise

    async def _deliver_track(self, job: Dict[str, Any], track: Dict[str, Any], stream_id: str) -> None:
        index = int(track["audio_index"])
        image_url = str(track.get("image_large_url") or track.get("image_url") or "")
        if self.plugin.config.delivery.send_cover and image_url:
            image_bytes, _ = await self.client.download(
                image_url,
                max_bytes=int(self.plugin.config.delivery.base64_max_bytes),
                timeout_seconds=float(self.plugin.config.delivery.download_timeout_seconds),
            )
            if not send_succeeded(await self.plugin.ctx.send.image(b64encode(image_bytes).decode("ascii"), stream_id)):
                raise RuntimeError("音频封面发送失败")

        operation = str(job["operation"])
        delivery_mode = (
            str(self.plugin.config.delivery.sound_mode)
            if operation == "sound"
            else str(self.plugin.config.delivery.music_mode)
        )
        try:
            await self._send_audio(track, stream_id, delivery_mode)
            self.repository.record_delivery_attempt(
                job_id=str(job["id"]),
                audio_index=index,
                delivery_mode=delivery_mode,
                success=True,
            )
        except Exception as exc:
            self.repository.record_delivery_attempt(
                job_id=str(job["id"]),
                audio_index=index,
                delivery_mode=delivery_mode,
                success=False,
                error_message=str(exc),
            )
            raise

        lyrics = str(track.get("lyrics") or "").strip()
        if self.plugin.config.delivery.send_lyrics and lyrics:
            lyrics_title = str(track.get("title") or "未命名歌曲").strip()
            await self._send_lyrics(stream_id, f"《{lyrics_title}》", lyrics)

    @staticmethod
    def _build_track_report(job: Dict[str, Any], tracks: List[Dict[str, Any]]) -> str:
        """为同一任务的全部候选音轨生成一条简洁报告。"""

        operation = str(job["operation"])
        label = {"instrumental": "音乐名", "sound": "音效名"}.get(operation, "歌名")
        default_title = {"instrumental": "未命名音乐", "sound": "未命名音效"}.get(operation, "未命名歌曲")
        titles = DeliveryService._unique_non_empty(
            [str(track.get("title") or default_title).strip() for track in tracks]
        )
        styles = DeliveryService._unique_non_empty([str(track.get("tags") or "").strip() for track in tracks])
        lines = ["✅ 音频生成完成", f"{label}：{'、'.join(f'《{title}》' for title in titles)}"]
        if styles:
            lines.append(f"风格：{'；'.join(styles)}")
        return "\n".join(lines)

    @staticmethod
    def _unique_non_empty(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value for value in values if value))

    async def _send_audio(self, track: Dict[str, Any], stream_id: str, mode: str) -> None:
        audio_url = str(track.get("audio_url") or "").strip()
        if not audio_url:
            raise RuntimeError("供应商结果没有 audio_url")
        filename, mime_type = self._build_filename(track, audio_url)
        if mode == "file_url":
            result = await self.plugin.ctx.send.custom(
                "file",
                {"name": filename, "mime_type": mime_type, "url": audio_url},
                stream_id,
            )
        else:
            audio_bytes, response_mime = await self.client.download(
                audio_url,
                max_bytes=int(self.plugin.config.delivery.base64_max_bytes),
                timeout_seconds=float(self.plugin.config.delivery.download_timeout_seconds),
            )
            audio_base64 = b64encode(audio_bytes).decode("ascii")
            if mode == "voice":
                result = await self.plugin.ctx.send.custom("voice", audio_base64, stream_id)
            elif mode == "file_base64":
                result = await self.plugin.ctx.send.custom(
                    "file",
                    {
                        "name": filename,
                        "size": len(audio_bytes),
                        "mime_type": response_mime or mime_type,
                        "url": f"base64://{audio_base64}",
                    },
                    stream_id,
                )
            else:
                raise ValueError(f"不支持的音频发送模式：{mode}")
        if not send_succeeded(result):
            raise RuntimeError("平台适配器未接受音频消息")

    async def _send_lyrics(self, stream_id: str, title: str, lyrics: str) -> None:
        if len(lyrics) <= 600:
            result = await self.plugin.ctx.send.text(f"📝 {title} 歌词\n\n{lyrics}", stream_id)
        else:
            segment_length = int(self.plugin.config.delivery.forward_segment_chars)
            chunks = [lyrics[index : index + segment_length] for index in range(0, len(lyrics), segment_length)]
            messages = [
                {
                    "user_id": "0",
                    "nickname": f"{title} 歌词｜{index}/{len(chunks)}",
                    "segments": [{"type": "text", "content": chunk}],
                }
                for index, chunk in enumerate(chunks, 1)
            ]
            result = await self.plugin.ctx.send.forward(messages, stream_id)
        if not send_succeeded(result):
            raise RuntimeError("歌词发送失败")

    @staticmethod
    def _build_filename(track: Dict[str, Any], audio_url: str) -> tuple[str, str]:
        path_name = PurePosixPath(urlparse(audio_url).path).name
        suffix = PurePosixPath(path_name).suffix.lower()
        if suffix not in {".mp3", ".wav", ".m4a", ".ogg", ".flac"}:
            suffix = ".mp3"
        title = str(track.get("title") or f"audio_{track['audio_index']}")
        safe_title = INVALID_FILENAME_CHARS.sub("_", title).strip(" ._")[:80] or "audio"
        mime_type = mimetypes.guess_type(f"x{suffix}")[0] or "audio/mpeg"
        return f"{safe_title}_{track['audio_index']}{suffix}", mime_type
