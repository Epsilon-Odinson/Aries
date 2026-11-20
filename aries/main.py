"""Entry point for Aries (Module 2).

By default ``python -m aries.main`` launches the PyQt5 desktop UI.
Pass ``--cli`` for the old text-only chat loop (Module 1).

    python -m aries.main          # desktop UI (default)
    python -m aries.main --cli    # CLI chat loop
"""

from __future__ import annotations

import sys

GREETING = "Aries active. Ready to cook."

# NOTE: config/llm are imported lazily inside run_cli()/main(), NOT
# at module top. The hidden --mic-* helper flags must run in a child
# process with as few imports as possible (mic_worker only) — a
# heavy import failing in the child would look like a broken mic.


def _install_crash_guard() -> None:
    """Never let an unhandled exception kill the app.

    A PyQt slot that raises (or a background thread that raises)
    would otherwise print a traceback and — for some Qt paths — take
    the whole process down with it. Instead, log the error and show
    it in plain words; the app stays open. The mic is already
    isolated in a child process (mic_worker.py), so this is the
    second line of defence, not the first.
    """
    import threading
    import traceback

    def _show(message: str) -> None:
        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance()
            if app is not None:
                QMessageBox.warning(
                    None, "Aries",
                    "Something unexpected happened, but Aries is still "
                    f"here:\n\n{message}")
                return
        except Exception:
            pass
        print(f"Aries hit an unexpected error and kept going: {message}",
              file=sys.stderr)

    def _hook(exc_type, exc_value, tb) -> None:
        try:
            traceback.print_exception(exc_type, exc_value, tb)
        except Exception:
            pass
        _show(str(exc_value) or repr(exc_type))

    def _thread_hook(args) -> None:
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def _run_mic_helper(argv: list[str]) -> int | None:
    """Run a hidden --mic-* helper flag in this process.

    The mic helper is the app re-invoking itself (frozen .exe) or
    ``python -m aries.main`` (dev) so all PortAudio work happens in
    a child process that may crash without taking the app down.
    Returns the exit code when a mic flag was given, else None.
    """
    if not argv or not argv[0].startswith("--mic-"):
        return None
    from . import mic_worker

    return mic_worker.main(argv)

SYSTEM_PROMPT = (
    "You are Aries, a helpful PC desktop companion and your "
    "friend's pal. Answer clearly and briefly. If you are unsure, "
    "say so.\n\n"
    "How you think and respond: direct, no fluff — never open with "
    "'Great question!' or 'I'd be happy to help!', just answer. "
    "Warm and casual, like a pal; use his name (Epsilon). Blunt "
    "and fast on money and security — if something is a scam or a "
    "bad deal, say it straight, with the reason and what to do. "
    "Explain the why, not just the what. Own your mistakes straight "
    "away: say so plainly and fix them, no excuses. Never lecture "
    "him on personal stuff — listen and receive it warmly, no "
    "verdicts. Match his energy: short when he's short, deep when "
    "he wants depth. Remember his world — school, CODM, piano, "
    "his routine, his goals — and use it."
)


def run_cli() -> None:
    """Text-only chat loop (Module 1)."""
    from .config import get_api_key
    from .llm import AriesLLMError, chat

    print(GREETING)
    try:
        get_api_key()
    except RuntimeError as exc:
        print(f"\n{exc}")
        return

    history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Type 'quit' to exit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{GREETING} — shutting down. Goodbye.")
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            print("Aries: Goodbye. Ready to cook again whenever you are.")
            break

        history.append({"role": "user", "content": user_input})
        try:
            reply = chat(history)
        except (AriesLLMError, RuntimeError) as exc:
            print(f"Aries: Sorry — {exc}")
            continue
        history.append({"role": "assistant", "content": reply})
        print(f"Aries: {reply}\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Hidden mic-helper flags: run in a child process so PortAudio
    # can never crash the app. Handled before anything else.
    mic_code = _run_mic_helper(args)
    if mic_code is not None:
        return mic_code
    _install_crash_guard()
    if "--cli" in args:
        run_cli()
        return 0
    # Desktop UI is the default. Import here so --cli never needs PyQt5.
    try:
        from .ui import run_ui
    except ImportError as exc:
        print(
            "The desktop UI needs PyQt5, which is not installed.\n"
            f"Import error: {exc}\n"
            "Install it with: pip install -r requirements.txt\n"
            "Or run the text-only version: python -m aries.main --cli"
        )
        return 1
    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
