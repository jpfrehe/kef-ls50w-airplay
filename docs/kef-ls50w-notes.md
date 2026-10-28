# Measured behaviour

Notes from building this project – the reasons behind the less obvious parts of the code.
Measured with one KEF LS50 Wireless (Gen 1, wired LAN), Music Assistant 2.10.8, a Moes UFO-R11 on a
TI CC2652 coordinator (Z-Stack) and an Apple TV 4K (tvOS 27). Your mileage may vary.

## KEF LS50 Wireless (Gen 1)

### Standby and power

- In standby the KEF switches off its **whole network module** (LAN and Wi-Fi): no ping, no TCP 50001,
  no Wake-on-LAN.
- After a **power loss it always boots into standby**, even if it was on before – a smart plug
  cannot switch it on.
- The **source keys of the remote do not wake it**, only the power key (a toggle).
- After the IR power signal it is on the network after **~13-17 s** (DLNA port 8080 and control
  port 50001 become reachable at the same time).
- Auto-standby (20 min / 60 min / never) is part of the source register and can be set with
  `kef_ctl.py autostandby`.

### Control port (TCP 50001)

- Protocol as in [aiokef](https://github.com/basnijholt/aiokef); register `0x30` holds source,
  standby timeout, L/R orientation and power.
- It accepts **only one connection at a time** and refuses a new one shortly after the previous one
  closed (ECONNREFUSED) → `kef_ctl.py` keeps one connection per run and retries.
- **Right after booting**, a source change is often not acknowledged or the connection is dropped
  for **~20 s** → `kef_ctl.py on` retries until `--wait` runs out (Home Assistant aborts a
  `shell_command` after 60 s). Waking from standby on Wi-Fi and switching to Optical therefore took
  ~42 s – which is why the automation lets the KEF fall asleep on Optical.

### DLNA

- Starting DLNA playback **switches the KEF to Wi-Fi by itself** (< 1 s when it is on), also from
  Optical. No control-port command is needed for AirPlay.
- The KEF buffers a **fixed amount of data**, not a fixed time: WAV 44.1/16 ≈ 3.7 s, WAV 44.1/24 ≈
  3.3 s, WAV 192/16 ≈ 1.2 s, live FLAC ≈ 6.8 s. Resampling to 192 kHz or padding to 24 bit starts
  faster but sounded audibly worse; 32 bit is not played at all.
- **Boot quirks** (0 s = port 8080 accepts connections after waking up):

  | Playback started (SetAVTransportURI/Play) | Result |
  |---|---|
  | immediately (< 1 s) | Play hangs for 30 s, the KEF never plays |
  | after 1-2 s | PLAYING after ~4-7 s |
  | from ~4 s | every DLNA command blocks ~10 s, PLAYING only after ~25 s |
  | over a kept-alive HTTP connection | the KEF fetches the stream only after that connection is closed (~20 s) |

  Hence the Music Assistant patches: reconnect within 0.1 s of the KEF appearing
  (`MA_DLNA_RECONNECT`), one connection per call (`MA_DLNA_FORCE_CLOSE`), and the hook waits
  `KEF_PLAY_DELAY` (1 s) before Music Assistant starts. Result: PLAYING ~7 s after the KEF is up.
- Music Assistant's Stop before playback returns UPnP error 701 on a freshly booted KEF – harmless.

### Latency

Classic AirPlay has ~2 s of lead time in shairport-sync plus the KEF buffer. `SHAIRPORT_LATENCY_OFFSET=-1.5`
brought "press play → sound" from ~6.4 s to ~5.1 s without dropouts. Values closer to -2.0 leave
less reserve against network hiccups.

## Moes UFO-R11 (Tuya TS1201) via ZHA

- Battery powered sleepy end device, **no Poll Control cluster** – the wake-up interval cannot be
  configured.
- `IRSend` (cluster `0xE004`, command 2) is sent **without acknowledgement**. The blaster then fetches
  the code in several round trips (`IR-Message-Code01`, three `0x02` parts). If it sleeps or the
  link is weak, the command is silently lost. Without a wake-up call only about one in three
  commands arrived.
- **`Identify` is acknowledged and retried** and keeps the blaster awake for a moment. Sending it
  first and only sending IR after it succeeded makes IR work on the first attempt. `IRSend` returns
  immediately; over a weak link (LQI ~47, no router) the transfer took up to 23 s, normally 1-5 s.
- After aborted transfers and after coordinator resets (Home Assistant restarts), the blaster was
  repeatedly unreachable for minutes (`NWK_NO_ROUTE`, then `MAC_TRANSACTION_EXPIRED`/`MAC_NO_ACK`).
  Once it stayed unreachable until it was **re-paired** (keeps IEEE and entities).
- Home Assistant script variables survive across `repeat` iterations – a `response_variable` from an
  earlier round must not count as success in a later one (hence the attempt number).
- The power-key code learned from the LS50 Wireless remote in this setup (76 characters; a first
  learned code of 128 characters did not work):
  `BYEjpxFDAuATAQGFBuAPAwB6YBvAAQOFBkMC4A8BwBtAB+ALAwk9nYEjzAhDAv//4BIHAghDAg==`

## Apple TV (tvOS 27, pyatv 0.18)

- The **power state is pushed immediately** (Off → On < 1 s after switching on, On → Off when going
  to sleep). Home Assistant's Apple TV integration still showed the device as "off" at times, and
  pyatv reported "Off" permanently earlier on – playback state is used as a second signal.
- Netflix reports "Playing" only once the video runs, not in its menus.
- The `pyatv` command line tools (`atvremote`) fail on Python 3.14 (no current event loop); the API
  works.

## shairport-sync / Music Assistant

- shairport-sync 4.3.7 needs nqptp with shared-memory interface **smi10** (nqptp 1.2.8; 1.2.9 is
  smi11). Only needed for AirPlay 2.
- AirPlay 2 from iOS to non-Apple receivers is always **AAC**; lossless only with classic AirPlay
  (ALAC).
- With a host firewall that only allows the LAN over IPv4, an mDNS announcement with IPv6
  addresses makes iPhones connect over IPv6 and fail – the image announces IPv4 only under its own
  host name.
- Music Assistant's DLNA provider only discovers devices by active SSDP search every 300 s and does
  not reconnect a dropped player on its own – a KEF waking from standby would stay unavailable for
  minutes without `MA_DLNA_RECONNECT`.
