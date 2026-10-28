#!/bin/sh
# Selects the shairport-sync variant (SHAIRPORT_MODE), configures and starts dbus, Avahi and nqptp,
# then hands over to the original Music Assistant entrypoint.
#   classic  (default): classic AirPlay, ALAC lossless, no nqptp needed
#   airplay2          : AirPlay 2 (AAC), needs nqptp
#
# Environment:
#   SHAIRPORT_MODE            classic | airplay2 (default classic)
#   SHAIRPORT_LATENCY_OFFSET  seconds, negative = hand audio to MA earlier (default 0.0)
#   AVAHI_HOSTNAME            mDNS host name of the receiver (default kef-airplay)
#   AVAHI_INTERFACES          comma-separated LAN interface(s) to announce on, e.g. eth0
#                             (default: all interfaces, including Docker bridges)

SHAIRPORT_MODE="${SHAIRPORT_MODE:-classic}"
case "$SHAIRPORT_MODE" in
    classic)  ln -sf /usr/local/bin/shairport-sync-classic /usr/local/bin/shairport-sync ;;
    airplay2) ln -sf /usr/local/bin/shairport-sync-ap2 /usr/local/bin/shairport-sync ;;
    *) echo "Unknown SHAIRPORT_MODE '$SHAIRPORT_MODE' (classic|airplay2)" >&2; exit 1 ;;
esac
echo "shairport-sync: $SHAIRPORT_MODE ($(/usr/local/bin/shairport-sync -V))"

# Latency offset: a negative value hands the audio to MA earlier (compensates the KEF buffer).
# The placeholder in MA's template is set on every start from the pristine copy in the image.
SHAIRPORT_LATENCY_OFFSET="${SHAIRPORT_LATENCY_OFFSET:-0.0}"
TPL=$(ls /app/venv/lib/python3*/site-packages/music_assistant/providers/airplay_receiver/bin/shairport-sync.conf)
[ -f "$TPL.orig" ] || cp "$TPL" "$TPL.orig"
sed "s/__LATENCY_OFFSET__/${SHAIRPORT_LATENCY_OFFSET}/" "$TPL.orig" > "$TPL"
echo "shairport-sync: audio_backend_latency_offset_in_seconds = $SHAIRPORT_LATENCY_OFFSET"

# Avahi configuration from the template in the image
AVAHI_HOSTNAME="${AVAHI_HOSTNAME:-kef-airplay}"
sed -e "s/__AVAHI_HOSTNAME__/${AVAHI_HOSTNAME}/" /etc/avahi/avahi-daemon.conf.tpl > /etc/avahi/avahi-daemon.conf
if [ -n "$AVAHI_INTERFACES" ]; then
    sed -i "s/^#allow-interfaces=.*/allow-interfaces=${AVAHI_INTERFACES}/" /etc/avahi/avahi-daemon.conf
fi
echo "avahi: host name ${AVAHI_HOSTNAME}.local, interfaces ${AVAHI_INTERFACES:-all}"

# nqptp opens its control port on every address of "localhost". If the host's /etc/hosts maps
# localhost to something other than 127.0.0.1 (or ::1 is disabled), pin it in the container's copy.
{ echo "127.0.0.1 localhost"; grep -vE '(^|[[:space:]])localhost([[:space:]]|$)' /etc/hosts; } > /tmp/hosts \
    && cat /tmp/hosts > /etc/hosts

# Private system bus for Avahi (the host bus has no Avahi policy)
mkdir -p /run/dbus
rm -f /run/dbus/pid
dbus-daemon --system --fork

# mDNS announcement (_raop._tcp, with AirPlay 2 also _airplay._tcp)
rm -f /run/avahi-daemon/pid
avahi-daemon --daemonize --no-chroot

# PTP time base, AirPlay 2 only (UDP 319/320)
if [ "$SHAIRPORT_MODE" = "airplay2" ]; then
    nqptp &
fi

exec /usr/local/bin/entrypoint.sh "$@"
