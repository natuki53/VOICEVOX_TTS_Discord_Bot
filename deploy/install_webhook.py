#!/usr/bin/env python3
"""Install this bot's hook into an existing adnanh/webhook receiver."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path


HOOK_ID = "deploy-voicevox-tts-bot"
DEFAULT_WEB_SERVER_DEPLOY_DIR = Path(
    "/home/natuki/services/web-server/deploy"
)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hooks-file",
        type=Path,
        default=DEFAULT_WEB_SERVER_DEPLOY_DIR / "hooks.json",
    )
    parser.add_argument(
        "--trigger-source",
        type=Path,
        default=script_dir / "webhook_trigger.sh",
    )
    parser.add_argument(
        "--trigger-destination",
        type=Path,
        default=DEFAULT_WEB_SERVER_DEPLOY_DIR / "voicebot-trigger.sh",
    )
    parser.add_argument(
        "--deploy-source",
        type=Path,
        default=script_dir / "auto_deploy.sh",
    )
    parser.add_argument(
        "--deploy-destination",
        type=Path,
        default=DEFAULT_WEB_SERVER_DEPLOY_DIR / "voicebot-auto-deploy.sh",
    )
    parser.add_argument(
        "--secret-file",
        type=Path,
        default=DEFAULT_WEB_SERVER_DEPLOY_DIR / "voicebot-webhook-secret",
    )
    parser.add_argument(
        "--repository",
        default="natuki53/VOICEVOX_TTS_Discord_Bot",
    )
    return parser.parse_args()


def load_or_create_secret(secret_file: Path) -> str:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        secret = secret_file.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise ValueError(f"{secret_file} contains an invalid secret")
        secret_file.chmod(0o600)
        return secret

    secret = secrets.token_hex(32)
    secret_file.write_text(f"{secret}\n", encoding="utf-8")
    secret_file.chmod(0o600)
    return secret


def build_hook(secret: str, repository: str) -> dict:
    return {
        "id": HOOK_ID,
        "execute-command": "/app/voicebot-trigger.sh",
        "response-message": "Deployment accepted.",
        "trigger-rule": {
            "and": [
                {
                    "match": {
                        "type": "payload-hmac-sha256",
                        "secret": secret,
                        "parameter": {
                            "source": "header",
                            "name": "X-Hub-Signature-256",
                        },
                    }
                },
                {
                    "match": {
                        "type": "value",
                        "value": "push",
                        "parameter": {
                            "source": "header",
                            "name": "X-GitHub-Event",
                        },
                    }
                },
                {
                    "match": {
                        "type": "value",
                        "value": "refs/heads/main",
                        "parameter": {
                            "source": "payload",
                            "name": "ref",
                        },
                    }
                },
                {
                    "match": {
                        "type": "value",
                        "value": repository,
                        "parameter": {
                            "source": "payload",
                            "name": "repository.full_name",
                        },
                    }
                },
            ]
        },
    }


def update_hooks(hooks_file: Path, hook: dict) -> bool:
    hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    if not isinstance(hooks, list):
        raise ValueError(f"{hooks_file} must contain a JSON array")
    hooks_file.chmod(0o600)

    updated_hooks = [
        hook if item.get("id") == HOOK_ID else item
        for item in hooks
    ]
    if not any(item.get("id") == HOOK_ID for item in hooks):
        updated_hooks.append(hook)

    if updated_hooks == hooks:
        return False

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = hooks_file.with_name(f"{hooks_file.name}.backup-{timestamp}")
    shutil.copy2(hooks_file, backup)
    backup.chmod(0o600)

    temporary = hooks_file.with_name(f".{hooks_file.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(updated_hooks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, hooks_file)
    return True


def install_executable(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o755)


def main() -> None:
    args = parse_args()
    secret = load_or_create_secret(args.secret_file)

    install_executable(args.trigger_source, args.trigger_destination)
    install_executable(args.deploy_source, args.deploy_destination)

    changed = update_hooks(
        args.hooks_file,
        build_hook(secret=secret, repository=args.repository),
    )
    state = "updated" if changed else "already configured"
    print(f"{HOOK_ID}: {state}")
    print("Restart the webhook receiver, then configure GitHub with the secret file.")


if __name__ == "__main__":
    main()
