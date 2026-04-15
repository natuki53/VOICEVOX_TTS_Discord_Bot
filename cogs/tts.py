"""TTS読み上げCog - on_messageハンドラとテキスト前処理"""

from collections import Counter
import logging
import re
import time

import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)

# テキスト前処理用の正規表現
RE_URL = re.compile(r"https?://\S+")
RE_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
RE_MENTION = re.compile(r"<@!?\d+>|<@&\d+>|<#\d+>")
RE_WHITESPACE = re.compile(r"\s+")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml"}


def preprocess_text(text: str, max_length: int = config.MAX_TEXT_LENGTH) -> str:
    """
    Discord メッセージテキストをTTS用に前処理する。

    - URLを「URL省略」に置換
    - カスタム絵文字を除去
    - メンション・チャンネルリンクを除去
    - 余分な空白・改行を整理
    - max_length を超える場合は切り捨て
    """
    # URL置換
    text = RE_URL.sub("URL省略", text)

    # カスタム絵文字除去
    text = RE_CUSTOM_EMOJI.sub("", text)

    # メンション除去
    text = RE_MENTION.sub("", text)

    # 空白・改行を整理
    text = RE_WHITESPACE.sub(" ", text).strip()

    # 長文切り捨て
    if len(text) > max_length:
        text = text[:max_length] + "、以下省略"

    return text


def classify_attachment(attachment: discord.Attachment) -> str:
    """添付ファイルを読み上げ用の種類ラベルに分類する。"""
    content_type = (attachment.content_type or "").lower()
    filename = attachment.filename.lower()

    if content_type.startswith("image/") or any(
        filename.endswith(ext) for ext in IMAGE_EXTENSIONS
    ):
        return "画像ファイル"
    if content_type.startswith("audio/") or any(
        filename.endswith(ext) for ext in AUDIO_EXTENSIONS
    ):
        return "音声ファイル"
    if content_type.startswith("video/") or any(
        filename.endswith(ext) for ext in VIDEO_EXTENSIONS
    ):
        return "動画ファイル"
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        return "PDFファイル"
    if content_type.startswith("text/") or any(
        filename.endswith(ext) for ext in TEXT_EXTENSIONS
    ):
        return "テキストファイル"
    return "ファイル"


def summarize_attachments(attachments: list[discord.Attachment]) -> str:
    """添付ファイル一覧を短い読み上げ文に変換する。"""
    if not attachments:
        return ""

    labels = [classify_attachment(attachment) for attachment in attachments]
    counts = Counter(labels)
    parts = [
        f"{label}{count}件" if count > 1 else f"{label}1件"
        for label, count in counts.items()
    ]
    return "、".join(parts) + "を添付"


class TtsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_sender_by_guild: dict[int, tuple[int, float]] = {}

    def _get_connected_vc(self, guild: discord.Guild) -> discord.VoiceClient | None:
        """接続済みVoiceClientを返す（未接続の古い参照は無視）。"""
        vc = guild.voice_client
        if vc and vc.is_connected() and vc.channel:
            return vc

        for candidate in self.bot.voice_clients:
            if (
                candidate.guild.id == guild.id
                and candidate.is_connected()
                and candidate.channel
            ):
                return candidate
        return None

    def _should_prepend_sender_name(self, guild_id: int, user_id: int) -> bool:
        now = time.monotonic()
        last_sender = self._last_sender_by_guild.get(guild_id)
        self._last_sender_by_guild[guild_id] = (user_id, now)

        if last_sender is None:
            return True

        last_user_id, last_message_at = last_sender
        if last_user_id != user_id:
            return True

        return (now - last_message_at) >= config.SENDER_NAME_REPEAT_INTERVAL_SECONDS

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # --- ガード条件 ---

        # Bot自身・他のBotのメッセージは無視
        if message.author.bot:
            return

        # DM（サーバー外）は無視
        if message.guild is None:
            return

        guild = message.guild

        # このギルドで読み上げが有効でなければ無視
        channel_id = config.TTS_CHANNEL_MAP.get(guild.id)
        if channel_id is None:
            return

        # 読み上げ対象チャンネル以外は無視
        if message.channel.id != channel_id:
            return

        # VCに接続していなければ無視
        vc = self._get_connected_vc(guild)
        if not vc:
            return

        # 本文・添付の両方が空なら無視
        if not message.content.strip() and not message.attachments:
            return

        # --- テキスト前処理 ---
        max_length = config.GUILD_MAX_LENGTH_MAP.get(guild.id, config.MAX_TEXT_LENGTH)
        cleaned = preprocess_text(message.content, max_length)
        attachment_summary = summarize_attachments(message.attachments)

        # 前処理後も読み上げ対象が無ければ無視（URL・絵文字のみ等）
        read_parts = [part for part in (cleaned, attachment_summary) if part]
        if not read_parts:
            return

        read_sender_name = config.GUILD_READ_SENDER_NAME_MAP.get(
            guild.id,
            config.DEFAULT_READ_SENDER_NAME,
        )
        if read_sender_name and self._should_prepend_sender_name(guild.id, message.author.id):
            display_name = message.author.display_name
            read_text = f"{display_name}。{'。'.join(read_parts)}"
        else:
            read_text = "。".join(read_parts)

        # --- TTS合成ジョブをキューに追加（合成は再生ワーカーと並走） ---
        user_speakers = config.GUILD_USER_SPEAKER_MAP.get(guild.id, {})
        speaker_id = user_speakers.get(
            message.author.id,
            config.GUILD_SPEAKER_MAP.get(guild.id, config.DEFAULT_SPEAKER_ID),
        )
        user_speeds = config.GUILD_USER_SPEED_MAP.get(guild.id, {})
        speed = user_speeds.get(
            message.author.id,
            config.GUILD_SPEED_MAP.get(guild.id, config.DEFAULT_SPEED),
        )
        try:
            await self.bot.audio_queue.enqueue(guild.id, read_text, speaker_id, speed, vc)
        except Exception as e:
            logger.error("予期しないエラー (on_message): %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TtsCog(bot))
