#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_URL="${VOICEBOT_REPOSITORY_URL:-https://github.com/natuki53/VOICEVOX_TTS_Discord_Bot.git}"
BRANCH="${VOICEBOT_BRANCH:-main}"
REPO_DIR="${VOICEBOT_REPO_DIR:-/voicebot}"
ENV_FILE="${VOICEBOT_ENV_FILE:-/run/secrets/voicebot.env}"
COMPOSE_FILE="${REPO_DIR}/docker-compose.yml"
PROJECT_NAME="${VOICEBOT_PROJECT_NAME:-voicevox_tts_discord_bot}"
BOT_CONTAINER="${VOICEBOT_CONTAINER_NAME:-voicevox-tts-discord-bot}"
DATA_VOLUME="${VOICEBOT_DATA_VOLUME:-voicevox-tts-discord-bot-data}"
READY_LOG_TEXT="${VOICEBOT_READY_LOG_TEXT:-Bot起動完了:}"
READY_TIMEOUT_SECONDS="${READY_TIMEOUT_SECONDS:-90}"
DEPLOY_UID="${DEPLOY_UID:-1000}"
DEPLOY_GID="${DEPLOY_GID:-1000}"
LOCK_FILE="${VOICEBOT_DEPLOY_LOCK_FILE:-/tmp/voicebot-auto-deploy.lock}"

previous_commit=""
previous_image=""
state_backup=""
seed_container=""

repair_ownership() {
  if [ -d "${REPO_DIR}" ]; then
    chown -R "${DEPLOY_UID}:${DEPLOY_GID}" "${REPO_DIR}" 2>/dev/null || true
  fi
}

cleanup() {
  status=$?
  trap - EXIT
  set +e

  if [ -n "${seed_container}" ]; then
    docker rm -f "${seed_container}" >/dev/null 2>&1 || true
  fi
  if [ -n "${state_backup}" ]; then
    rm -f "${state_backup}"
  fi

  if [ "${status}" -ne 0 ] \
    && [ -n "${previous_commit}" ] \
    && [ -d "${REPO_DIR}/.git" ]; then
    echo "Restoring managed checkout to ${previous_commit}..."
    git -C "${REPO_DIR}" reset --hard "${previous_commit}" >/dev/null 2>&1 || true
  fi

  repair_ownership
  exit "${status}"
}
trap cleanup EXIT

mkdir -p "$(dirname "${LOCK_FILE}")"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another deployment is already running; this delivery was skipped."
  exit 0
fi

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: Environment file is missing: ${ENV_FILE}"
  exit 1
fi

