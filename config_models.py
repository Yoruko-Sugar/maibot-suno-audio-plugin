"""Suno 音频工坊配置模型。"""

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "music"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用 Suno 音频工坊。",
        json_schema_extra={"label": "启用插件", "order": 1},
    )
    config_version: str = Field(
        default="0.1.1",
        description="配置文件版本。",
        json_schema_extra={"label": "配置版本", "disabled": True, "order": 2},
    )
    allow_private_chat: bool = Field(
        default=True,
        description="是否允许在私聊中生成音频。",
        json_schema_extra={"label": "允许私聊", "order": 3},
    )
    allow_group_chat: bool = Field(
        default=True,
        description="是否允许在群聊中生成音频。",
        json_schema_extra={"label": "允许群聊", "order": 4},
    )


class ApiMartConfig(PluginConfigBase):
    """API Mart 连接与轮询配置。"""

    __ui_label__: ClassVar[str] = "API Mart"
    __ui_icon__: ClassVar[str] = "radio"
    __ui_order__: ClassVar[int] = 1

    base_url: str = Field(
        default="https://api.apimart.ai",
        description="API Mart API 根地址。",
        json_schema_extra={"label": "API 地址", "order": 1},
    )
    api_key: str = Field(
        default="",
        description="API Mart Bearer Token。",
        json_schema_extra={
            "label": "API Key",
            "input_type": "password",
            "placeholder": "sk-...",
            "order": 2,
        },
    )
    model: str = Field(
        default="suno",
        description="供应商模型字段。",
        json_schema_extra={"label": "模型标识", "disabled": True, "order": 3},
    )
    default_version: Literal["v3.5", "v4", "v4.5", "v4.5+", "v4.5-all", "v5", "v5.5"] = Field(
        default="v5.5",
        description="默认音乐生成版本。",
        json_schema_extra={"label": "默认版本", "ui_type": "select", "order": 4},
    )
    lyrics_model: Literal["default", "classic", "remi"] = Field(
        default="default",
        description="歌词生成模型；default 表示由供应商选择。",
        json_schema_extra={"label": "歌词模型", "ui_type": "select", "order": 5},
    )
    connect_timeout_seconds: int = Field(
        default=10,
        ge=3,
        le=60,
        description="HTTP 建连超时。",
        json_schema_extra={"label": "建连超时（秒）", "order": 10},
    )
    request_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="单次提交或查询请求超时。",
        json_schema_extra={"label": "请求超时（秒）", "order": 11},
    )
    generation_timeout_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="单个供应商任务最长自动跟踪时间。",
        json_schema_extra={"label": "生成跟踪超时（秒）", "order": 12},
    )
    poll_interval_seconds: int = Field(
        default=4,
        ge=3,
        le=10,
        description="正常任务轮询间隔。",
        json_schema_extra={"label": "轮询间隔（秒）", "order": 13},
    )
    max_poll_errors: int = Field(
        default=5,
        ge=1,
        le=20,
        description="连续查询失败后停止自动跟踪的阈值。",
        json_schema_extra={"label": "查询失败阈值", "order": 14},
    )


class PromptOptimizerConfig(PluginConfigBase):
    """自然语言 Tool 与提示词优化配置。"""

    __ui_label__: ClassVar[str] = "语义与提示词"
    __ui_icon__: ClassVar[str] = "brain"
    __ui_order__: ClassVar[int] = 2

    semantic_tool_enabled: bool = Field(
        default=True,
        description="是否注册自然语言音频生成 Tool。",
        json_schema_extra={"label": "启用自然语言触发", "order": 1},
    )
    prompt_optimizer_enabled: bool = Field(
        default=True,
        description="是否使用 MaiBot 模型把自然语言优化为结构化 Suno 参数。",
        json_schema_extra={"label": "启用提示词优化", "order": 2},
    )
    llm_task_name: str = Field(
        default="utils",
        description="提示词优化使用的 MaiBot 模型任务。",
        json_schema_extra={
            "label": "提示词优化模型",
            "hint": "选项来自当前 MaiBot 模型任务配置。",
            "order": 3,
        },
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="结构化参数提取温度。",
        json_schema_extra={"label": "优化温度", "step": 0.1, "order": 4},
    )
    max_tokens: int = Field(
        default=4096,
        ge=512,
        le=16384,
        description="提示词优化最大 Token。",
        json_schema_extra={"label": "最大 Token", "order": 5},
    )
    llm_timeout_seconds: int = Field(
        default=90,
        ge=30,
        le=600,
        description="提示词优化模型 RPC 的最大等待时间。",
        json_schema_extra={"label": "模型超时（秒）", "order": 6},
    )
    failure_mode: Literal["abort", "raw_prompt"] = Field(
        default="abort",
        description="优化失败时中止，或显式使用原始提示词。",
        json_schema_extra={"label": "优化失败处理", "ui_type": "select", "order": 7},
    )
    optimize_command_prompts: bool = Field(
        default=True,
        description="指令入口是否也使用提示词优化器。",
        json_schema_extra={"label": "优化指令提示词", "order": 8},
    )
    preserve_user_lyrics: bool = Field(
        default=True,
        description="默认逐字保留用户提供的完整歌词。",
        json_schema_extra={"label": "保留用户歌词", "order": 9},
    )


