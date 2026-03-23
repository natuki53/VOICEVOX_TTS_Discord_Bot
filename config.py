import os
from dotenv import load_dotenv

load_dotenv()

# --- Discord ---
DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]

# --- VOICEVOX ---
VOICEVOX_BASE_URL: str = os.getenv("VOICEVOX_URL", "http://localhost:50021")

_command_guild_id = os.getenv("COMMAND_GUILD_ID")
if _command_guild_id:
    try:
        COMMAND_GUILD_ID: int | None = int(_command_guild_id)
    except ValueError:
        COMMAND_GUILD_ID = None
else:
    COMMAND_GUILD_ID = None

# 旧環境変数(DEFAULT_SPEAKER_STYLE)との互換マップ
LEGACY_STYLE_TO_SPEAKER_ID: dict[str, int] = {
    "normal":   3,
    "amaama":   1,
    "tsuntsun": 7,
    "sexy":     5,
}

_default_speaker_id = os.getenv("DEFAULT_SPEAKER_ID")
if _default_speaker_id is not None:
    try:
        DEFAULT_SPEAKER_ID = int(_default_speaker_id)
    except ValueError:
        DEFAULT_SPEAKER_ID = LEGACY_STYLE_TO_SPEAKER_ID["normal"]
else:
    _default_style = os.getenv("DEFAULT_SPEAKER_STYLE", "normal")
    DEFAULT_SPEAKER_ID = LEGACY_STYLE_TO_SPEAKER_ID.get(
        _default_style,
        LEGACY_STYLE_TO_SPEAKER_ID["normal"],
    )

# --- TTS設定 ---
MAX_TEXT_LENGTH: int = 100  # デフォルト最大文字数（これを超える文字は「以下省略」に切り捨て）
DEFAULT_SPEED: float = 1.0  # デフォルト読み上げ速度（0.5〜2.0）

# --- ランタイム状態（ギルドIDごとに管理） ---
# guild_id -> text_channel_id: どのテキストchを読み上げ対象にするか
TTS_CHANNEL_MAP: dict[int, int] = {}

# guild_id -> speaker_id: ギルド全体のデフォルト話者ID
GUILD_SPEAKER_MAP: dict[int, int] = {}

# guild_id -> {user_id -> speaker_id}: ユーザー個別の話者ID
GUILD_USER_SPEAKER_MAP: dict[int, dict[int, int]] = {}

# guild_id -> speed: ギルドごとの読み上げ速度
GUILD_SPEED_MAP: dict[int, float] = {}

# guild_id -> max_length: ギルドごとの最大文字数
GUILD_MAX_LENGTH_MAP: dict[int, int] = {}
