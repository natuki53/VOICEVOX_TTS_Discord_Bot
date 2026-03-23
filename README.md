# VOICEVOX読み上げBot

Discord のテキストチャンネルを、VOICEVOXで読み上げる Bot です。

## 機能

- 指定テキストチャンネルのメッセージを VC で自動読み上げ
- ユーザー個別の話者切り替え（`/speaker`）
- サーバー全体デフォルト話者切り替え（`/speakerall`）
- ユーザー個別の互換声スタイル切り替え（`/style`）
- サーバー全体デフォルト互換声スタイル切り替え（`/styleall`）
- 読み上げ速度変更（`0.5` 〜 `2.0`）
- 最大読み上げ文字数変更（`10` 〜 `500`）
- VC 入退室アナウンス
- URL・メンションなどの読み上げ前テキスト前処理
- 設定の再起動後復元（話者/スタイル/速度/文字数/読み上げチャンネル）

## 使用コマンド

| コマンド | 説明 |
|---|---|
| `/join` | Bot を VC に参加させ、現在のテキストチャンネルを読み上げ対象に設定 |
| `/leave` | VC から退出し、読み上げ停止 |
| `/speaker` | あなたの話者変更（インストール済み話者から選択） |
| `/speakerall` | サーバー全体デフォルト話者変更（要 `Manage Server`） |
| `/style` | あなたの互換声スタイル変更（`normal` / `amaama` / `tsuntsun` / `sexy`） |
| `/styleall` | サーバー全体デフォルト互換声スタイル変更（要 `Manage Server`） |
| `/speed <value>` | 読み上げ速度変更（`0.5`〜`2.0`） |
| `/maxlength <length>` | 最大読み上げ文字数変更（`10`〜`500`） |
| `/status` | 現在の設定確認 |
| `/about` | Bot 情報表示 |

`/style` と `/styleall` は、旧設定との互換のために `VOICEVOX:ずんだもん` のスタイルIDを基準にしたプリセットです。

## 動作要件

- Python 3.11 以上
- `ffmpeg`
- **VOICEVOX Engine**（HTTP で接続可能）

### VOICEVOX Engine の入手

本 Bot は VOICEVOX Engine の HTTP API を使用します。Engine は下記からダウンロードしてください。

