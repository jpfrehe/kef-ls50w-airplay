"""Reports to Home Assistant via webhooks whether the Apple TV is active (switched on or playing).

Active means: power state "On" or playback "Playing". The power state is pushed immediately when
the Apple TV is switched on - so the KEF already boots while you are still in the menu instead of
only when playback starts. Playback stays as a second signal because pyatv has reported the power
state as permanently "Off" with some tvOS versions (the HA integration may show the Apple TV as
"off" for that reason).

The pairing credentials are read from Home Assistant's Apple TV integration
(HA_STORAGE/core.config_entries, mounted read-only), so the Apple TV only has to be paired once.

Environment:
  ATV_NAME          title of the Apple TV entry in Home Assistant (required)
  HA_STORAGE        path to HA's .storage directory (default /ha-storage)
  WEBHOOK_ACTIVE    URL called when the Apple TV becomes active
  WEBHOOK_INACTIVE  URL called when it is no longer active (off and not playing)
  WEBHOOK_POWER_OFF URL called when the Apple TV is switched off (power On -> Off; not on
                    pause/end of playback). Optional.
  RESEND_SECONDS    re-send the current state periodically (default 600)
"""

import asyncio
import json
import logging
import os
import urllib.request

import pyatv
from pyatv import interface
from pyatv.const import DeviceState, PowerState, Protocol

ATV_NAME = os.environ["ATV_NAME"]
HA_STORAGE = os.environ.get("HA_STORAGE", "/ha-storage")
WEBHOOK_ACTIVE = os.environ["WEBHOOK_ACTIVE"]
WEBHOOK_INACTIVE = os.environ["WEBHOOK_INACTIVE"]
WEBHOOK_POWER_OFF = os.environ.get("WEBHOOK_POWER_OFF", "")
RESEND_SECONDS = int(os.environ.get("RESEND_SECONDS", "600"))
POLL_SECONDS = 15

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("atv_watcher")


def load_entry() -> dict:
    with open(os.path.join(HA_STORAGE, "core.config_entries"), encoding="utf-8") as f:
        entries = json.load(f)["data"]["entries"]
    for entry in entries:
        if entry["domain"] == "apple_tv" and entry["title"] == ATV_NAME and entry.get("source") != "ignore":
            return entry
    raise RuntimeError(f"no Apple TV entry '{ATV_NAME}' found in Home Assistant")


def post(url: str) -> None:
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            # log the webhook name without its secret suffix
            log.info("webhook %s -> HTTP %s", url.rsplit("/", 1)[-1].rsplit("_", 1)[0], resp.status)
    except OSError as err:
        log.warning("webhook failed: %s", err)


class Watcher(interface.PushListener, interface.PowerListener, interface.DeviceListener):
    def __init__(self) -> None:
        self.active: bool | None = None
        self.power_on = False
        self.playing = False
        self.lost = asyncio.Event()

    # PushListener
    def playstatus_update(self, updater, playstatus) -> None:
        self.playing = playstatus.device_state == DeviceState.Playing
        self.update(f"playback {playstatus.device_state.name} {playstatus.title!r}", "push")

    def playstatus_error(self, updater, exception) -> None:
        log.warning("push error: %r", exception)

    # PowerListener
    def powerstate_update(self, old_state: PowerState, new_state: PowerState) -> None:
        self.set_power(new_state == PowerState.On, "push")
        self.update(f"power {new_state.name}", "push")

    def set_power(self, on: bool, source: str) -> None:
        """Only an observed change On -> Off counts as switching off (not the initial state)."""
        if self.power_on and not on:
            log.info("Apple TV switched off (via %s)", source)
            if WEBHOOK_POWER_OFF:
                post(WEBHOOK_POWER_OFF)
        self.power_on = on

    # DeviceListener
    def connection_lost(self, exception) -> None:
        log.warning("connection lost: %r", exception)
        self.lost.set()

    def connection_closed(self) -> None:
        log.info("connection closed")
        self.lost.set()

    def update(self, reason: str, source: str, force: bool = False) -> None:
        active = self.power_on or self.playing
        if active != self.active or force:
            if active != self.active:
                log.info("Apple TV %s (%s, via %s)", "active" if active else "not active", reason, source)
            self.active = active
            post(WEBHOOK_ACTIVE if active else WEBHOOK_INACTIVE)


async def run_once(watcher: Watcher) -> None:
    entry = load_entry()
    data = entry["data"]
    loop = asyncio.get_running_loop()
    identifier = entry.get("unique_id")
    confs = await pyatv.scan(loop, identifier=identifier, timeout=5) if identifier else []
    if not confs and data.get("address"):
        confs = await pyatv.scan(loop, hosts=[data["address"]], timeout=5)
    if not confs:
        raise ConnectionError("Apple TV not found")
    conf = confs[0]
    for proto_id, cred in data.get("credentials", {}).items():
        proto = Protocol(int(proto_id))
        if conf.get_service(proto) is not None:
            conf.set_credentials(proto, cred)

    atv = await pyatv.connect(conf, loop)
    watcher.lost.clear()
    try:
        log.info("connected to %s (%s, tvOS %s)", conf.name, conf.device_info.model, conf.device_info.version)
        atv.listener = watcher
        atv.power.listener = watcher
        atv.push_updater.listener = watcher
        atv.push_updater.start()
        since_resend = 0
        while not watcher.lost.is_set():
            # fallback poll in case push updates do not arrive
            playing = await atv.metadata.playing()
            watcher.playing = playing.device_state == DeviceState.Playing
            watcher.set_power(atv.power.power_state == PowerState.On, "poll")
            since_resend += POLL_SECONDS
            force = since_resend >= RESEND_SECONDS or watcher.active is None
            if force:
                since_resend = 0
            watcher.update(f"power {atv.power.power_state.name}, playback {playing.device_state.name}",
                           "poll", force=force)
            try:
                await asyncio.wait_for(watcher.lost.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        atv.close()


async def main() -> None:
    watcher = Watcher()
    delay = 5
    while True:
        try:
            await run_once(watcher)
            delay = 5
        except Exception as err:  # noqa: BLE001 - keep the service running
            log.warning("error: %r - retrying in %ss", err, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 120)


if __name__ == "__main__":
    asyncio.run(main())
