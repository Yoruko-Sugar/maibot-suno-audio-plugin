"""简体中文结构化音频提示词优化模板。"""

from typing import Any, Dict
import json


def build_prompt(operation: str, request_data: Dict[str, Any], preserve_user_lyrics: bool) -> str:
    """构造严格 JSON 输出的提示词。"""

    source = json.dumps(request_data, ensure_ascii=False, sort_keys=True)
    return f"""你是专业的 Suno 音乐与音效提示词工程师。请把用户要求整理为 API 可用的结构化参数。

硬性规则：
1. 只输出一个 JSON 对象，不要 Markdown、代码围栏、解释或注释。
2. operation 必须保持为 {operation}，不得改变任务类型。
3. 普通音乐和音效的 prompt 应优化为具体、紧凑的英文制作描述。
4. 保留用户明确指定的主题、语言、情绪、乐器、人声、节奏、调性与排除项。
5. 不得模仿具体在世艺人的声音，不得擅自加入现实人物或受版权保护角色。
6. bpm 只能是 1 到 300 的整数或 null；key 只能是 C、C#、Cm、C#m 等升号写法或空字符串。
7. vocal_gender 只能是 Male、Female 或空字符串；sound_type 只能是 one-shot、loop 或 null。
8. 不要凭空添加用户没有明确要求的 bpm、key 或人声性别。
9. preserve_user_lyrics={str(preserve_user_lyrics).lower()}；为 true 且输入含歌词时，lyrics 必须逐字保持不变。
10. 自定义歌曲的 prompt 不代替歌词，歌词放在 lyrics；style 描述音乐风格、乐器、制作、人声与氛围。

必须输出这些字段：
{{
  "operation": "{operation}",
  "prompt": "",
  "title": "",
  "style": "",
  "negative_tags": "",
  "instrumental": false,
  "vocal_gender": "",
  "sound_type": null,
  "bpm": null,
  "key": "",
  "lyrics": ""
}}

输入 JSON：
{source}
"""
