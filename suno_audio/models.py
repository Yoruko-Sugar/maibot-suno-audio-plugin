"""Suno 音频请求、任务与音轨数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib
import json
import re


MUSIC_VERSIONS = {"v3.5", "v4", "v4.5", "v4.5+", "v4.5-all", "v5", "v5.5"}
SOUND_VERSIONS = {"v5", "v5.5"}
SOUND_TYPES = {"one-shot", "loop"}
VOCAL_GENDERS = {"", "Male", "Female"}
VENDOR_IN_PROGRESS_STATUSES = {"submitted", "pending", "processing"}
VENDOR_TASK_STATUSES = VENDOR_IN_PROGRESS_STATUSES | {"completed", "failed"}
KEY_PATTERN = re.compile(r"^(?:C|C#|D|D#|E|F|F#|G|G#|A|A#|B)m?$")


class AudioOperation(str, Enum):
    """P0 支持的音频操作。"""

    MUSIC = "music"
    INSTRUMENTAL = "instrumental"
    CUSTOM_SONG = "custom_song"
    LYRICS = "lyrics"
    SOUND = "sound"


@dataclass
class GenerationRequest:
    """经过语义解析和本地校验的生成请求。"""

    operation: AudioOperation
    original_prompt: str
    prompt: str
    version: str = "v5.5"
    title: str = ""
    style: str = ""
    lyrics: str = ""
    negative_tags: str = ""
    instrumental: bool = False
    vocal_gender: str = ""
    style_weight: float = 0.6
    weirdness_constraint: float = 0.3
    audio_weight: float = 0.5
    auto_lyrics: bool = False
    sound_type: str = "one-shot"
    bpm: Optional[int] = None
    key: str = ""
    lyrics_model: str = ""
    optimizer_model: str = ""

    def validate(self, *, max_prompt_chars: int = 3000, max_lyrics_chars: int = 12000) -> None:
        """在请求供应商之前完整校验参数。"""

        self.original_prompt = self.original_prompt.strip()
        self.prompt = self.prompt.strip()
        self.title = self.title.strip()
        self.style = self.style.strip()
        self.lyrics = self.lyrics.strip()
        self.negative_tags = self.negative_tags.strip()
        self.vocal_gender = self.vocal_gender.strip()
        self.key = self.key.strip()
        self.lyrics_model = self.lyrics_model.strip()

        if not self.original_prompt:
            raise ValueError("原始描述不能为空")
        if len(self.original_prompt) > max(max_prompt_chars, max_lyrics_chars):
            raise ValueError("原始描述超过允许长度")
        if len(self.prompt) > max_prompt_chars:
            raise ValueError(f"提示词不能超过 {max_prompt_chars} 个字符")
        if len(self.lyrics) > max_lyrics_chars:
            raise ValueError(f"歌词不能超过 {max_lyrics_chars} 个字符")
        if self.operation in {
            AudioOperation.MUSIC,
            AudioOperation.INSTRUMENTAL,
            AudioOperation.LYRICS,
            AudioOperation.SOUND,
        }:
            if not self.prompt:
                raise ValueError("提示词不能为空")
        if self.operation == AudioOperation.CUSTOM_SONG:
            if not self.title:
                raise ValueError("自定义歌曲必须填写标题")
            if not self.style:
                raise ValueError("自定义歌曲必须填写风格")
            if not self.instrumental and not self.lyrics:
                raise ValueError("自定义歌曲必须填写歌词")
        if self.operation in {AudioOperation.MUSIC, AudioOperation.INSTRUMENTAL, AudioOperation.CUSTOM_SONG}:
            if self.version not in MUSIC_VERSIONS:
                raise ValueError(f"不支持的音乐版本：{self.version}")
        if self.operation == AudioOperation.SOUND:
            if self.version not in SOUND_VERSIONS:
                raise ValueError(f"音效版本只支持：{', '.join(sorted(SOUND_VERSIONS))}")
            if self.sound_type not in SOUND_TYPES:
                raise ValueError("音效类型只支持 one-shot 或 loop")
            if self.bpm is not None and not 1 <= self.bpm <= 300:
                raise ValueError("BPM 必须在 1 到 300 之间")
            if self.key and not KEY_PATTERN.fullmatch(self.key):
                raise ValueError("调性格式无效，请使用 C、C#、Cm、C#m 等升号写法")
        if self.vocal_gender not in VOCAL_GENDERS:
            raise ValueError("人声性别只支持 Male、Female 或留空")
        for field_name, value in (
            ("风格权重", self.style_weight),
            ("创意度", self.weirdness_constraint),
            ("音频权重", self.audio_weight),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name}必须在 0 到 1 之间")
        if self.lyrics_model not in {"", "classic", "remi"}:
            raise ValueError("歌词模型只支持 classic、remi 或留空")

    def to_vendor_payload(self, model: str = "suno") -> Dict[str, Any]:
        """构造当前操作对应的 API Mart 请求体。"""

        if self.operation == AudioOperation.LYRICS:
            payload: Dict[str, Any] = {"model": model, "prompt": self.prompt}
            if self.lyrics_model:
                payload["lyrics_model"] = self.lyrics_model
            return payload
        if self.operation == AudioOperation.SOUND:
            payload = {
                "model": model,
                "version": self.version,
                "prompt": self.prompt,
                "type": self.sound_type,
            }
            if self.bpm is not None:
                payload["bpm"] = self.bpm
            if self.key:
                payload["key"] = self.key
            return payload

        custom = self.operation == AudioOperation.CUSTOM_SONG
        instrumental = self.operation == AudioOperation.INSTRUMENTAL or self.instrumental
        payload = {
            "model": model,
            "custom": custom,
            "instrumental": instrumental,
            "version": self.version,
            "prompt": self.lyrics if custom and not instrumental else self.prompt,
        }
        if self.vocal_gender:
            payload["vocal_gender"] = self.vocal_gender
        if custom:
            payload.update(
                title=self.title,
                style=self.style,
                negative_tags=self.negative_tags,
                auto_lyrics=self.auto_lyrics,
                style_weight=self.style_weight,
                weirdness_constraint=self.weirdness_constraint,
                audio_weight=self.audio_weight,
            )
        return payload

    def to_json(self) -> str:
        """序列化为稳定 JSON。"""

        data = asdict(self)
        data["operation"] = self.operation.value
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    @property
    def request_hash(self) -> str:
        """生成请求去重哈希。"""

        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass
class AudioTrack:
    """供应商返回的一首音轨。"""

    audio_index: int
    audio_id: str = ""
    title: str = ""
    duration_seconds: Optional[float] = None
    lyrics: str = ""
    tags: str = ""
    audio_url: str = ""
    image_url: str = ""
    image_large_url: str = ""
    video_url: str = ""

    @classmethod
    def from_vendor(cls, item: Dict[str, Any], audio_index: int) -> "AudioTrack":
        """从供应商 music[] 项构造音轨。"""

        duration_value = item.get("duration")
        duration = float(duration_value) if isinstance(duration_value, (int, float)) else None
        return cls(
            audio_index=audio_index,
            audio_id=str(item.get("audio_id") or ""),
            title=str(item.get("title") or ""),
            duration_seconds=duration,
            lyrics=str(item.get("lyrics") or ""),
            tags=str(item.get("tags") or ""),
            audio_url=str(item.get("audio_url") or ""),
            image_url=str(item.get("image_url") or ""),
            image_large_url=str(item.get("image_large_url") or ""),
            video_url=str(item.get("video_url") or ""),
        )


@dataclass
class VendorTaskSnapshot:
    """标准化后的供应商任务查询结果。"""

    task_id: str
    status: str
    progress: int
    tracks: List[AudioTrack] = field(default_factory=list)
    lyrics_text: str = ""
    error_message: str = ""
    raw_result: Dict[str, Any] = field(default_factory=dict)
