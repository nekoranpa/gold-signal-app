import json
import os
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SIGNAL_HEADERS  = ["timestamp", "channel", "signal_text", "decision", "direction",
                    "confidence", "predicted_pips", "passed_filter"]
LEARNING_HEADERS = ["timestamp", "source", "direction", "entry_price", "exit_price",
                    "pips", "outcome", "notes", "raw_text"]
LATEST_HEADERS  = ["id", "timestamp", "signal_text", "author", "channel", "result_json"]


def _client(sa_json: str) -> gspread.Client:
    creds = Credentials.from_service_account_file(sa_json, scopes=SCOPES)
    return gspread.authorize(creds)


def _client_from_dict(sa_dict: dict) -> gspread.Client:
    creds = Credentials.from_service_account_info(sa_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_ws(spreadsheet, title: str, headers: list) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def append_signal(sa_json: str, sheet_id: str, entry: dict):
    """シグナル履歴をGoogle Sheetsに追記（ファイルパス版）"""
    gc = _client(sa_json)
    append_signal_gc(gc, sheet_id, entry)


def append_signal_gc(gc: gspread.Client, sheet_id: str, entry: dict):
    """シグナル履歴をGoogle Sheetsに追記（クライアント版）"""
    ss = gc.open_by_key(sheet_id)
    ws = _get_or_create_ws(ss, "シグナル履歴", SIGNAL_HEADERS)
    ws.append_row([
        entry.get("timestamp", ""),
        entry.get("channel", ""),
        entry.get("signal_text", "")[:200],
        entry.get("decision", ""),
        entry.get("direction", ""),
        entry.get("confidence", 0),
        entry.get("predicted_pips", 0),
        "○" if entry.get("passed_filter") else "×",
    ])


def save_latest_result(gc: gspread.Client, sheet_id: str, data: dict):
    """最新シグナルをGoogle Sheetsに保存（1行のみ上書き）"""
    ss = gc.open_by_key(sheet_id)
    try:
        ws = ss.worksheet("最新シグナル")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title="最新シグナル", rows=10, cols=len(LATEST_HEADERS))
    ws.append_row(LATEST_HEADERS)
    ws.append_row([
        data.get("id", ""),
        data.get("timestamp", ""),
        data.get("signal_text", "")[:200],
        data.get("author", ""),
        data.get("channel", ""),
        json.dumps(data.get("result", {}), ensure_ascii=False),
    ])


def load_latest_result(gc: gspread.Client, sheet_id: str):
    """Google Sheetsから最新シグナルを読み込む"""
    try:
        ss = gc.open_by_key(sheet_id)
        ws = _get_or_create_ws(ss, "最新シグナル", LATEST_HEADERS)
        records = ws.get_all_records()
        if not records:
            return None
        row = records[-1]
        result_json = row.get("result_json", "{}")
        result = json.loads(result_json) if result_json else {}
        return {
            "id": row.get("id", ""),
            "timestamp": row.get("timestamp", ""),
            "signal_text": row.get("signal_text", ""),
            "author": row.get("author", ""),
            "channel": row.get("channel", ""),
            "result": result,
        }
    except Exception:
        return None


def load_signal_history(gc: gspread.Client, sheet_id: str) -> list:
    """Google Sheetsからシグナル履歴を読み込む"""
    try:
        ss = gc.open_by_key(sheet_id)
        ws = _get_or_create_ws(ss, "シグナル履歴", SIGNAL_HEADERS)
        records = ws.get_all_records()
        result = []
        for r in records:
            result.append({
                "timestamp": r.get("timestamp", ""),
                "channel": r.get("channel", ""),
                "signal_text": r.get("signal_text", ""),
                "decision": r.get("decision", ""),
                "direction": r.get("direction", ""),
                "confidence": r.get("confidence", 0),
                "predicted_pips": r.get("predicted_pips", 0),
                "passed_filter": r.get("passed_filter", "×") == "○",
            })
        return result
    except Exception:
        return []


def append_learning(sa_json: str, sheet_id: str, entry: dict):
    """LINE学習データをGoogle Sheetsに追記（ファイルパス版）"""
    gc = _client(sa_json)
    append_learning_gc(gc, sheet_id, entry)


def append_learning_gc(gc: gspread.Client, sheet_id: str, entry: dict):
    """LINE学習データをGoogle Sheetsに追記（クライアント版）"""
    ss = gc.open_by_key(sheet_id)
    ws = _get_or_create_ws(ss, "LINE学習データ", LEARNING_HEADERS)
    ws.append_row([
        entry.get("timestamp", ""),
        entry.get("source", "LINE"),
        entry.get("direction", ""),
        entry.get("entry_price", ""),
        entry.get("exit_price", ""),
        entry.get("pips", ""),
        entry.get("outcome", ""),
        entry.get("notes", ""),
        entry.get("raw_text", "")[:200],
    ])


def load_learning_history(sa_json: str, sheet_id: str) -> list:
    """Google SheetsからLINE学習データを読み込む"""
    try:
        gc = _client(sa_json)
        ss = gc.open_by_key(sheet_id)
        ws = _get_or_create_ws(ss, "LINE学習データ", LEARNING_HEADERS)
        return ws.get_all_records()
    except Exception:
        return []
