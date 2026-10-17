"""Record docs/assets/demo.gif from a real odoobuddy session.

The session runs in a throw-away HOME with two example instances, and the install is a
--dry-run, so nothing on this machine changes.

Needs: pip install pexpect, and agg (https://github.com/asciinema/agg/releases) on PATH.
Run:   python scripts/record_demo.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pexpect

COLS, ROWS = 100, 30
UP, DOWN, ENTER, CLEAR = "\x1b[A", "\x1b[B", "\r", "\x15"
REPO = Path(__file__).resolve().parent.parent


def make_sandbox(home: Path) -> None:
    code = """
from odoobuddy.layout import Layout
from odoobuddy.models import Instance
from odoobuddy.registry import Registry
lay = Layout.default(); reg = Registry(lay)
for name, ver, ed, port, ide in (("shop", "18.0", "community", 8069, "vscode"),
                                 ("crm19", "19.0", "enterprise", 8169, "pycharm")):
    d = lay.instance_dir("local", name)
    reg.add(Instance(name=name, version=ver, edition=ed, mode="local", http_port=port,
        gevent_port=port + 3, db_user=f"odoo_{name}", python="3.12", instance_dir=d,
        config_path=lay.config_path("local", name), log_path=d / "logs" / "odoo.log",
        data_dir=d / "data", venv_dir=d / "venv", community_dir=d / "odoo" / "odoo",
        enterprise_dir=d / "odoo" / "enterprise" if ed == "enterprise" else None,
        ide=ide, run_mode="editor", created_at="2026-10-14T09:00:00+00:00"))
"""
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "HOME": str(home)}, check=True)


def record(home: Path, cast: Path) -> None:
    events: list[list[object]] = []
    start = time.monotonic()

    class Recorder:
        def write(self, data: str) -> None:
            events.append([round(time.monotonic() - start, 3), "o", data])

        def flush(self) -> None:
            pass

    rec = Recorder()
    env = {**os.environ, "HOME": str(home), "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"}

    def type_command(cmd: str) -> None:
        rec.write("\r\n\x1b[1;32m$\x1b[0m ")
        time.sleep(0.4)
        for ch in cmd:
            rec.write(ch)
            time.sleep(0.06)
        time.sleep(0.4)
        rec.write("\r\n")

    def spawn(args: list[str]) -> pexpect.spawn:
        child = pexpect.spawn(
            "odoobuddy", args, env=env, dimensions=(ROWS, COLS), encoding="utf-8", timeout=90
        )
        child.logfile_read = rec
        return child

    def answer(child: pexpect.spawn, prompt: str, *keys: str, pause: float = 0.9) -> None:
        child.expect(prompt)
        time.sleep(pause)
        for key in keys:
            child.send(key)
            time.sleep(0.35)

    def pointing_at(child: pexpect.spawn, title: str) -> None:
        """Stop unless the menu's pointer is on ``title`` (Install now would install for real)."""
        child.expect(rf"▶ (?:\x1b\[[0-9;]*m)*{title}", timeout=10)

    type_command("odoobuddy install --dry-run")
    c = spawn(["install", "--dry-run"])
    answer(c, "Press Enter to continue", ENTER, pause=2.0)
    answer(c, "Press Enter to start the questions", ENTER, pause=3.5)
    # Show the explanations change under the menu, then go back to Development.
    answer(c, "What is this Odoo for", UP, UP, pause=1.2)
    time.sleep(1.2)
    for key in (DOWN, DOWN, ENTER):
        c.send(key)
        time.sleep(0.8)
    answer(c, "Which Odoo version", DOWN, ENTER)
    answer(c, "Which edition", ENTER)
    answer(c, "Instance name", CLEAR, *"shop19", ENTER)
    answer(c, "How do you want to run Odoo", ENTER, pause=2.0)
    # Review: Install now, Dry run, Change a setting, ...
    answer(c, "Ready to install", DOWN, DOWN, pause=2.5)
    pointing_at(c, "Change a setting")
    c.send(ENTER)
    answer(c, "Which setting", DOWN, DOWN, DOWN, DOWN, ENTER)  # Port
    answer(c, "Which port", CLEAR, *"8469", ENTER)
    # Never Install now here: in a --dry-run session it would install for real.
    answer(c, "Ready to install", DOWN, pause=2.0)
    pointing_at(c, "Dry run")
    time.sleep(1.0)
    c.send(ENTER)
    c.expect(pexpect.EOF, timeout=120)
    time.sleep(2)
    type_command("odoobuddy list")
    spawn(["list"]).expect(pexpect.EOF)
    time.sleep(2.5)
    type_command("odoobuddy crm19 info")
    spawn(["crm19", "info"]).expect(pexpect.EOF)
    time.sleep(5)

    header = {"version": 2, "width": COLS, "height": ROWS, "env": {"TERM": "xterm-256color"}}
    cast.write_text("\n".join(json.dumps(e) for e in [header, *events]) + "\n", encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="odoobuddy-demo-") as tmp:
        home, cast = Path(tmp) / "home", Path(tmp) / "demo.cast"
        home.mkdir()
        make_sandbox(home)
        record(home, cast)
        target = REPO / "docs" / "assets" / "demo.gif"
        subprocess.run(
            [
                "agg",
                "--font-size",
                "15",
                "--idle-time-limit",
                "2.5",
                "--speed",
                "1.15",
                "--theme",
                "monokai",
                "--last-frame-duration",
                "4",
                str(cast),
                str(target),
            ],
            check=True,
        )
        print(f"wrote {target}")


if __name__ == "__main__":
    main()
