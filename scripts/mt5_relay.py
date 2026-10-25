#!/usr/bin/env python3
"""Host-side file relay for the MT5 EA (Linux/Wine).

WHY THIS EXISTS
MT5's WebRequest allowlist is GUI-only and, under Wine, does not survive a
restart (proved 2026-09-23 — startup ini keys, terminal.ini/common.ini
[Experts] and a settings.ini snapshot all failed). So the EA is given a second
transport: it writes each request into its own file sandbox
(`MQL5/Files/xau_relay_req.json`) and this process — a normal host process, not
subject to WebRequest — forwards it to the service over an ordinary socket and
writes the reply to `MQL5/Files/xau_relay_resp.json`.

The sandbox lives inside the Wine prefix, which the Linux host can read and
write directly, so this needs no Windows Python and nothing installed in Wine.

Wire format (line-based, so the EA never parses nested JSON):
    request : {"id":N,"method":"POST","path":"/heartbeat","body":{...}}
              or {...,"body_b64":"<base64, for binary e.g. a screenshot>"}
    response: "<id> <status>\\n<raw body, single line>\\n"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RELAY_REQ = "xau_relay_req.json"
RELAY_RESP = "xau_relay_resp.json"
POLL_S = 0.05


def acquire_lock(root: Path) -> bool:
    """One relay per machine. O_EXCL is atomic, so a duplicate start loses.

    A lock left by a hard kill would block restarts forever, so the PID inside
    is checked for liveness and a stale lock is replaced."""
    lock = root / ".run" / "mt5-relay.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or "0")
            if pid > 0:
                os.kill(pid, 0)          # POSIX: raises if not alive
                return False             # a live relay already owns it
        except (OSError, ValueError):
            pass                         # stale or unreadable -> take it over
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            lock.unlink()
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def files_dir(prefix: Path) -> Path | None:
    """The EA's sandbox: <data path>/MQL5/Files inside the Wine prefix."""
    for candidate in sorted(prefix.glob("drive_c/**/MQL5/Files")):
        if candidate.is_dir():
            return candidate
    return None


def feed_key(service_env: Path) -> str:
    try:
        for line in service_env.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("FEED_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def call(service: str, key: str, method: str, path: str, body, body_b64) -> tuple:
    """Returns (status, body_text); status 0 means it never reached the service."""
    headers = {"X-Feed-Key": key}
    data = None
    try:
        if body_b64:
            data = base64.b64decode(body_b64)
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(service + path, data=data, headers=headers,
                                     method=method.upper())
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception:
            return exc.code, ""
    except Exception:
        return 0, ""


def process_one_req(req_path: Path, sandbox: Path, service: str, key: str,
                    last_ids: dict[str, int]) -> bool:
    try:
        request = json.loads(req_path.read_text(encoding="utf-8"))
        rid = int(request.get("id", 0))
        fname = req_path.name
        if rid != last_ids.get(fname, -1):
            status, body = call(service, key, request.get("method", "POST"),
                                request.get("path", "/"),
                                request.get("body"), request.get("body_b64"))
            flat = body.replace("\r", " ").replace("\n", " ")
            # Derive corresponding response file name from request file name:
            # xau_relay_req.json -> xau_relay_resp.json
            # xau_relay_req_BTCUSD.json -> xau_relay_resp_BTCUSD.json
            suffix = fname[len(RELAY_REQ.replace(".json", "")):-len(".json")]
            resp_name = f"xau_relay_resp{suffix}.json"
            tmp = sandbox / (resp_name + ".tmp")
            tmp.write_text(f"{rid} {status}\n{flat}\n", encoding="utf-8")
            os.replace(tmp, sandbox / resp_name)  # atomic: EA never reads a partial file
            last_ids[fname] = rid
            return True
    except Exception:
        pass
    return False


def serve(prefix: Path, service: str, key: str, once: bool = False,
          explicit_files: Path | None = None) -> int:
    sandbox = explicit_files if explicit_files is not None else files_dir(prefix)
    if sandbox is None or not sandbox.is_dir():
        print(f"mt5_relay: no MQL5/Files sandbox found", file=sys.stderr)
        return 1
    print(f"mt5_relay: watching {sandbox} -> {service}", flush=True)
    last_ids: dict[str, int] = {}
    while True:
        try:
            for req_file in list(sandbox.glob("xau_relay_req*.json")):
                if not req_file.name.endswith(".tmp"):
                    handled = process_one_req(req_file, sandbox, service, key, last_ids)
                    if handled and once:
                        return 0
        except Exception:
            pass                                    # never die: the EA must keep trading
        time.sleep(POLL_S)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prefix", default=os.environ.get("WINEPREFIX", str(Path.home() / ".mt5")),
                    help="Wine prefix (Linux); ignored when --files-dir is given")
    ap.add_argument("--files-dir", default="",
                    help="explicit path to MQL5/Files (used on Windows, where the "
                         "terminal data folder is under AppData)")
    ap.add_argument("--service", default="http://127.0.0.1:9000")
    ap.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / "service" / ".env"))
    ap.add_argument("--once", action="store_true", help="answer one request then exit")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not acquire_lock(root):
        print("mt5_relay: another relay is already running; exiting")
        return 0
    explicit = Path(args.files_dir) if args.files_dir else None
    return serve(Path(args.prefix).expanduser(), args.service.rstrip("/"),
                 feed_key(Path(args.env)), args.once, explicit)


if __name__ == "__main__":
    raise SystemExit(main())
