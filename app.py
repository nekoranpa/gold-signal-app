import os
import json
from pathlib import Path
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from utils.market_data import get_gold_price, get_gold_intraday

load_dotenv(Path(__file__).parent / ".env", override=True)

# Streamlit Cloud の Secrets にも対応
def _get_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.getenv(key, default)

RESULT_FILE  = Path(__file__).parent / "latest_result.json"
HISTORY_FILE = Path(__file__).parent / "signal_history.json"


@st.cache_resource
def _get_sheets_client():
    """Google Sheets クライアント（Streamlit Secrets → ローカルファイルの順で取得）"""
    try:
        from utils.drive import _client_from_dict
        sa_dict = dict(st.secrets["gcp_service_account"])
        return _client_from_dict(sa_dict)
    except Exception:
        pass
    try:
        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
        sa_path = Path(__file__).parent / sa_json
        if sa_path.exists():
            from utils.drive import _client
            return _client(str(sa_path))
    except Exception:
        pass
    return None


@st.cache_data(ttl=5)
def _load_result_from_sheets(sheet_id: str):
    gc = _get_sheets_client()
    if gc is None:
        return None
    from utils.drive import load_latest_result
    return load_latest_result(gc, sheet_id)


@st.cache_data(ttl=5)
def _load_history_from_sheets(sheet_id: str):
    gc = _get_sheets_client()
    if gc is None:
        return []
    from utils.drive import load_signal_history
    return load_signal_history(gc, sheet_id)

