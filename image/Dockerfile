# Music Assistant with its own shairport-sync builds (classic AirPlay + AirPlay 2), Avahi and nqptp,
# patched for the KEF LS50 Wireless (Gen 1). See ../README.md.
#
# SHAIRPORT_MODE=classic  (default): classic AirPlay, iOS sends ALAC -> lossless
# SHAIRPORT_MODE=airplay2          : AirPlay 2 (Home app, multiroom), iOS sends AAC
# Both use Avahi (IPv4 only, own host name) instead of the tinysvcmdns of the official image.
# Builder and runtime use the same base image so that the libraries match.

ARG MA_VERSION=2.10.8

FROM ghcr.io/music-assistant/server:${MA_VERSION} AS builder

ARG SHAIRPORT_VERSION=4.3.7
ARG SHAIRPORT_SHA=0b1c4391ffd398e7b145eb4b98416261380adeea
# nqptp has to speak the same shared-memory interface version (smi10) as shairport-sync 4.3.7;
# nqptp 1.2.9 is already smi11.
ARG NQPTP_VERSION=1.2.8
ARG NQPTP_SHA=c925f27c1fd12e4033ac477e5a405969b0b0260b

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git autoconf automake libtool pkg-config xxd ca-certificates \
        libpopt-dev libconfig-dev libssl-dev libsoxr-dev \
        libavahi-client-dev libplist-dev libsodium-dev libgcrypt20-dev uuid-dev \
        libavutil-dev libavcodec-dev libavformat-dev libswresample-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN git init nqptp && cd nqptp \
    && git remote add origin https://github.com/mikebrady/nqptp.git \
    && git fetch --depth 1 origin "refs/tags/${NQPTP_VERSION}" && git checkout FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${NQPTP_SHA}" \
    && autoreconf -fi && ./configure && make -j"$(nproc)" && strip nqptp

RUN git init shairport-sync && cd shairport-sync \
    && git remote add origin https://github.com/mikebrady/shairport-sync.git \
    && git fetch --depth 1 origin "refs/tags/${SHAIRPORT_VERSION}" && git checkout FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${SHAIRPORT_SHA}" \
    && autoreconf -fi \
    && cp -a /build/shairport-sync /build/shairport-sync-classic

# AirPlay 2: iOS sends AAC (lossy), needs nqptp
RUN cd shairport-sync && ./configure \
        --with-airplay-2 \
        --with-avahi \
        --with-pipe \
        --with-stdout \
        --with-metadata \
        --with-soxr \
        --with-ssl=openssl \
        --sysconfdir=/etc \
    && make -j"$(nproc)" && strip shairport-sync

# Classic AirPlay (AirPlay 1): iOS sends ALAC 16 bit/44.1 kHz, lossless
RUN cd shairport-sync-classic && ./configure \
        --with-avahi \
        --with-pipe \
        --with-stdout \
        --with-metadata \
        --with-soxr \
        --with-ssl=openssl \
        --sysconfdir=/etc \
    && make -j"$(nproc)" && strip shairport-sync


FROM ghcr.io/music-assistant/server:${MA_VERSION}

# Runtime libraries missing from the base image
RUN apt-get update && apt-get install -y --no-install-recommends \
        avahi-daemon dbus libavformat61 libplist-2.0-4 libsoxr0 libuuid1 curl \
    && rm -rf /var/lib/apt/lists/*

# Both variants; entrypoint.sh links /usr/local/bin/shairport-sync to one of them depending on
# SHAIRPORT_MODE (classic|airplay2) - Music Assistant looks for shairport-sync in PATH.
COPY --from=builder /build/shairport-sync/shairport-sync /usr/local/bin/shairport-sync-ap2
COPY --from=builder /build/shairport-sync-classic/shairport-sync /usr/local/bin/shairport-sync-classic
COPY --from=builder /build/nqptp/nqptp /usr/local/bin/nqptp
COPY avahi-daemon.conf /etc/avahi/avahi-daemon.conf.tpl
COPY entrypoint.sh /usr/local/bin/entrypoint-kef.sh
# Separate venv for the Apple TV watcher (service kef-atv-watcher), independent of MA's venv
ARG PYATV_VERSION=0.18.0
RUN python3 -m venv /opt/atv && /opt/atv/bin/pip install --no-cache-dir "pyatv==${PYATV_VERSION}"

COPY kef_ctl.py kef_power_hook.sh atv_watcher.py /usr/local/bin/
COPY patch_template.py patch_ma.py shairport-sync.conf.extra /tmp/
RUN chmod +x /usr/local/bin/entrypoint-kef.sh /usr/local/bin/kef_ctl.py /usr/local/bin/kef_power_hook.sh \
    && python3 /tmp/patch_template.py /tmp/shairport-sync.conf.extra \
    && python3 /tmp/patch_ma.py \
    && rm /tmp/patch_template.py /tmp/patch_ma.py /tmp/shairport-sync.conf.extra

ENTRYPOINT ["/usr/local/bin/entrypoint-kef.sh", "--data-dir", "/data", "--cache-dir", "/data/.cache"]
