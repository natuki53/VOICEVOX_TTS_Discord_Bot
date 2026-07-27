#!/usr/bin/env bash
set -euo pipefail

HOST_REPO_DIR="${VOICEBOT_HOST_REPO_DIR:-/home/natuki/services/discord-bots/VOICEVOX_TTS_Discord_Bot-managed}"
HOST_ENV_FILE="${VOICEBOT_HOST_ENV_FILE:-/home/natuki/services/discord-bots/VOICEVOX_TTS_Discord_Bot/.env}"
HOST_DEPLOY_SCRIPT="${VOICEBOT_HOST_DEPLOY_SCRIPT:-/home/natuki/services/web-server/deploy/voicebot-auto-deploy.sh}"
DEPLOY_IMAGE="${VOICEBOT_DEPLOY_IMAGE:-web-server-deploy}"
RUNNER_NAME="${VOICEBOT_DEPLOY_RUNNER:-voicebot-deployer}"

if docker inspect "${RUNNER_NAME}" >/dev/null 2>&1; then
  if [ "$(docker inspect --format '{{.State.Running}}' "${RUNNER_NAME}")" = "true" ]; then
    echo "A deployment is already running."
    exit 0
  fi

  docker rm "${RUNNER_NAME}" >/dev/null
fi

docker run --detach \
  --name "${RUNNER_NAME}" \
  --env DEPLOY_UID=1000 \
  --env DEPLOY_GID=1000 \
  --volume "${HOST_REPO_DIR}:/voicebot:rw" \
  --volume "${HOST_ENV_FILE}:/run/secrets/voicebot.env:ro" \
  --volume "${HOST_DEPLOY_SCRIPT}:/runner/auto_deploy.sh:ro" \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --entrypoint /runner/auto_deploy.sh \
  "${DEPLOY_IMAGE}" >/dev/null

echo "Deployment accepted."