- **最新リリース（推奨）**: [VOICEVOX ENGINE - Latest Release](https://github.com/VOICEVOX/voicevox_engine/releases/latest)

リリースページでは OS・CPU/GPU 別にパッケージが用意されています。

| プラットフォーム | パッケージ例 |
|------------------|--------------|
| **Windows** | CPU版 / GPU（DirectML）版 / GPU（CUDA）版 |
| **macOS** | CPU（x64）版 / CPU（arm64, M1/M2 等）版 |
| **Linux** | CPU（x64）版 / CPU（arm64）版 / GPU（CUDA）版 |

---

## VOICEVOX Engine の起動方法（OS 別）

Bot を動かす前に、VOICEVOX Engine を起動し、`http://localhost:50021` で待ち受けている必要があります。

### Windows

1. [最新リリース](https://github.com/VOICEVOX/voicevox_engine/releases/latest) から Windows 用をダウンロード（CPU版 or DirectML/CUDA 版）
2. ZIP を解凍し、中の `run.exe`（または `run.bat`）を実行
3. 既定で `http://localhost:50021` で起動します。別ホスト/ポートの場合は `.env` の `VOICEVOX_URL` を合わせてください

### macOS

- **Apple Silicon（M1/M2 等）**: `macos-arm64` 用パッケージをダウンロード・解凍後:
  ```bash
  ./run --host 127.0.0.1 --port 50021
  ```
- **Intel（x64）**: `macos-x64` 用パッケージを同様にダウンロード・解凍し、`./run --host 127.0.0.1 --port 50021` で起動

### Linux

- **CPU 版（x64）**: `voicevox_engine-linux-cpu-*.tar.gz` をダウンロード・展開後、同梱の `run` スクリプトで起動
- **ARM64 / CUDA 版**: 同じリリースページから該当アーキテクチャ用を選択

本リポジトリの `deploy/setup_ubuntu.sh` では、Engine を `/opt/voicevox_engine/` に展開する想定で systemd の例を用意しています。

---

## セットアップ（ローカル）

### 1. Discord Bot 設定

Discord Developer Portal の対象アプリで以下を設定してください。

- Bot トークンを取得
- Privileged Gateway Intents の `MESSAGE CONTENT INTENT` を有効化

### 2. 依存パッケージをインストール

**ffmpeg**

- **Windows**: [ffmpeg 公式](https://ffmpeg.org/download.html) または Chocolatey で `choco install ffmpeg`。PATH に通す。
- **macOS**: `brew install ffmpeg`
- **Linux（Debian/Ubuntu）**: `sudo apt install ffmpeg`

**Python 依存関係**

```bash
# Python 仮想環境（任意）
python3 -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

### 3. 環境変数を設定

```bash
# macOS / Linux
cp .env.example .env
# Windows (コマンドプロンプト)
copy .env.example .env
```

`.env` を編集して値を設定してください。

| 変数名 | 説明 | 例 / デフォルト |
|---|---|---|
| `DISCORD_TOKEN` | Discord Bot トークン（必須） | `xxxxxxxx` |
| `VOICEVOX_URL` | VOICEVOX Engine の URL | `http://localhost:50021` |
| `DEFAULT_SPEAKER_ID` | 初期話者ID（スタイルID） | `3` |
| `DEFAULT_SPEAKER_STYLE` | 旧互換: 初期声スタイル | `normal` |
| `COMMAND_GUILD_ID` | 指定サーバーでスラッシュコマンドを即時同期（開発向け） | `123456789012345678` |
| `RUNTIME_STATE_FILE` | 設定保存先JSONパス（任意） | `data/runtime_state.json` |

## 起動方法

VOICEVOX Engine の起動方法は OS により異なります。未読の場合は上記「[VOICEVOX Engine の起動方法（OS 別）](#voicevox-engine-の起動方法os-別)」を参照してください。

### ローカル起動（macOS で同梱 run を使う場合）

```bash
# 1) VOICEVOX Engine を起動（解凍した Engine ディレクトリ内で）
./run --host 127.0.0.1 --port 50021
```

別ターミナルで:

```bash
# 2) Bot を起動
python main.py
```

### ローカル起動（すでに Engine が動いている場合）

1. VOICEVOX Engine を任意の方法で起動済みであること  
2. `.env` の `VOICEVOX_URL` をその URL に合わせる（既定: `http://localhost:50021`）  
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
- コマンド定義を変更したのに古い内容が表示される  
  `.env` に `COMMAND_GUILD_ID=<あなたのサーバーID>` を設定してBotを再起動してください。ギルドコマンドを再同期して即時反映できます。
- VOICEVOX に接続できない  
  `VOICEVOX_URL` と Engine の起動ポート（既定 `50021`）を確認してください。

## クレジット・ライセンス

### VOICEVOX について

- **VOICEVOX**: https://voicevox.hiroshiba.jp/
- **利用規約（必読）**: https://voicevox.hiroshiba.jp/term/
- **VOICEVOX Engine（GitHub）**: https://github.com/VOICEVOX/voicevox_engine
- **音声クレジット表記（デフォルト互換プリセット）**: `VOICEVOX:ずんだもん`

VOICEVOX で生成した音声は商用・非商用を問わず利用可能ですが、**VOICEVOX を利用したことが分かるクレジット表記が必須**です。各音声ライブラリの規約にも従ってください。逆コンパイル・無断再配布等は禁止されています。詳細は上記利用規約を参照してください。

### 本 Bot で利用しているライブラリ

- **discord.py**: https://github.com/Rapptz/discord.py
