"""ボイスチャンネル管理Cog - /join, /leave, /speaker, /speed, /maxlength, /status + 入退室アナウンス"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from services.voicevox import VoicevoxError

logger = logging.getLogger(__name__)

SPEAKER_CHOICES = [
    app_commands.Choice(name="ノーマル", value="normal"),
    app_commands.Choice(name="あまあま", value="amaama"),
    app_commands.Choice(name="ツンツン", value="tsuntsun"),
    app_commands.Choice(name="セクシー", value="sexy"),
]

STYLE_NAMES = {
    "normal": "ノーマル",
    "amaama": "あまあま",
    "tsuntsun": "ツンツン",
    "sexy": "セクシー",
}
IDLE_DISCONNECT_SECONDS = 60


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._idle_disconnect_tasks: dict[int, asyncio.Task[None]] = {}

    def cog_unload(self) -> None:
        for task in self._idle_disconnect_tasks.values():
            if not task.done():
                task.cancel()
        self._idle_disconnect_tasks.clear()

    # -----------------------------------------------------------------------
    # ユーティリティ
    # -----------------------------------------------------------------------

    def _get_connected_vc(self, guild: discord.Guild | None) -> discord.VoiceClient | None:
        """接続済みのVoiceClientのみ返す（未接続の古い参照は無視）。"""
        if guild is None:
            return None

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

    def _get_bot_voice_channel(
        self,
        guild: discord.Guild,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        """VoiceClientが取れない場合のフォールバックとしてBot自身のVC状態を参照する。"""
        if self.bot.user is None:
            return None

        bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None or bot_member.voice is None:
            return None

        channel = bot_member.voice.channel
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return channel
        return None

    async def _get_or_recover_vc(
        self,
        guild: discord.Guild,
    ) -> discord.VoiceClient | None:
        """
        接続済みVoiceClientを取得する。

        BotがVCにいるのにVoiceClient参照が同期ずれしている場合は、
        1回だけ再接続で回復を試みる。
        """
        vc = self._get_connected_vc(guild)
        if vc:
            return vc

        bot_channel = self._get_bot_voice_channel(guild)
        if bot_channel is None:
            return None

        logger.warning(
            "VoiceClientの同期ずれを検出しました。再接続で回復を試行します (guild=%d, channel=%s)",
            guild.id,
            bot_channel.name,
        )

        # 同ギルドの古いVoiceClient参照を掃除する
        for stale in list(self.bot.voice_clients):
            if stale.guild.id != guild.id:
                continue
            try:
                await stale.disconnect(force=True)
            except Exception as e:
                logger.debug("古いVoiceClientの切断をスキップしました (guild=%d): %s", guild.id, e)

        try:
            recovered = await bot_channel.connect(timeout=15.0, reconnect=True)
            if recovered.is_connected():
                return recovered
        except asyncio.TimeoutError:
            logger.warning("VoiceClient再接続がタイムアウトしました (guild=%d)", guild.id)
        except discord.ClientException as e:
            logger.warning("VoiceClient再接続に失敗しました (guild=%d): %s", guild.id, e)
        except Exception as e:
            logger.error("VoiceClient再接続中に予期しないエラーが発生しました (guild=%d): %s", guild.id, e)

        return self._get_connected_vc(guild)

    async def _ensure_guild(
        self,
        interaction: discord.Interaction,
    ) -> discord.Guild | None:
        guild = interaction.guild
        if guild is None:
            await self._send_once(
                interaction,
                "このコマンドはサーバー内でのみ利用できます。",
                ephemeral=True,
            )
            return None
        return guild

    def _get_speaker_id(self, guild_id: int) -> int:
        return config.GUILD_SPEAKER_MAP.get(guild_id, config.DEFAULT_SPEAKER_ID)

    def _get_speed(self, guild_id: int) -> float:
        return config.GUILD_SPEED_MAP.get(guild_id, config.DEFAULT_SPEED)

    def _has_human_member(
        self,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        return any(not member.bot for member in channel.members)

    def _clear_guild_runtime(self, guild_id: int) -> None:
        self.bot.audio_queue.cleanup(guild_id)
        config.TTS_CHANNEL_MAP.pop(guild_id, None)
        config.GUILD_SPEAKER_MAP.pop(guild_id, None)
        config.GUILD_SPEED_MAP.pop(guild_id, None)
        config.GUILD_MAX_LENGTH_MAP.pop(guild_id, None)

    def _cancel_idle_disconnect(self, guild_id: int) -> None:
        task = self._idle_disconnect_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _resolve_read_channel_mention(
        self,
        guild_id: int,
        fallback_channel_id: int | None = None,
    ) -> str:
        channel_id = config.TTS_CHANNEL_MAP.get(guild_id, fallback_channel_id)
        return f"<#{channel_id}>" if channel_id else "未設定"

    def _build_disconnect_embed(
        self,
        voice_channel_name: str,
        read_channel_mention: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="ずんだもん読み上げBot - 切断しました",
            color=discord.Color.red(),
        )
        embed.add_field(name="ボイスチャンネル", value=voice_channel_name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=read_channel_mention, inline=False)
        return embed

    async def _defer_once(self, interaction: discord.Interaction) -> bool:
        """Interactionを一度だけdeferする。重複応答時はFalseを返す。"""
        if interaction.response.is_done():
            return False

        try:
            await interaction.response.defer()
            return True
        except discord.NotFound as e:
            if e.code == 10062:
                logger.info("defer前にInteractionが失効しました (cmd=%s, interaction_id=%s)", interaction.command, interaction.id)
                return False
            raise
        except discord.HTTPException as e:
            if e.code == 40060:
                logger.warning("Interactionは既にack済みです (cmd=%s, interaction_id=%s)", interaction.command, interaction.id)
                return False
            raise

    async def _send_once(
        self,
        interaction: discord.Interaction,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        ephemeral: bool = False,
    ) -> None:
        """Interactionへ1回だけ応答を返す（競合時はfollowupへフォールバック）。"""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                )
            else:
                await interaction.response.send_message(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                )
        except discord.NotFound as e:
            if e.code == 10062:
                logger.info("Interaction応答時にトークンが失効しました (cmd=%s, interaction_id=%s)", interaction.command, interaction.id)
                return
            raise
        except discord.HTTPException as e:
            if e.code == 40060:
                await interaction.followup.send(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                )
                return
            raise

    def _schedule_idle_disconnect(self, guild: discord.Guild) -> None:
        guild_id = guild.id
        self._cancel_idle_disconnect(guild_id)
        self._idle_disconnect_tasks[guild_id] = asyncio.create_task(
            self._idle_disconnect_after_delay(guild_id)
        )

    async def _idle_disconnect_after_delay(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return

            vc = self._get_connected_vc(guild)
            if not vc:
                return

            if self._has_human_member(vc.channel):
                return

            logger.info(
                "VC無人のため自動切断します (guild=%d, timeout=%ds)",
                guild_id,
                IDLE_DISCONNECT_SECONDS,
            )
            voice_channel_name = vc.channel.name
            read_channel_mention = self._resolve_read_channel_mention(guild_id)

            tts_channel = None
            tts_channel_id = config.TTS_CHANNEL_MAP.get(guild_id)
            if tts_channel_id is not None:
                tts_channel = guild.get_channel(tts_channel_id)
            if isinstance(tts_channel, discord.abc.Messageable):
                try:
                    await tts_channel.send(
                        embed=self._build_disconnect_embed(
                            voice_channel_name,
                            read_channel_mention,
                        )
                    )
                except discord.HTTPException as e:
                    logger.warning("自動切断通知の送信に失敗しました (guild=%d): %s", guild_id, e)

            await vc.disconnect()
            if not vc.is_connected():
                self._clear_guild_runtime(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("自動切断タスクでエラーが発生しました (guild=%d): %s", guild_id, e)
        finally:
            task = self._idle_disconnect_tasks.get(guild_id)
            if task is asyncio.current_task():
                self._idle_disconnect_tasks.pop(guild_id, None)

    async def _speak(
        self,
        text: str,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
    ) -> None:
        """テキストをTTSで読み上げキューに追加する"""
        if self.bot.voicevox is None:
            return

        try:
            speaker_id = self._get_speaker_id(guild.id)
            speed = self._get_speed(guild.id)
            wav = await self.bot.voicevox.tts(text, speaker_id, speed)
            await self.bot.audio_queue.enqueue(guild.id, wav, voice_client)
        except VoicevoxError as e:
            logger.warning("TTS合成エラー: %s", e)
        except Exception as e:
            logger.error("予期しないエラー (_speak): %s", e)

    # -----------------------------------------------------------------------
    # スラッシュコマンド
    # -----------------------------------------------------------------------

    @app_commands.command(name="join", description="ボイスチャンネルに参加して読み上げを開始します")
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        if not await self._defer_once(interaction):
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("ボイスチャンネルに参加してから呼び出してください。")
            return

        target_vc = interaction.user.voice.channel
        vc = await self._get_or_recover_vc(guild)

        channel_mention = (
            interaction.channel.mention if interaction.channel else f"<#{interaction.channel_id}>"
        )

        if vc and vc.channel == target_vc:
            self._cancel_idle_disconnect(guild.id)
            config.TTS_CHANNEL_MAP[guild.id] = interaction.channel_id
            await interaction.followup.send(
                f"すでに {target_vc.name} にいます。読み上げチャンネルを {channel_mention} に変更しました。"
            )
            return

        try:
            if vc:
                await vc.move_to(target_vc)
            else:
                vc = await target_vc.connect(timeout=15.0, reconnect=True)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "ボイスチャンネルへの接続がタイムアウトしました。時間をおいて再試行してください。"
            )
            return
        except Exception as e:
            logger.error("VC接続エラー: %s", e)
            await interaction.followup.send(
                "ボイスチャンネルへの接続に失敗しました。"
            )
            return

        self._cancel_idle_disconnect(guild.id)
        config.TTS_CHANNEL_MAP[guild.id] = interaction.channel_id

        speaker_id = self._get_speaker_id(guild.id)
        style_map = {v: k for k, v in config.SPEAKERS.items()}
        style_key = style_map.get(speaker_id, "normal")
        style_display = STYLE_NAMES.get(style_key, style_key)
        speed = self._get_speed(guild.id)
        max_length = config.GUILD_MAX_LENGTH_MAP.get(guild.id, config.MAX_TEXT_LENGTH)

        embed = discord.Embed(title="ずんだもん読み上げBot - 接続しました", color=discord.Color.green())
        embed.add_field(name="ボイスチャンネル", value=target_vc.name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=channel_mention, inline=False)
        embed.add_field(name="声スタイル", value=style_display, inline=True)
        embed.add_field(name="読み上げ速度", value=str(speed), inline=True)
        embed.add_field(name="最大文字数", value=f"{max_length}文字", inline=True)

        await interaction.followup.send(embed=embed)
        await self._speak("接続しました。", guild, vc)

    @app_commands.command(name="leave", description="ボイスチャンネルから退出して読み上げを停止します")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        if not await self._defer_once(interaction):
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            self._cancel_idle_disconnect(guild.id)
            self._clear_guild_runtime(guild.id)
            await interaction.followup.send("すでに切断済みです。", ephemeral=True)
            return

        voice_channel_name = vc.channel.name
        read_channel_mention = self._resolve_read_channel_mention(
            guild.id,
            interaction.channel_id,
        )
        await self._speak("切断します。またね。", guild, vc)

        try:
            q = self.bot.audio_queue._queues.get(guild.id)
            if q:
                await asyncio.wait_for(q.join(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        self._cancel_idle_disconnect(guild.id)
        self._clear_guild_runtime(guild.id)

        try:
            await vc.disconnect()
        except Exception as e:
            logger.warning("VC切断時にエラーが発生しました (guild=%d): %s", guild.id, e)

        await interaction.followup.send(
            embed=self._build_disconnect_embed(
                voice_channel_name,
                read_channel_mention,
            )
        )

    @app_commands.command(name="speaker", description="ずんだもんの声スタイルを変更します")
    @app_commands.guild_only()
    @app_commands.describe(style="声スタイルを選んでください")
    @app_commands.choices(style=SPEAKER_CHOICES)
    async def speaker(self, interaction: discord.Interaction, style: str) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_once(
                interaction,
                "読み上げ中ではありません。先に /join を実行してください。",
                ephemeral=True,
            )
            return

        speaker_id = config.SPEAKERS.get(style, config.DEFAULT_SPEAKER_ID)
        config.GUILD_SPEAKER_MAP[guild.id] = speaker_id
        style_display = STYLE_NAMES.get(style, style)

        await self._send_once(
            interaction,
            f"声スタイルを **{style_display}** に変更しました。"
        )
        await self._speak(f"声スタイルを{style_display}に変更しました。", guild, vc)

    @app_commands.command(name="speed", description="読み上げ速度を変更します（0.5〜2.0、デフォルト: 1.0）")
    @app_commands.guild_only()
    @app_commands.describe(value="読み上げ速度（0.5=ゆっくり / 1.0=普通 / 2.0=はやい）")
    async def speed(self, interaction: discord.Interaction, value: float) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_once(
                interaction,
                "読み上げ中ではありません。先に /join を実行してください。",
                ephemeral=True,
            )
            return

        if not (0.5 <= value <= 2.0):
            await self._send_once(
                interaction,
                "速度は 0.5〜2.0 の範囲で指定してください。",
                ephemeral=True,
            )
            return

        config.GUILD_SPEED_MAP[guild.id] = value
        message = f"読み上げ速度を {value} に変更しました。"
        await self._send_once(
            interaction,
            message
        )
        await self._speak(message, guild, vc)

    @app_commands.command(name="maxlength", description="読み上げる最大文字数を変更します（10〜500）")
    @app_commands.guild_only()
    @app_commands.describe(length="最大文字数（デフォルト: 100）")
    async def maxlength(self, interaction: discord.Interaction, length: int) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_once(
                interaction,
                "読み上げ中ではありません。先に /join を実行してください。",
                ephemeral=True,
            )
            return

        if not (10 <= length <= 500):
            await self._send_once(
                interaction,
                "文字数は 10〜500 の範囲で指定してください。",
                ephemeral=True,
            )
            return

        config.GUILD_MAX_LENGTH_MAP[guild.id] = length
        await self._send_once(
            interaction,
            f"最大読み上げ文字数を **{length}文字** に変更しました。"
        )

    @app_commands.command(name="about", description="このBotについての情報を表示します")
    async def about(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="ずんだもん読み上げBot",
            description="VOICEVOXのずんだもん音声でDiscordのテキストを読み上げるBotなのだ。",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="使い方",
            value="`/join` → ボイスチャンネルに参加\n`/leave` → 退出\n`/speaker` → 声スタイル変更\n`/speed` → 速度変更\n`/maxlength` → 最大文字数変更\n`/status` → 現在の設定確認",
            inline=False,
        )
        embed.add_field(
            name="クレジット",
            value=(
                "**VOICEVOX** - 音声合成エンジン\n"
                "[https://voicevox.hiroshiba.jp/](https://voicevox.hiroshiba.jp/)\n\n"
                "**ずんだもん** - CV: 四国めたん・ずんだもん（VOICEVOX）\n"
                "© Hiroshiba Kazuyuki\n\n"
                "**discord.py** - Discord API ライブラリ\n"
                "[https://github.com/Rapptz/discord.py](https://github.com/Rapptz/discord.py)"
            ),
            inline=False,
        )
        embed.set_footer(text="Powered by VOICEVOX & discord.py")
        await self._send_once(interaction, embed=embed)

    @app_commands.command(name="status", description="現在の読み上げBot状態を確認します")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_once(
                interaction,
                "現在ボイスチャンネルに参加していません。",
                ephemeral=True,
            )
            return

        channel_id = config.TTS_CHANNEL_MAP.get(guild.id)
        channel_mention = f"<#{channel_id}>" if channel_id else "未設定"

        speaker_id = self._get_speaker_id(guild.id)
        style_map = {v: k for k, v in config.SPEAKERS.items()}
        style_key = style_map.get(speaker_id, "normal")
        style_display = STYLE_NAMES.get(style_key, style_key)

        speed = self._get_speed(guild.id)
        max_length = config.GUILD_MAX_LENGTH_MAP.get(guild.id, config.MAX_TEXT_LENGTH)

        embed = discord.Embed(title="ずんだもん読み上げBot - 状態", color=discord.Color.green())
        embed.add_field(name="ボイスチャンネル", value=vc.channel.name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=channel_mention, inline=False)
        embed.add_field(name="声スタイル", value=style_display, inline=True)
        embed.add_field(name="読み上げ速度", value=str(speed), inline=True)
        embed.add_field(name="最大文字数", value=f"{max_length}文字", inline=True)

        await self._send_once(interaction, embed=embed)

    # -----------------------------------------------------------------------
    # VC入退室アナウンス
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild

        if member == self.bot.user:
            if before.channel is not None and after.channel is None:
                logger.info("BotがVCから切断されました (guild=%d)", guild.id)
                self._cancel_idle_disconnect(guild.id)
                self._clear_guild_runtime(guild.id)
            elif after.channel is not None:
                self._cancel_idle_disconnect(guild.id)
            return

        vc = self._get_connected_vc(guild)
        if not vc:
            return

        bot_channel = vc.channel

        if before.channel != bot_channel and after.channel == bot_channel:
            self._cancel_idle_disconnect(guild.id)
            await self._speak(f"{member.display_name}が入室しました。", guild, vc)
        elif before.channel == bot_channel and after.channel != bot_channel:
            await self._speak(f"{member.display_name}が退室しました。", guild, vc)
            if not self._has_human_member(bot_channel):
                self._schedule_idle_disconnect(guild)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))
