"""ボイスチャンネル管理Cog - /join, /leave, /speaker, /speakerall, /style, /styleall, /speed, /maxlength, /status + 入退室アナウンス"""

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from services.state_store import save_runtime_state
from services.voicevox import VoicevoxError

logger = logging.getLogger(__name__)

IDLE_DISCONNECT_SECONDS = 60
MAX_AUTOCOMPLETE_CHOICES = 25
SPEAKER_CACHE_TTL_SECONDS = 60
STYLE_CHOICES = [
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
NOTICE_COLORS = {
    "info": discord.Color.blurple(),
    "success": discord.Color.green(),
    "warning": discord.Color.orange(),
    "error": discord.Color.red(),
}
NOTICE_PREFIX = {
    "info": "",
    "success": "",
    "warning": "⚠ ",
    "error": "❌ ",
}


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._idle_disconnect_tasks: dict[int, asyncio.Task[None]] = {}
        self._speaker_label_cache: dict[int, str] = {}
        self._speaker_options_cache: list[tuple[int, str]] = []
        self._speaker_cache_updated_at = 0.0
        self._speaker_cache_lock = asyncio.Lock()

    @staticmethod
    def _pick_representative_style_id(styles: list[dict]) -> int | None:
        """話者選択用に代表スタイルIDを1つ選ぶ。"""
        talk_styles = [
            style
            for style in styles
            if style.get("type") == "talk" and isinstance(style.get("id"), int)
        ]
        for style in talk_styles:
            if str(style.get("name", "")).strip() == "ノーマル":
                return style["id"]
        if talk_styles:
            return talk_styles[0]["id"]

        for style in styles:
            style_id = style.get("id")
            if isinstance(style_id, int):
                return style_id
        return None

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
            await self._send_notice(
                interaction,
                title="このコマンドはサーバー内でのみ利用できます",
                kind="error",
                ephemeral=True,
            )
            return None
        return guild

    def _get_speaker_id(self, guild_id: int) -> int:
        return config.GUILD_SPEAKER_MAP.get(guild_id, config.DEFAULT_SPEAKER_ID)

    def _get_user_speaker_id(self, guild_id: int, user_id: int) -> int:
        user_speakers = config.GUILD_USER_SPEAKER_MAP.get(guild_id, {})
        return user_speakers.get(user_id, self._get_speaker_id(guild_id))

    def _set_user_speaker_id(self, guild_id: int, user_id: int, speaker_id: int) -> None:
        user_speakers = config.GUILD_USER_SPEAKER_MAP.setdefault(guild_id, {})
        user_speakers[user_id] = speaker_id
        self._persist_runtime_state()

    def _persist_runtime_state(self) -> None:
        save_runtime_state()

    def _get_speed(self, guild_id: int) -> float:
        return config.GUILD_SPEED_MAP.get(guild_id, config.DEFAULT_SPEED)

    async def _refresh_speaker_cache(self, *, force: bool = False) -> None:
        if self.bot.voicevox is None:
            return

        now = time.monotonic()
        if (
            not force
            and self._speaker_options_cache
            and (now - self._speaker_cache_updated_at) < SPEAKER_CACHE_TTL_SECONDS
        ):
            return

        async with self._speaker_cache_lock:
            now = time.monotonic()
            if (
                not force
                and self._speaker_options_cache
                and (now - self._speaker_cache_updated_at) < SPEAKER_CACHE_TTL_SECONDS
            ):
                return

            try:
                speakers = await self.bot.voicevox.list_speakers()
            except VoicevoxError as e:
                logger.warning("話者一覧の取得に失敗しました: %s", e)
                return
            except Exception as e:
                logger.error("話者一覧の取得中に予期しないエラーが発生しました: %s", e)
                return

            labels: dict[int, str] = {}
            options: list[tuple[int, str]] = []
            for speaker in speakers:
                if not isinstance(speaker, dict):
                    continue

                speaker_name = str(speaker.get("name", "不明話者"))
                styles = speaker.get("styles")
                if not isinstance(styles, list):
                    continue

                representative_style_id = self._pick_representative_style_id(styles)
                if representative_style_id is not None:
                    options.append((representative_style_id, speaker_name))

                for style in styles:
                    if not isinstance(style, dict):
                        continue

                    style_id = style.get("id")
                    if not isinstance(style_id, int):
                        continue

                    labels[style_id] = speaker_name

            self._speaker_label_cache = labels
            self._speaker_options_cache = options
            self._speaker_cache_updated_at = time.monotonic()

    def _get_speaker_display_name(self, speaker_id: int) -> str:
        label = self._speaker_label_cache.get(speaker_id)
        if label:
            return label
        return "不明な話者"

    def _get_speaker_read_name(self, speaker_id: int) -> str:
        label = self._speaker_label_cache.get(speaker_id)
        if label:
            return label
        return "不明な話者"

    async def speaker_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        await self._refresh_speaker_cache()

        query = current.casefold().strip()
        choices: list[app_commands.Choice[str]] = []
        for speaker_id, label in self._speaker_options_cache:
            display = label
            if query and query not in display.casefold():
                continue
            choices.append(
                app_commands.Choice(name=display[:100], value=str(speaker_id))
            )
            if len(choices) >= MAX_AUTOCOMPLETE_CHOICES:
                break
        return choices

    def _has_human_member(
        self,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        return any(not member.bot for member in channel.members)

    def _clear_guild_runtime(self, guild_id: int) -> None:
        self.bot.audio_queue.cleanup(guild_id)
        config.TTS_CHANNEL_MAP.pop(guild_id, None)
        config.GUILD_SPEAKER_MAP.pop(guild_id, None)
        config.GUILD_USER_SPEAKER_MAP.pop(guild_id, None)
        config.GUILD_SPEED_MAP.pop(guild_id, None)
        config.GUILD_MAX_LENGTH_MAP.pop(guild_id, None)
        self._persist_runtime_state()

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
        embed = self._build_notice_embed(title="切断しました", kind="error")
        embed.add_field(name="ボイスチャンネル", value=voice_channel_name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=read_channel_mention, inline=False)
        return embed

    def _build_notice_embed(
        self,
        title: str,
        description: str | None = None,
        *,
        kind: str = "info",
    ) -> discord.Embed:
        color = NOTICE_COLORS.get(kind, NOTICE_COLORS["info"])
        prefix = NOTICE_PREFIX.get(kind, "")
        embed = discord.Embed(title=f"{prefix}{title}", color=color)
        if description:
            embed.description = description
        return embed

    async def _send_notice(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        description: str | None = None,
        kind: str = "info",
        ephemeral: bool = False,
    ) -> None:
        await self._send_once(
            interaction,
            embed=self._build_notice_embed(
                title=title,
                description=description,
                kind=kind,
            ),
            ephemeral=ephemeral,
        )

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
        speaker_id: int | None = None,
    ) -> None:
        """テキストをTTSで読み上げキューに追加する"""
        if self.bot.voicevox is None:
            return

        try:
            if speaker_id is None:
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
            await self._send_notice(
                interaction,
                title="ボイスチャンネルに参加してください",
                description="/join を実行する前に、参加するチャンネルを選択してください。",
                kind="warning",
            )
            return

        target_vc = interaction.user.voice.channel
        vc = await self._get_or_recover_vc(guild)
        current_read_channel_id = config.TTS_CHANNEL_MAP.get(guild.id)

        channel_mention = (
            interaction.channel.mention if interaction.channel else f"<#{interaction.channel_id}>"
        )

        if vc and vc.channel == target_vc:
            self._cancel_idle_disconnect(guild.id)
            if current_read_channel_id == interaction.channel_id:
                await self._send_notice(
                    interaction,
                    title=f"すでに {target_vc.name} に接続しています",
                    kind="info",
                )
                return
            config.TTS_CHANNEL_MAP[guild.id] = interaction.channel_id
            self._persist_runtime_state()
            await self._send_notice(
                interaction,
                title="読み上げチャンネルを変更しました",
                description=f"接続中のボイスチャンネル: **{target_vc.name}**\n読み上げチャンネル: {channel_mention}",
                kind="success",
            )
            return

        moved = vc is not None and vc.channel != target_vc
        try:
            if vc:
                await vc.move_to(target_vc)
            else:
                vc = await target_vc.connect(timeout=15.0, reconnect=True)
        except asyncio.TimeoutError:
            await self._send_notice(
                interaction,
                title="ボイスチャンネルへの接続がタイムアウトしました",
                description="時間をおいて再試行してください。",
                kind="error",
            )
            return
        except Exception as e:
            logger.error("VC接続エラー: %s", e)
            await self._send_notice(
                interaction,
                title="ボイスチャンネルへの接続に失敗しました",
                description="接続先チャンネルやBot権限を確認してください。",
                kind="error",
            )
            return

        self._cancel_idle_disconnect(guild.id)
        config.TTS_CHANNEL_MAP[guild.id] = interaction.channel_id
        self._persist_runtime_state()

        await self._refresh_speaker_cache()
        speaker_id = self._get_user_speaker_id(guild.id, interaction.user.id)
        speaker_display = self._get_speaker_display_name(speaker_id)
        speed = self._get_speed(guild.id)
        max_length = config.GUILD_MAX_LENGTH_MAP.get(guild.id, config.MAX_TEXT_LENGTH)

        title = "ボイスチャンネルを移動しました" if moved else "ボイスチャンネルに接続しました"
        embed = self._build_notice_embed(title=title, kind="success")
        embed.add_field(name="ボイスチャンネル", value=target_vc.name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=channel_mention, inline=False)
        embed.add_field(name="話者", value=speaker_display, inline=True)
        embed.add_field(name="読み上げ速度", value=str(speed), inline=True)
        embed.add_field(name="最大文字数", value=f"{max_length}文字", inline=True)

        await self._send_once(interaction, embed=embed)
        await self._speak("移動しました。" if moved else "接続しました。", guild, vc)

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
            await self._send_notice(
                interaction,
                title="すでに切断済みです",
                kind="info",
                ephemeral=True,
            )
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

        await self._send_once(
            interaction,
            embed=self._build_disconnect_embed(
                voice_channel_name,
                read_channel_mention,
            ),
        )

    @app_commands.command(name="speaker", description="あなたの話者を変更します")
    @app_commands.guild_only()
    @app_commands.describe(speaker="あなたの話者を選んでください（候補から選択）")
    @app_commands.autocomplete(speaker=speaker_autocomplete)
    async def speaker(self, interaction: discord.Interaction, speaker: str) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        try:
            speaker_id = int(speaker)
        except ValueError:
            await self._send_notice(
                interaction,
                title="話者の指定が不正です",
                description="候補から選択してください。",
                kind="error",
                ephemeral=True,
            )
            return

        await self._refresh_speaker_cache(force=True)
        if self._speaker_options_cache and speaker_id not in self._speaker_label_cache:
            await self._send_notice(
                interaction,
                title="指定した話者は利用できません",
                description="現在のVOICEVOX Engineに存在しない可能性があります。",
                kind="error",
                ephemeral=True,
            )
            return

        self._set_user_speaker_id(guild.id, interaction.user.id, speaker_id)
        speaker_display = self._get_speaker_display_name(speaker_id)
        speaker_read_name = self._get_speaker_read_name(speaker_id)
        user_name = interaction.user.display_name

        await self._send_notice(
            interaction,
            title=f"{user_name}の話者を変更しました",
            description=f"現在の{user_name}の話者: **{speaker_display}**",
            kind="success",
        )
        await self._speak(
            f"{user_name}の話者を{speaker_read_name}に変更しました。",
            guild,
            vc,
            speaker_id=speaker_id,
        )

    @app_commands.command(name="speakerall", description="サーバー全体のデフォルト話者を変更します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(speaker="全体デフォルト話者を選んでください（候補から選択）")
    @app_commands.autocomplete(speaker=speaker_autocomplete)
    async def speakerall(self, interaction: discord.Interaction, speaker: str) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        try:
            speaker_id = int(speaker)
        except ValueError:
            await self._send_notice(
                interaction,
                title="話者の指定が不正です",
                description="候補から選択してください。",
                kind="error",
                ephemeral=True,
            )
            return

        await self._refresh_speaker_cache(force=True)
        if self._speaker_options_cache and speaker_id not in self._speaker_label_cache:
            await self._send_notice(
                interaction,
                title="指定した話者は利用できません",
                description="現在のVOICEVOX Engineに存在しない可能性があります。",
                kind="error",
                ephemeral=True,
            )
            return

        config.GUILD_SPEAKER_MAP[guild.id] = speaker_id
        self._persist_runtime_state()
        speaker_display = self._get_speaker_display_name(speaker_id)
        speaker_read_name = self._get_speaker_read_name(speaker_id)

        await self._send_notice(
            interaction,
            title="全体デフォルト話者を変更しました",
            description=f"現在の全体デフォルト話者: **{speaker_display}**",
            kind="success",
        )
        await self._speak(
            f"全体デフォルト話者を{speaker_read_name}に変更しました。",
            guild,
            vc,
            speaker_id=speaker_id,
        )

    @app_commands.command(name="style", description="あなたの声スタイル（互換プリセット）を変更します")
    @app_commands.guild_only()
    @app_commands.describe(style="互換プリセットを選んでください")
    @app_commands.choices(style=STYLE_CHOICES)
    async def style(self, interaction: discord.Interaction, style: str) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        speaker_id = config.LEGACY_STYLE_TO_SPEAKER_ID.get(style)
        if speaker_id is None:
            await self._send_notice(
                interaction,
                title="不正な声スタイルです",
                kind="error",
                ephemeral=True,
            )
            return

        await self._refresh_speaker_cache(force=True)
        if self._speaker_options_cache and speaker_id not in self._speaker_label_cache:
            await self._send_notice(
                interaction,
                title="指定した声スタイルは利用できません",
                description="このVOICEVOX環境に存在しない可能性があります。",
                kind="error",
                ephemeral=True,
            )
            return

        self._set_user_speaker_id(guild.id, interaction.user.id, speaker_id)
        style_display = STYLE_NAMES.get(style, style)
        speaker_display = self._get_speaker_display_name(speaker_id)
        user_name = interaction.user.display_name

        await self._send_notice(
            interaction,
            title=f"{user_name}の声スタイルを変更しました",
            description=f"現在の{user_name}のスタイル: **{style_display}**\n話者: {speaker_display}",
            kind="success",
        )
        await self._speak(
            f"{user_name}の声スタイルを{style_display}に変更しました。",
            guild,
            vc,
            speaker_id=speaker_id,
        )

    @app_commands.command(name="styleall", description="サーバー全体のデフォルト声スタイル（互換プリセット）を変更します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(style="全体デフォルトの互換プリセットを選んでください")
    @app_commands.choices(style=STYLE_CHOICES)
    async def styleall(self, interaction: discord.Interaction, style: str) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        speaker_id = config.LEGACY_STYLE_TO_SPEAKER_ID.get(style)
        if speaker_id is None:
            await self._send_notice(
                interaction,
                title="不正な声スタイルです",
                kind="error",
                ephemeral=True,
            )
            return

        await self._refresh_speaker_cache(force=True)
        if self._speaker_options_cache and speaker_id not in self._speaker_label_cache:
            await self._send_notice(
                interaction,
                title="指定した声スタイルは利用できません",
                description="このVOICEVOX環境に存在しない可能性があります。",
                kind="error",
                ephemeral=True,
            )
            return

        config.GUILD_SPEAKER_MAP[guild.id] = speaker_id
        self._persist_runtime_state()
        style_display = STYLE_NAMES.get(style, style)
        speaker_display = self._get_speaker_display_name(speaker_id)

        await self._send_notice(
            interaction,
            title="全体デフォルト声スタイルを変更しました",
            description=f"現在の全体デフォルトスタイル: **{style_display}**\n話者: {speaker_display}",
            kind="success",
        )
        await self._speak(
            f"全体デフォルト声スタイルを{style_display}に変更しました。",
            guild,
            vc,
            speaker_id=speaker_id,
        )

    @app_commands.command(name="speed", description="読み上げ速度を変更します（0.5〜2.0、デフォルト: 1.0）")
    @app_commands.guild_only()
    @app_commands.describe(value="読み上げ速度（0.5=ゆっくり / 1.0=普通 / 2.0=はやい）")
    async def speed(self, interaction: discord.Interaction, value: float) -> None:
        guild = await self._ensure_guild(interaction)
        if guild is None:
            return

        vc = await self._get_or_recover_vc(guild)
        if not vc:
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        if not (0.5 <= value <= 2.0):
            await self._send_notice(
                interaction,
                title="速度の指定が範囲外です",
                description="0.5〜2.0 の範囲で指定してください。",
                kind="error",
                ephemeral=True,
            )
            return

        config.GUILD_SPEED_MAP[guild.id] = value
        self._persist_runtime_state()
        message = f"読み上げ速度を {value} に変更しました。"
        await self._send_notice(
            interaction,
            title="読み上げ速度を変更しました",
            description=f"現在の速度: **{value}**",
            kind="success",
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
            await self._send_notice(
                interaction,
                title="読み上げ中ではありません",
                description="先に /join を実行してください。",
                kind="warning",
                ephemeral=True,
            )
            return

        if not (10 <= length <= 500):
            await self._send_notice(
                interaction,
                title="文字数の指定が範囲外です",
                description="10〜500 の範囲で指定してください。",
                kind="error",
                ephemeral=True,
            )
            return

        config.GUILD_MAX_LENGTH_MAP[guild.id] = length
        self._persist_runtime_state()
        await self._send_notice(
            interaction,
            title="最大読み上げ文字数を変更しました",
            description=f"現在の上限: **{length}文字**",
            kind="success",
        )

    @app_commands.command(name="about", description="このBotについての情報を表示します")
    async def about(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="VOICEVOX読み上げBot",
            description="VOICEVOX音声でDiscordのテキストを読み上げるBotです。",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="使い方",
            value="`/join` → ボイスチャンネルに参加\n`/leave` → 退出\n`/speaker` → あなたの話者変更\n`/speakerall` → 全体デフォルト話者変更\n`/style` → あなたの互換スタイル変更\n`/styleall` → 全体デフォルト互換スタイル変更\n`/speed` → 速度変更\n`/maxlength` → 最大文字数変更\n`/status` → 現在の設定確認",
            inline=False,
        )
        embed.add_field(
            name="クレジット",
            value=(
                "**VOICEVOX** - 音声合成エンジン\n"
                "https://voicevox.hiroshiba.jp/\n\n"
                "**VOICEVOX利用規約**\n"
                "https://voicevox.hiroshiba.jp/term/\n\n"
                "**話者クレジット**\n"
                "各話者の音声ライブラリ規約を確認し、適切にクレジット表記してください。\n\n"
                "各話者の利用条件はVOICEVOX公式規約・各音声ライセンスを確認してください。\n\n"
                "**discord.py** - Discord API ライブラリ\n"
                "https://github.com/Rapptz/discord.py"
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
            await self._send_notice(
                interaction,
                title="現在ボイスチャンネルに参加していません",
                kind="warning",
                ephemeral=True,
            )
            return

        channel_id = config.TTS_CHANNEL_MAP.get(guild.id)
        channel_mention = f"<#{channel_id}>" if channel_id else "未設定"

        await self._refresh_speaker_cache()
        speaker_id = self._get_user_speaker_id(guild.id, interaction.user.id)
        default_speaker_id = self._get_speaker_id(guild.id)
        speaker_display = self._get_speaker_display_name(speaker_id)
        default_speaker_display = self._get_speaker_display_name(default_speaker_id)

        speed = self._get_speed(guild.id)
        max_length = config.GUILD_MAX_LENGTH_MAP.get(guild.id, config.MAX_TEXT_LENGTH)

        embed = self._build_notice_embed(title="現在の状態", kind="info")
        embed.add_field(name="ボイスチャンネル", value=vc.channel.name, inline=False)
        embed.add_field(name="読み上げチャンネル", value=channel_mention, inline=False)
        embed.add_field(name="あなたの話者", value=speaker_display, inline=True)
        embed.add_field(name="全体デフォルト話者", value=default_speaker_display, inline=True)
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
                # TTS通知チャンネルを先に取得してからランタイムをクリアする
                tts_channel_id = config.TTS_CHANNEL_MAP.get(guild.id)
                voice_channel_name = before.channel.name
                self._cancel_idle_disconnect(guild.id)
                self._clear_guild_runtime(guild.id)
                # 読み上げチャンネルに強制切断を通知する
                if tts_channel_id is not None:
                    tts_channel = guild.get_channel(tts_channel_id)
                    if isinstance(tts_channel, discord.abc.Messageable):
                        embed = self._build_notice_embed(
                            title="ボイスチャンネルから切断されました",
                            description=(
                                f"ボイスチャンネル **{voice_channel_name}** から切断されました。\n"
                                "再び読み上げを開始するには `/join` を実行してください。"
                            ),
                            kind="warning",
                        )
                        try:
                            await tts_channel.send(embed=embed)
                        except discord.HTTPException as e:
                            logger.warning(
                                "強制切断通知の送信に失敗しました (guild=%d): %s", guild.id, e
                            )
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
