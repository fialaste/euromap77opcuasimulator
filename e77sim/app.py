"""Command line entry point: starts up to three machines and the web UI in one process."""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import os
import sys
import threading
import webbrowser

from . import __version__
from .config import MAX_MACHINES, builtin_profiles, load_profile
from .resources import data_dir
from .sim.machine import Machine
from .ui.web import WebUI

log = logging.getLogger("e77sim")

DEFAULT_MACHINES = ["arburg_like", "zhafir_like"]
BASE_PORT = 4840

HELP = """Příkazy konzole:
  s, status   stav strojů
  o, open     otevřít UI v prohlížeči
  q, quit     ukončit simulátor (také Ctrl+C)
"""


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="e77sim",
        description="Simulátor vstřikovacích lisů s rozhraním Euromap 77 (OPC 40077) přes OPC UA.",
        epilog=f"Vestavěné profily: {', '.join(builtin_profiles())}. Příklad: e77sim -m arburg_like:4840 -m zhafir_like:4841",
    )
    p.add_argument("-m", "--machine", action="append", metavar="PROFIL[:PORT]",
                   help=f"stroj: vestavěný profil nebo cesta k YAML, volitelně s portem (nejvýše {MAX_MACHINES}×)")
    p.add_argument("--host", default="0.0.0.0", help="adresa, na které poslouchají OPC UA servery (výchozí 0.0.0.0)")
    p.add_argument("--ui-host", default="127.0.0.1", help="adresa webového UI (výchozí 127.0.0.1, jen lokálně)")
    p.add_argument("--ui-port", type=int, default=8080, help="port webového UI (výchozí 8080)")
    p.add_argument("--no-browser", action="store_true", help="po startu neotevírat prohlížeč")
    p.add_argument("--seed", type=int, help="seed náhodných generátorů pro opakovatelný běh")
    p.add_argument("--no-security", action="store_true", help="jen endpoint bez zabezpečení (bez certifikátu)")
    p.add_argument("--no-anonymous", action="store_true", help="zakázat anonymní přihlášení")
    p.add_argument("--password", default="", help="heslo uživatele OPC40077 (prázdné = libovolné)")
    p.add_argument("--password-read-only", default="", help="heslo uživatele OPC40077_read_only (prázdné = libovolné)")
    p.add_argument("--data-dir", help="složka pro certifikáty (výchozí e77sim-data vedle programu)")
    p.add_argument("-v", "--verbose", action="store_true", help="podrobný log včetně asyncua")
    p.add_argument("--version", action="version", version=f"e77sim {__version__}")
    return p.parse_args(argv)


def setup_console(verbose: bool) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleTitleW(f"Euromap 77 Simulator {__version__}")
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    log.setLevel(logging.INFO)
    if not verbose:
        for name in ("asyncua", "aiohttp"):
            logging.getLogger(name).setLevel(logging.ERROR)


def machine_specs(args) -> list[tuple[dict, int]]:
    items = args.machine or DEFAULT_MACHINES
    if len(items) > MAX_MACHINES:
        raise SystemExit(f"Simulátor podporuje nejvýše {MAX_MACHINES} stroje.")
    out = []
    used_ports, names = set(), set()
    for i, item in enumerate(items):
        name, port = item, None
        head, sep, tail = item.rpartition(":")
        if sep and tail.isdigit() and not (len(head) == 1 and os.name == "nt"):
            name, port = head, int(tail)
        prof = copy.deepcopy(load_profile(name))
        port = port or int(prof.get("port") or BASE_PORT + i)
        while port in used_ports:
            port += 1
        used_ports.add(port)
        # The same profile twice needs a different serial number, so the machines are distinguishable
        sn = str(prof["identity"]["serial_number"])
        key = (prof["identity"]["manufacturer"], sn)
        n = 2
        while key in names:
            key = (prof["identity"]["manufacturer"], f"{sn}-{n}")
            n += 1
        names.add(key)
        prof["identity"]["serial_number"] = key[1]
        if key[1] != sn:
            conf = prof.setdefault("configuration", {})
            conf["user_machine_name"] = f"{conf.get('user_machine_name', 'Lis')} ({n - 1})"
        out.append((prof, port))
    return out


def console_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            return
        if not line:
            return
        loop.call_soon_threadsafe(queue.put_nowait, line.strip().lower())


def print_status(machines: list[Machine], ui: WebUI) -> None:
    print(f"\nWeb UI: {ui.url()}")
    for m in machines:
        ok, reason = m.can_produce()
        av = "Jobs/ActiveJobValues/"
        clients = len(m.server.bserver.clients) if m.running and m.server.bserver else 0
        print(f"  {m.name:<34} {m.endpoint:<26} {'běží' if m.running else 'STOP'}  klienti {clients}  "
              f"{'výroba' if ok else 'stojí: ' + reason}  cykly {m.get(av + 'JobCycleCounter', '-')}")
    print()


async def run(args) -> int:
    specs = machine_specs(args)
    ddir = data_dir() if not args.data_dir else __import__("pathlib").Path(args.data_dir)
    ddir.mkdir(parents=True, exist_ok=True)
    print(f"Euromap 77 Simulator {__version__} – spouštím {len(specs)} stroj(e)…")
    machines = []
    passwords = {"OPC40077": args.password, "OPC40077_read_only": args.password_read_only}
    for i, (prof, port) in enumerate(specs):
        seed = None if args.seed is None else args.seed + i
        m = Machine(prof, port, args.host, ddir, seed=seed, passwords=passwords,
                    allow_anonymous=not args.no_anonymous, security=not args.no_security)
        try:
            await m.start()
        except OSError as e:
            print(f"Port {port} pro {m.name} nelze otevřít: {e}")
            for x in machines:
                await x.stop()
            return 2
        machines.append(m)

    ui = WebUI(machines, args.ui_host, args.ui_port)
    try:
        await ui.start()
    except OSError as e:
        print(f"Web UI nelze spustit na portu {args.ui_port}: {e}. Zkuste --ui-port.")
        for m in machines:
            await m.stop()
        return 2

    print("\n" + "=" * 78)
    print(f" Web UI:  {ui.url()}")
    for m in machines:
        print(f" {m.profile.get('profile', ''):<12} {m.name:<34} {m.endpoint}")
    print(f" Bezpečnost: None{'' if args.no_security else ', Basic256 a Basic256Sha256 (Sign, SignAndEncrypt)'}")
    print(f" Uživatelé: {'anonymní, ' if not args.no_anonymous else ''}OPC40077, OPC40077_read_only")
    print("=" * 78)
    print(HELP)

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(ui.url())).start()

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    if sys.stdin is not None and sys.stdin.isatty():
        threading.Thread(target=console_reader, args=(loop, queue), daemon=True).start()
    try:
        while True:
            cmd = await queue.get()
            if cmd in ("q", "quit", "exit", "konec"):
                break
            if cmd in ("s", "status"):
                print_status(machines, ui)
            elif cmd in ("o", "open"):
                webbrowser.open(ui.url())
            elif cmd in ("h", "help", "?"):
                print(HELP)
            elif cmd:
                print(f"Neznámý příkaz {cmd!r}. " + HELP)
    finally:
        print("Ukončuji…")
        await ui.stop()
        for m in machines:
            await m.stop()
    return 0


def main(argv=None) -> None:
    args = parse_args(argv)
    setup_console(args.verbose)
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 0
    except Exception as e:  # keep the console window readable when started by double click
        log.exception("Simulátor skončil chybou: %s", e)
        if getattr(sys, "frozen", False) and os.name == "nt":
            input("Stiskněte Enter pro zavření…")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