class GenerationConfig(PluginConfigBase):
    """默认生成参数。"""

    __ui_label__: ClassVar[str] = "生成参数"
    __ui_icon__: ClassVar[str] = "sliders"
    __ui_order__: ClassVar[int] = 3

    vocal_gender: str = Field(
        default="auto",
        description="默认人声性别；auto 表示不指定。旧配置中的空字符串仍兼容读取。",
        json_schema_extra={
            "label": "默认人声性别",
            "ui_type": "select",
            "choices": ["auto", "Male", "Female"],
            "order": 1,
        },
    )
    style_weight: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="默认风格权重。",
        json_schema_extra={"label": "风格权重", "step": 0.05, "order": 2},
    )
    weirdness_constraint: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="默认创意度。",
        json_schema_extra={"label": "创意度", "step": 0.05, "order": 3},
    )
    audio_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="默认音频权重。",
        json_schema_extra={"label": "音频权重", "step": 0.05, "order": 4},
    )
    auto_lyrics: bool = Field(
        default=False,
        description="自定义歌曲是否允许供应商二次创作歌词。",
        json_schema_extra={"label": "自动改写歌词", "order": 5},
    )
    negative_tags: str = Field(
        default="",
        description="默认排除的风格标签。",
        json_schema_extra={"label": "默认负向标签", "order": 6},
    )
    sound_version: Literal["v5", "v5.5"] = Field(
        default="v5.5",
        description="默认音效版本。",
        json_schema_extra={"label": "音效版本", "ui_type": "select", "order": 7},
    )


class PermissionConfig(PluginConfigBase):
    """权限配置。"""

    __ui_label__: ClassVar[str] = "权限"
    __ui_icon__: ClassVar[str] = "shield"
    __ui_order__: ClassVar[int] = 4

    access_mode: Literal["public", "admin_only", "whitelist"] = Field(
        default="public",
        description="插件访问模式。",
        json_schema_extra={"label": "访问模式", "ui_type": "select", "order": 1},
    )
    global_admin_ids: List[str] = Field(
        default_factory=list,
        description="可执行管理操作且不受普通用户额度限制的用户 ID。",
        json_schema_extra={"label": "全局管理员", "item_type": "text", "order": 2},
    )
    user_whitelist: List[str] = Field(
        default_factory=list,
        description="白名单模式下允许使用的用户 ID。",
        json_schema_extra={"label": "用户白名单", "item_type": "text", "order": 3},
    )
    group_whitelist: List[str] = Field(
        default_factory=list,
        description="非空时只允许这些群使用。",
        json_schema_extra={"label": "群白名单", "item_type": "text", "order": 4},
    )


class LimitConfig(PluginConfigBase):
    """并发、冷却和额度配置。"""

    __ui_label__: ClassVar[str] = "限制"
    __ui_icon__: ClassVar[str] = "gauge"
    __ui_order__: ClassVar[int] = 5

    max_concurrent_jobs: int = Field(
        default=2,
        ge=1,
        le=10,
        description="同时跟踪的最大任务数。",
        json_schema_extra={"label": "全局并发任务", "order": 1},
    )
    max_active_jobs_per_stream: int = Field(
        default=1,
        ge=1,
        le=5,
        description="单聊天流活动任务上限。",
        json_schema_extra={"label": "单聊天流任务", "order": 2},
    )
    user_cooldown_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="同一用户提交冷却时间。",
        json_schema_extra={"label": "用户冷却（秒）", "order": 3},
    )
    daily_jobs_per_user: int = Field(
        default=10,
        ge=0,
        le=1000,
        description="每用户每日任务上限，0 表示不限制。",
        json_schema_extra={"label": "每日任务额度", "order": 4},
    )
    max_prompt_chars: int = Field(
        default=3000,
        ge=100,
        le=20000,
        description="普通提示词最大字符数。",
        json_schema_extra={"label": "提示词长度", "order": 5},
    )
    max_lyrics_chars: int = Field(
        default=12000,
        ge=1000,
        le=50000,
        description="完整歌词最大字符数。",
        json_schema_extra={"label": "歌词长度", "order": 6},
    )


class DeliveryConfig(PluginConfigBase):
    """结果发送配置。"""

    __ui_label__: ClassVar[str] = "发送"
    __ui_icon__: ClassVar[str] = "send"
    __ui_order__: ClassVar[int] = 6

    music_mode: Literal["file_url", "file_base64"] = Field(
        default="file_url",
        description="完整音乐发送模式。",
        json_schema_extra={"label": "音乐发送模式", "ui_type": "select", "order": 1},
    )
    sound_mode: Literal["file_url", "file_base64", "voice"] = Field(
        default="file_url",
        description="音效发送模式。",
        json_schema_extra={"label": "音效发送模式", "ui_type": "select", "order": 2},
    )
    auto_send_all_tracks: bool = Field(
        default=True,
        description="是否自动发送供应商返回的全部音轨。",
        json_schema_extra={"label": "发送全部音轨", "order": 3},
    )
    send_cover: bool = Field(
        default=True,
        description="是否发送结果封面。",
        json_schema_extra={"label": "发送封面", "order": 4},
    )
    send_lyrics: bool = Field(
        default=True,
        description="是否发送歌词。",
        json_schema_extra={"label": "发送歌词", "order": 5},
    )
    base64_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
        description="Base64 文件或语音发送允许的最大字节数。",
        json_schema_extra={"label": "Base64 大小上限", "order": 6},
    )
    download_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="下载封面或 Base64 音频的超时时间。",
        json_schema_extra={"label": "下载超时（秒）", "order": 7},
    )
    forward_segment_chars: int = Field(
        default=1800,
        ge=200,
        le=5000,
        description="长歌词合并转发的单节点长度。",
        json_schema_extra={"label": "歌词分段长度", "order": 8},
    )


class SunoAudioPluginConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    apimart: ApiMartConfig = Field(default_factory=ApiMartConfig)
    prompt_optimizer: PromptOptimizerConfig = Field(default_factory=PromptOptimizerConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    limits: LimitConfig = Field(default_factory=LimitConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
