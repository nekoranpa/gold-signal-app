import os
import json
from pathlib import Path
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from utils.market_data import get_gold_price, get_gold_intraday, build_market_summary

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
  var POLL_SECS = 5;
  var shownAt   = null;

  function getHoldSecs() {
    var el = document.getElementById('signal-hold-secs');
    return el ? parseInt(el.dataset.secs || '300') : 300;
  }

  function isEntry() {
    return document.body.innerText.includes('エントリー');
  }

  function isTyping() {
    var a = document.activeElement;
    return a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT');
  }

  function tick() {
    if (isTyping()) { setTimeout(tick, 2000); return; }
    if (isEntry()) {
      if (!shownAt) shownAt = Date.now();
      var elapsed = (Date.now() - shownAt) / 1000;
      if (elapsed >= getHoldSecs()) {
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
    return result


# ================================================================
# UI
# ================================================================

# ---- Gold現在価格（Yahoo Finance、5秒更新）----
try:
    p = get_gold_price()
    color = "#00ff88" if p["change"] >= 0 else "#ff4444"
    change_str = f"{p['change']:+,.2f} ({p['change_pct']})"
    price_str  = f"${p['price']:,.2f}"
except Exception:
    color = "#666"
    change_str = ""
    price_str  = "---"

st.markdown(f"""
<div style="text-align:center; padding:10px 0 4px; font-family:sans-serif;">
  <span style="font-size:26px; font-weight:700; color:#f5c842;">金価格</span>
  <span id="gold-price" style="font-size:34px; font-weight:900; color:#fff; margin:0 14px;">{price_str}</span>
  <span style="font-size:20px; color:{color};">{change_str}</span>
  <span style="font-size:13px; color:#666; margin-left:10px;">XAU/USD /
    <span id="gold-clock">--:--:--</span>
  </span>
</div>
<script>
(function() {{
  function updateClock() {{
    var now = new Date();
    var h = String(now.getHours()).padStart(2,'0');
    var m = String(now.getMinutes()).padStart(2,'0');
    var s = String(now.getSeconds()).padStart(2,'0');
    var el = document.getElementById('gold-clock');
    if (el) el.textContent = h + ':' + m + ':' + s;
  }}
  setInterval(updateClock, 1000);
  updateClock();
}})();
</script>
""", unsafe_allow_html=True)

st.divider()

# ---- メイン判定表示 ----
data = _load_result()

# 監視ウィンドウが終了していたら待機画面に戻す
_signal_active = False
if data and "result" in data:
    _mu = data.get("monitoring_until", "")
    if _mu:
        try:
            _signal_active = datetime.now() < datetime.strptime(_mu, "%Y-%m-%d %H:%M:%S")
        except Exception:
            _signal_active = True
    else:
        _signal_active = True  # monitoring_until 未設定の古いデータはそのまま表示

if _signal_active and data and "result" in data:
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

        passed       = data.get("passed_filter", decision == "ENTRY" and conf >= 80)
        hold_secs    = r.get("hold_seconds", 300)
        source_type  = r.get("source_type", data.get("channel", ""))
        recv_time    = data.get("timestamp", "")

        # 保持秒数を JS が読み取れる要素として埋め込む
        st.markdown(f'<div id="signal-hold-secs" data-secs="{hold_secs}" style="display:none"></div>',
                    unsafe_allow_html=True)

        # ---- 通知受信バナー ----
        st.markdown(f"""
        <div style="background:#0a0a1a; border:1px solid #334; border-radius:10px;
                    padding:10px 20px; margin-bottom:14px; display:flex;
                    align-items:center; gap:16px; flex-wrap:wrap;">
          <span style="font-size:22px;">📡</span>
          <span style="color:#888; font-size:13px;">シグナル受信</span>
          <span style="color:#fff; font-size:18px; font-weight:700;">{recv_time}</span>
          <span style="background:#1a1a33; color:#aaf; font-size:13px;
                       padding:3px 10px; border-radius:20px;">{source_type}</span>
          <span style="color:#666; font-size:12px; margin-left:auto;">
            {data.get('signal_text','')[:60]}
          </span>
        </div>""", unsafe_allow_html=True)

        if decision == "ENTRY" and direction == "SELL":
            bg, fg, emoji, label = "#1a0808", "#ff3333", "🔴", "SHORT エントリー"
        elif decision == "ENTRY" and direction == "BUY":
            bg, fg, emoji, label = "#081a08", "#00dd55", "🟢", "LONG エントリー"
        elif decision == "WAIT":
            bg, fg, emoji, label = "#0e0e1a", "#888888", "⏳", "様子見 / WAIT"
        else:
            bg, fg, emoji, label = "#0e0e1a", "#555555", "⚫", "スルー / SKIP"

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
          <div style="font-size:14px; color:#555; margin-top:10px;">
            📡 {source_type}&nbsp;&nbsp;⏱ {hold_secs//60}分間表示
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

        # ---- リアルタイム監視バッジ ----
        monitoring_until_str = data.get("monitoring_until", "")
        if monitoring_until_str:
            try:
                from datetime import datetime as _dt
                until_dt = _dt.strptime(monitoring_until_str, "%Y-%m-%d %H:%M:%S")
                remaining = int((until_dt - _dt.now()).total_seconds())
                if remaining > 0:
                    # 現在価格 vs 予測方向で可能性を判定
                    try:
                        cur_price = get_gold_price()["price"]
                    except Exception:
                        cur_price = None

                    possible = None
                    reason = ""
                    if cur_price and price_lv:
                        diff = cur_price - price_lv
                        if direction == "SELL":
                            possible = diff >= -8
                            reason = (f"現在${cur_price:,.1f} / 目標${price_lv:,.0f} → "
                                      ("まだエントリー圏内" if possible else "価格が下がりすぎ・チャンス逃し"))
                        elif direction == "BUY":
                            possible = diff <= 8
                            reason = (f"現在${cur_price:,.1f} / 目標${price_lv:,.0f} → "
                                      ("まだエントリー圏内" if possible else "価格が上がりすぎ・チャンス逃し"))
                        else:
                            possible = None
                            reason = "方向性不明のため判定不可"
                    elif cur_price and not price_lv:
                        possible = True
                        reason = f"価格水準未設定 / 現在${cur_price:,.1f}"

                    if possible is True:
                        badge_bg, badge_fg, badge_txt = "#1a1500", "#f5c842", "🟡 可能性あり・監視中"
                    elif possible is False:
                        badge_bg, badge_fg, badge_txt = "#111118", "#555555", "⚫ スルー（条件不一致）"
                    else:
                        badge_bg, badge_fg, badge_txt = "#0e1a0e", "#888888", "⏳ 監視中"

                    mins, secs = divmod(remaining, 60)
                    st.markdown(f"""
                    <div style="background:{badge_bg}; border:2px solid {badge_fg};
                                border-radius:14px; padding:16px 24px; margin:16px 0; text-align:center;">
                      <div style="font-size:28px; font-weight:900; color:{badge_fg};">{badge_txt}</div>
                      <div style="font-size:14px; color:#888; margin-top:6px;">{reason}</div>
                      <div style="font-size:13px; color:#555; margin-top:4px;">
                        残り監視時間: <span style="color:{badge_fg}; font-weight:700;">{mins}分{secs:02d}秒</span>
                      </div>
                    </div>""", unsafe_allow_html=True)
            except Exception:
                pass

        # ---- 3アナリスト議論パネル ----
        aa = r.get("analyst_a", {})
        ab = r.get("analyst_b", {})
        ac = r.get("analyst_c", {})
        vote = r.get("consensus", {}).get("vote", "")
        if aa or ab or ac:
            def _stance_color(s):
                return "#00cc44" if s in ("BUY","ENTRY") else "#ff4444" if s in ("SELL",) else "#888888"

            st.markdown("<div style='margin-top:18px;color:#666;font-size:13px;'>━━ 3アナリスト議論 ━━</div>",
                        unsafe_allow_html=True)
            ca, cb, cc = st.columns(3)
            for col, label, icon, analyst in [
                (ca, "アナリストA 強気派", "📈", aa),
                (cb, "アナリストB 慎重派", "🛡️", ab),
                (cc, "アナリストC リスク管理", "⚖️", ac),
            ]:
                stance = analyst.get("stance", "-")
                sc = _stance_color(stance)
                with col:
                    st.markdown(f"""
                    <div style="background:#0e0e1a; border:1px solid #333; border-radius:10px; padding:14px;">
                      <div style="color:#888;font-size:12px;">{icon} {label}</div>
                      <div style="color:{sc};font-size:22px;font-weight:900;margin:6px 0;">{stance}</div>
                      <div style="color:#aaa;font-size:11px;">{analyst.get('confidence',0)}%</div>
                      <div style="color:#777;font-size:12px;margin-top:6px;line-height:1.5;">
                        {analyst.get('reasoning','')}
                      </div>
                    </div>""", unsafe_allow_html=True)

            if vote:
                st.markdown(f"<div style='text-align:center;color:#f5c842;font-size:14px;"
                            f"margin-top:10px;'>🗳️ {vote}</div>", unsafe_allow_html=True)

        st.markdown(f"<div style='color:#555; font-size:12px; margin-top:12px; text-align:right;'>"
                    f"シグナル受信: {data.get('timestamp','')} ／ "
                    f"{data.get('channel','')} ／ {data.get('author','')}</div>",
                    unsafe_allow_html=True)

else:
    # ---- 待機画面：ライブチャート＋相場分析 ----
    st.markdown("""
    <div style="text-align:center; padding:20px 0 10px;">
      <span style="font-size:18px; color:#666;">📡 シグナル待機中...</span>
      <span style="font-size:13px; color:#444; margin-left:12px;">（5秒ごと自動更新）</span>
    </div>""", unsafe_allow_html=True)

    # ライブチャート
    try:
        import pandas as pd
        intraday = get_gold_intraday()
        candles  = intraday.get("candles", [])
        if candles:
            df = pd.DataFrame(list(reversed(candles))).set_index("time")
            trend_c = "#00ff88" if intraday["trend"] == "上昇" else "#ff4444"
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("直近トレンド", intraday["trend"],
                          delta_color="normal" if intraday["trend"]=="上昇" else "inverse")
            col_t2.metric("直近高値", f"${intraday['recent_high']:,.2f}")
            col_t3.metric("直近安値",  f"${intraday['recent_low']:,.2f}")
            st.line_chart(df["close"], height=180, use_container_width=True)
    except Exception:
        pass

    # 相場サマリー
    try:
        summary = build_market_summary()
        lines = [l for l in summary.split("\n") if l.strip() and "【" not in l]
        st.markdown(
            "<div style='background:#0e0e1a; border-radius:10px; padding:14px 20px; "
            "font-family:monospace; font-size:13px; color:#888; line-height:1.9;'>"
            + "<br>".join(lines) + "</div>",
            unsafe_allow_html=True
        )
    except Exception:
        pass

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
                    from datetime import timedelta as _td
                    _hold = result.get("hold_seconds", 300)
                    _until = (datetime.now() + _td(seconds=_hold)).strftime("%Y-%m-%d %H:%M:%S")
                    save_data = {
                        "id": str(uuid.uuid4()),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "signal_text": manual_text,
                        "author": "手動入力",
                        "channel": "-",
                        "result": result,
                        "monitoring_until": _until,
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
    rows = list(reversed(history))
    import pandas as pd
    table_data = []
    for h in rows[:100]:
        table_data.append({
            "日時": h.get("timestamp", ""),
            "判定": h.get("decision", "") + " " + h.get("direction", ""),
            "確信度": str(h.get("confidence", 0)) + "%",
            "シグナル": h.get("signal_text", "")[:50],
            "根拠": h.get("reasoning", "")[:80],
        })
    if table_data:
        st.dataframe(pd.DataFrame(table_data), use_container_width=True)
else:
    st.markdown("<div style='color:#555; padding:20px;'>まだシグナル履歴がありません</div>", unsafe_allow_html=True)
# ---- サイドバー（管理者ステータスのみ・キー非表示）----
with st.sidebar:
    st.header("⚙️ システム状態")
    st.write("🤖 Claude AI:", "✅" if ANTHROPIC_API_KEY else "❌ 未設定")
    st.write("📈 Alpha Vantage:", "✅" if os.getenv("ALPHA_VANTAGE_API_KEY") else "❌ 未設定")
    st.write("🤖 Discord Bot:", "✅" if os.getenv("DISCORD_BOT_TOKEN") else "❌ 未設定")
    st.caption("APIキーは管理者のみ .env で設定")
