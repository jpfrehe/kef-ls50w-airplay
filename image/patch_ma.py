"""Patches Music Assistant for the KEF LS50 Wireless. Aborts the build if a patched spot changed in MA.

1. Bit depth (MA_PAD_BIT_DEPTH=1): pad losslessly to the player's highest bit depth. The KEF buffers
   a fixed amount of data, with 24 bit the buffer fills sooner (3.7 -> 3.3 s) - but it sounded worse
   on the KEF, so it is off by default.
2. DLNA reconnect (MA_DLNA_RECONNECT=1): in standby the KEF disappears from the network, MA drops the
   DLNA player and would only reconnect it on the next SSDP search (every 300 s). With the patch the
   player poll starts a loop of its own that reconnects the device via setup() (as discovery does):
   every 3 s, every 0.1 s while kef_power_hook.sh waits for the KEF (/tmp/kef_wake_pending). Then
   MA writes /tmp/ma_dlna_reconnected, which the hook waits for. A loop of its own is needed because
   the player poll only runs once per second and blocks; the KEF only accepts DLNA commands
   promptly within the first ~2 s after booting.
3. No keep-alive (MA_DLNA_FORCE_CLOSE=1): MA uses one persistent HTTP connection for all DLNA
   calls. A freshly booted KEF only fetches the stream once that connection is closed (aiohttp idle
   timeout 15 s, measured 2.5-4 s after that) and stays in TRANSITIONING until then. A session of
   its own with force_close, i.e. one connection per call.
"""

import os

import music_assistant.controllers.streams.audio as audio
import music_assistant.providers.dlna.player as dlna_player
import music_assistant.providers.dlna.provider as dlna_provider


def patch(module, old: str, new: str, what: str) -> None:
    path = module.__file__
    src = open(path, encoding="utf-8").read()
    if src.count(old) != 1:
        raise SystemExit(f"MA patch failed ({what}): spot not found in {path}")
    open(path, "w", encoding="utf-8").write(src.replace(old, new))
    # Drop the bytecode cache so the patched file is loaded
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    stem = os.path.basename(path).rsplit(".", 1)[0] + "."
    for f in os.listdir(cache):
        if f.startswith(stem):
            os.remove(os.path.join(cache, f))
    print(f"MA patched ({what}):", path)


old = "        output_bit_depth = min(content_bit_depth, max(bit_depths_for_rate, default=16))\n"
patch(audio, old, old + (
    "        # kef-ls50w-airplay: pad losslessly to the player's highest bit depth (MA_PAD_BIT_DEPTH=1)\n"
    "        if __import__('os').environ.get('MA_PAD_BIT_DEPTH') == '1' and content_type.is_lossless():\n"
    "            output_bit_depth = max(bit_depths_for_rate, default=output_bit_depth)\n"
), "bit depth")

old = (
    "        if not self.device:\n"
    "            if not self.force_poll:\n"
    "                return\n"
)
patch(dlna_player, old, (
    "        if not self.device:\n"
    "            if not self.force_poll:\n"
    "                if __import__('os').environ.get('MA_DLNA_RECONNECT') != '1':\n"
    "                    return\n"
    "                # kef-ls50w-airplay: a loop of its own reconnects as soon as the device answers again\n"
    "                _task = getattr(self, '_kef_reconnect_task', None)\n"
    "                if _task is None or _task.done():\n"
    "                    self._kef_reconnect_task = asyncio.create_task(self._kef_reconnect_loop())\n"
    "                raise PlayerUnavailableError\n"
), "DLNA reconnect")

old = "    async def poll(self) -> None:\n"
patch(dlna_player, old, (
    "    async def _kef_reconnect_loop(self) -> None:\n"
    "        \"\"\"kef-ls50w-airplay: reconnect a dropped device (e.g. KEF after standby) right after it boots.\n"
    "\n"
    "        Instead of waiting for the next SSDP search (every 300 s) the port from description_url is\n"
    "        probed: every 3 s, every 0.1 s while kef_power_hook.sh waits for the KEF (WAKE_PENDING).\n"
    "        The KEF only accepts DLNA commands promptly within the first ~2 s after booting, then it\n"
    "        blocks for about 20 s - so MA has to be connected right away.\n"
    "        \"\"\"\n"
    "        import os\n"
    "        url = urlparse(self.description_url)\n"
    "        while not self.device:\n"
    "            fast = os.path.exists(KEF_WAKE_PENDING)\n"
    "            try:\n"
    "                _r, _w = await asyncio.wait_for(\n"
    "                    asyncio.open_connection(url.hostname, url.port or 80), 0.3 if fast else 1\n"
    "                )\n"
    "                _w.close()\n"
    "            except (OSError, asyncio.TimeoutError):\n"
    "                await asyncio.sleep(0.1 if fast else 3)\n"
    "                continue\n"
    "            try:\n"
    "                await self.setup()\n"
    "            except Exception as err:\n"
    "                self.logger.debug('Reconnect failed: %r', err)\n"
    "                await asyncio.sleep(0.2)\n"
    "                continue\n"
    "        # Report availability right away (otherwise only on the next event/poll) - the universal\n"
    "        # player would reject playback as 'not available'\n"
    "        await self._update_player()\n"
    "        _parent_id = self.protocol_parent_id\n"
    "        _parent = None\n"
    "        for _ in range(30):\n"
    "            _parent = self.mass.players.get_player(_parent_id) if _parent_id else None\n"
    "            if _parent is None or _parent.state.available:\n"
    "                break\n"
    "            _parent.refresh_state()\n"
    "            await asyncio.sleep(0.1)\n"
    "        self.logger.info(\n"
    "            'DLNA device %s reconnected (available: %s, %s: %s)', self.description_url,\n"
    "            self.state.available, _parent_id, _parent.state.available if _parent else None,\n"
    "        )\n"
    "        with open(KEF_RECONNECTED, 'w') as f:\n"
    "            f.write(self.description_url)\n"
    "\n"
) + old, "DLNA reconnect loop")

old = "from music_assistant.helpers.upnp import create_didl_metadata\n"
patch(dlna_player, old, old + (
    "\n# kef-ls50w-airplay: marker files shared with kef_power_hook.sh, see patch_ma.py\n"
    "KEF_WAKE_PENDING = '/tmp/kef_wake_pending'\n"
    "KEF_RECONNECTED = '/tmp/ma_dlna_reconnected'\n"
), "DLNA markers")

old = "        self.requester = AiohttpSessionRequester(self.mass.http_session, with_sleep=True)\n"
patch(dlna_provider, old, (
    "        # kef-ls50w-airplay: no keep-alive (MA_DLNA_FORCE_CLOSE=1), see patch_ma.py\n"
    "        self._kef_session = None\n"
    "        if __import__('os').environ.get('MA_DLNA_FORCE_CLOSE') == '1':\n"
    "            import aiohttp\n"
    "\n"
    "            self._kef_session = aiohttp.ClientSession(\n"
    "                connector=aiohttp.TCPConnector(force_close=True)\n"
    "            )\n"
    "        self.requester = AiohttpSessionRequester(\n"
    "            self._kef_session or self.mass.http_session, with_sleep=True\n"
    "        )\n"
), "DLNA without keep-alive")

old = '        self.mass.streams.unregister_dynamic_route("/notify", "NOTIFY")\n'
patch(dlna_provider, old, old + (
    "        if getattr(self, '_kef_session', None):\n"
    "            await self._kef_session.close()\n"
), "DLNA without keep-alive, close session")
