#!/usr/bin/env bash
# =============================================================================
# 【自有服务器】一键部署 —— 本项目**不用 Render**（512MB 会 OOM），生产就在这台机器上
#
# 用法（在项目根目录）：
#   ./deploy.sh                  全量：拉代码 → 装依赖 → 构建前端 → 重启 → 健康检查
#   ./deploy.sh --skip-pull      代码已用 tar 解包更新，只做后面几步
#   ./deploy.sh --skip-frontend  前端已构建好，跳过 npm（**内存紧张时用这个**）
#   ./deploy.sh --no-restart     只构建，不重启服务
#
# 可覆盖的环境变量：SERVICE（默认 stock-scoring）、HEALTH_URL、APP_DIR
#
# ⚠️⚠️ 前端必须用 `npm run build:server`，**不能**用 `npm run build`：
#   build       → base=/stock-scoring-v2/ + API 指向 Render ⇒ 服务器上 js/css 全 404 / CORS 报错
#   build:server→ 读 `.env.server`（VITE_BASE=/ 、VITE_API_BASE_URL=/api，同源走 nginx 反代）
#   这个坑 2026-11-03 已经踩过两次，见 `frontend/vite.config.js` 头部注释。
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
APP_DIR="$PWD"
SERVICE="${SERVICE:-stock-scoring}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"

DO_PULL=1; DO_FRONT=1; DO_RESTART=1
for a in "$@"; do
  case "$a" in
    --skip-pull)     DO_PULL=0 ;;
    --skip-frontend) DO_FRONT=0 ;;
    --no-restart)    DO_RESTART=0 ;;
    -h|--help)       sed -n '2,17p' "$0"; exit 0 ;;
    *)               echo "未知参数: $a（-h 看用法）" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

mem_avail_mb() { awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0; }

# ── 1. 代码 ──────────────────────────────────────────────────────────────
if [ "$DO_PULL" = 1 ] && [ -d .git ]; then
  log "拉取代码（--ff-only：有本地改动会直接失败，不会覆盖你的修改）"
  git pull --ff-only || die "git pull 失败 —— 请手动处理，或用 --skip-pull 跳过"
fi
log "当前版本：$(git rev-parse --short HEAD 2>/dev/null || echo '非 git 目录（tar 部署）')"

# ── 2. 找 Python（兼容 venv / .venv / 系统 python3）────────────────────────
PY=""
for c in "$APP_DIR/backend/venv/bin/python" "$APP_DIR/venv/bin/python" "$APP_DIR/backend/.venv/bin/python"; do
  if [ -x "$c" ]; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then PY="$(command -v python3 || true)"; fi
[ -n "$PY" ] || die "找不到 python3"
log "Python：$PY（$("$PY" -V 2>&1)）"

# ── 3. 后端依赖（requirements.txt 变了才装）────────────────────────────────
REQ="$APP_DIR/backend/requirements.txt"
STAMP="$APP_DIR/backend/.deps.stamp"
if [ -f "$REQ" ]; then
  H="$(md5sum "$REQ" | awk '{print $1}')"
  if [ "$H" != "$(cat "$STAMP" 2>/dev/null || true)" ]; then
    log "依赖有变化，安装中（慢，耐心等）"
    "$PY" -m pip install -q --disable-pip-version-check -r "$REQ" || die "pip install 失败"
    echo "$H" > "$STAMP"
  else
    log "依赖无变化，跳过"
  fi
fi

