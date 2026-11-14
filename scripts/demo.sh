#!/usr/bin/env bash
# Переключить сценарий демо одной командой — без перезапуска бота и без нового онбординга.
#
#   scripts/demo.sh night_hypo          # с моментом пропуска по умолчанию для сценария
#   scripts/demo.sh noise_spike 30 50   # сценарий, скорость, пропуск (мин)
#
# Что делает:
#   1. останавливает fake-NS;
#   2. чистит из базы показания, уведомления и режимы прошлого прогона.
#      Пользователи, подключение к Nightscout и доверенные лица остаются;
#   3. запускает fake-NS с новым сценарием (свежие данные и время).
# Режим (например, «Ночной») после переключения включите в боте: /mode.
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:-night_hypo}"
SPEED="${2:-30}"
# Пропуск по умолчанию — за несколько минут сценария до интересного момента
case "$SCENARIO" in
  night_hypo)     DEFAULT_SKIP=150 ;;  # 01:30, low в 01:50, very_low в 03:00
  noise_spike)    DEFAULT_SKIP=50  ;;  # 10:50, выброс в 11:00 — уведомлений быть не должно
  meal)           DEFAULT_SKIP=5   ;;  # 08:05, быстрый рост в 08:30 — ~50 с на фото еды
  meal_overbolus) DEFAULT_SKIP=30  ;;  # 13:30, рост в 13:40, низкий в 15:40
  fast_drop)      DEFAULT_SKIP=35  ;;  # 16:35, быстрое падение в 16:45
  sensor_gap)     DEFAULT_SKIP=35  ;;  # 18:35, данные пропадают в 18:50
  hyper_long)     DEFAULT_SKIP=80  ;;  # 15:20, высокий > 1 ч в 15:30
  *)              DEFAULT_SKIP=0   ;;
esac
SKIP="${3:-$DEFAULT_SKIP}"

# Порядок важен: сначала остановить fake-NS, потом чистить базу, потом запускать новый сценарий.
# Иначе pipeline успевает смешать показания старого и нового сценария и прислать лишнее уведомление.
docker compose --profile demo stop fake-ns >/dev/null 2>&1 || true

# База — внутри контейнера бота: файл в ./data создаёт root, с хоста его не изменить.
docker compose exec -T bot python - <<'PY' || echo "!! бот не запущен: docker compose --profile demo up -d"
import os, sqlite3
url = os.environ.get("DATABASE_URL", "sqlite:///data/diabetrics.db")
path = url.removeprefix("sqlite:///")
if not os.path.exists(path):
    print("→ базы ещё нет — пройдите /start в боте (адрес http://fake-ns:1337, токен secret)")
else:
    con = sqlite3.connect(path, timeout=10)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # trusted_deliveries — вместе с alerts: иначе записи «доверенному уже доставлено» от прошлого
    # прогона остались бы в базе, и во втором прогоне доверенное лицо могло не получить сообщение.
    for table in ("trusted_deliveries", "readings", "alerts", "modes"):
        if table in tables:
            con.execute(f"DELETE FROM {table}")
    con.commit()
    users = con.execute("SELECT COUNT(*) FROM users WHERE ns_url IS NOT NULL").fetchone()[0]
    print(f"→ база очищена: показания, уведомления, режимы. Пользователей с Nightscout: {users}")
PY

echo "→ fake-NS: $SCENARIO, ×$SPEED, с $SKIP-й минуты"
SCENARIO="$SCENARIO" SPEED="$SPEED" SKIP="$SKIP" \
  docker compose --profile demo up -d --force-recreate fake-ns

sleep 2
curl -s "http://localhost:1337/api/v1/status.json?token=${FAKE_NS_TOKEN:-secret}" \
  | python3 -c "import json,sys; print('→ время fake-NS:', json.load(sys.stdin)['serverTime'])" \
  || echo "!! fake-NS не отвечает: docker compose logs fake-ns"
