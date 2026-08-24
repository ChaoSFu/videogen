#!/bin/bash
# 统一进程管理：后台启动/停止/重启/查看各服务，PID 记录在 run/ 下。
#
# 用法:
#   scripts/ctl.sh start   <service|all>
#   scripts/ctl.sh stop    <service|all>
#   scripts/ctl.sh restart <service|all>
#   scripts/ctl.sh status  [service]
#   scripts/ctl.sh logs    <service>          # tail -f 对应日志
#
# service: h3 | videogen | comfyui | pixelle-web | pixelle-api
#
# 设计说明（避免留下孤儿/僵尸进程）：
#   - 用 setsid 把每个服务起在自己独立的会话/进程组里，记录的 PID 就是
#     该进程组的组长。conda run 有时不会把 SIGTERM 正确转发给它启动的
#     子进程（真正的 uvicorn），所以 stop 时按 **进程组**（负 PID）整体
#     发信号，而不是只 kill 记录的那一个 PID，这样 conda run 的壳和它
#     启动的 python 解释器会一起收到信号。
#   - 后台进程的父进程是这个脚本本身；脚本执行完 start 就退出，子进程
#     会被重新挂到 init(1) 下——这是标准的守护进程化方式，init 会正常
#     回收退出的子进程，不会产生 <defunct> 僵尸进程。
#   - 各服务自己的环境变量覆盖方式不变（比如 H3_TE_DEVICE=cuda:0），
#     照常在调用 ctl.sh 前 export 或写进 scripts/h3.env 即可，ctl.sh
#     只是把已有的 server-*.sh 脚本包了一层后台生命周期管理。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/run"
mkdir -p "$RUN_DIR"

# 服务名 -> 启动脚本（用 case 而非关联数组，兼容 bash 3.2，不依赖 bash 4+）
service_script() {
    case "$1" in
        h3)           echo "$ROOT/scripts/server-h3.sh" ;;
        videogen)     echo "$ROOT/scripts/server-videogen.sh" ;;
        comfyui)      echo "$ROOT/scripts/server-comfyui.sh" ;;
        pixelle-web)  echo "$ROOT/scripts/server-web.sh" ;;
        pixelle-api)  echo "$ROOT/scripts/server-api.sh" ;;
        *)            return 1 ;;
    esac
}
ALL_SERVICES="h3 videogen comfyui pixelle-web pixelle-api"

pidfile() { echo "$RUN_DIR/$1.pid"; }
logfile() { echo "$RUN_DIR/$1.log"; }

is_running() {
    local name="$1" pid
    [ -f "$(pidfile "$name")" ] || return 1
    pid="$(cat "$(pidfile "$name")" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_one() {
    local name="$1" script
    script="$(service_script "$name")" || {
        echo "❌ 未知服务: $name（可选: $ALL_SERVICES）"
        return 1
    }
    if is_running "$name"; then
        echo "✅ $name 已在运行 (PID $(cat "$(pidfile "$name")"))，跳过"
        return 0
    fi
    echo "🚀 启动 $name ..."
    setsid nohup bash "$script" >>"$(logfile "$name")" 2>&1 </dev/null &
    local pid=$!
    echo "$pid" > "$(pidfile "$name")"
    sleep 2
    if is_running "$name"; then
        echo "✅ $name 已启动 (PID $pid)，日志: $(logfile "$name")"
    else
        echo "❌ $name 启动失败，看日志排查: $(logfile "$name")"
        tail -n 20 "$(logfile "$name")" 2>/dev/null
        rm -f "$(pidfile "$name")"
        return 1
    fi
}

stop_one() {
    local name="$1"
    if ! is_running "$name"; then
        echo "○ $name 未在运行"
        rm -f "$(pidfile "$name")"
        return 0
    fi
    local pid
    pid="$(cat "$(pidfile "$name")")"
    echo "🛑 停止 $name (进程组 $pid)..."
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        is_running "$name" || break
        sleep 1
    done
    if is_running "$name"; then
        echo "⚠️  $name 未在 10s 内退出，强制 kill -9"
        kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$(pidfile "$name")"
    echo "✅ $name 已停止"
}

status_one() {
    local name="$1"
    if is_running "$name"; then
        echo "🟢 $name  运行中 (PID $(cat "$(pidfile "$name")"))  日志: $(logfile "$name")"
    else
        echo "⚪ $name  未运行"
    fi
}

expand_targets() {
    if [ "$1" = "all" ]; then
        echo "$ALL_SERVICES"
    else
        echo "$1"
    fi
}

cmd="${1:-}"
target="${2:-}"

case "$cmd" in
    start|stop|restart)
        [ -n "$target" ] || { echo "用法: $0 $cmd <service|all>（可选: $ALL_SERVICES, all）"; exit 1; }
        for name in $(expand_targets "$target"); do
            case "$cmd" in
                start)   start_one "$name" ;;
                stop)    stop_one "$name" ;;
                restart) stop_one "$name"; start_one "$name" ;;
            esac
        done
        ;;
    status)
        if [ -n "$target" ]; then
            status_one "$target"
        else
            for name in $ALL_SERVICES; do status_one "$name"; done
        fi
        ;;
    logs)
        [ -n "$target" ] || { echo "用法: $0 logs <service>"; exit 1; }
        tail -f "$(logfile "$target")"
        ;;
    *)
        echo "用法: $0 <start|stop|restart|status|logs> <service|all>"
        echo "service: $ALL_SERVICES"
        exit 1
        ;;
esac