st.set_page_config(
    page_title="Gold Signal",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 自動リフレッシュ:
#  - ENTRY判定表示中は10秒保持してからリロード
#  - 入力中はリロードしない
#  - 待機中は5秒ごとにリロード
st.markdown("""
<script>
(function() {
  var HOLD_SECS  = 10;  // ENTRY表示の保持秒数
  var POLL_SECS  = 5;   // 待機中のポーリング間隔

  var resultBox  = null;
  var shownAt    = null;

  function isEntry() {
    // ページ内にENTRYバッジが存在するか確認
    return document.body.innerText.includes('エントリー');
  }

  function isTyping() {
    var a = document.activeElement;
    return a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT');
  }

  function tick() {
    if (isTyping()) {
      setTimeout(tick, 2000);
      return;
    }
    if (isEntry()) {
      if (!shownAt) shownAt = Date.now();
      var elapsed = (Date.now() - shownAt) / 1000;
      if (elapsed >= HOLD_SECS) {
        location.reload();
      } else {
        setTimeout(tick, 1000);
      }
    } else {
      shownAt = null;
      setTimeout(function(){ location.reload(); }, POLL_SECS * 1000);
    }
  }

  setTimeout(tick, POLL_SECS * 1000);
})();
</script>
""", unsafe_allow_html=True)

# APIキーは .env または Streamlit Secrets から取得（UIには表示しない）
ANTHROPIC_API_KEY = _get_secret("ANTHROPIC_API_KEY")


# ---- 結果ファイル読み込み ----
def _load_result():
    if RESULT_FILE.exists():
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # Streamlit Cloud: Google Sheets から読み込み
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    if sheet_id:
        return _load_result_from_sheets(sheet_id)
    return None


def _load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # Streamlit Cloud: Google Sheets から読み込み
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    if sheet_id:
        return _load_history_from_sheets(sheet_id)
    return []


def _append_history(signal_text: str, result: dict, source: str = "手動"):
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signal_text": signal_text[:120],
        "channel": source,
        "decision": result.get("entry_decision", ""),
        "direction": result.get("direction", ""),
        "confidence": result.get("confidence", 0),
        "predicted_pips": result.get("predicted_pips", 0),
        "passed_filter": True,
    }
    # ローカルファイルに保存
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        else:
            history = []
        history.append(entry)
        history = history[-500:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    # Google Sheets にも保存
    sheet_id = _get_secret("GOOGLE_SHEET_ID")
    if sheet_id:
        try:
            gc = _get_sheets_client()
            if gc:
                from utils.drive import append_signal_gc
                append_signal_gc(gc, sheet_id, entry)
        except Exception:
            pass


# ---- 手動解析 ----
def _load_learning_context() -> str:
    learn_file = Path(__file__).parent / "learning_data.json"
    if not learn_file.exists():
        return ""
    with open(learn_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        return ""
    recent = data[-20:]  # 直近20件
    wins   = sum(1 for d in recent if d.get("outcome") == "WIN")
    losses = sum(1 for d in recent if d.get("outcome") == "LOSS")
    lines  = [f"【LINEグループ実績（直近{len(recent)}件）】勝:{wins} 負:{losses}"]
    for d in recent[-5:]:
        lines.append(f"- {d.get('direction','')} {d.get('pips','')}pips "
                     f"{d.get('outcome','')} ({d.get('timestamp','')[:10]})")
    return "\n".join(lines)


def _run_manual(text: str) -> dict:
    import anthropic

    api_key = ANTHROPIC_API_KEY
    from utils.market_data import build_market_summary
    market = build_market_summary()

    system = """あなたはゴールド（XAU/USD）の専門トレードアナリストです。
必ず以下のJSON形式のみで回答してください：
{
  "direction": "BUY" | "SELL" | "NEUTRAL",
  "price_level": 価格数値 or null,
  "entry_decision": "ENTRY" | "WAIT" | "SKIP",
  "entry_timing": "エントリータイミングの説明（日本語）",
  "predicted_pips": 予想pips数値（下落予想はマイナス、上昇はプラス）,
  "confidence": 0〜100の確信度,
  "reasoning": "判断理由（日本語、200字以内）",
  "risk_note": "リスク注意点（日本語、100字以内）"
}"""
    learning = _load_learning_context()
    user_msg = f"【シグナル】\n{text}\n\n{market}\n\n{learning}\n\nJSONで回答してください。"

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
    return json.loads(raw)


# ================================================================
# UI
# ================================================================

# ---- Gold現在価格 ----
try:
    p = get_gold_price()
    color = "#00ff88" if p["change"] >= 0 else "#ff4444"
    st.markdown(f"""
    <div style="text-align:center; padding:10px 0 4px; font-family:sans-serif;">
      <span style="font-size:26px; font-weight:700; color:#f5c842;">金価格</span>
      <span style="font-size:34px; font-weight:900; color:#fff; margin:0 14px;">${p['price']:,.2f}</span>
      <span style="font-size:20px; color:{color};">{p['change']:+,.2f} ({p['change_pct']})</span>
      <span style="font-size:13px; color:#666; margin-left:10px;">GC=F / {datetime.now().strftime('%H:%M')}</span>
    </div>""", unsafe_allow_html=True)
except Exception:
    st.markdown("<div style='text-align:center;color:#666;padding:8px'>価格取得中...</div>",
                unsafe_allow_html=True)

# ---- ミニチャート（1時間足） ----
try:
    import pandas as pd
    intraday = get_gold_intraday()
    candles = intraday.get("candles", [])
    if candles:
        df = pd.DataFrame(reversed(candles))
        trend_color = "#00ff88" if intraday["trend"] == "上昇" else "#ff4444"
        st.markdown(
            f'<div style="display:flex; gap:24px; justify-content:center; '
            f'font-family:sans-serif; font-size:13px; color:#888; margin-bottom:4px;">'
            f'<span>直近トレンド: <b style="color:{trend_color}">{intraday["trend"]}</b></span>'
            f'<span>高値: <b style="color:#fff">${intraday["recent_high"]:,.2f}</b></span>'
            f'<span>安値: <b style="color:#fff">${intraday["recent_low"]:,.2f}</b></span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.line_chart(df.set_index("time")["close"], height=120, use_container_width=True)
except Exception:
    pass

st.divider()

# ---- メイン判定表示 ----
data = _load_result()

if data and "result" in data:
    r = data["result"]

    if "error" in r:
        st.error(f"解析エラー: {r['error']}")
    else:
        decision  = r.get("entry_decision", "WAIT")
        direction = r.get("direction", "")
        pips      = r.get("predicted_pips", 0) or 0
        conf      = r.get("confidence", 0)
        timing    = r.get("entry_timing", "")
        reasoning = r.get("reasoning", "")
        risk      = r.get("risk_note", "")
        price_lv  = r.get("price_level")

        if decision == "ENTRY" and direction == "SELL":
            bg, fg, emoji, label = "#1a0808", "#ff3333", "🔴", "SHORT エントリー"
        elif decision == "ENTRY" and direction == "BUY":
            bg, fg, emoji, label = "#081a08", "#00dd55", "🟢", "LONG エントリー"
        else:
            bg, fg, emoji, label = "#111118", "#777777", "⚫", "スルー / 様子見"

        # ---- BIG判定 ----
        st.markdown(f"""
        <div style="background:{bg}; border:3px solid {fg}; border-radius:24px;
                    padding:52px 32px; text-align:center; margin-bottom:20px;">
          <div style="font-size:88px; line-height:1;">{emoji}</div>
          <div style="font-size:72px; font-weight:900; color:{fg};
                      letter-spacing:4px; margin-top:10px;">{label}</div>
          <div style="font-size:24px; color:#bbb; margin-top:14px;">
            確信度&nbsp;<span style="color:#fff;font-weight:700;">{conf}%</span>
            &nbsp;&nbsp;｜&nbsp;&nbsp;
            予想&nbsp;<span style="color:#f5c842;font-weight:700;">{pips:+.0f} pips</span>
            {"&nbsp;&nbsp;｜&nbsp;&nbsp;価格水準&nbsp;<span style='color:#fff;font-weight:700;'>$" + f"{price_lv:,.0f}" + "</span>" if price_lv else ""}
          </div>
        </div>""", unsafe_allow_html=True)

        # ---- タイミング・根拠 ----
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"""
            <div style="background:#161622; border-radius:12px; padding:20px; height:100%;">
              <div style="color:#888; font-size:13px; margin-bottom:6px;">⏱ エントリータイミング</div>
              <div style="color:#fff; font-size:16px; line-height:1.6;">{timing}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div style="background:#161622; border-radius:12px; padding:20px; height:100%;">
              <div style="color:#888; font-size:13px; margin-bottom:6px;">🧠 判断根拠</div>
              <div style="color:#fff; font-size:15px; line-height:1.6;">{reasoning}</div>
            </div>""", unsafe_allow_html=True)

        if risk:
            st.markdown(f"""
            <div style="background:#1a1100; border:1px solid #ff9900; border-radius:10px;
                        padding:12px 20px; margin-top:14px; color:#ffbb44;">
              ⚠️ {risk}
            </div>""", unsafe_allow_html=True)

        st.markdown(f"<div style='color:#555; font-size:12px; margin-top:12px; text-align:right;'>"
                    f"シグナル受信: {data.get('timestamp','')} ／ "
                    f"{data.get('channel','')} ／ {data.get('author','')}</div>",
                    unsafe_allow_html=True)

else:
    # 待機画面
    st.markdown("""
    <div style="text-align:center; padding:80px 0;">
      <div style="font-size:80px;">🪙</div>
      <div style="font-size:38px; font-weight:700; color:#fff; margin-top:16px;">
        Gold Signal Analyzer
      </div>
      <div style="font-size:18px; color:#666; margin-top:12px;">
        Discord シグナル待機中... （5秒ごとに自動更新）
      </div>
    </div>""", unsafe_allow_html=True)

# ---- 手動入力 & LINE学習 ----
st.divider()
col_manual, col_line = st.columns(2)

with col_manual:
  with st.expander("✍️ 手動でシグナルを入力する"):
    manual_text = st.text_area(
        "テキストを貼り付け", height=120,
        placeholder="ゴールド下落予想、4540付近でショート SL:4560 TP:4500"
    )
    if st.button("解析実行", type="primary", disabled=not manual_text.strip()):
        if not ANTHROPIC_API_KEY:
            st.error("Anthropic API Keyが未設定です")
        else:
            with st.spinner("AI解析中..."):
                try:
                    result = _run_manual(manual_text)
                    import uuid
                    save_data = {
                        "id": str(uuid.uuid4()),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "signal_text": manual_text,
                        "author": "手動入力",
                        "channel": "-",
                        "result": result,
                    }
                    # ローカル保存
                    try:
                        with open(RESULT_FILE, "w", encoding="utf-8") as f:
                            json.dump(save_data, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                    # Google Sheets に保存（Streamlit Cloud 用）
                    sheet_id = _get_secret("GOOGLE_SHEET_ID")
                    if sheet_id:
                        try:
                            gc = _get_sheets_client()
                            if gc:
                                from utils.drive import save_latest_result
                                save_latest_result(gc, sheet_id, save_data)
                                _load_result_from_sheets.clear()
                                _load_history_from_sheets.clear()
                        except Exception:
                            pass
                    _append_history(manual_text, result, "手動入力")
                    st.rerun()
                except Exception as e:
                    st.error(f"エラー: {e}")

with col_line:
 with st.expander("📱 LINEグループ結果を学習させる"):
    st.caption("メンバーの取引結果をAIに蓄積して判断精度を上げます")
    line_text = st.text_area(
        "LINEのテキストを貼り付け", height=100,
        placeholder="例: ショート +42pips 利確🎉\n　　4520でロング入り →4550で決済 +30pips"
    )
    line_image = st.file_uploader("取引画像（任意）", type=["png","jpg","jpeg"])
    if st.button("学習データとして保存", disabled=not line_text.strip() and not line_image):
        with st.spinner("解析・保存中..."):
            try:
                import anthropic, base64, uuid as _uuid
                client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

                content = []
                if line_text.strip():
                    content.append({"type": "text", "text": f"LINEグループの取引結果:\n{line_text}"})
                if line_image:
                    img_bytes = line_image.read()
                    b64 = base64.standard_b64encode(img_bytes).decode()
                    ext = line_image.name.split(".")[-1].lower()
                    media = "image/jpeg" if ext in ["jpg","jpeg"] else "image/png"
                    content.append({"type": "image",
                                    "source": {"type": "base64", "media_type": media, "data": b64}})
                    content.append({"type": "text", "text": "この取引画像から結果を読み取ってください。"})

                content.append({"type": "text", "text": """
以下のJSON形式で取引結果を抽出してください：
{
  "direction": "BUY" or "SELL",
  "entry_price": エントリー価格 or null,
  "exit_price": 決済価格 or null,
  "pips": 損益pips（プラスが利益）or null,
  "outcome": "WIN" or "LOSS" or "BREAK_EVEN",
  "notes": "補足コメント（日本語）"
}"""})

                msg = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=512,
                    messages=[{"role": "user", "content": content}],
                )
                raw = msg.content[0].text.strip()
                if "```json" in raw:
                    raw = raw.split("```json")[1].split("```")[0].strip()
                elif "```" in raw:
                    raw = raw.split("```")[1].split("```")[0].strip()
                parsed = json.loads(raw)

                # 学習ファイルに追記
                learn_file = Path(__file__).parent / "learning_data.json"
                history = []
                if learn_file.exists():
                    with open(learn_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                history.append({
                    "id": str(_uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "LINE",
                    "raw_text": line_text,
                    **parsed,
                })
                with open(learn_file, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)

                # Google Sheets にも保存
                sheet_id = _get_secret("GOOGLE_SHEET_ID")
                if sheet_id:
                    try:
                        gc = _get_sheets_client()
                        if gc:
                            from utils.drive import append_learning_gc
                            append_learning_gc(gc, sheet_id, {
                                **parsed,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "source": "LINE",
                                "raw_text": line_text,
                            })
                    except Exception as e:
                        st.warning(f"Sheets保存エラー: {e}")

                outcome_label = "✅ 勝ち" if parsed.get("outcome") == "WIN" else "❌ 負け" if parsed.get("outcome") == "LOSS" else "➖ 引分"
                st.success(f"保存しました: {outcome_label} / {parsed.get('pips','')}pips")
            except Exception as e:
                st.error(f"エラー: {e}")

# ---- シグナル履歴 ----
st.divider()
st.markdown("### 📋 シグナル履歴")

history = _load_history()
if history:
    # 新しい順に並べる
    rows = list(reversed(history))
    for h in rows[:50]:
        decision   = h.get("decision", "")
        direction  = h.get("direction", "")
        confidence = h.get("confidence", 0)
        pips       = h.get("predicted_pips", 0) or 0
        passed     = h.get("passed_filter", False)
        ts         = h.get("timestamp", "")
        ch         = h.get("channel", "")
        sig        = h.get("signal_text", "")

        if decision == "ENTRY" and direction == "SELL":
            badge = "🔴 SHORT"
            color = "#ff4444"
        elif decision == "ENTRY" and direction == "BUY":
            badge = "🟢 LONG"
            color = "#00cc44"
        elif decision == "WAIT":
            badge = "⏳ WAIT"
            color = "#aaaaaa"
        else:
            badge = "⚫ SKIP"
            color = "#555555"

        filter_tag = "" if passed else " <span style='color:#555;font-size:11px;'>（フィルター除外）</span>"

        st.markdown(
            f"""<div style="border-left:3px solid {color}; padding:6px 12px; margin:4px 0;
                background:#0e0e1a; border-radius:4px; font-family:monospace;">
              <span style="color:#666; font-size:12px;">{ts}</span>
              &nbsp;&nbsp;
              <span style="color:{color}; font-weight:700;">{badge}</span>
              &nbsp;
              <span style="color:#aaa; font-size:12px;">確信度:{confidence}%</span>
              &nbsp;
              <span style="color:#f5c842; font-size:12px;">{pips:+.0f}pips</span>
              &nbsp;
              <span style="color:#555; font-size:11px;">{ch}</span>
              {filter_tag}
              <br>
              <span style="color:#777; font-size:12px;">{sig}</span>
            </div>""",
            unsafe_allow_html=True,
        )
else:
    st.markdown("<div style='color:#555; padding:20px;'>まだシグナル履歴がありません</div>",
                unsafe_allow_html=True)

# ---- サイドバー（管理者ステータスのみ・キー非表示）----
with st.sidebar:
    st.header("⚙️ システム状態")
    st.write("🤖 Claude AI:", "✅" if ANTHROPIC_API_KEY else "❌ 未設定")
    st.write("📈 Alpha Vantage:", "✅" if os.getenv("ALPHA_VANTAGE_API_KEY") else "❌ 未設定")
    st.write("🤖 Discord Bot:", "✅" if os.getenv("DISCORD_BOT_TOKEN") else "❌ 未設定")
    st.caption("APIキーは管理者のみ .env で設定")
