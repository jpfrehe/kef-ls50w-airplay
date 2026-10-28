"""Hooks kef_power_hook.sh into the session hooks of Music Assistant's shairport-sync template.

start: the hook runs BEFORE the MA_PLAY_BEGIN marker (MA only starts once the KEF is ready).
stop:  the hook runs AFTER the MA_PLAY_END marker (MA reacts without delay).
Also appends shairport-sync.conf.extra. Aborts the build if the template has changed, so that a
Music Assistant update cannot silently drop the hook.
"""

import os
import sys

import music_assistant.providers.airplay_receiver as receiver

template = os.path.join(os.path.dirname(receiver.__file__), "bin", "shairport-sync.conf")
text = open(template, encoding="utf-8").read()

replacements = {
    "'echo MA_PLAY_BEGIN > {METADATA_PIPE}'": "'/usr/local/bin/kef_power_hook.sh start; echo MA_PLAY_BEGIN > {METADATA_PIPE}'",
    "'echo MA_PLAY_END > {METADATA_PIPE}'": "'echo MA_PLAY_END > {METADATA_PIPE}; /usr/local/bin/kef_power_hook.sh stop'",
    # Shorter lead time: entrypoint.sh sets the value from SHAIRPORT_LATENCY_OFFSET on start
    "\tport = {PORT};\n": "\tport = {PORT};\n\taudio_backend_latency_offset_in_seconds = __LATENCY_OFFSET__;\n",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        sys.exit(f"Template patch failed, not found: {old}")
    text = text.replace(old, new)

text += open(sys.argv[1], encoding="utf-8").read()
open(template, "w", encoding="utf-8").write(text)
print(f"Template patched: {template}")
