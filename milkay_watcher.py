"""
MILKAYチャンネル監視 → AI解析直結版
- milkay / new-milkay: 15分判定ウィンドウ
- milkay5: 5分判定ウィンドウ
- test-hiroshi: 5分判定ウィンドウ
"""
import time
import json
import os
import uuid
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

TOKEN = os.getenv("DISCORD_USER_TOKEN", "")
HEADERS = {"Authorization": TOKEN, "User-Agent": "Mozilla/5.0"}

CHANNELS = {
    "milkay":        "1254683730086465677",
    "new-milkay":    "1334524310416654437",
    "milkay5":       "1345694143292375050",
    "test-hiroshi":  "1376124655416508512",
}

WINDOW = {
    "milkay":        900,
    "new-milkay":    900,
    "milkay5":       300,
    "test-hiroshi":  300,
}

SEEN_FILE   = Path(__file__).parent / "milkay_seen.json"
RESULT_FILE = Path(__file__).parent / "latest_result.json"


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen))


def fetch_messages(channel_id, limit=3):
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit={limit}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[ERROR] fetch失敗: {e}")
        return []


def analyze_and_save(ch_name, content, author):
    try:
        from discord_bot import analyze_signal
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        print(f"[{ch_name}] 🔍 AI解析開始: {content[:50]}")
        result = analyze_signal(
            discord_text=content,
            source_type=f"MILKAY/{ch_name}",
            api_key=api_key
        )
        win_secs = WINDOW.get(ch_name, 900)
        until_dt = datetime.now() + timedelta(seconds=win_secs)
        data = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": f"MILKAY/{ch_name}",
            "author": author,
            "signal_text": content,
            "monitoring_until": until_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "result": result
        }
        RESULT_FILE.write_text(json.dumps(data, ensure_ascii=False))
        try:
            creds_raw = os.getenv("GDRIVE_CREDENTIALS", "")
            sheet_id  = os.getenv("SHEET_ID", "")
            if creds_raw and sheet_id:
                from utils.drive import _client_from_dict, save_latest_result, append_signal_gc
                gc = _client_from_dict(json.loads(creds_raw))
                save_latest_result(gc, sheet_id, data)
                append_signal_gc(gc, sheet_id, {
                    "timestamp": data["timestamp"],
                    "channel":   data["channel"],
                    "signal_text": content,
                    "decision":  result.get("entry_decision", ""),
                    "direction": result.get("direction", ""),
                    "confidence": result.get("confidence", 0),
                    "predicted_pips": result.get("predicted_pips", 0),
                    "passed_filter": result.get("entry_decision") == "ENTRY",
                })
                print(f"[{ch_name}] ☁️ Google Sheets更新完了")
        except Exception as e:
            print(f"[{ch_name}] ⚠️ Sheets書き込み失敗: {e}")
        decision  = result.get("entry_decision", "?")
        direction = result.get("direction", "")
        pips      = result.get("predicted_pips", 0) or 0
        big       = "🚨 大型!" if abs(pips) >= 400 else ""
        win_label = f"{win_secs//60}分"
        print(f"[{ch_name}] ✅ 判定: {decision} {direction} {pips:+.0f}pips {big} → {win_label}ウィンドウ開始")
    except Exception as e:
        print(f"[{ch_name}] ❌ 解析エラー: {e}")


def main():
    print("=" * 50)
    print("MILKAYウォッチャー起動")
    print("  milkay / new-milkay : 15分判定")
    print("  milkay5             : 5分判定")
    print("  test-hiroshi        : 5分判定")
    print("=" * 50)
    seen = load_seen()
    print("初回スキャン（既存メッセージをスキップ）...")
    for ch_name, ch_id in CHANNELS.items():
        msgs = fetch_messages(ch_id, limit=5)
        if ch_name not in seen:
            seen[ch_name] = []
        for msg in msgs:
            seen[ch_name].append(msg["id"])
        time.sleep(1)
    save_seen(seen)
    print("✅ 監視開始！新着メッセージを待っています...\n")
    while True:
        for ch_name, ch_id in CHANNELS.items():
            msgs = fetch_messages(ch_id, limit=3)
            for msg in reversed(msgs):
                msg_id  = msg["id"]
                content = msg.get("content", "").strip()
                author  = msg.get("author", {}).get("username", "")
                if not content:
                    continue
                if msg_id in seen.get(ch_name, []):
                    continue
                analyze_and_save(ch_name, content, author)
                if ch_name not in seen:
                    seen[ch_name] = []
                seen[ch_name].append(msg_id)
                seen[ch_name] = seen[ch_name][-50:]
            save_seen(seen)
            time.sleep(2)
        print(f"[{time.strftime('%H:%M:%S')}] 監視中... (30秒後に再チェック)")
        time.sleep(30)


if __name__ == "__main__":
    main()
