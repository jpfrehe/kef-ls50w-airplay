#!/bin/sh
# Session hook for shairport-sync (called through Music Assistant's template, see patch_template.py):
#   kef_power_hook.sh start  -> Home Assistant webhook (HA wakes the KEF via IR), wait for the KEF
#   kef_power_hook.sh stop   -> Home Assistant webhook "stop"
#
# "start" runs before the MA_PLAY_BEGIN marker, i.e. Music Assistant only starts DLNA playback once
# this script is done (from standby: as soon as MA has reconnected the KEF). Nobody has to select the
# Wi-Fi source: the KEF switches to Wi-Fi by itself when DLNA playback starts (measured < 1 s, also
# from Opt). The control port 50001 is therefore not used here.
#
# Configuration via environment variables (docker-compose.yml):
#   KEF_HOST              IP address of the KEF (required)
#   KEF_WEBHOOK_START     URL of the HA webhook "AirPlay starts"
#   KEF_WEBHOOK_STOP      URL of the HA webhook "AirPlay ends"
#   KEF_BOOT_TIMEOUT      max. seconds to wait for the KEF (default 150)
#   KEF_SETTLE_SECONDS    fallback: wait this long after the KEF is up if MA never dropped the
#                         DLNA player (default 1)
#   KEF_PLAY_DELAY        pause between reconnect and start of playback (default 1, see below)

KEF_BOOT_TIMEOUT="${KEF_BOOT_TIMEOUT:-150}"
KEF_SETTLE_SECONDS="${KEF_SETTLE_SECONDS:-1}"
KEF_PLAY_DELAY="${KEF_PLAY_DELAY:-1}"
LOG=/data/kef_power_hook.log
# Marker files shared with Music Assistant (patch_ma.py): hook is waiting / MA reconnected the player
WAKE_PENDING=/tmp/kef_wake_pending
MA_MARKER=/tmp/ma_dlna_reconnected

log() {
    # keep the log small
    [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.1"
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG"
}

if [ -z "$KEF_HOST" ]; then
    log "$1" "KEF_HOST is not set, nothing to do"
    exit 0
fi

kef_ready() {
    curl -s -m 0.5 -o /dev/null "http://${KEF_HOST}:8080/description.xml"
}

call_webhook() {
    [ -n "$2" ] || return 0
    code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "$2")
    log "$1" "webhook -> HTTP ${code}"
}

case "$1" in
    start)
        # Webhook first so the IR signal loses no time (HA checks itself whether the KEF is
        # already running). From here on MA probes the KEF every 0.1 s (WAKE_PENDING).
        rm -f "$MA_MARKER"
        touch "$WAKE_PENDING"
        call_webhook start "$KEF_WEBHOOK_START"
        if kef_ready; then
            rm -f "$WAKE_PENDING"
            log start "KEF already online"
        else
            # MA reconnects the DLNA player as soon as the KEF answers and then creates $MA_MARKER
            # (patch_ma.py). Measured after booting (port 8080 open = 0 s): starting playback at
            # ~0.5 s is accepted but never plays; at 1.5-3 s the KEF plays after ~4 s; from ~4 s on
            # it blocks DLNA commands for ~20 s. Hence exactly KEF_PLAY_DELAY after the reconnect
            # (and MA without keep-alive, MA_DLNA_FORCE_CLOSE=1).
            log start "KEF offline, waiting up to ${KEF_BOOT_TIMEOUT}s for it to boot"
            t0=$(date +%s)
            online=""
            n=0
            until [ -f "$MA_MARKER" ]; do
                now=$(date +%s)
                if [ $(( now - t0 )) -ge "$KEF_BOOT_TIMEOUT" ]; then
                    log start "timeout: KEF not reachable, MA will try anyway"
                    break
                fi
                # Fallback in case MA never dropped the player (short standby): then no marker
                # comes, but the KEF is reachable
                if [ $(( n % 10 )) -eq 0 ] && [ -z "$online" ] && kef_ready; then
                    online=$now
                fi
                if [ -n "$online" ] && [ $(( now - online )) -ge "$KEF_SETTLE_SECONDS" ]; then
                    log start "KEF online after $(( online - t0 ))s, MA did not need to reconnect"
                    break
                fi
                sleep 0.1
                n=$(( n + 1 ))
            done
            rm -f "$WAKE_PENDING"
            if [ -f "$MA_MARKER" ]; then
                log start "KEF online and MA reconnected after $(( $(date +%s) - t0 ))s, pausing ${KEF_PLAY_DELAY}s"
                sleep "$KEF_PLAY_DELAY"
            fi
        fi
        ;;
    stop)
        call_webhook stop "$KEF_WEBHOOK_STOP"
        ;;
    *)
        echo "Usage: $0 start|stop" >&2
        exit 1
        ;;
esac
exit 0
