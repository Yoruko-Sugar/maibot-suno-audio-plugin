"""Suno 音频工坊领域异常。"""

from typing import Optional


class SunoAudioError(Exception):
    """插件可识别的基础异常。"""


class ApiMartError(SunoAudioError):
    """API Mart 调用异常。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None, error_type: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class ApiMartInvalidRequestError(ApiMartError):
    """供应商拒绝请求参数。"""


class ApiMartAuthenticationError(ApiMartError):
    """API Key 无效。"""


class ApiMartPaymentRequiredError(ApiMartError):
    """供应商账户余额不足。"""


class ApiMartPermissionError(ApiMartError):
    """供应商拒绝访问。"""


class ApiMartRateLimitError(ApiMartError):
    """供应商限流。"""

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message, status_code=429, error_type="rate_limit_error")
        self.retry_after = retry_after


class ApiMartServerError(ApiMartError):
    """供应商服务器或网关异常。"""


class ApiMartProtocolError(ApiMartError):
    """供应商响应不符合已知协议。"""


class ApiMartSubmitUnknownError(ApiMartError):
    """提交连接中断，无法判断供应商是否创建了任务。"""


class ApiMartTaskFailedError(ApiMartError):
    """供应商任务明确失败。"""


class PromptPlanningError(SunoAudioError):
    """提示词优化或结构化参数解析失败。"""


class AccessDeniedError(SunoAudioError):
    """插件访问权限不足。"""


class UsageLimitError(SunoAudioError):
    """冷却、每日额度或并发限制。"""


class JobNotFoundError(SunoAudioError):
    """找不到本地任务。"""
