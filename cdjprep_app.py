#!/usr/bin/env python3
"""cdjprep menu-bar app — a thin GUI over cdjprep.py.

Lives in the macOS menu bar (🎧). Lets you pick the input/output folders,
run the pipeline with live progress in the bar, preview the dry-run plan,
and toggle launch-at-login. All processing logic stays in cdjprep.py.
"""

import re
import subprocess
import tempfile
import threading
import tomllib
from pathlib import Path

import rumps

HERE = Path(__file__).resolve().parent
PY = HERE / ".venv" / "bin" / "python"
TOOL = HERE / "cdjprep.py"
CFG = HERE / "config.toml"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.mashtaler.cdjprep.plist"

PLIST_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mashtaler.cdjprep</string>
  <key>ProgramArguments</key><array>
    <string>{PY}</string>
    <string>{HERE / 'cdjprep_app.py'}</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
"""


def read_cfg():
    with open(CFG, "rb") as f:
        return tomllib.load(f)


def _replace_in_cfg(pattern, repl):
    text = CFG.read_text()
    new, n = re.subn(pattern, repl, text, count=1)
    if n:
        CFG.write_text(new)
    return bool(n)


def set_source(path):
    """The app manages a single input folder: replaces the whole [sources].paths list."""
    return _replace_in_cfg(r"(?ms)^paths\s*=\s*\[.*?\]", f'paths = [\n    "{path}",\n]')


def set_staging(path):
    return _replace_in_cfg(r'(?m)^path\s*=\s*".*"', f'path = "{path}"')


def choose_folder(prompt):
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    p = r.stdout.strip().rstrip("/")
    return p or None


def short(path, maxlen=40):
    s = str(path).replace(str(Path.home()), "~")
    return s if len(s) <= maxlen else "…" + s[-maxlen:]


class CdjPrepApp(rumps.App):
    def __init__(self):
        super().__init__("cdjprep", title="🎧", quit_button="Выйти")
        self.busy = False
        self.progress = ""
        self.result = None  # (output_text, returncode, was_dry)
        self.in_item = rumps.MenuItem("Вход: …", callback=self.pick_in)
        self.out_item = rumps.MenuItem("Выход: …", callback=self.pick_out)
        self.run_item = rumps.MenuItem("▶ Обработать библиотеку", callback=self.run_now)
        self.plan_item = rumps.MenuItem("Показать план (ничего не пишет)",
                                        callback=self.run_plan)
        self.open_out = rumps.MenuItem("Открыть выходную папку", callback=self.do_open_out)
        self.open_rep = rumps.MenuItem("Открыть последний отчёт", callback=self.do_open_rep)
        self.login_item = rumps.MenuItem("Автозапуск при входе", callback=self.toggle_login)
        self.menu = [self.in_item, self.out_item, None,
                     self.run_item, self.plan_item, None,
                     self.open_out, self.open_rep, None, self.login_item]
        self.refresh_labels()
        rumps.Timer(self.tick, 1).start()

    # ------------------------------------------------------------ settings
    def refresh_labels(self):
        try:
            cfg = read_cfg()
            paths = cfg["sources"]["paths"]
            extra = f" (+{len(paths) - 1})" if len(paths) > 1 else ""
            self.in_item.title = f"Вход: {short(Path(paths[0]).expanduser())}{extra}"
            self.out_item.title = f"Выход: {short(Path(cfg['staging']['path']).expanduser())}"
        except Exception as e:  # noqa: BLE001
            self.in_item.title = f"Вход: ошибка конфига ({e})"
        self.login_item.state = PLIST.exists()

    def pick_in(self, _):
        p = choose_folder("Входная папка (откуда брать треки)")
        if p:
            set_source(p)
            self.refresh_labels()

    def pick_out(self, _):
        p = choose_folder("Выходная папка (куда складывать готовое)")
        if p:
            set_staging(p)
            self.refresh_labels()

    # ------------------------------------------------------------ running
    def run_now(self, _):
        self._start(dry=False)

    def run_plan(self, _):
        self._start(dry=True)

    def _start(self, dry):
        if self.busy:
            rumps.alert("cdjprep", "Обработка уже идёт — дождись окончания.")
            return
        self.busy = True
        self.progress = "старт…"
        threading.Thread(target=self._worker, args=(dry,), daemon=True).start()

    def _worker(self, dry):
        cmd = [str(PY), str(TOOL)] + (["--dry-run"] if dry else [])
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            lines = []
            for line in p.stdout:
                lines.append(line)
                m = re.match(r"\[\s*(\d+)/(\d+)\]", line)
                if m:
                    self.progress = f"{m.group(1)}/{m.group(2)}"
            p.wait()
            self.result = ("".join(lines), p.returncode, dry)
        except Exception as e:  # noqa: BLE001
            self.result = (f"запуск не удался: {e}", 1, dry)
        finally:
            self.busy = False
            self.progress = ""

    def tick(self, _timer):
        self.title = f"🎧 {self.progress}" if self.busy else "🎧"
        if self.result is None:
            return
        out, rc, dry = self.result
        self.result = None
        if dry:
            plan = Path(tempfile.gettempdir()) / "cdjprep_plan.txt"
            plan.write_text(out)
            subprocess.run(["open", "-e", str(plan)], stdin=subprocess.DEVNULL)
            return
        m = re.search(r"copied as-is:\s*(\d+).*?converted[^:]*:\s*(\d+).*?"
                      r"skipped[^:]*:\s*(\d+).*?rejected:\s*(\d+)", out, re.S)
        if rc == 0 and m:
            msg = (f"скопировано {m.group(1)}, сконвертировано {m.group(2)}, "
                   f"пропущено {m.group(3)}, отклонено {m.group(4)}")
        else:
            tail = out.strip().splitlines()[-1] if out.strip() else "нет вывода"
            msg = f"код {rc}: {tail}"
        try:
            rumps.notification("cdjprep", "Обработка завершена" if rc == 0 else "Ошибка", msg)
        except Exception:  # noqa: BLE001 — notifications may be unavailable outside a bundle
            rumps.alert("cdjprep", msg)
        self.refresh_labels()

    # ------------------------------------------------------------ helpers
    def do_open_out(self, _):
        cfg = read_cfg()
        out = Path(cfg["staging"]["path"]).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(out)], stdin=subprocess.DEVNULL)

    def do_open_rep(self, _):
        cfg = read_cfg()
        rep = Path(cfg["staging"]["path"]).expanduser() / ".cdjprep" / "report.json"
        if rep.exists():
            subprocess.run(["open", "-e", str(rep)], stdin=subprocess.DEVNULL)
        else:
            rumps.alert("cdjprep", "Отчёта ещё нет — сначала запусти обработку.")

    def toggle_login(self, item):
        if PLIST.exists():
            subprocess.run(["launchctl", "unload", str(PLIST)], stdin=subprocess.DEVNULL,
                           capture_output=True)
            PLIST.unlink()
        else:
            PLIST.parent.mkdir(parents=True, exist_ok=True)
            PLIST.write_text(PLIST_BODY)
            subprocess.run(["launchctl", "load", str(PLIST)], stdin=subprocess.DEVNULL,
                           capture_output=True)
        item.state = PLIST.exists()


if __name__ == "__main__":
    CdjPrepApp().run()
