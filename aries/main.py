"""Entry point for Aries (Module 2).

By default ``python -m aries.main`` launches the PyQt5 desktop UI.
Pass ``--cli`` for the old text-only chat loop (Module 1).

    python -m aries.main          # desktop UI (default)
    python -m aries.main --cli    # CLI chat loop
"""

from __future__ import annotations

import sys

from .config import get_api_key
from .llm import AriesLLMError, chat

GREETING = "Aries active. Ready to cook."

SYSTEM_PROMPT = (
    "You are Aries, a helpful PC desktop companion. "
    "Answer clearly and briefly. If you are unsure, say so."
)


def run_cli() -> None:
    """Text-only chat loop (Module 1)."""
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
