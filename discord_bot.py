"""
Discord Bot - シグナル検出 → AI解析 → latest_result.json に保存
起動: python3 discord_bot.py
"""
import discord
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

RESULT_FILE   = Path(__file__).parent / "latest_result.json"
HISTORY_FILE  = Path(__file__).parent / "signal_history.json"
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
_ch_env = os.getenv("DISCORD_CHANNEL_ID", "")
WATCH_CHANNEL_IDS = [int(x.strip()) for x in _ch_env.split(",") if x.strip().isdigit()]

GOLD_KEYWORDS = [
    "gold", "ゴールド", "xau", "金", "buy", "sell",
    "short", "long", "ショート", "ロング", "エントリー",
    "sl:", "tp:", "pips", "milkay", "n-milkay",
    "下落", "上昇", "下降",
]


def _is_gold_signal(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in GOLD_KEYWORDS)


def _detect_source_type(text: str) -> str:
    lower = text.lower()
    if "milkay5" in lower:
        return "MILKAY5"
    elif "n-milkay" in lower:
        return "N-MILKAY"
    elif "milkay" in lower:
        return "MILKAY"
    return "OTHER"


def _analyze(discord_text: str, source_type: str = "OTHER") -> dict:
    """AI解析を実行して結果を返す"""
    import anthropic
    from utils.market_data import build_market_summary

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY が未設定です"}

    # ソースに応じた時間足指定
    if source_type == "MILKAY5":
        timeframe_instruction = "【分析時間足】15分足 → 5分足 → 1分足の順で確認して判断してください。"
        hold_seconds = 300
    else:
        timeframe_instruction = "【分析時間足】15分足 → 1分足の順で確認して判断してください。"
        hold_seconds = 900

    # 相場データ取得
    try:
        market = build_market_summary()
    except Exception as e:
        market = f"相場データ取得失敗: {e}"

    system = """あなたはゴールド（XAU/USD）取引の専門チームです。
以下の3つの役割を演じて、シグナルを分析・議論してください：

【アナリストA: 強気派】エントリー機会を積極的に探し、上昇・下落の勢いを重視する
【アナリストB: 慎重派】リスクと失敗パターンを重視し、見送り理由を探す
【アナリストC: リスク管理者】損益比率・タイミング・資金管理を最優先する

3人が議論した上でコンセンサスを出してください。
必ず以下のJSON形式のみで回答：
{
  "analyst_a": {
    "stance": "BUY" | "SELL" | "NEUTRAL",
    "reasoning": "根拠（日本語60字以内）",
    "confidence": 0〜100
  },
  "analyst_b": {
    "stance": "BUY" | "SELL" | "NEUTRAL",
    "reasoning": "根拠（日本語60字以内）",
    "confidence": 0〜100
  },
  "analyst_c": {
    "stance": "ENTRY" | "WAIT" | "SKIP",
    "reasoning": "根拠（日本語60字以内）",
    "confidence": 0〜100
  },
  "consensus": {
    "direction": "BUY" | "SELL" | "NEUTRAL",
    "entry_decision": "ENTRY" | "WAIT" | "SKIP",
    "entry_timing": "エントリータイミング（日本語）",
    "predicted_pips": 予想pips数値,
    "confidence": 0〜100,
    "reasoning": "コンセンサス根拠（日本語200字以内）",
    "risk_note": "リスク注意点（日本語100字以内）",
    "price_level": 価格数値 or null,
    "vote": "例: 2-1でエントリー賛成"
  }
}"""

    # LINE学習データを読み込む
    learn_context = ""
    learn_file = Path(__file__).parent / "learning_data.json"
    if learn_file.exists():
        with open(learn_file, "r", encoding="utf-8") as f:
            ldata = json.load(f)
        if ldata:
            recent = ldata[-20:]
            wins = sum(1 for d in recent if d.get("outcome") == "WIN")
            losses = sum(1 for d in recent if d.get("outcome") == "LOSS")
            learn_context = f"\n【LINEグループ実績（直近{len(recent)}件）】勝:{wins} 負:{losses}\n"
            for d in recent[-5:]:
                learn_context += (f"- {d.get('direction','')} {d.get('pips','')}pips "
                                  f"{d.get('outcome','')} ({d.get('timestamp','')[:10]})\n")

    user_msg = f"""【シグナルソース: {source_type}】
{timeframe_instruction}

【Discordシグナル】
{discord_text}

{market}
{learn_context}
JSONで回答してください。"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = msg.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    c = result.get("consensus", {})
    result["entry_decision"] = c.get("entry_decision", "WAIT")
    result["direction"]      = c.get("direction", "NEUTRAL")
    result["price_level"]    = c.get("price_level")
    result["entry_timing"]   = c.get("entry_timing", "")
    result["predicted_pips"] = c.get("predicted_pips", 0)
    result["confidence"]     = c.get("confidence", 0)
    result["reasoning"]      = c.get("reasoning", "")
    result["risk_note"]      = c.get("risk_note", "")
    result["hold_seconds"]   = hold_seconds
    result["source_type"]    = source_type
    return result


CONFIDENCE_THRESHOLD = 80  # 80%以上のみ保存


def _append_history(entry: dict):
    """全シグナルをローカルファイル＋Google Sheetsに追記"""
    # ローカル保存
    history = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    history.append(entry)
    history = history[-500:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # Google Sheets に保存
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        if sheet_id:
            try:
                from utils.drive import _client_from_dict
                import json as _json
                sa_dict = _json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
                gc = _client_from_dict(sa_dict)
                from utils.drive import append_signal_gc
                append_signal_gc(gc, sheet_id, entry)
            except Exception as e:
                print(f"[Bot] Sheets保存エラー: {e}")


def _save_result(signal_text: str, author: str, channel: str, result: dict):
    decision   = result.get("entry_decision", "")
    confidence = result.get("confidence", 0)
    direction  = result.get("direction", "")

    passed = decision == "ENTRY" and confidence >= CONFIDENCE_THRESHOLD

    # 履歴に全件記録（reasoning・risk_noteも保存）
    history_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signal_text": signal_text[:120],
        "channel": channel,
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "predicted_pips": result.get("predicted_pips", 0),
        "passed_filter": passed,
        "reasoning": result.get("reasoning", ""),
        "risk_note": result.get("risk_note", ""),
    }
    _append_history(history_entry)

    # 全シグナルをメイン表示用に保存（判定・理由を常に表示するため）
    from datetime import timedelta
    hold_secs = result.get("hold_seconds", 300)
    monitoring_until = (datetime.now() + timedelta(seconds=hold_secs)).strftime("%Y-%m-%d %H:%M:%S")

    data = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signal_text": signal_text,
        "author": author,
        "channel": channel,
        "result": result,
        "passed_filter": passed,
        "monitoring_until": monitoring_until,
    }
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[Bot] {'✅ ENTRY' if passed else '⚫ ' + decision}: {direction} / 確信度{confidence}%")

   # Google Sheets の「最新シグナル」にも保存（Streamlit Cloud 用）
        sheet_id_ = os.getenv("GOOGLE_SHEET_ID", "")
        if sheet_id_:
            try:
            from utils.drive import _client_from_dict, save_latest_result
            import json as _json
            sa_dict = _json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
            gc = _client_from_dict(sa_dict)
            save_latest_result(gc, sheet_id_, data)
            except Exception as e:
 print(f"[Bot] Sheets最新シグナル保存エラー: {e}")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"[Bot] ログイン完了: {client.user}")
    for cid in WATCH_CHANNEL_IDS:
        ch = client.get_channel(cid)
        print(f"[Bot] 監視: {ch.name if ch else f'ID:{cid}'}")


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if WATCH_CHANNEL_IDS and message.channel.id not in WATCH_CHANNEL_IDS:
        return

    text = message.content.strip()
    if not text or not _is_gold_signal(text):
        return

    source_type = _detect_source_type(text)
    print(f"[Bot] シグナル検出 [{source_type}]: {text[:80]}")
    print("[Bot] AI解析開始...")

    try:
        result = _analyze(text, source_type)
        _save_result(text, str(message.author), str(message.channel), result)
    except Exception as e:
        print(f"[Bot] 解析エラー: {e}")
        _save_result(text, str(message.author), str(message.channel), {"error": str(e)})


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("[ERROR] .env に DISCORD_BOT_TOKEN を設定してください")
    else:
        client.run(DISCORD_TOKEN)
