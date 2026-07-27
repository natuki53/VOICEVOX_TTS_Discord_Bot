import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "install_webhook.py"
SPEC = importlib.util.spec_from_file_location("install_webhook", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
install_webhook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_webhook)


class InstallWebhookTests(unittest.TestCase):
    def test_build_hook_restricts_repository_branch_and_event(self) -> None:
        hook = install_webhook.build_hook(
            secret="a" * 64,
            repository="natuki53/VOICEVOX_TTS_Discord_Bot",
        )

        rules = hook["trigger-rule"]["and"]
        self.assertEqual("deploy-voicevox-tts-bot", hook["id"])
        self.assertIn(
            "payload-hmac-sha256",
            [rule["match"]["type"] for rule in rules],
        )
        values = {
            rule["match"]["parameter"]["name"]: rule["match"]["value"]
            for rule in rules
            if rule["match"]["type"] == "value"
        }
        self.assertEqual("push", values["X-GitHub-Event"])
        self.assertEqual("refs/heads/main", values["ref"])
        self.assertEqual(
            "natuki53/VOICEVOX_TTS_Discord_Bot",
            values["repository.full_name"],
        )

    def test_update_hooks_preserves_existing_entries_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            hooks_file = Path(temporary_directory) / "hooks.json"
            hooks_file.write_text(
                json.dumps([{"id": "existing-hook"}]),
                encoding="utf-8",
            )
            hook = install_webhook.build_hook(
                secret="b" * 64,
                repository="owner/repository",
            )

            self.assertTrue(install_webhook.update_hooks(hooks_file, hook))
            self.assertEqual(
                ["existing-hook", "deploy-voicevox-tts-bot"],
                [item["id"] for item in json.loads(hooks_file.read_text())],
            )
            self.assertFalse(install_webhook.update_hooks(hooks_file, hook))

    def test_secret_is_persistent_and_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            secret_file = Path(temporary_directory) / "secret"

            first = install_webhook.load_or_create_secret(secret_file)
            secret_file.chmod(0o644)
            second = install_webhook.load_or_create_secret(secret_file)

            self.assertEqual(first, second)
            self.assertEqual(64, len(first))
            if os.name != "nt":
                mode = stat.S_IMODE(secret_file.stat().st_mode)
                self.assertEqual(0o600, mode)


if __name__ == "__main__":
    unittest.main()
