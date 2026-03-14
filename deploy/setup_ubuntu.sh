#!/bin/bash
# Ubuntu向けセットアップスクリプト
# 実行方法: sudo bash deploy/setup_ubuntu.sh

set -e

echo "=== ずんだもん読み上げBot セットアップ ==="

# --- システムパッケージのインストール ---
echo "[1/5] システムパッケージをインストール..."
apt-get update -qq
apt-get install -y python3.11 python3.11-venv ffmpeg

# --- 専用ユーザーの作成 ---
echo "[2/5] 専用ユーザー voicebot を作成..."
if ! id -u voicebot >/dev/null 2>&1; then
    useradd -r -m -d /opt/voicebot -s /bin/bash voicebot
    echo "ユーザー voicebot を作成しました"
else
    echo "ユーザー voicebot はすでに存在します"
fi

# --- プロジェクトファイルのデプロイ ---
echo "[3/5] プロジェクトファイルをコピー..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    "$PROJECT_DIR/" /opt/voicebot/

chown -R voicebot:voicebot /opt/voicebot

# --- Python仮想環境のセットアップ ---
echo "[4/5] Python仮想環境を構築..."
sudo -u voicebot python3.11 -m venv /opt/voicebot/.venv
sudo -u voicebot /opt/voicebot/.venv/bin/pip install --upgrade pip -q
sudo -u voicebot /opt/voicebot/.venv/bin/pip install -r /opt/voicebot/requirements.txt -q

# --- .envファイルの作成 ---
if [ ! -f /opt/voicebot/.env ]; then
    echo "[5/5] .envファイルを作成してください..."
    cp /opt/voicebot/.env.example /opt/voicebot/.env
    chown voicebot:voicebot /opt/voicebot/.env
    chmod 600 /opt/voicebot/.env
    echo ""
    echo "⚠️  /opt/voicebot/.env を編集してDiscordトークンを設定してください:"
    echo "    sudo nano /opt/voicebot/.env"
else
    echo "[5/5] .envファイルはすでに存在します"
fi

# --- systemdサービスのインストール ---
echo "systemdサービスをインストール..."
cp /opt/voicebot/deploy/voicevox-engine.service /etc/systemd/system/
cp /opt/voicebot/deploy/voicebot.service /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "次の手順:"
echo "  1. VOICEVOX Engine をダウンロードして /opt/voicevox_engine/ に展開"
echo "     https://github.com/VOICEVOX/voicevox_engine/releases"
echo "     (Linux CPU版: voicevox_engine-linux-cpu-*.tar.gz)"
echo ""
echo "  2. .envファイルにDiscordトークンを設定:"
echo "     sudo nano /opt/voicebot/.env"
echo ""
echo "  3. サービスを起動:"
echo "     sudo systemctl enable --now voicevox-engine"
echo "     sudo systemctl enable --now voicebot"
echo ""
echo "  4. ログの確認:"
echo "     journalctl -u voicebot -f"
echo "     journalctl -u voicevox-engine -f"
