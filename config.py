import os
from dotenv import load_dotenv

load_dotenv()

# --- Discord ---
DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]

# --- VOICEVOX ---
VOICEVOX_BASE_URL: str = os.getenv("VOICEVOX_URL", "http://localhost:50021")

# ずんだもんのスピーカーID
SPEAKERS: dict[str, int] = {
    "normal":   3,
    "amaama":   1,
    "tsuntsun": 7,
    "sexy":     5,
}

_default_style = os.getenv("DEFAULT_SPEAKER_STYLE", "normal")
DEFAULT_SPEAKER_ID: int = SPEAKERS.get(_default_style, SPEAKERS["normal"])

# --- TTS設定 ---
MAX_TEXT_LENGTH: int = 100  # デフォルト最大文字数（これを超える文字は「以下省略」に切り捨て）
DEFAULT_SPEED: float = 1.0  # デフォルト読み上げ速度（0.5〜2.0）

# --- ランタイム状態（ギルドIDごとに管理） ---
# guild_id -> text_channel_id: どのテキストchを読み上げ対象にするか
TTS_CHANNEL_MAP: dict[int, int] = {}

# guild_id -> speaker_id: ギルドごとの声スタイル
GUILD_SPEAKER_MAP: dict[int, int] = {}

# guild_id -> speed: ギルドごとの読み上げ速度
GUILD_SPEED_MAP: dict[int, float] = {}

# guild_id -> max_length: ギルドごとの最大文字数
GUILD_MAX_LENGTH_MAP: dict[int, int] = {}
