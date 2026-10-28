#!/usr/bin/env python3
"""Minimal control for the KEF LS50 Wireless (Gen 1) / LSX via TCP port 50001.

Protocol as implemented by aiokef (github.com/basnijholt/aiokef):
  GET  b"G" <reg> 0x80          -> b"R" <reg> 0x81 <value> <crc>
  SET  b"S" <reg> 0x81 <value>  -> b"R" 0x11 0xFF
Register 0x30 ('0') = source. Value = source code + 16 * standby index (+64 for R/L,
+128 = standby). Standby index: 0 = 20 min, 1 = 60 min, 2 = never.

The host is taken from --host or the environment variable KEF_HOST.

Examples:
  kef_ctl.py status
  kef_ctl.py on [--source wifi] [--wait 30]
  kef_ctl.py standby [--only-if-source opt]
  kef_ctl.py autostandby 60|20|never
  kef_ctl.py online [--wait 25]     # DLNA port 8080 only, exit code 0 = on the network
"""

import argparse
import os
import socket
import sys
import time
import urllib.request

PORT = 50001
SOURCES = {"wifi": 2, "bluetooth": 9, "aux": 10, "opt": 11, "usb": 12}
SOURCE_NAMES = {v: k for k, v in SOURCES.items()}
SOURCE_NAMES[15] = "bluetooth"  # Bluetooth paired (read only)
STANDBY = {0: "20", 1: "60", 2: "never"}
REG_SOURCE = ord("0")


# The KEF refuses a new connection shortly after the previous one (ECONNREFUSED).
# So keep one connection per host open and use it for all commands (as aiokef does);
# "refused" while connecting is retried a few times.
_CONNECTIONS: dict = {}
_REFUSED_RETRIES = 10
_REFUSED_DELAY = 0.5


def _connect(host: str, timeout: float) -> socket.socket:
    for attempt in range(_REFUSED_RETRIES):
        try:
            return socket.create_connection((host, PORT), timeout=timeout)
        except ConnectionRefusedError:
            if attempt == _REFUSED_RETRIES - 1:
                raise
            time.sleep(_REFUSED_DELAY)
    raise AssertionError("unreachable")


def _send(host: str, msg: bytes, timeout: float) -> bytes:
    for attempt in range(2):
        sock = _CONNECTIONS.get(host)
        if sock is None:
            sock = _CONNECTIONS[host] = _connect(host, timeout)
        try:
            sock.sendall(msg)
            reply = sock.recv(64)
            if not reply:
                raise ConnectionResetError("connection closed by the KEF")
            return reply
        except OSError:
            sock.close()
            _CONNECTIONS.pop(host, None)
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def _drop(host: str) -> None:
    """Discard the connection so that a late reply does not arrive with the next command."""
    sock = _CONNECTIONS.pop(host, None)
    if sock is not None:
        sock.close()


def get_raw(host: str, timeout: float = 3.0) -> int:
    reply = _send(host, bytes([ord("G"), REG_SOURCE, 0x80]), timeout)
    for part in reply.split(b"R"):
        if len(part) >= 3 and part[0] == REG_SOURCE:
            return part[2]
    raise ConnectionError(f"unexpected reply: {list(reply)}")


def set_raw(host: str, value: int, timeout: float = 3.0) -> None:
    reply = _send(host, bytes([ord("S"), REG_SOURCE, 0x81, value]), timeout)
    if bytes([0x52, 0x11, 0xFF]) not in reply:
        raise ConnectionError(f"SET not acknowledged: {list(reply)}")


