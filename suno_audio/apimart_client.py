"""API Mart Suno 异步任务客户端。"""

from __future__ import annotations

from typing import Any, Dict, Optional
import json

import httpx

from .errors import (
    ApiMartAuthenticationError,
    ApiMartError,
    ApiMartInvalidRequestError,
    ApiMartPaymentRequiredError,
    ApiMartPermissionError,
    ApiMartProtocolError,
    ApiMartRateLimitError,
    ApiMartServerError,
    ApiMartSubmitUnknownError,
)
from .models import AudioOperation, AudioTrack, GenerationRequest, VENDOR_TASK_STATUSES, VendorTaskSnapshot


ENDPOINTS = {
    AudioOperation.MUSIC: "/v1/music/generations",
    AudioOperation.INSTRUMENTAL: "/v1/music/generations",
    AudioOperation.CUSTOM_SONG: "/v1/music/generations",
    AudioOperation.LYRICS: "/v1/music/generations/lyrics",
    AudioOperation.SOUND: "/v1/music/generations/sounds",
}


class ApiMartClient:
    """只负责 API Mart HTTP 协议和响应标准化。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "suno",
        connect_timeout_seconds: float = 10,
        request_timeout_seconds: float = 30,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url.startswith("https://"):
            raise ValueError("API Mart 地址必须使用 HTTPS")
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("API Mart API Key 不能为空")
        self.base_url = normalized_url
        self.api_key = normalized_key.removeprefix("Bearer ").strip()
        self.model = model.strip() or "suno"
        timeout = httpx.Timeout(request_timeout_seconds, connect=connect_timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    async def close(self) -> None:
        """关闭内部创建的 HTTP Client。"""

        if self._owns_client:
            await self._client.aclose()

    async def submit(self, request: GenerationRequest) -> str:
        """提交生成任务并返回供应商 task_id。"""

        endpoint = ENDPOINTS[request.operation]
        payload = request.to_vendor_payload(self.model)
        data = await self._request_json("POST", endpoint, payload=payload, submitting=True)
        if isinstance(data.get("error"), dict):
            self._raise_api_error(400, data)
        response_data = data.get("data")
        if not isinstance(response_data, list) or not response_data or not isinstance(response_data[0], dict):
            raise ApiMartProtocolError("供应商提交响应缺少 data[0]", error_type="protocol_error")
        task_id = str(response_data[0].get("task_id") or "").strip()
        if not task_id:
            raise ApiMartProtocolError("供应商提交响应缺少 task_id", error_type="protocol_error")
        return task_id

    async def get_task(self, task_id: str) -> VendorTaskSnapshot:
        """查询并标准化一个供应商任务。"""

        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("task_id 不能为空")
        payload = await self._request_json("GET", f"/v1/music/tasks/{normalized_task_id}")
        task_data = self._unwrap_task_payload(payload)
        status = str(task_data.get("status") or "").strip().lower()
        if status not in VENDOR_TASK_STATUSES:
            raise ApiMartProtocolError(f"未知供应商任务状态：{status or '<空>'}", error_type="protocol_error")
        task_id_value = str(task_data.get("task_id") or normalized_task_id).strip()
        progress_value = task_data.get("progress", 0)
        try:
            progress = max(0, min(100, int(progress_value)))
        except (TypeError, ValueError) as exc:
            raise ApiMartProtocolError("供应商 progress 不是整数", error_type="protocol_error") from exc

        nested_data = task_data.get("data")
        nested = nested_data if isinstance(nested_data, dict) else {}
        result_container = nested if "result" in nested or "error" in nested else task_data
        error_data = result_container.get("error")
        error_message = ""
        if isinstance(error_data, dict):
            error_message = str(error_data.get("message") or "").strip()
        result_data = result_container.get("result")
        result = result_data if isinstance(result_data, dict) else {}
        tracks = self._parse_tracks(result)
        lyrics_text = self._parse_lyrics(result, tracks)
        return VendorTaskSnapshot(
            task_id=task_id_value,
            status=status,
            progress=progress,
            tracks=tracks,
            lyrics_text=lyrics_text,
            error_message=error_message,
            raw_result=result,
            response_shape=self._describe_response_shape(task_data),
        )

    @staticmethod
    def _describe_response_shape(task_data: Dict[str, Any]) -> str:
        """仅记录响应字段结构，不把 URL、歌词或其他结果内容写入日志。"""

        task_keys = sorted(str(key) for key in task_data)
        data_value = task_data.get("data")
        data_keys = sorted(str(key) for key in data_value) if isinstance(data_value, dict) else []
        if isinstance(data_value, dict) and "result" in data_value:
            result_value = data_value.get("result")
        else:
            result_value = task_data.get("result")
        if isinstance(result_value, dict):
            result_shape = f"object:{sorted(str(key) for key in result_value)}"
        elif isinstance(result_value, list):
            result_shape = f"array:length={len(result_value)}"
        else:
            result_shape = type(result_value).__name__
        return f"task={task_keys}; data={data_keys}; result={result_shape}"

    async def download(self, url: str, *, max_bytes: int, timeout_seconds: float = 120) -> tuple[bytes, str]:
        """下载受大小限制的音频或封面。"""

        if not url.startswith("https://"):
            raise ValueError("只允许下载 HTTPS 结果地址")
        try:
            async with self._client.stream("GET", url, timeout=timeout_seconds) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise ValueError(f"文件超过允许大小：{content_length} > {max_bytes}")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise ValueError(f"文件超过允许大小：>{max_bytes}")
                return bytes(chunks), str(response.headers.get("Content-Type") or "application/octet-stream")
        except httpx.HTTPError as exc:
            raise ApiMartServerError(f"下载结果文件失败：{exc}", error_type="download_error") from exc

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        submitting: bool = False,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.request(method, f"{self.base_url}{endpoint}", headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if submitting:
                raise ApiMartSubmitUnknownError(
                    "提交请求中断，无法确认供应商是否已经创建任务；不会自动重复提交",
                    error_type="submit_unknown",
                ) from exc
            raise ApiMartServerError(f"查询供应商失败：{exc}", error_type="network_error") from exc

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ApiMartProtocolError(
                f"供应商返回非 JSON 响应（HTTP {response.status_code}）",
                status_code=response.status_code,
                error_type="protocol_error",
            ) from exc
        if not isinstance(data, dict):
            raise ApiMartProtocolError("供应商 JSON 顶层不是对象", error_type="protocol_error")
        if response.status_code >= 400:
            self._raise_api_error(response.status_code, data, response.headers.get("Retry-After"))
        return data

    @staticmethod
    def _unwrap_task_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        if "status" in payload:
            return payload
        data = payload.get("data")
        if isinstance(data, dict) and "status" in data:
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict) and "status" in data[0]:
            return data[0]
        raise ApiMartProtocolError("任务查询响应缺少状态对象", error_type="protocol_error")

    @staticmethod
    def _parse_tracks(result: Dict[str, Any]) -> list[AudioTrack]:
        music = result.get("music")
        if music is None:
            return []
        if not isinstance(music, list):
            raise ApiMartProtocolError("供应商 result.music 不是数组", error_type="protocol_error")
        tracks: list[AudioTrack] = []
        for index, item in enumerate(music, 1):
            if not isinstance(item, dict):
                raise ApiMartProtocolError("供应商 music[] 项不是对象", error_type="protocol_error")
            tracks.append(AudioTrack.from_vendor(item, index))
        return tracks

    @staticmethod
    def _parse_lyrics(result: Dict[str, Any], tracks: list[AudioTrack]) -> str:
        for key in ("lyrics", "text", "content"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for track in tracks:
            if track.lyrics.strip():
                return track.lyrics.strip()
        return ""

    @staticmethod
    def _raise_api_error(status_code: int, payload: Dict[str, Any], retry_after_raw: Optional[str] = None) -> None:
        error = payload.get("error")
        error_data = error if isinstance(error, dict) else {}
        message = str(error_data.get("message") or f"API Mart 请求失败（HTTP {status_code}）")
        error_type = str(error_data.get("type") or "")
        error_classes = {
            400: ApiMartInvalidRequestError,
            401: ApiMartAuthenticationError,
            402: ApiMartPaymentRequiredError,
            403: ApiMartPermissionError,
        }
        if status_code == 429:
            retry_after: Optional[float] = None
            if retry_after_raw:
                try:
                    retry_after = float(retry_after_raw)
                except ValueError:
                    retry_after = None
            raise ApiMartRateLimitError(message, retry_after=retry_after)
        if status_code >= 500:
            raise ApiMartServerError(message, status_code=status_code, error_type=error_type or "server_error")
        error_class = error_classes.get(status_code, ApiMartError)
        raise error_class(message, status_code=status_code, error_type=error_type)
