import anthropic
import json
import pandas as pd
from typing import Optional


SYSTEM_PROMPT = """あなたはゴールド（XAU/USD）の専門トレードアナリストです。
Discordから来た相場シグナルテキストを分析し、エントリーすべきか判断してください。

分析の際は以下を考慮してください：
- テキストから方向性（BUY/SELL/NEUTRAL）と価格水準を抽出
- 過去のシグナル履歴があれば、精度・パターンを参照
- リスクリワード比、トレンドの強さ、エントリータイミングを評価

必ず以下のJSON形式のみで回答してください（他のテキストは不要）：
{
  "direction": "BUY" | "SELL" | "NEUTRAL",
  "price_level": 価格数値 or null,
  "entry_decision": "ENTRY" | "WAIT" | "SKIP",
  "entry_timing": "エントリータイミングの説明（日本語）",
  "predicted_pips": 予想pips数値（下落予想の場合はマイナス、上昇はプラス）,
  "confidence": 0〜100の確信度,
  "reasoning": "判断理由（日本語、200字以内）",
  "risk_note": "リスク注意点（日本語、100字以内）"
}"""


def _build_history_context(history_df: Optional[pd.DataFrame]) -> str:
    if history_df is None or history_df.empty:
        return "（過去のシグナル履歴なし）"

    recent = history_df.tail(10)
    lines = ["【直近10件のシグナル履歴】"]
    for _, row in recent.iterrows():
        outcome = row.get("outcome", "未確認")
        lines.append(
            f"- {row.get('timestamp', '')} | {row.get('direction', '')} | "
            f"価格:{row.get('price_level', '')} | 判断:{row.get('entry_decision', '')} | "
            f"予想pips:{row.get('predicted_pips', '')} | 結果:{outcome}"
        )
    return "\n".join(lines)


def analyze_signal(
    api_key: str,
    discord_text: str,
    history_df: Optional[pd.DataFrame] = None,
    market_summary: Optional[str] = None,
) -> dict:
    client = anthropic.Anthropic(api_key=api_key)

    history_context = _build_history_context(history_df)
    market_context = market_summary or "（相場データなし）"

    user_message = f"""以下のDiscordシグナルを分析してください。

【Discordテキスト】
{discord_text}

{market_context}

{history_context}

上記を踏まえてJSONで回答してください。"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text.strip()

    # JSON部分を抽出
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    return result
