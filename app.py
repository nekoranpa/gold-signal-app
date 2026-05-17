"""
TRAロジック・ナレッジをGoogle Driveから読み込んでプロンプトに注入するユーティリティ。
ファイルID: 1_rEU4H2lLLZCX9JzYHT1XMsY0e8a6KC7K0wwXVw6IsU
"""
import os
import json
from pathlib import Path

# ドライブファイルID（TRAロジック_プロンプト用）
TRA_KNOWLEDGE_FILE_ID = "1DrjDVZDrtlCqUF_Yoz6PB43hwVubajZCNXpW4fXKIx8"

# ローカルキャッシュ（起動時に一度だけ取得）
_cache: str | None = None


def _load_from_drive() -> str:
    """Google Drive APIでTRAナレッジファイルを読み込む"""
    try:
        from utils.drive import _client_from_dict
        import json as _json

        creds_raw = os.environ.get("GDRIVE_CREDENTIALS", "")
        if not creds_raw:
            return ""

        creds_dict = _json.loads(creds_raw)
        client = _client_from_dict(creds_dict)

        # Drive APIでファイルをテキストとしてエクスポート
        content = (
            client.files()
            .export(fileId=TRA_KNOWLEDGE_FILE_ID, mimeType="text/plain")
            .execute()
        )
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return str(content)

    except Exception as e:
        print(f"[TRA Knowledge] Drive読み込み失敗: {e}")
        return _load_from_local()


def _load_from_local() -> str:
    """フォールバック: ローカルのtra_knowledge.txtを読む"""
    local = Path(__file__).parent.parent / "tra_knowledge.txt"
    if local.exists():
        return local.read_text(encoding="utf-8")
    return ""


def get_tra_prompt_section() -> str:
    """
    プロンプトに注入するTRAロジックセクションを返す。
    キャッシュがあればそれを使う（起動中は同じ内容を使い回す）。
    """
    global _cache
    if _cache is None:
        _cache = _load_from_drive()
    if not _cache:
        return ""
    return f"""
【TRAインジケーター（トレードラッシュ）ロジック・背景知識】
以下の知識をトレード判断の参考情報として活用してください：

{_cache}
"""


def refresh_cache() -> str:
    """キャッシュを強制更新して最新のTRAロジックを取得する"""
    global _cache
    _cache = None
    return get_tra_prompt_section()
