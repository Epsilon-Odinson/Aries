"""PC control for Aries — desktop only.

An operating agent: the desktop app can open apps, files and URLs,
type, press keys and hotkeys, move/click/scroll the mouse, use the
clipboard, run shell commands, read/write files, report system
info, control windows (minimise/maximise/restore/close), volume
and media keys, take screenshots, lock the PC and open Windows
Settings pages. This is the defined command set from the fixed
build spec, expanded in 0.6.0 — nothing else.

Rules (fixed spec, autonomy section):
- Max 5 agent steps per command (:data:`MAX_STEPS`).
- Confirmation BEFORE any action runs. The UI shows the plan and
  asks; nothing here runs on its own.
- Destructive actions (shutdown, restart, delete) need EXPLICIT
  confirmation — the UI asks a second time, with a warning.
- Desktop software only. This module is never used by the webapp.

How it works: the LLM answers with a fenced ````pc-actions`` block
holding a JSON list of steps. :func:`extract_pc_actions` pulls it
out, :func:`describe_action` turns each step into one plain line
for the confirmation dialog, and :func:`execute_steps` runs the
plan after the user says yes.

Heavy imports (pyautogui, psutil, pyperclip) are loaded lazily
inside the functions that need them, so importing this module
never needs a display, a mouse, or those packages installed —
``python -m py_compile`` and the text-only CLI work anywhere.
On Windows (the only supported target for the .exe) they are in
requirements.txt.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_STEPS = 5

# Largest file read in one step (200 KB) and largest text typed in
# one step (2000 chars) — caps a runaway action before it starts.
MAX_READ_BYTES = 200_000
MAX_TYPE_CHARS = 2_000
SHELL_TIMEOUT = 30  # seconds per shell command

ACTIONS = {
    "open_app",
    "open_path",
    "open_url",
    "type_text",
    "press_keys",
    "mouse_move",
    "mouse_click",
    "mouse_scroll",
    "clipboard_copy",
    "clipboard_paste",
    "shell",
    "read_file",
    "write_file",
    "system_info",
    "window_minimise",
    "window_maximise",
    "window_restore",
    "window_close",
    "volume_up",
    "volume_down",
    "volume_mute",
    "media_key",
    "screenshot",
    "lock_pc",
    "open_settings",
}

# Required fields per action (besides "action" itself).
_REQUIRED = {
    "open_app": ("name",),
    "open_path": ("path",),
    "open_url": ("url",),
    "type_text": ("text",),
    "press_keys": ("keys",),
    "mouse_move": ("x", "y"),
    "mouse_click": ("x", "y"),
    "mouse_scroll": ("amount",),
    "clipboard_copy": ("text",),
    "clipboard_paste": (),
    "shell": ("command",),
    "read_file": ("path",),
    "write_file": ("path", "text"),
    "system_info": (),
    "window_minimise": (),
    "window_maximise": (),
    "window_restore": (),
    "window_close": (),
    "volume_up": (),
    "volume_down": (),
    "volume_mute": (),
    "media_key": ("key",),
    "screenshot": (),
    "lock_pc": (),
    "open_settings": (),
}

# Shell text that means "destroy something". Matched case-insensitively
# against shell commands; a match marks the step destructive, which
# makes the UI ask a second time before running it.
_DESTRUCTIVE_SHELL = (
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\brestart\b.*\b/computer\b",
    r"\bformat\b",
    r"\bdel\b\s+",
    r"\berase\b\s+",
    r"\brmdir\b\s+/s",
    r"\brd\b\s+/s",
    r"\bremove-item\b.*-recurse\b",
    r"\brm\b\s+-[a-z]*r[a-z]*\b",
    r"\brm\b\s+--recursive\b",
    r"\breg\s+delete\b",
    r"\bdiskpart\b",
)


class PcControlError(RuntimeError):
    """A clear, user-facing error from a PC control step."""


# --- lazy heavy imports -------------------------------------------------------


def _pyautogui():
    """pyautogui, or a plain-words error if it is missing/unusable."""
    try:
        import pyautogui
    except Exception as exc:
        raise PcControlError(
            "Mouse/keyboard control needs pyautogui, which is not "
            f"available here ({exc}). Install it with: "
            "pip install -r requirements.txt"
        ) from exc
    return pyautogui


def _psutil():
    try:
        import psutil
    except Exception as exc:
        raise PcControlError(
            "System info needs psutil, which is not available here "
            f"({exc}). Install it with: pip install -r requirements.txt"
        ) from exc
    return psutil


def _pyperclip():
    try:
        import pyperclip
    except Exception as exc:
        raise PcControlError(
            "Clipboard access needs pyperclip, which is not available "
            f"here ({exc}). Install it with: pip install -r requirements.txt"
        ) from exc
    return pyperclip


# --- parsing the model's action block ------------------------------------------


def _parse_action_list(text: str) -> list[dict] | None:
    """Parse *text* as a JSON list of action steps; None when it is not.

    A step counts only when it is an object whose "action" is one of
    the known actions — so ordinary JSON (a list of strings, a config
    blob) is never mistaken for a plan.
    """
    try:
        steps = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(steps, list) or not steps:
        return None
    if not all(
        isinstance(step, dict) and str(step.get("action", "")) in ACTIONS
        for step in steps
    ):
        return None
    return steps


def _fence_blocks(reply: str):
    """Yield (tag, body) for every fenced code block in *reply*.

    Tolerates a missing closing fence (the block runs to the end of
    the reply) and a tag with trailing text — the model does not
    always write the fence cleanly.
    """
    pattern = re.compile(r"```([^\n`]*)\r?\n(.*?)(?:```|$)", re.DOTALL)
    for match in pattern.finditer(reply):
        tag_words = (match.group(1) or "").strip().split()
        tag = tag_words[0].lower() if tag_words else ""
        yield tag, match.group(2)


def extract_pc_actions(reply: str) -> list[dict] | None:
    """Pull a PC action plan out of a model reply, tolerantly.

    Accepts a ````pc-actions`` fence (any case, trailing text on the
    fence line, even an unclosed fence), a ````json`` or untagged
    fence whose content is a list of action steps, or a bare JSON
    list of steps with no fence at all. Returns the list of steps,
    or None when the reply holds no such plan.

    Raises :class:`PcControlError` — a clean one-line message, never
    raw JSON — when a ````pc-actions`` block is present but is not
    valid JSON / not a list. The UI shows that as an error instead
    of running anything, and never shows the raw block.
    """
    for tag, body in _fence_blocks(reply):
        if tag == "pc-actions":
            try:
                steps = json.loads(body)
            except json.JSONDecodeError:
                raise PcControlError(
                    "I tried to plan PC actions but the plan was not "
                    "valid JSON, so nothing ran. Ask me again and I will "
                    "re-plan it."
                ) from None
            if not isinstance(steps, list):
                raise PcControlError(
                    "The PC action plan must be a JSON list of steps, "
                    "not a single object — nothing ran."
                )
            if not steps:
                raise PcControlError(
                    "The PC action plan was empty — nothing ran."
                )
            return steps
        if tag in ("json", ""):
            steps = _parse_action_list(body)
            if steps is not None:
                return steps
    # No fence held a plan — try a bare JSON list anywhere in the reply.
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = reply.find("[", index)
        if start == -1:
            break
        try:
            obj, _end = decoder.raw_decode(reply, start)
        except (json.JSONDecodeError, ValueError):
            index = start + 1
            continue
        if (
            isinstance(obj, list)
            and obj
            and all(
                isinstance(step, dict)
                and str(step.get("action", "")) in ACTIONS
                for step in obj
            )
        ):
            return obj
        index = start + 1
    return None


def _remove_bare_action_lists(text: str) -> str:
    """Remove a bare JSON list of action steps (no fence) from *text*."""
    decoder = json.JSONDecoder()
    out: list[str] = []
    index = 0
    while True:
        start = text.find("[", index)
        if start == -1:
            out.append(text[index:])
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            out.append(text[index:start + 1])
            index = start + 1
            continue
        if (
            isinstance(obj, list)
            and obj
            and all(
                isinstance(step, dict)
                and str(step.get("action", "")) in ACTIONS
                for step in obj
            )
        ):
            out.append(text[index:start])
            index = end
        else:
            out.append(text[index:start + 1])
            index = start + 1
    return "".join(out)


def strip_pc_actions_block(reply: str) -> str:
    """Remove every PC action block from a reply, so the chat shows
    plain words only. Removes ````pc-actions`` fences (even unclosed
    or malformed), ````json``/untagged fences that hold action steps,
    and bare JSON lists of steps. Any other text is left untouched.
    """
    def _sub_fence(match: "re.Match[str]") -> str:
        tag_words = (match.group(1) or "").strip().split()
        tag = tag_words[0].lower() if tag_words else ""
        body = match.group(2)
        if tag == "pc-actions":
            return ""
        if tag in ("json", "") and _parse_action_list(body) is not None:
            return ""
        return match.group(0)

    text = re.sub(
        r"```([^\n`]*)\r?\n(.*?)(?:```|$)",
        _sub_fence,
        reply,
        flags=re.DOTALL,
    )
    text = _remove_bare_action_lists(text)
    # Last resort: a stray pc-actions fence fragment must never show.
    text = re.sub(
        r"```pc-actions.*$", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sanitize_display(reply: str) -> str:
    """Text safe to show in chat: action blocks removed, never raw
    JSON, never a fence, never a field name like "appname". This is
    the one entry the UI uses before displaying any reply."""
    return strip_pc_actions_block(reply)


# --- describing and checking a plan ----------------------------------------------


def describe_action(step: dict) -> str:
    """One plain line describing a step, for the confirmation dialog."""
    action = str(step.get("action", ""))
    if action == "open_app":
        return f"Open the app “{step.get('name', '')}”"
    if action == "open_path":
        return f"Open “{step.get('path', '')}”"
    if action == "open_url":
        return f"Open {step.get('url', '')} in the browser"
    if action == "press_keys":
        keys = step.get("keys", "")
        if isinstance(keys, (list, tuple)):
            keys = "+".join(str(k) for k in keys)
        return f"Press the keys {keys}"
    if action == "window_minimise":
        return "Minimise the focused window"
    if action == "window_maximise":
        return "Maximise the focused window"
    if action == "window_restore":
        return "Restore the focused window to its normal size"
    if action == "window_close":
        return "Close the focused window"
    if action == "volume_up":
        return "Turn the volume up"
    if action == "volume_down":
        return "Turn the volume down"
    if action == "volume_mute":
        return "Mute / unmute the sound"
    if action == "media_key":
        return f"Media key: {step.get('key', '')}"
    if action == "screenshot":
        path = str(step.get("path", "")).strip()
        return f"Take a screenshot" + (f" and save it to “{path}”" if path else "")
    if action == "lock_pc":
        return "Lock the PC"
    if action == "open_settings":
        page = str(step.get("page", "")).strip()
        return f"Open Settings" + (f" — {page}" if page else "")
    if action == "type_text":
        text = str(step.get("text", ""))
        shown = text if len(text) <= 60 else text[:57] + "…"
        return f"Type: “{shown}”"
    if action == "mouse_move":
        return f"Move the mouse to ({step.get('x')}, {step.get('y')})"
    if action == "mouse_click":
        button = step.get("button", "left")
        return f"Click ({step.get('x')}, {step.get('y')}) with the {button} mouse button"
    if action == "mouse_scroll":
        amount = step.get("amount", 0)
        direction = "up" if int(amount) > 0 else "down"
        return f"Scroll {direction} ({amount})"
    if action == "clipboard_copy":
        text = str(step.get("text", ""))
        shown = text if len(text) <= 60 else text[:57] + "…"
        return f"Copy to the clipboard: “{shown}”"
    if action == "clipboard_paste":
        return "Read the clipboard"
    if action == "shell":
        return f"Run the command: {step.get('command', '')}"
    if action == "read_file":
        return f"Read the file “{step.get('path', '')}”"
    if action == "write_file":
        return f"Write to the file “{step.get('path', '')}”"
    if action == "system_info":
        return "Check the PC's system info (CPU, memory, disk, battery)"
    return f"Unknown action: {action}"


def is_destructive(step: dict) -> bool:
    """True when a step destroys something and needs a second ask.

    Destructive means: shut the PC down, restart it, delete files,
    or overwrite a file that already exists. Everything else is
    reversible enough for the single confirmation.
    """
    action = str(step.get("action", ""))
    if action == "shell":
        command = str(step.get("command", ""))
        lowered = command.lower()
        # A bare "shutdown" / "restart" anywhere is destructive; the
        # patterns above catch the command forms Windows and
        # PowerShell actually use.
        return any(re.search(p, lowered) for p in _DESTRUCTIVE_SHELL)
    if action == "write_file":
        # Overwriting an existing file destroys its old contents.
        path = str(step.get("path", ""))
        try:
            return bool(path) and Path(path).expanduser().exists()
        except OSError:
            return True  # unknown path — treat as destructive, ask twice
    return False


def validate_steps(steps: list[dict]) -> None:
    """Check a plan before anything runs. Raises PcControlError."""
    if not isinstance(steps, list) or not steps:
        raise PcControlError("The PC action plan is empty.")
    if len(steps) > MAX_STEPS:
        raise PcControlError(
            f"That plan has {len(steps)} steps — the limit is {MAX_STEPS} "
            "per command. Ask me to split it into smaller pieces."
        )
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise PcControlError(f"Step {i} is not an action (it must be an object).")
        action = step.get("action")
        if action not in ACTIONS:
            raise PcControlError(
                f"Step {i} asks for an unknown action: {action!r}. "
                f"Known actions: {', '.join(sorted(ACTIONS))}."
            )
        for field in _REQUIRED[action]:
            if field not in step or step[field] in (None, ""):
                raise PcControlError(
                    f"Step {i} ({action}) is missing its “{field}”."
                )


# --- running one step ----------------------------------------------------------


# Friendly names people actually say, mapped to the real executable.
# "Windows Explorer" is explorer.exe, "VLC" is vlc.exe — the name
# you see in the Start menu is often not the .exe name, and `start`
# cannot find an app by its display name alone.
_APP_ALIASES = {
    "windows explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "my computer": "explorer.exe",
    "this pc": "explorer.exe",
    "vlc": "vlc.exe",
    "vlc media player": "vlc.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "word": "winword.exe",
    "microsoft word": "winword.exe",
    "excel": "excel.exe",
    "microsoft excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "microsoft powerpoint": "powerpnt.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "spotify": "spotify.exe",
    "whatsapp": "WhatsApp.exe",
    "telegram": "Telegram.exe",
    "discord": "Discord.exe",
    "steam": "steam.exe",
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "settings": "ms-settings:",
}

# URI schemes (Settings pages and the like) are opened with the
# shell, not as an .exe.
_URI_SCHEMES = ("ms-settings:",)


def _resolve_app_exe(name: str) -> str | None:
    """Resolve a friendly app name to a real executable path or URI.

    Returns the .exe path (or a URI like "ms-settings:") when the app
    is actually on this PC, else None. Looks in the alias map, then
    PATH, then the Windows App Paths registry (where installers
    register their .exe), then as a literal file path.
    """
    key = name.strip().lower()
    if not key:
        return None
    exe = _APP_ALIASES.get(key)
    if exe is None:
        exe = key if key.endswith(".exe") else key + ".exe"
    if any(exe.startswith(s) for s in _URI_SCHEMES):
        return exe
    # A literal path to an .exe the user gave.
    if os.path.isfile(exe) or os.path.isfile(os.path.expanduser(exe)):
        return exe
    found = shutil.which(exe)
    if found:
        return found
    if os.name == "nt":
        try:
            import winreg

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe,
                    ) as k:
                        path, _ = winreg.QueryValueEx(k, "")
                        if path and os.path.exists(path):
                            return path
                except OSError:
                    continue
        except Exception:
            pass
    return None


def _run_open_app(step: dict) -> str:
    name = str(step["name"]).strip()
    if not name:
        raise PcControlError("The app name is empty.")
    if os.name != "nt":
        raise PcControlError(
            "Opening apps by name works on Windows only — "
            "this PC control build targets the Windows .exe."
        )
    target = _resolve_app_exe(name)
    if target is None:
        raise PcControlError(
            f"There's no app called “{name}” on this PC. "
            "Check the name and try again — for example, the file "
            "manager is “Windows Explorer” and the media player is “VLC”."
        )
    # URI schemes (Settings pages) go through the shell.
    if any(target.startswith(s) for s in _URI_SCHEMES):
        try:
            os.startfile(target)  # noqa: S606 - user-confirmed target
        except OSError as exc:
            raise PcControlError(f"Could not open “{name}”: {exc}") from exc
        return f"Opened “{name}”."
    # Launch with `start` and CHECK the result. The old code used
    # fire-and-forget Popen with stderr swallowed, so a name `start`
    # could not find still reported "Opened" — a false success.
    # `start` exits non-zero and prints the error when it cannot find
    # the program, so a real failure is now reported, not hidden.
    try:
        proc = subprocess.run(
            ["cmd", "/c", "start", "", target],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PcControlError(f"Could not open “{name}”: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        detail = err.splitlines()[-1] if err else f"exit code {proc.returncode}"
        raise PcControlError(f"Could not open “{name}”: {detail}")
    return f"Opened “{name}”."


def _run_open_path(step: dict) -> str:
    path = str(step["path"]).strip()
    if not path:
        raise PcControlError("The path is empty.")
    target = Path(path).expanduser()
    if not target.exists():
        raise PcControlError(f"That does not exist: {path}")
    if os.name == "nt":
        os.startfile(str(target))  # noqa: S606 - user-confirmed path
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return f"Opened “{path}”."


def _run_type_text(step: dict) -> str:
    text = str(step["text"])
    if len(text) > MAX_TYPE_CHARS:
        raise PcControlError(
            f"That text is {len(text)} characters — the limit per step "
            f"is {MAX_TYPE_CHARS}."
        )
    gui = _pyautogui()
    gui.write(text, interval=0.02)
    return f"Typed {len(text)} characters."


def _run_mouse_move(step: dict) -> str:
    gui = _pyautogui()
    gui.moveTo(int(step["x"]), int(step["y"]), duration=0.25)
    return f"Moved the mouse to ({step['x']}, {step['y']})."


def _run_mouse_click(step: dict) -> str:
    gui = _pyautogui()
    button = str(step.get("button", "left")).lower()
    if button not in ("left", "right", "middle"):
        raise PcControlError(f"Unknown mouse button: {button!r}.")
    gui.click(int(step["x"]), int(step["y"]), button=button)
    return f"Clicked ({step['x']}, {step['y']}) with the {button} button."


def _run_mouse_scroll(step: dict) -> str:
    gui = _pyautogui()
    gui.scroll(int(step["amount"]))
    return f"Scrolled by {step['amount']}."


def _run_clipboard_copy(step: dict) -> str:
    clip = _pyperclip()
    try:
        clip.copy(str(step["text"]))
    except Exception as exc:
        raise PcControlError(f"Could not copy to the clipboard: {exc}") from exc
    return "Copied to the clipboard."


def _run_clipboard_paste(step: dict) -> str:
    clip = _pyperclip()
    try:
        text = clip.paste()
    except Exception as exc:
        raise PcControlError(f"Could not read the clipboard: {exc}") from exc
    if not text:
        return "The clipboard is empty."
    shown = text if len(text) <= 500 else text[:497] + "…"
    return f"Clipboard holds: “{shown}”"


def _run_shell(step: dict) -> str:
    command = str(step["command"]).strip()
    if not command:
        raise PcControlError("The shell command is empty.")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise PcControlError(
            f"The command ran longer than {SHELL_TIMEOUT} seconds, so I stopped it."
        ) from None
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    detail = out or err or "(no output)"
    if len(detail) > 1000:
        detail = detail[:997] + "…"
    if proc.returncode != 0:
        raise PcControlError(
            f"Command exited with code {proc.returncode}: {detail}"
        )
    return f"Command ran (exit 0): {detail}"


def _run_read_file(step: dict) -> str:
    path = Path(str(step["path"])).expanduser()
    if not path.exists():
        raise PcControlError(f"That file does not exist: {step['path']}")
    if not path.is_file():
        raise PcControlError(f"That is not a file: {step['path']}")
    if path.stat().st_size > MAX_READ_BYTES:
        raise PcControlError(
            f"That file is bigger than {MAX_READ_BYTES // 1000} KB — "
            "too big to read in one step."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    shown = text if len(text) <= 2000 else text[:1997] + "…"
    return f"Read “{path}” ({len(text)} characters):\n{shown}"


def _run_write_file(step: dict) -> str:
    path = Path(str(step["path"])).expanduser()
    text = str(step["text"])
    try:
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise PcControlError(f"Could not write “{path}”: {exc}") from exc
    return f"Wrote {len(text)} characters to “{path}”."


def _run_system_info(step: dict) -> str:
    psutil = _psutil()
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    parts = [
        f"CPU {cpu:.0f}%",
        f"memory {mem.percent:.0f}% used "
        f"({mem.used // (1024 ** 2)} of {mem.total // (1024 ** 2)} MB)",
        f"disk {disk.percent:.0f}% used "
        f"({disk.used // (1024 ** 3)} of {disk.total // (1024 ** 3)} GB free "
        f"{disk.free // (1024 ** 3)} GB)",
    ]
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        parts.append(f"battery {battery.percent:.0f}% ({state})")
    return "System info: " + "; ".join(parts) + "."


# --- new actions (0.6.0): full PC control --------------------------------

_SETTINGS_PAGES = {
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "network": "ms-settings:network",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "apps": "ms-settings:appsfeatures",
    "windows update": "ms-settings:windowsupdate",
    "update": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy",
    "accounts": "ms-settings:yourinfo",
    "personalisation": "ms-settings:personalization",
    "personalization": "ms-settings:personalization",
    "system": "ms-settings:system",
    "about": "ms-settings:about",
    "power": "ms-settings:powersleep",
    "battery": "ms-settings:powersleep",
    "storage": "ms-settings:storagesense",
    "notifications": "ms-settings:notifications",
}

_MEDIA_KEYS = {
    "play_pause": "audioplaypause",
    "play": "audioplaypause",
    "pause": "audioplaypause",
    "next": "audionext",
    "previous": "audioprev",
    "prev": "audioprev",
    "stop": "audiostop",
}


def _run_open_url(step: dict) -> str:
    from urllib.parse import urlparse

    url = str(step["url"]).strip()
    if not url:
        raise PcControlError("The URL is empty.")
    parsed = urlparse(url)
    if not parsed.scheme:
        # "google.com" -> "https://google.com".
        url = "https://" + url
        parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PcControlError(
            f"That is not a web address I can open: {step['url']}")
    import webbrowser

    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        raise PcControlError(f"Could not open {url}: {exc}") from exc
    if not opened:
        raise PcControlError(
            f"The browser did not open {url}. Is a browser installed?")
    return f"Opened {url} in the browser."


def _run_press_keys(step: dict) -> str:
    keys = step["keys"]
    if isinstance(keys, str):
        keys = [keys]
    if not isinstance(keys, (list, tuple)) or not keys:
        raise PcControlError("press_keys needs a key or a list of keys.")
    keys = [str(k).strip().lower() for k in keys]
    if any(not k for k in keys):
        raise PcControlError("press_keys got an empty key name.")
    gui = _pyautogui()
    try:
        if len(keys) == 1:
            gui.press(keys[0])
        else:
            gui.hotkey(*keys)
    except Exception as exc:
        raise PcControlError(
            f"Could not press {'+'.join(keys)}: {exc}") from exc
    return f"Pressed {'+'.join(keys)}."


def _run_window_minimise(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.hotkey("win", "down")
    except Exception as exc:
        raise PcControlError(f"Could not minimise the window: {exc}") from exc
    return "Minimised the focused window."


def _run_window_maximise(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.hotkey("win", "up")
    except Exception as exc:
        raise PcControlError(f"Could not maximise the window: {exc}") from exc
    return "Maximised the focused window."


def _run_window_restore(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.hotkey("alt", "space")
        gui.press("r")
    except Exception as exc:
        raise PcControlError(f"Could not restore the window: {exc}") from exc
    return "Restored the focused window."


def _run_window_close(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.hotkey("alt", "f4")
    except Exception as exc:
        raise PcControlError(f"Could not close the window: {exc}") from exc
    return "Closed the focused window."


def _run_volume_up(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.press("volumeup")
    except Exception as exc:
        raise PcControlError(f"Could not turn the volume up: {exc}") from exc
    return "Turned the volume up."


def _run_volume_down(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.press("volumedown")
    except Exception as exc:
        raise PcControlError(f"Could not turn the volume down: {exc}") from exc
    return "Turned the volume down."


def _run_volume_mute(step: dict) -> str:
    gui = _pyautogui()
    try:
        gui.press("volumemute")
    except Exception as exc:
        raise PcControlError(f"Could not mute the sound: {exc}") from exc
    return "Muted / unmuted the sound."


def _run_media_key(step: dict) -> str:
    key = str(step["key"]).strip().lower()
    if key not in _MEDIA_KEYS:
        raise PcControlError(
            f"Unknown media key: {step['key']!r}. Use play_pause, next, "
            "previous or stop.")
    gui = _pyautogui()
    try:
        gui.press(_MEDIA_KEYS[key])
    except Exception as exc:
        raise PcControlError(f"Could not press the media key: {exc}") from exc
    return f"Media key: {key}."


def _run_screenshot(step: dict) -> str:
    import datetime

    gui = _pyautogui()
    path = str(step.get("path", "") or "").strip()
    if path:
        target = Path(path).expanduser()
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise PcControlError(
                "A screenshot path must end in .png or .jpg.")
    else:
        folder = Path.home() / "Pictures" / "Aries"
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"screenshot-{stamp}.png"
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        shot = gui.screenshot()
        shot.save(str(target))
    except Exception as exc:
        raise PcControlError(f"Could not take a screenshot: {exc}") from exc
    return f"Screenshot saved to “{target}”."


def _run_lock_pc(step: dict) -> str:
    if os.name != "nt":
        raise PcControlError(
            "Locking the PC works on Windows only — this build "
            "targets the Windows .exe.")
    try:
        import ctypes

        if not ctypes.windll.user32.LockWorkStation():
            raise PcControlError("Windows refused to lock the PC.")
    except PcControlError:
        raise
    except Exception as exc:
        raise PcControlError(f"Could not lock the PC: {exc}") from exc
    return "Locked the PC."


def _run_open_settings(step: dict) -> str:
    if os.name != "nt":
        raise PcControlError(
            "Opening Windows Settings works on Windows only — this "
            "build targets the Windows .exe.")
    page = str(step.get("page", "") or "").strip().lower()
    uri = _SETTINGS_PAGES.get(page, "ms-settings:")
    try:
        os.startfile(uri)  # noqa: S606 - user-confirmed target
    except OSError as exc:
        raise PcControlError(f"Could not open Settings: {exc}") from exc
    shown = page or "Settings"
    return f"Opened {shown}."


_RUNNERS = {
    "open_app": _run_open_app,
    "open_path": _run_open_path,
    "open_url": _run_open_url,
    "type_text": _run_type_text,
    "press_keys": _run_press_keys,
    "mouse_move": _run_mouse_move,
    "mouse_click": _run_mouse_click,
    "mouse_scroll": _run_mouse_scroll,
    "clipboard_copy": _run_clipboard_copy,
    "clipboard_paste": _run_clipboard_paste,
    "shell": _run_shell,
    "read_file": _run_read_file,
    "write_file": _run_write_file,
    "system_info": _run_system_info,
    "window_minimise": _run_window_minimise,
    "window_maximise": _run_window_maximise,
    "window_restore": _run_window_restore,
    "window_close": _run_window_close,
    "volume_up": _run_volume_up,
    "volume_down": _run_volume_down,
    "volume_mute": _run_volume_mute,
    "media_key": _run_media_key,
    "screenshot": _run_screenshot,
    "lock_pc": _run_lock_pc,
    "open_settings": _run_open_settings,
}


# --- running a plan ------------------------------------------------------------


def execute_step(step: dict) -> dict:
    """Run one validated step. Returns {"ok", "summary", "detail"}."""
    action = str(step.get("action", ""))
    try:
        summary = _RUNNERS[action](step)
        return {"ok": True, "summary": summary, "detail": summary}
    except PcControlError as exc:
        return {"ok": False, "summary": str(exc), "detail": str(exc)}
    except Exception as exc:  # never let a step crash the app
        message = f"{action} failed: {exc}"
        return {"ok": False, "summary": message, "detail": message}


def execute_steps(steps: list[dict]) -> list[dict]:
    """Validate a plan, then run it step by step.

    Stops at the first failed step and returns the results so far
    (each ``{"ok", "summary", "detail"}``). The caller reports them
    to the user and to the model.
    """
    validate_steps(steps)
    results: list[dict] = []
    for step in steps:
        result = execute_step(step)
        results.append(result)
        if not result["ok"]:
            break
    return results


# --- what the model is told -----------------------------------------------------


PC_CONTROL_INSTRUCTIONS = """\
You can also act on this PC — open apps and files, type, click,
use the clipboard, run commands, read/write files, check system
info. This is a desktop app on Windows, so PC actions are real.

