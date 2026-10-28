#!/usr/bin/env python3
"""Fills the Home Assistant templates in homeassistant/ with the values from .env.

  python3 tools/render_ha.py [--env .env] [--out build/homeassistant]

Replaces ${VAR} placeholders (Jinja {{ ... }} is left alone) and refuses to write anything if a
variable is missing or still set to a placeholder from .env.example. Copy the rendered snippets
into your Home Assistant configuration (see README.md).
"""

import argparse
import pathlib
import re
import string
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# values from .env.example that still have to be replaced
PLACEHOLDER = re.compile(r"CHANGE_ME|^(00:){7}00$")


def read_env(path: pathlib.Path) -> dict:
    values = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            sys.exit(f"{path}:{n}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=ROOT / ".env", type=pathlib.Path)
    parser.add_argument("--out", default=ROOT / "build" / "homeassistant", type=pathlib.Path)
    args = parser.parse_args()

    env = read_env(args.env)
    rendered = {}
    for tpl in sorted((ROOT / "homeassistant").glob("*.yaml")):
        if tpl.name.startswith("."):
            continue  # e.g. macOS metadata files
        text = tpl.read_text(encoding="utf-8")
        names = {m.group(1) for m in re.finditer(r"\$\{(\w+)\}", text)}
        missing = sorted(n for n in names if not env.get(n) or PLACEHOLDER.search(env[n]))
        if missing:
            sys.exit(f"{tpl.name}: please set {', '.join(missing)} in {args.env}")
        rendered[tpl.name] = string.Template(text).substitute(env)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (args.out / name).write_text(text, encoding="utf-8")
        print(f"written: {args.out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
