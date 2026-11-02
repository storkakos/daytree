"""Cloudflare tunnel: starts and stops cloudflared according to the settings."""
import os
import re
import subprocess
import threading
import time

from daytree.core import settings

status = {"proc": None, "status": "off", "error": ""}
restart = threading.Event()
TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9+/_=\-]+")


def _redact(line):
    return TOKEN_RE.sub("***", line)


def _reader(proc):
    for line in proc.stdout:
        if "Registered tunnel connection" in line:
            status.update(status="connected", error="")
        elif " ERR " in line:
            status["error"] = _redact(line.strip())[-200:]
    proc.wait()
    if status["proc"] is proc:
        status["status"] = "disconnected"


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


def supervisor():
    """Start/stop cloudflared according to the settings. The token is passed via environment only."""
    retry_at = 0.0
    while True:
        try:
            want = bool(settings["tunnel_enabled"] and settings["tunnel_token"])
            proc = status["proc"]
            running = proc is not None and proc.poll() is None
            if restart.is_set():
                restart.clear()
                if running:
                    _stop(proc)
                    running = False
                retry_at = 0.0
            if running and not want:
                _stop(proc)
                status.update(proc=None, status="off", error="")
            elif want and not running and time.time() >= retry_at:
                retry_at = time.time() + 15
                status.update(status="connecting", error="")
                env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
                env["TUNNEL_TOKEN"] = settings["tunnel_token"]
                try:
                    proc = subprocess.Popen(
                        ["cloudflared", "tunnel", "--no-autoupdate", "run"],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, text=True, bufsize=1,
                    )
                    status["proc"] = proc
                    threading.Thread(target=_reader, args=(proc,), daemon=True).start()
                except FileNotFoundError:
                    status.update(proc=None, status="error", error="cloudflared is not present in the image")
            elif not want and not running and status["status"] != "off":
                status.update(proc=None, status="off", error="")
        except Exception as e:  # noqa: BLE001
            status.update(status="error", error=str(e)[:200])
        time.sleep(3)
