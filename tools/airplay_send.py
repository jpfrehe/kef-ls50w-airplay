"""Streams a WAV file via AirPlay (RAOP) to the Music Assistant receiver - testing without an iPhone.

Run it inside the image, which has pyatv in /opt/atv:

  docker exec music-assistant ffmpeg -loglevel error -y -f lavfi \\
      -i 'sine=f=440:d=30,volume=0.03' -ar 44100 -ac 2 -sample_fmt s16 /tmp/tone.wav
  docker cp tools/airplay_send.py music-assistant:/tmp/
  docker exec music-assistant /opt/atv/bin/python3 /tmp/airplay_send.py <host-ip> /tmp/tone.wav

Note: pyatv ends the stream with an abort, so shairport-sync does not run the "stop" hook
(Home Assistant does not see "AirPlay ended"). The "start" path is the same as with an iPhone.
"""

import asyncio
import sys
import time

import pyatv
from pyatv.const import Protocol


async def main(host, path):
    loop = asyncio.get_running_loop()
    confs = [c for c in await pyatv.scan(loop, protocol=Protocol.RAOP, timeout=5) if str(c.address) == host]
    for c in confs:
        print("found:", c.name, [s.protocol.name for s in c.services], flush=True)
    if not confs:
        raise SystemExit(f"no RAOP receiver found on {host}")
    atv = await pyatv.connect(confs[0], loop)
    try:
        t = time.time()
        print(f"{time.strftime('%H:%M:%S')} stream_file start", flush=True)
        await atv.stream.stream_file(path)
        print(f"{time.strftime('%H:%M:%S')} stream_file done after {time.time() - t:.1f}s", flush=True)
    finally:
        atv.close()


asyncio.run(main(sys.argv[1], sys.argv[2]))
