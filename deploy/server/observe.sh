#!/usr/bin/env bash
# 服务器 deep-searcher-study 长时间观察监控。
# 用法：bash observe.sh <REMOTE_ROOT> <start|status|stop|clean> [DURATION_MIN=240] [INTERVAL_SEC=60]
# start 追加采样到 $REMOTE_ROOT/backups/observation.log（可重复 start 补足缺失时间）。
set -euo pipefail

REMOTE_ROOT="$1"
ACTION="${2:-status}"
DURATION_MIN="${3:-240}"
INTERVAL="${4:-60}"
OBS_LOG="$REMOTE_ROOT/backups/observation.log"
PID_FILE="$REMOTE_ROOT/backups/observation.pid"
ENV_FILE="$REMOTE_ROOT/.env.server"
RELEASE_DIR="$(ls -1d "$REMOTE_ROOT"/releases/* 2>/dev/null | tail -1 || echo "$REMOTE_ROOT")"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"

case "$ACTION" in
  start)
    mkdir -p "$REMOTE_ROOT/backups"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "observe 已在运行 (pid $(cat "$PID_FILE"))，请先 stop"
      exit 1
    fi
    cat > "$REMOTE_ROOT/backups/observe-loop.sh" <<EOF
#!/usr/bin/env bash
set -u
OBS_LOG="$OBS_LOG"
ENV_FILE="$ENV_FILE"
RELEASE_DIR="$RELEASE_DIR"
COMPOSE="$COMPOSE"
DURATION_MIN="$DURATION_MIN"
INTERVAL="$INTERVAL"
END=\$(( \$(date +%s) + DURATION_MIN*60 ))
while [ \$(date +%s) -lt \$END ]; do
  TS="\$(date '+%Y-%m-%d %H:%M:%S')"
  {
    echo "===== \$TS ====="
    echo "--- containers ---"
    \$COMPOSE ps --format '{{.Name}}\t{{.Status}}' 2>/dev/null || true
    echo "--- stats (本项目) ---"
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | grep -E 'deepsearcher|NAME' || true
    echo "--- mem/swap ---"
    free -m
    swapon --show 2>/dev/null || true
    echo "--- disk / ---"
    df -h /
    echo "--- docker log size ---"
    du -sh /var/lib/docker/containers 2>/dev/null | awk '{print \$1}' || true
    echo "--- rocketmq consumer ---"
    \$COMPOSE exec -T rocketmq-namesrv sh mqadmin consumerProgress -n rocketmq-namesrv:9876 -g deepsearcher-document-ingest 2>/dev/null | tail -3 || true
    DISK_GB="\$(df -BG / | awk 'NR==2 {gsub(/G/,"",\$4); print \$4}')"
    if [ -n "\$DISK_GB" ] && [ "\$DISK_GB" -lt 10 ] 2>/dev/null; then
      echo "WARNING 剩余磁盘 <10GB (\$DISK_GB GB)，触发停止条件"
    fi
  } >> "\$OBS_LOG"
  sleep "\$INTERVAL"
done
echo "===== observe finished \$(date '+%Y-%m-%d %H:%M:%S') =====" >> "\$OBS_LOG"
EOF
    chmod +x "$REMOTE_ROOT/backups/observe-loop.sh"
    nohup bash "$REMOTE_ROOT/backups/observe-loop.sh" >/dev/null 2>&1 &
    echo $! > "$PID_FILE"
    echo "observe 已启动 pid=$(cat "$PID_FILE") duration=${DURATION_MIN}min interval=${INTERVAL}s log=$OBS_LOG"
    ;;

  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "RUNNING pid=$(cat "$PID_FILE")"
    else
      echo "STOPPED"
    fi
    if [ -f "$OBS_LOG" ]; then
      echo "log=$OBS_LOG"
      echo "samples=$(grep -c '^===== ' "$OBS_LOG" || true)"
      echo "first=$(grep -m1 '^===== ' "$OBS_LOG" | tr -d '= ' || echo -)"
      echo "last=$(grep '^===== ' "$OBS_LOG" | tail -1 | tr -d '= ' || echo -)"
      echo "warnings=$(grep -c '^WARNING' "$OBS_LOG" || true)"
    else
      echo "log 不存在"
    fi
    ;;

  stop)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE"
      echo "observe 已停止"
    else
      rm -f "$PID_FILE"
      echo "observe 未在运行"
    fi
    ;;

  clean)
    rm -f "$OBS_LOG" "$PID_FILE" "$REMOTE_ROOT/backups/observe-loop.sh"
    echo "已清理观察日志与进程文件"
    ;;

  *)
    echo "用法：bash observe.sh <REMOTE_ROOT> <start|status|stop|clean> [DURATION_MIN] [INTERVAL_SEC]"
    exit 1
    ;;
esac