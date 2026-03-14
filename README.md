# ずんだもん読み上げBot

Discord のテキストチャンネルを、VOICEVOX（ずんだもん）で読み上げる Bot です。

## 機能

- 指定テキストチャンネルのメッセージを VC で自動読み上げ
- 声スタイル切り替え（`normal` / `amaama` / `tsuntsun` / `sexy`）
- 読み上げ速度変更（`0.5` 〜 `2.0`）
- 最大読み上げ文字数変更（`10` 〜 `500`）
- VC 入退室アナウンス
- URL・メンションなどの読み上げ前テキスト前処理

## 使用コマンド

| コマンド | 説明 |
|---|---|
| `/join` | Bot を VC に参加させ、現在のテキストチャンネルを読み上げ対象に設定 |
| `/leave` | VC から退出し、読み上げ停止 |
| `/speaker` | 声スタイル変更 |
| `/speed <value>` | 読み上げ速度変更（`0.5`〜`2.0`） |
| `/maxlength <length>` | 最大読み上げ文字数変更（`10`〜`500`） |
| `/status` | 現在の設定確認 |
| `/about` | Bot 情報表示 |

## 動作要件

- Python 3.11 以上
- `ffmpeg`
- VOICEVOX Engine（HTTP で接続可能）

## セットアップ（ローカル）

### 1. Discord Bot 設定

Discord Developer Portal の対象アプリで以下を設定してください。

- Bot トークンを取得
- Privileged Gateway Intents の `MESSAGE CONTENT INTENT` を有効化

### 2. 依存パッケージをインストール

```bash
# macOS の場合
brew install ffmpeg

# Python 仮想環境（任意）
python3 -m venv .venv
source .venv/bin/activate

# Python 依存関係
pip install -r requirements.txt
```

### 3. 環境変数を設定

```bash
cp .env.example .env
```

`.env` を編集して値を設定してください。

| 変数名 | 説明 | 例 / デフォルト |
|---|---|---|
| `DISCORD_TOKEN` | Discord Bot トークン（必須） | `xxxxxxxx` |
| `VOICEVOX_URL` | VOICEVOX Engine の URL | `http://localhost:50021` |
| `DEFAULT_SPEAKER_STYLE` | 初期声スタイル | `normal` |

## 起動方法

### ローカル起動（macOS 同梱 Engine を使う場合）

```bash
# 1) VOICEVOX Engine を起動
./macos-arm64/run --host 127.0.0.1 --port 50021
```

別ターミナルで:

```bash
# 2) Bot を起動
python main.py
```

### ローカル起動（外部 Engine を使う場合）

1. VOICEVOX Engine を任意の方法で起動  
2. `.env` の `VOICEVOX_URL` をその URL に設定  
3. `python main.py` を実行

## Ubuntu（systemd）での起動

`deploy/setup_ubuntu.sh` が用意されています。

```bash
sudo bash deploy/setup_ubuntu.sh
```

セットアップ後:

```bash
# VOICEVOX Engine / Bot を常駐起動
sudo systemctl enable --now voicevox-engine
sudo systemctl enable --now voicebot

# ログ確認
journalctl -u voicevox-engine -f
journalctl -u voicebot -f
```

`voicevox-engine.service` の `ExecStart` は、VOICEVOX Engine の展開先に合わせて調整してください。

## トラブルシュート

- `ffmpegが見つかりません`  
  `ffmpeg` が PATH に入っていません。macOS は `brew install ffmpeg`、Ubuntu は `sudo apt install ffmpeg` を実行してください。
- スラッシュコマンドが表示されない  
  Bot 招待時の権限不足、または Developer Portal 側の Intent 設定を確認してください。
- VOICEVOX に接続できない  
  `VOICEVOX_URL` と Engine の起動ポート（既定 `50021`）を確認してください。

## クレジット

- VOICEVOX: https://voicevox.hiroshiba.jp/
- 利用規約: https://voicevox.hiroshiba.jp/term/
- discord.py: https://github.com/Rapptz/discord.py
