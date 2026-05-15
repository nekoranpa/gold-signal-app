# Gold Signal Analyzer セットアップガイド

## 1. 依存パッケージのインストール

```bash
cd gold-signal-app
pip install -r requirements.txt
```

## 2. Anthropic API Key の取得

1. https://console.anthropic.com にアクセス
2. API Keys → Create Key
3. キーをコピーしておく

## 3. Google Drive（Sheets）の設定

### 3-1. Google Cloud プロジェクト作成
1. https://console.cloud.google.com にアクセス
2. 新しいプロジェクトを作成

### 3-2. API を有効化
- Google Sheets API
- Google Drive API
の2つを有効化する

### 3-3. サービスアカウントの作成
1. 「IAMと管理」→「サービスアカウント」→「作成」
2. 名前を入力して作成
3. 「キー」タブ → 「鍵を追加」→「JSON」
4. ダウンロードした JSON を `gold-signal-app/service_account.json` として保存

### 3-4. Google Spreadsheet の作成
1. Google Drive で新しいスプレッドシートを作成
2. URLから Sheet ID をコピー
   - 例: `https://docs.google.com/spreadsheets/d/【ここがID】/edit`
3. スプレッドシートをサービスアカウントのメールアドレスと共有（編集者権限）

## 4. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集：
```
ANTHROPIC_API_KEY=sk-ant-xxxxx
GOOGLE_SHEET_ID=xxxxxxxxxxxxxxxxxxxxxx
GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json
```

## 5. アプリの起動

```bash
streamlit run app.py
```

ブラウザで http://localhost:8501 が開きます。

## 使い方

1. サイドバーに API Key と Google Sheet ID を入力
2. Discordからシグナルテキストをコピー
3. テキストエリアに貼り付け
4.「AI解析を実行」ボタンをクリック
5. 判断結果・タイミング・予想pipsを確認
6. トレード後に実際の結果を「シグナル履歴」から記録

## Google Drive なしで使う場合

Google Drive 設定なしでも AI 解析は動作します（履歴の保存・読み込みは無効）。
