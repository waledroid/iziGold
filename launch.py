#!/usr/bin/env python3
"""Cross-platform staged launcher for XAU Assistant.

Docker owns the Linux services. MT5 remains a host process: native on Windows
or inside the user's Wine prefix on Linux. The launcher is deliberately
fail-open around MT5 and the optional feed bridge; the service stack can still
be used while either host component is being configured.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "service" / ".env"
ENV_EXAMPLE = ROOT / "service" / ".env.example"
COMPOSE_FILE = ROOT / "compose.yaml"
MT5_INSTALLER = "https://download.terminal.free/cdn/web/metaquotes.software.corp/mt5/mt5linux.sh"
PYTHON_WINDOWS_INSTALLER = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"


class LaunchError(RuntimeError):
    pass


class Progress:
    def __init__(self, total: int):
        self.total = total
        self.done = 0

    def stage(self, name: str) -> None:
        self.done += 1
        width = 28
        filled = int(width * self.done / self.total)
        bar = "#" * filled + "." * (width - filled)
        print(f"\n[{self.done}/{self.total}] [{bar}] {name}", flush=True)

    @staticmethod
    def note(message: str) -> None:
        print(f"      {message}", flush=True)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def wineserver_command() -> str:
    direct = shutil.which("wineserver")
    if direct:
        return direct
    for candidate in Path("/usr/lib").glob("*/wine/wineserver"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "wineserver"


def run(command: list[str], *, check: bool = True, dry_run: bool = False,
        env: dict[str, str] | None = None,
        cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    shown = " ".join(command)
    Progress.note(f"$ {shown}")
    if dry_run:
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(command, check=check, text=True, env=env,
                          cwd=str(cwd) if cwd else None)


def run_capture(command: list[str], *, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL,
                                   env=env).strip()


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("\"'")
    return values


def write_env_value(key: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith(prefix) and not replaced:
            output.append(f"{prefix}{value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{prefix}{value}")
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def ensure_env(dry_run: bool) -> None:
    if not ENV_FILE.exists():
        if dry_run:
            Progress.note(f"would create {ENV_FILE} from {ENV_EXAMPLE}")
        else:
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            Progress.note("created service/.env from service/.env.example")
    values = read_env()
    if not values.get("FEED_KEY"):
        if dry_run:
            Progress.note("would generate service/.env FEED_KEY")
        else:
            write_env_value("FEED_KEY", secrets.token_hex(24))
            Progress.note("generated service/.env FEED_KEY")


def install_linux_packages(dry_run: bool) -> None:
    if (command_exists("wine") and command_exists("docker")
            and command_exists("winetricks")):
        return
    if not command_exists("sudo"):
        raise LaunchError("sudo is required to install missing Linux prerequisites")
    run(["sudo", "dpkg", "--add-architecture", "i386"], dry_run=dry_run)
    run(["sudo", "apt-get", "update"], dry_run=dry_run)
    packages = ["docker.io", "docker-compose-plugin", "wine", "wine64",
                "wine32:i386", "winetricks", "cabextract", "unzip", "wget"]
    run(["sudo", "apt-get", "install", "-y", *packages], dry_run=dry_run)


def install_windows_packages(dry_run: bool) -> None:
    if not command_exists("winget") and (
            not command_exists("docker") or not command_exists("py")
            and not command_exists("python")):
        raise LaunchError("a required Windows package is missing and winget is unavailable")
    if not command_exists("docker"):
        run(["winget", "install", "--id", "Docker.DockerDesktop", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
            dry_run=dry_run)
    if not command_exists("py") and not command_exists("python"):
        run(["winget", "install", "--id", "Python.Python.3.12", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
            dry_run=dry_run)


def find_wine_executable(prefix: Path, name: str) -> Path | None:
    matches = list(prefix.rglob(name)) if prefix.exists() else []
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def find_windows_mt5() -> Path | None:
    roots = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
             Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))]
    for root in roots:
        candidate = root / "MetaTrader 5" / "terminal64.exe"
        if candidate.exists():
            return candidate
    return None


def wine_env(prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix)
    env["WINEARCH"] = "win64"
    env["WINEDEBUG"] = "-all"
    return env


def ensure_mt5_linux(prefix: Path, dry_run: bool) -> tuple[Path | None, Path | None]:
    terminal = find_wine_executable(prefix, "terminal64.exe")
    editor = find_wine_executable(prefix, "MetaEditor64.exe")
    if terminal:
        return terminal, editor
    installer = ROOT / ".run" / "mt5linux.sh"
    if dry_run:
        Progress.note(f"would download and run MetaQuotes MT5 installer in {prefix}")
        return None, None
    installer.parent.mkdir(parents=True, exist_ok=True)
    Progress.note("MT5 was not found in the Wine prefix; downloading MetaQuotes installer")
    with urlopen(MT5_INSTALLER, timeout=30) as response:
        installer.write_bytes(response.read())
    installer.chmod(0o755)
    env = wine_env(prefix)
    run([str(installer)], env=env)
    terminal = find_wine_executable(prefix, "terminal64.exe")
    editor = find_wine_executable(prefix, "MetaEditor64.exe")
    return terminal, editor


def mt5_running_windows() -> bool:
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
                            capture_output=True, text=True, check=False)
    return "terminal64.exe" in result.stdout.lower()


def mt5_running_linux() -> bool:
    result = subprocess.run(["pgrep", "-af", "terminal64.exe"],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0


def wine_path(path: Path, prefix: Path) -> str:
    try:
        return run_capture(["winepath", "-w", str(path)], env=wine_env(prefix))
    except (OSError, subprocess.CalledProcessError):
        relative = path.relative_to(Path.home())
        return "Z:\\home\\" + str(relative).replace("/", "\\")


def launch_mt5(system: str, terminal: Path, prefix: Path, dry_run: bool,
               restart: bool = False) -> None:
    log_path = ROOT / ".run" / "mt5-launch.log"
    if not dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def start_detached(command: list[str], env: dict[str, str] | None = None) -> None:
        Progress.note(f"starting detached: {' '.join(command)}")
        if dry_run:
            return
        with log_path.open("a", encoding="utf-8") as stream:
            kwargs = {
                "stdout": stream,
                "stderr": subprocess.STDOUT,
                "cwd": str(ROOT),
                "env": env,
            }
            if system == "Windows":
                kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(command, **kwargs)

    if system == "Windows":
        if mt5_running_windows():
            if not restart or dry_run:
                Progress.note("native MT5 is already running")
                return
            stop_mt5(system, prefix, dry_run)
            if not dry_run:
                time.sleep(2)
        command = [str(terminal), f"/config:{ROOT / 'scripts' / 'mt5-start.ini'}"]
        start_detached(command)
        return
    if mt5_running_linux():
        if not restart or dry_run:
            Progress.note("Wine MT5 is already running")
            return
        stop_mt5(system, prefix, dry_run)
    command = ["wine", str(terminal)]
    config = ROOT / "scripts" / "mt5-start.ini"
    if config.exists():
        command.append(f"/config:{wine_path(config, prefix)}")
    start_detached(command, env=wine_env(prefix))


def stop_mt5(system: str, prefix: Path, dry_run: bool = False) -> bool:
    """Close MT5 so it FLUSHES its settings to disk. Returns True if it stopped.

    MT5 keeps the WebRequest allowlist (and other Tools>Options state) in
    `Config/settings.ini`, which is BINARY/obfuscated and is only written on a
    clean exit. A hard `wineserver -k` skips that write, which is exactly how
    the allowlist kept disappearing and produced recurring `err=4014` after
    launcher restarts. Always prefer the graceful close; the force-kill is a
    last resort and costs the allowlist.
    """
    if system == "Windows":
        if not mt5_running_windows():
            return True
        run(["taskkill", "/IM", "terminal64.exe", "/T"], check=False, dry_run=dry_run)
        if not dry_run:
            for _ in range(10):
                if not mt5_running_windows():
                    return True
                time.sleep(1)
        return not mt5_running_windows()
    if not mt5_running_linux():
        return True
    Progress.note("closing Wine MT5 gracefully so terminal settings persist")
    run(["wine", r"C:\windows\system32\taskkill.exe", "/IM",
         "terminal64.exe"], check=False, dry_run=dry_run, env=wine_env(prefix))
    if dry_run:
        return True
    # 30 s, not 10: MT5 under Wine routinely needs longer to flush
    # Config/settings.ini, and a premature force-kill can truncate it (that file
    # holds the WebRequest allowlist, which is GUI-only and painful to redo).
    for _ in range(30):
        if not mt5_running_linux():
            return True
        time.sleep(1)
    if mt5_running_linux():
        Progress.note("WARNING: graceful close timed out — force-killing wineserver "
                      "(the WebRequest allowlist may need to be re-set in the GUI)")
        run([wineserver_command(), "-k"], check=False, env=wine_env(prefix))
        time.sleep(3)
    return not mt5_running_linux()


def find_mt5_data_dir(system: str, prefix: Path) -> Path | None:
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        roots = [Path(appdata) / "MetaQuotes" / "Terminal"] if appdata else []
    else:
        roots = [prefix / "drive_c" / "users" / os.environ.get("USER", "user") /
                 "AppData" / "Roaming" / "MetaQuotes" / "Terminal"]
    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend(path / "MQL5" for path in root.iterdir()
                             if path.is_dir() and (path / "MQL5").is_dir())
    # A terminal started with a portable/config profile keeps MQL5 beside the
    # executable instead of under AppData. This is common with the bundled
    # Wine startup config and must be preferred over treating the data folder
    # as missing.
    search_root = prefix if system != "Windows" else Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    if search_root.exists():
        candidates.extend(path for path in search_root.rglob("MQL5")
                          if path.is_dir() and (path / "Experts").exists())
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def make_chart_chr(symbol: str, desc: str, period_size: int, chart_id: int = 130000000000000001) -> bytes:
    txt = (
        f"<chart>\r\n"
        f"id={chart_id}\r\n"
        f"symbol={symbol}\r\n"
        f"description={desc}\r\n"
        f"period_type=0\r\n"
        f"period_size={period_size}\r\n"
        f"digits=2\r\n"
        f"scale=8\r\n"
        f"mode=1\r\n"
        f"fore=0\r\n"
        f"grid=0\r\n"
        f"volume=0\r\n"
        f"scroll=1\r\n"
        f"shift=1\r\n"
        f"shift_size=20.000000\r\n"
        f"background_color=0\r\n"
        f"foreground_color=16777215\r\n"
        f"barup_color=65280\r\n"
        f"bardown_color=65280\r\n"
        f"bullcandle_color=0\r\n"
        f"bearcandle_color=16777215\r\n"
        f"chartline_color=65280\r\n"
        f"windows_total=0\r\n"
        f"<expert>\r\n"
        f"name=iziBot\r\n"
        f"path=Experts\\iziBot.ex5\r\n"
        f"expertmode=1\r\n"
        f"flags=3\r\n"
        f"<inputs>\r\n"
        f"</inputs>\r\n"
        f"</expert>\r\n"
        f"</chart>\r\n"
    )
    return txt.encode("utf-16")


def ensure_chart_templates(data_dir: Path, dry_run: bool) -> None:
    """Installs iziBot.tpl and XauAssistant.tpl to MT5 templates directories and pre-provisions
    the multi-asset Default profile (XAUUSD M15 and BTCUSD M5) so both charts
    immediately have the EA attached with auto-trading enabled upon launch."""
    terminal_root = data_dir.parent if data_dir.name == "MQL5" else data_dir
    target_dirs = [
        terminal_root / "Profiles" / "Templates",
        data_dir / "Profiles" / "Templates",
        data_dir / "Experts",
        data_dir / "Files",
    ]
    izibot_tpl = ROOT / "mt5" / "Templates" / "iziBot.tpl"
    xau_tpl = ROOT / "mt5" / "Templates" / "XauAssistant.tpl"
    for tdir in target_dirs:
        if not dry_run:
            tdir.mkdir(parents=True, exist_ok=True)
            if izibot_tpl.exists():
                shutil.copy2(izibot_tpl, tdir / "iziBot.tpl")
                shutil.copy2(izibot_tpl, tdir / "default.tpl")
            if xau_tpl.exists():
                shutil.copy2(xau_tpl, tdir / "XauAssistant.tpl")

    charts_dirs = [
        terminal_root / "Profiles" / "Charts" / "Default",
        data_dir / "Profiles" / "Charts" / "Default",
    ]
    prog_files_charts = Path.home() / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5" / "Profiles" / "Charts" / "Default"
    if prog_files_charts.parent.parent.exists():
        charts_dirs.append(prog_files_charts)
    xau_data = make_chart_chr("XAUUSD", "Gold vs US Dollar", 15, 130000000000000001)
    btc_data = make_chart_chr("BTCUSD", "Bitcoin vs US Dollar", 5, 130000000000000002)
    order_data = "chart01.chr\r\nchart02.chr\r\n".encode("utf-16")

    if not dry_run:
        for cdir in charts_dirs:
            cdir.mkdir(parents=True, exist_ok=True)
            for f in list(cdir.glob("chart*.chr")):
                if f.name not in ("chart01.chr", "chart02.chr"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
            (cdir / "chart01.chr").write_bytes(xau_data)
            (cdir / "chart02.chr").write_bytes(btc_data)
            (cdir / "order.wnd").write_bytes(order_data)

    Progress.note("multi-asset chart profiles and templates installed (XAUUSD M15 + BTCUSD M5)")


def install_ea_sources(system: str, prefix: Path, editor: Path | None,
                        dry_run: bool) -> None:
    data_dir = find_mt5_data_dir(system, prefix)
    if data_dir is None:
        Progress.note("MT5 data folder is not ready yet; attach MT5 once, then re-run")
        return
    experts = data_dir / "Experts"
    includes = data_dir / "Include" / "XauAssistant"
    if not dry_run:
        experts.mkdir(parents=True, exist_ok=True)
        for mq_name in ("iziBot.mq5", "XauAssistant.mq5"):
            src = ROOT / "mt5" / "Experts" / mq_name
            if src.exists():
                shutil.copy2(src, experts / mq_name)
        if includes.exists():
            shutil.rmtree(includes)
        shutil.copytree(ROOT / "mt5" / "Include" / "XauAssistant", includes)
    Progress.note(f"EA sources copied to {data_dir}")
    ensure_chart_templates(data_dir, dry_run)
    if editor is None:
        Progress.note("MetaEditor not found; compile iziBot.mq5 manually")
        return
    source = experts / "iziBot.mq5"
    if system == "Windows":
        command = [str(editor), f'/compile:"{source}"', "/log"]
        run(command, check=False, dry_run=dry_run)
        output = experts / "iziBot.ex5"
    else:
        # MetaEditor under Wine can silently skip compilation when the source
        # path contains spaces. The default MT5 install lives under
        # `Program Files`, so expose the same directory through a short alias.
        alias_root = prefix / "drive_c" / "MT5"
        install_root = editor.parent
        if not dry_run:
            if not alias_root.exists():
                alias_root.symlink_to(install_root, target_is_directory=True)
            # Ensure sources are also copied to the install root where MetaEditor compiles them
            inst_experts = install_root / "MQL5" / "Experts"
            inst_includes = install_root / "MQL5" / "Include" / "XauAssistant"
            inst_experts.mkdir(parents=True, exist_ok=True)
            for mq_name in ("iziBot.mq5", "XauAssistant.mq5"):
                src = ROOT / "mt5" / "Experts" / mq_name
                if src.exists():
                    shutil.copy2(src, inst_experts / mq_name)
            if inst_includes.exists():
                shutil.rmtree(inst_includes)
            shutil.copytree(ROOT / "mt5" / "Include" / "XauAssistant", inst_includes)

        source = install_root / "MQL5" / "Experts" / "iziBot.mq5"
        command = ["wine", r"C:\MT5\MetaEditor64.exe",
                   r"/compile:C:\MT5\MQL5\Experts\iziBot.mq5",
                   r"/inc:C:\MT5\MQL5",
                   "/log"]
        run(command, check=False, dry_run=dry_run, env=wine_env(prefix),
            cwd=alias_root)
        output = install_root / "MQL5" / "Experts" / "iziBot.ex5"
    if not dry_run:
        # Freshness, not existence: MetaEditor can exit 0 without producing a
        # new .ex5 (an existing instance, a stale lock, a silent skip), and
        # "the file is there" is exactly how a stale EA ran unnoticed. Wait for
        # the .ex5 to be at least as new as the source we just copied.
        deadline = time.time() + 60
        fresh = False
        while time.time() < deadline:
            try:
                fresh = (output.exists() and source.exists()
                         and output.stat().st_mtime >= source.stat().st_mtime)
            except OSError:
                fresh = False
            if fresh:
                break
            time.sleep(1)
        if fresh and output.exists():
            # Synchronize compiled .ex5 to both iziBot and XauAssistant names
            for ex_name in ("iziBot.ex5", "XauAssistant.ex5"):
                for dest_dir in (experts, ROOT / "mt5" / "Experts",
                                 (install_root / "MQL5" / "Experts") if (system != "Windows" and install_root is not None) else None):
                    if dest_dir is not None:
                        dest = dest_dir / ex_name
                        try:
                            if dest.resolve() != output.resolve():
                                shutil.copy2(output, dest)
                        except OSError:
                            pass
    else:
        fresh = True
    if dry_run or fresh:
        Progress.note("EA compiled (iziBot.ex5 is fresh)")
    else:
        Progress.note("WARNING: compile did not refresh iziBot.ex5 — "
                      "restarting MT5 lets the terminal compile it, or press F7 "
                      "in MetaEditor")


def start_windows_bridge(dry_run: bool) -> None:
    interpreters = []
    if command_exists("py"):
        interpreters.append(["py", "-3"])
    for name in ("python", "python3"):
        if command_exists(name):
            interpreters.append([name])
    for interpreter in interpreters:
        probe = subprocess.run([*interpreter, "-c", "import MetaTrader5"],
                               capture_output=True, check=False)
        if probe.returncode != 0:
            Progress.note(f"installing MetaTrader5 package into {' '.join(interpreter)}")
            run([*interpreter, "-m", "pip", "install", "MetaTrader5"],
                check=False, dry_run=dry_run)
            probe = subprocess.run([*interpreter, "-c", "import MetaTrader5"],
                                   capture_output=True, check=False)
            if probe.returncode != 0:
                continue
        script = ROOT / "bridge" / "mt5_feed.py"
        log = ROOT / "bridge_stdout.log"
        if dry_run:
            Progress.note(f"would start {' '.join(interpreter)} {script}")
            return
        with log.open("a", encoding="utf-8") as stream:
            subprocess.Popen([*interpreter, str(script)], stdout=stream,
                             stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        Progress.note("Windows MT5 feed bridge started")
        return
    Progress.note("Windows bridge skipped: install Windows Python + MetaTrader5 package")


def bridge_running_linux() -> bool:
    result = subprocess.run(["pgrep", "-af", "mt5_feed.py"],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0


def start_linux_bridge(prefix: Path, dry_run: bool, allow_install: bool) -> None:
    """Run the read-only bridge with Windows Python inside the MT5 prefix."""
    if bridge_running_linux():
        Progress.note("Linux/Wine MT5 feed bridge is already running")
        return
    python = find_wine_executable(prefix, "python.exe")
    if python is None:
        if not allow_install:
            Progress.note("WARNING: Wine Python is missing; rerun without --no-install to enable the bridge")
            return
        installer = ROOT / ".run" / "python-3.12.10-amd64.exe"
        if dry_run:
            Progress.note(f"would install Windows Python into {prefix}")
            return
        installer.parent.mkdir(parents=True, exist_ok=True)
        Progress.note("Wine Python not found; downloading Python 3.12 for the bridge")
        with urlopen(PYTHON_WINDOWS_INSTALLER, timeout=60) as response:
            installer.write_bytes(response.read())
        run(["wine", str(installer), "/quiet", "InstallAllUsers=1",
             "PrependPath=1", "Include_test=0"], env=wine_env(prefix), check=False)
        deadline = time.time() + 30
        while python is None and time.time() < deadline:
            time.sleep(1)
            python = find_wine_executable(prefix, "python.exe")
    if python is None:
        Progress.note("WARNING: Wine Python installation did not complete")
        return

    env = wine_env(prefix)
    probe = subprocess.run(["wine", str(python), "-c", "import MetaTrader5"],
                           capture_output=True, check=False, env=env)
    if probe.returncode != 0:
        if not allow_install:
            Progress.note("WARNING: Wine Python lacks MetaTrader5; rerun without --no-install")
            return
        Progress.note("installing MetaTrader5 into Wine Python")
        run(["wine", str(python), "-m", "pip", "install", "MetaTrader5",
             "numpy<2.0"],
            check=False, env=env)
        probe = subprocess.run(["wine", str(python), "-c", "import MetaTrader5"],
                               capture_output=True, check=False, env=env)
    if probe.returncode != 0:
        Progress.note("WARNING: Wine Python cannot import MetaTrader5; bridge skipped")
        return

    script = ROOT / "bridge" / "mt5_feed.py"
    log = ROOT / "bridge_wine_stdout.log"
    if dry_run:
        Progress.note(f"would start Wine Python bridge: {script}")
        return
    with log.open("a", encoding="utf-8") as stream:
        subprocess.Popen(["wine", str(python), wine_path(script, prefix)],
                         stdout=stream, stderr=subprocess.STDOUT,
                         env=env, cwd=str(ROOT), start_new_session=True)
    Progress.note("Linux/Wine MT5 feed bridge started")


def start_host_relay(system: str, prefix: Path, dry_run: bool) -> None:
    """Start the EA's file relay as a HOST process (both platforms).

    The relay exists because MT5's WebRequest allowlist does not persist under
    Wine, so the EA falls back to writing requests into its MQL5/Files sandbox.
    This process answers them. It is a normal host process, so it needs no
    WebRequest permission and nothing installed inside Wine.

    It runs on Windows too: there the allowlist persists and HTTP normally wins,
    but starting the relay means the EA's FILE fallback is complete rather than a
    dead end if WebRequest is ever refused.
    """
    script = ROOT / "scripts" / "mt5_relay.py"
    if not script.is_file():
        Progress.note("host relay script missing; file transport would be unavailable")
        return
    running = subprocess.run(["pgrep", "-f", "mt5_relay.py"],
                             capture_output=True, text=True, check=False).returncode == 0
    if system == "Windows":
        # No pgrep there, and the relay is idempotent enough for the single
        # launcher invocation that starts it; a duplicate is prevented by the
        # relay's own lock file.
        running = False
    if running:
        Progress.note("host MT5 relay already running")
        return
    args = [sys.executable, str(script)]
    if system == "Windows":
        data_dir = find_mt5_data_dir(system, prefix)
        if data_dir is None:
            Progress.note("MT5 data folder not found; file-transport relay not started")
            return
        args += ["--files-dir", str(data_dir / "Files")]
    else:
        args += ["--prefix", str(prefix)]
    if dry_run:
        Progress.note("would start host relay: " + " ".join(args))
        return
    log = ROOT / ".run" / "mt5-relay.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), start_new_session=True)
    Progress.note("host MT5 relay started (EA file transport)")


def health(url: str) -> dict | None:
    try:
        with urlopen(Request(url), timeout=4) as response:
            return json.loads(response.read())
    except (OSError, ValueError, URLError):
        return None


def wait_for_services(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health("http://127.0.0.1:9000/health") and health("http://127.0.0.1:9101/healthz"):
            return True
        time.sleep(1)
    return False


def onboarding_profile() -> dict[str, str]:
    """Read the unmasked onboarding values from the persistent service DB.

    The profile endpoint intentionally masks secrets, so launcher integration
    uses the database inside the already-running main container and never
    prints the returned token.
    """
    query = (
        "import json, sqlite3; "
        "c=sqlite3.connect('/data/xau_assistant.db'); "
        "r=c.execute('SELECT telegram_bot_token, telegram_chat_id, "
        "ngrok_authtoken, ngrok_domain, miniapp_direct_link FROM profile "
        "WHERE id=1').fetchone(); "
        "print(json.dumps(dict(zip(['telegram_bot_token','telegram_chat_id',"
        "'ngrok_authtoken','ngrok_domain','miniapp_direct_link'], r or []))))"
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "main", "python", "-c", query],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def configure_onboarding_integrations(profile: dict[str, str],
                                       compose_env: dict[str, str],
                                       dry_run: bool) -> None:
    telegram_ready = bool(profile.get("telegram_bot_token") and
                          profile.get("telegram_chat_id"))
    if telegram_ready:
        Progress.note("Telegram credentials found in onboarding profile")
    else:
        Progress.note("WARNING: Telegram is not configured in onboarding")

    token = str(profile.get("ngrok_authtoken") or "").strip()
    domain = str(profile.get("ngrok_domain") or "").strip()
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    if not token or not domain:
        Progress.note("WARNING: ngrok is not configured; tunnel profile skipped")
        if not dry_run:
            run(["docker", "compose", "--profile", "tunnel", "rm", "-sf", "ngrok"],
                check=False, env=compose_env)
        return

    public_url = f"https://{domain}"
    compose_env["NGROK_DOMAIN"] = domain
    if dry_run:
        Progress.note(f"would start ngrok tunnel for {public_url}")
        return

    # Keep the ignored runtime env in sync so Compose, the main service, and
    # the tunnel all use the onboarding profile as their source of truth.
    write_env_value("NGROK_AUTHTOKEN", token)
    write_env_value("NGROK_DOMAIN", domain)
    write_env_value("MINIAPP_PUBLIC_URL", public_url)
    direct_link = str(profile.get("miniapp_direct_link") or "").strip()
    if direct_link:
        write_env_value("MINIAPP_DIRECT_LINK", direct_link)

    run(["docker", "compose", "up", "-d", "--force-recreate", "main"],
        check=False, env=compose_env)
    if not wait_for_services(30):
        Progress.note("WARNING: main service did not recover after onboarding sync")
        return
    result = run(["docker", "compose", "--profile", "tunnel", "up", "-d", "ngrok"],
                 check=False, env=compose_env)
    if result.returncode == 0:
        Progress.note(f"ngrok tunnel started: {public_url}")
    else:
        Progress.note("WARNING: ngrok failed to start; inspect docker compose logs ngrok")


def _mt5_log_has_4014(prefix: Path, tail: int = 300) -> bool:
    """True when the recent MQL5 log shows MT5 rejecting a WebRequest.

    4014 = ERR_FUNCTION_NOT_ALLOWED: the WebRequest allowlist does not contain
    the EA's URL. This is the ONLY reliable signal, because MT5 stores the
    allowlist obfuscated in a binary file that cannot be read or written by a
    script (see scripts/mt5-settings.sh)."""
    logs = list(prefix.rglob("MQL5/logs/*.log")) if prefix.exists() else []
    if not logs:
        return False
    latest = max(logs, key=lambda path: path.stat().st_mtime)
    try:
        text = latest.read_bytes().decode("utf-16-le", errors="ignore")
    except OSError:
        return False
    return "err=4014" in "\n".join(text.splitlines()[-tail:])


def restore_mt5_settings(prefix: Path, terminal: Path | None) -> bool:
    """Copy a saved Config snapshot back over the live one.

    Returns False when there is nothing to restore. MT5 only writes its config
    on a genuine clean exit, so a force-killed terminal comes back WITHOUT the
    WebRequest allowlist; a snapshot taken after a good exit is the only way to
    get it back without repeating the GUI step."""
    snapshot = ROOT / ".run" / "mt5-settings"
    if not snapshot.is_dir():
        return False
    config = (terminal.parent / "Config") if terminal else None
    if config is None or not config.is_dir():
        return False
    restored = False
    for name in ("settings.ini", "common.ini", "terminal.ini"):
        src = snapshot / name
        if src.is_file():
            shutil.copyfile(src, config / name)
            restored = True
    return restored


def verify_ea(timeout: int, prefix: Path | None = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = health("http://127.0.0.1:9000/api/state")
        if state and state.get("age_s") is not None and state["age_s"] < 60:
            Progress.note(f"EA heartbeat received (age {state['age_s']}s)")
            return True
        time.sleep(2)
    if dt.datetime.now().weekday() >= 5:
        Progress.note("WEEKEND: EA heartbeat/trading validation may require the next broker session")
    else:
        Progress.note("EA heartbeat not observed; check MT5 WebRequest and EA attachment")
    if prefix is not None and _mt5_log_has_4014(prefix):
        Progress.note("DIAGNOSIS: MT5 rejected WebRequest (err=4014) — the allowlist "
                      "does not contain http://127.0.0.1:9000")
        Progress.note("This list is GUI-only and does NOT persist reliably under Wine:")
        Progress.note("MT5 saves Config/settings.ini on a clean exit, but the allowlist")
        Progress.note("is not restored from it (tested: terminal.ini, common.ini,")
        Progress.note("registry and a settings.ini snapshot all fail). Re-set it in the GUI")
        Progress.note("after a restart, or use the file-relay transport (see docs).")
    return False


def is_relay_running() -> bool:
    lock = ROOT / ".run" / "mt5-relay.lock"
    if lock.is_file():
        try:
            pid = int(lock.read_text().strip() or "0")
            if pid > 0:
                os.kill(pid, 0)
                return True
        except (OSError, ValueError):
            pass
    if platform.system() != "Windows":
        return subprocess.run(["pgrep", "-f", "mt5_relay.py"],
                              capture_output=True, text=True, check=False).returncode == 0
    return False


def print_final_summary(ea_ok: bool) -> None:
    main_ok = health("http://127.0.0.1:9000/health") is not None
    mini = health("http://127.0.0.1:9101/healthz") or {}
    relay_ok = is_relay_running()
    tunnel_services = ""
    try:
        tunnel_services = run_capture(
            ["docker", "compose", "--profile", "tunnel", "ps",
             "--services", "--filter", "status=running"])
    except (OSError, subprocess.CalledProcessError):
        pass
    print("\nSystem summary:")
    print(f"  [{'OK' if main_ok else '!!'}] main service")
    print(f"  [{'OK' if mini.get('ok') else '!!'}] mini-app")
    print(f"  [{'OK' if mini.get('feed_age_s') is not None else '--'}] MT5 feed "
          f"({mini.get('feed_age_s', 'no data')}s)")
    print(f"  [{'OK' if relay_ok else '--'}] MT5 relay (file transport)")
    print(f"  [{'OK' if 'ngrok' in tunnel_services.split() else '--'}] ngrok tunnel")
    print(f"  [{'OK' if ea_ok else '--'}] EA heartbeat")


def seed_candles_cache(system: str, prefix: Path) -> None:
    try:
        py = find_wine_executable(prefix, "python.exe") if system == "Linux" else Path(sys.executable)
        if not py or not py.exists():
            return
        script = (
            "import MetaTrader5 as mt5, json\n"
            "if mt5.initialize():\n"
            "    rows = []\n"
            "    for sym in ['XAUUSD', 'BTCUSD']:\n"
            "        for tf_name, tf_const in [('M5', mt5.TIMEFRAME_M5), ('M15', mt5.TIMEFRAME_M15)]:\n"
            "            rates = mt5.copy_rates_from_pos(sym, tf_const, 1, 300)\n"
            "            if rates is not None and len(rates) > 0:\n"
            "                for r in rates:\n"
            "                    rows.append((sym, tf_name, int(r['time']), float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['tick_volume'])))\n"
            "    mt5.shutdown()\n"
            "    with open('scripts/seeded_candles.json', 'w') as f:\n"
            "        json.dump(rows, f)\n"
        )
        cmd = ["wine", str(py), "-c", script] if system == "Linux" else [str(py), "-c", script]
        subprocess.run(cmd, capture_output=True, env=wine_env(prefix) if system == "Linux" else None, timeout=12)
        cache_file = ROOT / "scripts" / "seeded_candles.json"
        if cache_file.exists():
            import sqlite3
            rows = json.loads(cache_file.read_text(encoding="utf-8"))
            local_db = ROOT / "service" / "xau_assistant.db"
            if local_db.exists():
                try:
                    c = sqlite3.connect(local_db)
                    c.executemany("INSERT OR REPLACE INTO candles (symbol, timeframe, bar_time, o, h, l, c, v) VALUES (?,?,?,?,?,?,?,?)", rows)
                    c.commit()
                except Exception:
                    pass
            subprocess.run(["docker", "cp", str(cache_file), "izigold-main-1:/tmp/seeded_candles.json"], capture_output=True)
            subprocess.run(["docker", "exec", "izigold-main-1", "python", "-c", "import json, sqlite3; rows=json.load(open('/tmp/seeded_candles.json')); c=sqlite3.connect('/data/xau_assistant.db'); c.executemany('INSERT OR REPLACE INTO candles (symbol, timeframe, bar_time, o, h, l, c, v) VALUES (?,?,?,?,?,?,?,?)', rows); c.commit()"], capture_output=True)
            cache_file.unlink(missing_ok=True)
            Progress.note("seeded initial 300 bars for XAUUSD & BTCUSD (M5 & M15)")
    except Exception as e:
        Progress.note(f"initial candle seeding skipped: {e}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show actions without changing the host")
    parser.add_argument("--no-install", action="store_true", help="do not install missing host packages")
    parser.add_argument("--skip-mt5", action="store_true", help="start Docker services only")
    parser.add_argument("--restart-mt5", action="store_true",
                        help="restart a running MT5 so the startup profile can attach the EA")
    parser.add_argument("--install-model", action="store_true",
                        help="install the large real-model dependencies during the Compose build")
    parser.add_argument("--stop", action="store_true",
                        help="stop MT5 gracefully and the Docker services, then exit")
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for health checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    system = platform.system()
    if system not in {"Linux", "Windows"}:
        raise LaunchError(f"unsupported operating system: {system}")
    prefix = Path(os.environ.get("WINEPREFIX", Path.home() / ".mt5"))
    progress = Progress(8)

    if args.stop:
        # Stop order matters: MT5 first (graceful, so its settings flush), then
        # the containers. Use this instead of killing wine by hand.
        progress.stage("Stopping MT5 gracefully")
        stopped = stop_mt5(system, prefix, args.dry_run)
        Progress.note("MT5 stopped" if stopped
                      else "MT5 did not stop cleanly — settings may not persist")
        progress.stage("Stopping Docker services")
        run(["docker", "compose", "--profile", "tunnel", "down"],
            check=False, dry_run=args.dry_run)
        Progress.note("Done. Restart with: python3 launch.py")
        return 0

    progress.stage("Checking host prerequisites")
    if dt.datetime.now().weekday() >= 5:
        Progress.note("WEEKEND: market validation is deferred until the next broker trading session")
    if args.no_install:
        Progress.note("automatic package installation disabled")
    elif system == "Linux":
        install_linux_packages(args.dry_run)
    else:
        install_windows_packages(args.dry_run)
    if not command_exists("docker") and not args.dry_run:
        raise LaunchError("Docker is unavailable after prerequisite setup")
    if not COMPOSE_FILE.exists():
        raise LaunchError(f"missing {COMPOSE_FILE}")

    progress.stage("Preparing local configuration")
    ensure_env(args.dry_run)

    progress.stage("Building and starting Docker services")
    compose_env = os.environ.copy()
    configured_forecaster = read_env().get("FORECASTER", "chronos").lower()
    if args.install_model:
        compose_env["INSTALL_MODEL"] = "true"
        Progress.note(f"{configured_forecaster} forecaster selected; model dependencies requested")
    elif configured_forecaster != "fake":
        Progress.note("model dependencies not installed; use --install-model for real Chronos inference")
    run(["docker", "compose", "up", "-d", "--build"], dry_run=args.dry_run,
        env=compose_env)
    if not args.dry_run and not wait_for_services(args.wait):
        raise LaunchError("Docker services did not become healthy; run docker compose logs")
    Progress.note("main service: http://127.0.0.1:9000")
    Progress.note("mini-app:     http://127.0.0.1:9101/")

    progress.stage("Applying onboarding integrations")
    configure_onboarding_integrations(onboarding_profile(), compose_env, args.dry_run)

    progress.stage("Detecting MT5")
    terminal = editor = None
    if args.skip_mt5:
        Progress.note("MT5 setup skipped")
    elif system == "Windows":
        terminal = find_windows_mt5()
        if terminal is None:
            raise LaunchError("native MT5 not found; install it and run the launcher again")
        Progress.note(f"native MT5: {terminal}")
    else:
        terminal, editor = ensure_mt5_linux(prefix, args.dry_run)
        if terminal:
            Progress.note(f"Wine MT5: {terminal}")
        else:
            Progress.note("Wine MT5 installation still needs completion")

    progress.stage("Starting MT5 and installing the EA")
    if terminal and not args.skip_mt5:
        install_ea_sources(system, prefix, editor, args.dry_run)
        launch_mt5(system, terminal, prefix, args.dry_run, args.restart_mt5)
        time.sleep(3 if args.dry_run else 8)
    else:
        Progress.note("no MT5 executable available")

    progress.stage("Starting the host feed bridge")
    if system == "Windows" and not args.skip_mt5:
        start_windows_bridge(args.dry_run)
        # Also start the relay on Windows: HTTP normally wins there, but this
        # makes the EA's FILE fallback functional instead of a dead end.
        start_host_relay(system, prefix, args.dry_run)
    elif system == "Linux" and not args.skip_mt5:
        start_linux_bridge(prefix, args.dry_run, not args.no_install)
        # The relay is what keeps the EA→service link alive on Wine, where the
        # WebRequest allowlist does not persist. Cheap and idempotent.
        start_host_relay(system, prefix, args.dry_run)
    else:
        Progress.note("host feed bridge skipped")

    progress.stage("Verifying service and EA links")
    ea_ok = False
    if not args.dry_run:
        ea_ok = verify_ea(args.wait, prefix)
        if ea_ok:
            seed_candles_cache(system, prefix)
        if not ea_ok and terminal and _mt5_log_has_4014(prefix):
            # Deliberately NO automatic restore/restart here. Two reasons:
            #  * restarting MT5 discards any allowlist the owner just set in the
            #    GUI but that MT5 has not saved yet;
            #  * overwriting Config/ with a snapshot would also clobber a
            #    hand-applied fix (e.g. the [Experts] section in terminal.ini).
            # Recovery is an explicit, reversible action the owner runs.
            Progress.note("the WebRequest allowlist is missing; to recover from a "
                          "snapshot run: scripts/mt5-settings.sh restore")
    else:
        Progress.note("would wait for /api/state EA heartbeat")

    print("\nLaunch sequence complete.")
    if not args.dry_run:
        print_final_summary(ea_ok)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nLaunch cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except (LaunchError, OSError, subprocess.CalledProcessError, URLError) as exc:
        print(f"\nLaunch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
