"""使用 MaiBot 模型把自然语言转换为严格 Suno 参数。"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Dict
import json

from .errors import PromptPlanningError
from .models import AudioOperation, GenerationRequest
from .prompts import build_prompt


class PromptPlanningService:
    """统一处理指令和 Tool 入口的提示词优化。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    async def optimize(self, request: GenerationRequest, *, enabled: bool = True) -> GenerationRequest:
        """优化请求；失败时按显式配置中止或返回原请求。"""

        config = self.plugin.config.prompt_optimizer
        if not enabled or not config.prompt_optimizer_enabled:
            return request
        source = asdict(request)
        source["operation"] = request.operation.value
        prompt = build_prompt(
            request.operation.value,
            source,
            bool(config.preserve_user_lyrics),
        )
        self.plugin.ctx.logger.info("开始优化音频提示词：operation=%s", request.operation.value)
        try:
            result = await self.plugin.ctx.llm.generate(
                prompt=prompt,
                model=str(config.llm_task_name),
                temperature=float(config.temperature),
                max_tokens=int(config.max_tokens),
                rpc_timeout_ms=int(config.llm_timeout_seconds) * 1000,
            )
            if not isinstance(result, dict):
                raise PromptPlanningError("提示词优化模型返回格式无效")
            if not bool(result.get("success")):
                raise PromptPlanningError(str(result.get("error") or "提示词优化模型返回失败"))
            response = str(result.get("response") or "").strip()
            if not response:
                raise PromptPlanningError("提示词优化模型返回了空内容")
            data = self._parse_json(response)
            optimized = self._apply_result(request, data, str(result.get("model") or ""))
            self.plugin.ctx.logger.info(
                "音频提示词优化完成：operation=%s model=%s",
                request.operation.value,
                optimized.optimizer_model or config.llm_task_name,
            )
            return optimized
        except Exception as exc:
            if str(config.failure_mode) == "raw_prompt":
                self.plugin.ctx.logger.warning("音频提示词优化失败，按配置使用原始提示词：%s", exc)
                return request
            if isinstance(exc, PromptPlanningError):
                raise
            raise PromptPlanningError(f"音频提示词优化失败：{exc}") from exc

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        normalized = response.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                normalized = "\n".join(lines[1:-1])
                if normalized.lstrip().startswith("json"):
                    normalized = normalized.lstrip()[4:].lstrip()
        try:
            data = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise PromptPlanningError("提示词优化模型未返回合法 JSON") from exc
        if not isinstance(data, dict):
            raise PromptPlanningError("提示词优化结果必须是 JSON 对象")
        return data

    def _apply_result(
        self,
        request: GenerationRequest,
        data: Dict[str, Any],
        optimizer_model: str,
    ) -> GenerationRequest:
        operation = str(data.get("operation") or "")
        if operation != request.operation.value:
            raise PromptPlanningError("提示词优化模型改变了任务类型")
        lyrics = str(data.get("lyrics") or "")
        if self.plugin.config.prompt_optimizer.preserve_user_lyrics and request.lyrics:
            lyrics = request.lyrics
        bpm_value = data.get("bpm")
        bpm = None if bpm_value is None or bpm_value == "" else int(bpm_value)
        sound_type_value = data.get("sound_type")
        optimized = replace(
            request,
            prompt=str(data.get("prompt") or request.prompt),
            title=str(data.get("title") or request.title),
            style=str(data.get("style") or request.style),
            negative_tags=str(data.get("negative_tags") or request.negative_tags),
            lyrics=lyrics,
            vocal_gender=str(data.get("vocal_gender") or request.vocal_gender),
            sound_type=str(sound_type_value or request.sound_type),
            bpm=bpm,
            key=str(data.get("key") or request.key),
            instrumental=request.operation == AudioOperation.INSTRUMENTAL or request.instrumental,
            optimizer_model=optimizer_model or str(self.plugin.config.prompt_optimizer.llm_task_name),
        )
        optimized.validate(
            max_prompt_chars=int(self.plugin.config.limits.max_prompt_chars),
            max_lyrics_chars=int(self.plugin.config.limits.max_lyrics_chars),
        )
        return optimized