# ── 4. 前端（build:server）───────────────────────────────────────────────
if [ "$DO_FRONT" = 1 ]; then
  if [ -d "$APP_DIR/frontend" ]; then
    # ★ 2026-11-04：宝塔装的 node（如 `/www/server/nodejs/v22.1.0/bin`）**未必在 PATH 里**，
    #   直接调 `npm` 会 `command not found`。这里自动补最新版本到 PATH（`sort -V` 取最高）。
    if ! command -v npm >/dev/null 2>&1; then
      _ndir="$(ls -1d /www/server/nodejs/*/bin 2>/dev/null | sort -V | tail -1 || true)"
      if [ -n "${_ndir:-}" ] && [ -x "$_ndir/npm" ]; then
        export PATH="$_ndir:$PATH"
        log "PATH 无 npm，已自动加入：$_ndir"
      else
        warn "找不到 npm（尝试过 PATH 与 /www/server/nodejs/*/bin）"
      fi
    fi
    AV="$(mem_avail_mb)"
    log "可用内存 ${AV}MB"
    if [ "$AV" -gt 0 ] && [ "$AV" -lt 400 ]; then
      warn "内存紧张：vite build 可能被 OOM 杀掉。建议改用 --skip-frontend，在本地构建后传 dist/"
    fi
    cd "$APP_DIR/frontend"
    # ★★ 2026-11-04（实测报错 `EACCES mkdir /www/server/nodejs/cache/_cacache`）：
    #   宝塔的 Node 把 npm **全局缓存**放在 `/www/server/nodejs/cache`，属主是 root
    #   ⇒ 普通用户跑 npm 直接 errno -13 失败。
    #   解法：**只给本项目指定缓存目录**（`npm_config_cache` 环境变量，只在本脚本内生效，
    #   不写 `.npmrc`、不改宝塔全局目录）—— 全局目录是同机所有站点共用的，别去动它。
    export npm_config_cache="$APP_DIR/.npm-cache"
    mkdir -p "$npm_config_cache"
    # ⚠️ 同源但更隐蔽的问题：之前若用 `sudo npm` 跑过，`node_modules` 会变成 root 拥有，
    #   之后普通用户永远写不进去（且报错同样是 EACCES，很容易误判成同一个原因）。
    #   这里提前拦下，并给出**可直接复制**的修复命令。
    if [ ! -w "$APP_DIR/frontend" ]; then
      die "frontend/ 对当前用户（$(id -un)）不可写。请改用项目属主执行，或：sudo chown -R $(id -u):$(id -g) $APP_DIR/frontend"
    fi
    if [ -d node_modules ] && [ ! -w node_modules ]; then
      die "node_modules 属主不是 $(id -un)（多半之前用 sudo npm 跑过）。修复：sudo chown -R $(id -u):$(id -g) $APP_DIR/frontend/node_modules"
    fi
    if [ ! -d node_modules ]; then
      log "首次构建，安装前端依赖（缓存 → .npm-cache）"
      npm ci --no-audit --no-fund || npm install --no-audit --no-fund || die "前端依赖安装失败（装不上先看上面的权限提示）"
    fi
    log "构建前端（build:server → base=/  API=/api）"
    npm run build:server || die "前端构建失败"
    cd "$APP_DIR"
  else
    warn "没有 frontend/ 目录，跳过"
  fi
else
  log "已跳过前端构建（--skip-frontend）"
fi

# ── 5. 编译检查：语法错就别重启了，让旧进程继续顶着 ────────────────────────
log "后端语法检查"
"$PY" -m compileall -q "$APP_DIR/backend/app" >/dev/null || die "后端有语法错误，已中止（服务仍在跑旧代码，未受影响）"

# ── 6. 重启 + 健康检查 ───────────────────────────────────────────────────
if [ "$DO_RESTART" = 1 ]; then
  SUDO=""
  if [ "$(id -u)" != "0" ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
  log "重启服务 $SERVICE"
  $SUDO systemctl restart "$SERVICE" || die "systemctl restart 失败"
  log "等待健康检查（最多 40 秒）：$HEALTH_URL"
  OK=0
  for i in $(seq 1 40); do
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then OK=1; break; fi
    sleep 1
  done
  if [ "$OK" = 1 ]; then
    log "✅ 部署完成，健康检查通过（${i}s）"
    free -m | awk 'NR<=2'
  else
    warn "❌ 健康检查 40 秒未通过，最近日志："
    $SUDO journalctl -u "$SERVICE" -n 50 --no-pager || true
    die "部署失败（服务可能没起来）"
  fi
else
  log "已跳过重启（--no-restart）"
fi