def wait_online(host: str, wait: float) -> int:
    """Checks via the DLNA port 8080 whether the KEF is on the network (in standby it is not).

    Deliberately does not use TCP 50001: the control port accepts only one connection and stays
    free for whoever sets the source.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            with urllib.request.urlopen(f"http://{host}:8080/description.xml", timeout=1):
                print("online")
                return 0
        except OSError:
            if time.monotonic() >= deadline:
                print("offline")
                return 2
            time.sleep(0.5)


def decode(raw: int) -> dict:
    is_on = raw < 128
    code = raw % 128
    if code == 48:  # special case according to aiokef: Wi-Fi, R/L, 60 min
        code = 82
    rl = bool(code & 64)
    code &= 63
    return {
        "on": is_on,
        "source": SOURCE_NAMES.get(code % 16, f"?{code % 16}"),
        "standby": STANDBY.get(code // 16, "?"),
        "orientation": "R/L" if rl else "L/R",
        "raw": raw,
    }


def encode(source: str, standby_idx: int, rl: bool, on: bool) -> int:
    value = SOURCES[source] + 16 * standby_idx + (64 if rl else 0)
    return value if on else value + 128


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("KEF_HOST"), help="IP address of the KEF (default: $KEF_HOST)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_on = sub.add_parser("on")
    p_on.add_argument("--source", choices=SOURCES, default=None)
    p_on.add_argument("--wait", type=float, default=0, help="seconds to wait for the KEF to become reachable")
    p_standby = sub.add_parser("standby")
    p_standby.add_argument("--only-if-source", choices=SOURCES, default=None,
                           help="only go to standby if this source is selected")
    p_as = sub.add_parser("autostandby")
    p_as.add_argument("minutes", choices=["20", "60", "never"])
    p_online = sub.add_parser("online", help="KEF on the network? (DLNA port 8080, leaves TCP 50001 free)")
    p_online.add_argument("--wait", type=float, default=0, help="seconds to wait for the KEF to become reachable")
    args = parser.parse_args()
    if not args.host:
        parser.error("no host: pass --host or set KEF_HOST")

    if args.cmd == "online":
        return wait_online(args.host, args.wait)

    deadline = time.monotonic() + args.wait if args.cmd == "on" else 0
    while True:
        try:
            state = decode(get_raw(args.host))
            break
        except OSError as err:
            if time.monotonic() >= deadline:
                print(f"KEF {args.host} not reachable: {err}", file=sys.stderr)
                return 2
            time.sleep(1)

    rl = state["orientation"] == "R/L"
    standby_idx = {v: k for k, v in STANDBY.items()}.get(state["standby"], 0)

    if args.cmd == "status":
        print(state)
        return 0
    if args.cmd == "on":
        source = args.source or state["source"]
        if state["on"] and state["source"] == source:
            print(state)
            return 0
        # Right after booting the KEF often does not acknowledge the SET or drops the connection
        # (measured up to ~20 s) - retry until --wait runs out
        while True:
            try:
                set_raw(args.host, encode(source, standby_idx, rl, on=True))
                break
            except OSError as err:
                _drop(args.host)
                if time.monotonic() >= deadline:
                    print(f"KEF {args.host}: {err}", file=sys.stderr)
                    return 3
                time.sleep(1)
                try:
                    state = decode(get_raw(args.host))
                except OSError:
                    continue
                if state["on"] and state["source"] == source:
                    print(state)
                    return 0
    elif args.cmd == "standby":
        if not state["on"]:
            print("already in standby")
            return 0
        if args.only_if_source and state["source"] != args.only_if_source:
            print(f"source {state['source']}, no standby")
            return 0
        set_raw(args.host, encode(state["source"], standby_idx, rl, on=False))
        print("standby sent")
        return 0
    elif args.cmd == "autostandby":
        idx = {"20": 0, "60": 1, "never": 2}[args.minutes]
        set_raw(args.host, encode(state["source"], idx, rl, on=state["on"]))

    target = (getattr(args, "source", None) or state["source"]) if args.cmd == "on" else None
    # Switching takes up to ~20 s; at most 5 s beyond --wait (Home Assistant aborts a
    # shell_command after 60 s)
    verify_until = max(deadline, time.monotonic()) + 5 if args.cmd == "on" else time.monotonic() + 20
    for _ in range(20):
        if time.monotonic() >= verify_until:
            break
        time.sleep(1)
        try:
            state = decode(get_raw(args.host))
        except OSError:
            continue  # the KEF occasionally drops the connection while switching
        if args.cmd == "autostandby" or (state["on"] and state["source"] == target):
            print(state)
            return 0
    print(f"target state not reached: {state}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
