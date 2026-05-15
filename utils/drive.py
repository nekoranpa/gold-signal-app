import os
from pathlib import Path
from datetime import datetime
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


def _client(sa_json: str) -> gspread.Client:
    creds = Credentials.from_service_account_file(sa_json, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_ws(spreadsheet, title: str, headers: list) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def append_signal(sa_json: str, sheet_id: str, entry: dict):
    """シグナル履歴をGoogle Sheetsに追記"""
    client  = _client(sa_json)
    ss      = client.open_by_key(sheet_id)
    ws      = _get_or_create_ws(ss, "シグナル履歴", SIGNAL_HEADERS)
    row = [
        entry.get("timestamp", ""),
        entry.get("channel", ""),
        entry.get("signal_text", "")[:200],
        entry.get("decision", ""),
        entry.get("direction", ""),
        entry.get("confidence", 0),
        entry.get("predicted_pips", 0),
        "○" if entry.get("passed_filter") else "×",
    ]
    ws.append_row(row)


def append_learning(sa_json: str, sheet_id: str, entry: dict):
    """LINE学習データをGoogle Sheetsに追記"""
    client = _client(sa_json)
    ss     = client.open_by_key(sheet_id)
    ws     = _get_or_create_ws(ss, "LINE学習データ", LEARNING_HEADERS)
    row = [
        entry.get("timestamp", ""),
        entry.get("source", "LINE"),
        entry.get("direction", ""),
        entry.get("entry_price", ""),
        entry.get("exit_price", ""),
        entry.get("pips", ""),
        entry.get("outcome", ""),
        entry.get("notes", ""),
        entry.get("raw_text", "")[:200],
    ]
    ws.append_row(row)


def load_learning_history(sa_json: str, sheet_id: str) -> list:
    """Google SheetsからLINE学習データを読み込む"""
    try:
        client = _client(sa_json)
        ss     = client.open_by_key(sheet_id)
        ws     = _get_or_create_ws(ss, "LINE学習データ", LEARNING_HEADERS)
        return ws.get_all_records()
    except Exception:
        return []