if [ ! -d "${REPO_DIR}/.git" ]; then
  if [ -d "${REPO_DIR}" ] && [ -n "$(find "${REPO_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "ERROR: ${REPO_DIR} is not an empty directory or a Git checkout."
    exit 1
  fi

  mkdir -p "${REPO_DIR}"
  echo "Cloning ${REPOSITORY_URL} (${BRANCH})..."
  git clone --branch "${BRANCH}" --single-branch "${REPOSITORY_URL}" "${REPO_DIR}"
fi

git config --global --add safe.directory "${REPO_DIR}" 2>/dev/null || true

configured_remote="$(git -C "${REPO_DIR}" remote get-url origin)"
if [ "${configured_remote}" != "${REPOSITORY_URL}" ]; then
  echo "ERROR: Unexpected origin URL: ${configured_remote}"
  exit 1
fi

if [ -n "$(git -C "${REPO_DIR}" status --porcelain)" ]; then
  echo "ERROR: Managed checkout has local changes; refusing to overwrite them."
  git -C "${REPO_DIR}" status --short
  exit 1
fi

previous_commit="$(git -C "${REPO_DIR}" rev-parse HEAD)"
previous_image="$(docker inspect --format '{{.Config.Image}}' "${BOT_CONTAINER}" 2>/dev/null || true)"

echo "Fetching origin/${BRANCH}..."
git -C "${REPO_DIR}" fetch --prune origin "${BRANCH}"
target_commit="$(git -C "${REPO_DIR}" rev-parse "origin/${BRANCH}")"

echo "Updating managed checkout: ${previous_commit} -> ${target_commit}"
git -C "${REPO_DIR}" checkout -B "${BRANCH}" "origin/${BRANCH}"
git -C "${REPO_DIR}" reset --hard "${target_commit}"

if [ ! -f "${COMPOSE_FILE}" ] || [ ! -f "${REPO_DIR}/Dockerfile" ]; then
  echo "ERROR: Dockerfile or docker-compose.yml is missing from ${target_commit}."
  exit 1
fi

candidate_image="voicevox-tts-discord-bot:${target_commit}"

BOT_IMAGE="${candidate_image}" \
VOICEBOT_ENV_FILE="${ENV_FILE}" \
docker compose \
  --project-name "${PROJECT_NAME}" \
  --file "${COMPOSE_FILE}" \
  config --quiet

state_backup="$(mktemp)"
if ! docker cp "${BOT_CONTAINER}:/app/data/runtime_state.json" "${state_backup}" >/dev/null 2>&1; then
  rm -f "${state_backup}"
  state_backup=""
fi

if ! docker image inspect "${candidate_image}" >/dev/null 2>&1; then
  echo "Building ${candidate_image}..."
  docker build --pull --tag "${candidate_image}" "${REPO_DIR}"
else
  echo "Using existing image ${candidate_image}."
fi

docker volume create "${DATA_VOLUME}" >/dev/null

if [ -n "${state_backup}" ]; then
  seed_container="voicebot-state-seed-$$"
  docker create \
    --name "${seed_container}" \
    --volume "${DATA_VOLUME}:/app/data" \
    --entrypoint /bin/sh \
    "${candidate_image}" \
    -c true >/dev/null

  existing_state="$(mktemp)"
  if docker cp \
    "${seed_container}:/app/data/runtime_state.json" \
    "${existing_state}" >/dev/null 2>&1; then
    rm -f "${existing_state}"
  else
    rm -f "${existing_state}"
    echo "Migrating runtime state into ${DATA_VOLUME}..."
    docker cp "${state_backup}" "${seed_container}:/app/data/runtime_state.json"
  fi

  docker rm "${seed_container}" >/dev/null
  seed_container=""
fi

rollback_container() {
  if [ -z "${previous_image}" ] \
    || ! docker image inspect "${previous_image}" >/dev/null 2>&1; then
    echo "No previous image is available for rollback."
    return
  fi

  echo "Rolling back ${BOT_CONTAINER} to ${previous_image}..."
  BOT_IMAGE="${previous_image}" \
  VOICEBOT_ENV_FILE="${ENV_FILE}" \
  docker compose \
    --project-name "${PROJECT_NAME}" \
    --file "${COMPOSE_FILE}" \
    up -d --no-build --remove-orphans
}

echo "Starting ${BOT_CONTAINER}..."
if ! BOT_IMAGE="${candidate_image}" \
  VOICEBOT_ENV_FILE="${ENV_FILE}" \
  docker compose \
    --project-name "${PROJECT_NAME}" \
    --file "${COMPOSE_FILE}" \
    up -d --no-build --remove-orphans; then
  rollback_container
  exit 1
fi

ready=0
for ((elapsed = 0; elapsed < READY_TIMEOUT_SECONDS; elapsed++)); do
  if ! docker inspect "${BOT_CONTAINER}" >/dev/null 2>&1; then
    break
  fi

  running="$(docker inspect --format '{{.State.Running}}' "${BOT_CONTAINER}")"
  if [ "${running}" != "true" ]; then
    break
  fi

  if docker logs "${BOT_CONTAINER}" 2>&1 | grep -Fq "${READY_LOG_TEXT}"; then
    ready=1
    break
  fi

  sleep 1
done

if [ "${ready}" -ne 1 ]; then
  echo "ERROR: New container did not become ready. Recent logs:"
  docker logs --tail 100 "${BOT_CONTAINER}" 2>&1 || true
  rollback_container
  exit 1
fi

echo "Deployment completed: ${target_commit}"