When the user asks you to DO something on the PC (not just answer
a question), reply with ONLY the fenced pc-actions block and
nothing else — no greeting, no sentence before it, no explanation
after it. The app shows the plan to the user, asks for permission,
runs it, and reports the result in plain words. After the result
comes back, say what happened in plain words, briefly.

Never put the JSON, any field name (like "action", "appname" or
"name"), or the block itself into your visible answer — the user
must only ever see plain words and results, never raw JSON.

```pc-actions
[{"action": "open_app", "name": "notepad"}]
```

Actions and their fields:
- open_app: {"action": "open_app", "name": "notepad"} — app name or .exe
- open_path: {"action": "open_path", "path": "C:\\Users\\Epsilon\\file.txt"} — file or folder
- open_url: {"action": "open_url", "url": "https://example.com"} — opens in the browser
- type_text: {"action": "type_text", "text": "hello"} — types into the focused window
- press_keys: {"action": "press_keys", "keys": ["ctrl", "c"]} — one key, or a hotkey combo
- mouse_move: {"action": "mouse_move", "x": 500, "y": 300}
- mouse_click: {"action": "mouse_click", "x": 500, "y": 300, "button": "left"}
- mouse_scroll: {"action": "mouse_scroll", "amount": 3} — positive scrolls up
- clipboard_copy: {"action": "clipboard_copy", "text": "…"}
- clipboard_paste: {"action": "clipboard_paste"} — reads the clipboard
- shell: {"action": "shell", "command": "dir"} — one Windows command, 30 s limit
- read_file: {"action": "read_file", "path": "…"} — text files up to 200 KB
- write_file: {"action": "write_file", "path": "…", "text": "…"}
- system_info: {"action": "system_info"} — CPU, memory, disk, battery
- window_minimise: {"action": "window_minimise"} — minimise the focused window
- window_maximise: {"action": "window_maximise"} — maximise the focused window
- window_restore: {"action": "window_restore"} — restore the focused window
- window_close: {"action": "window_close"} — close the focused window
- volume_up: {"action": "volume_up"} — volume_up / volume_down / volume_mute
- media_key: {"action": "media_key", "key": "play_pause"} — play_pause, next, previous, stop
- screenshot: {"action": "screenshot"} — saves to Pictures/Aries, reports the path (or pass "path")
- lock_pc: {"action": "lock_pc"} — lock the PC (Win+L)
- open_settings: {"action": "open_settings", "page": "bluetooth"} — a Settings page (wifi, display, sound, apps, update, privacy, accounts, power, storage, notifications); empty page opens Settings itself

Rules: at most 5 steps in one plan. Do NOT plan shutdown, restart,
or deleting files unless the user explicitly asked for that — those
need a second confirmation. If the user only asks a question, just
answer it; do not add a pc-actions block."""
