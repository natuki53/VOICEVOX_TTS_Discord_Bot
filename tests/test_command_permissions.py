import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from cogs.voice import VoiceCog
from main import VoiceBot

SERVER_WIDE_COMMANDS = (
    "speakerall",
    "styleall",
    "speedall",
    "maxlength",
    "readname",
)


class CommandPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_wide_commands_require_manage_guild(self) -> None:
        interaction = SimpleNamespace(
            permissions=discord.Permissions.none(),
        )

        for command_name in SERVER_WIDE_COMMANDS:
            command = getattr(VoiceCog, command_name)
            self.assertTrue(
                command.default_permissions.manage_guild,
                command_name,
            )
            self.assertTrue(command.checks, command_name)
            with self.assertRaises(app_commands.MissingPermissions):
                await command.checks[0](interaction)

    async def test_missing_permission_error_gets_ephemeral_response(self) -> None:
        response = SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(
            response=response,
            followup=AsyncMock(),
            command=SimpleNamespace(name="speedall"),
        )

        await VoiceBot.on_app_command_error(
            None,
            interaction,
            app_commands.MissingPermissions(["manage_guild"]),
        )

        response.send_message.assert_awaited_once()
        self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
