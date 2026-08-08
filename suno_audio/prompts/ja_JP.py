"""日本語の構造化オーディオプロンプト最適化テンプレート。"""

from typing import Any, Dict
import json


def build_prompt(operation: str, request_data: Dict[str, Any], preserve_user_lyrics: bool) -> str:
    """厳密な JSON 出力プロンプトを構築する。"""

    source = json.dumps(request_data, ensure_ascii=False, sort_keys=True)
    return f"""あなたは Suno の音楽・効果音プロンプトエンジニアです。ユーザー要件を API 用の構造化パラメータに変換してください。

必須ルール：
1. Markdown、コードフェンス、説明、コメントなしで JSON オブジェクトを一つだけ出力する。
2. operation は {operation} のままにし、タスク種別を変更しない。
3. 通常の音楽・効果音 prompt は具体的で簡潔な英語の制作記述に最適化する。
4. テーマ、言語、感情、楽器、声、テンポ、キー、除外条件を保持する。
5. 特定の存命アーティストの声を模倣せず、実在人物や著作権キャラクターを勝手に追加しない。
6. bpm は 1〜300 の整数または null。key は C、C#、Cm、C#m のようなシャープ表記、または空文字列。
7. vocal_gender は Male、Female、空文字列。sound_type は one-shot、loop、null のいずれか。
8. ユーザーが明示していない bpm、key、人声性別を追加しない。
9. preserve_user_lyrics={str(preserve_user_lyrics).lower()}。true で歌詞がある場合、lyrics を一字一句保持する。
10. カスタム曲では歌詞を lyrics に入れ、style はジャンル、楽器、制作、声、雰囲気を記述する。

以下の全フィールドを出力する：
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

入力 JSON：
{source}
"""
