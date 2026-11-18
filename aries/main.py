"""Minimal CLI entry point for Aries (Module 1).

Prints the greeting, then runs a simple chat loop against the
NVIDIA LLM. Type 'quit' or 'exit' (or Ctrl+C) to stop.

This is the text-only core. The PyQt5 desktop UI is Module 2.
"""

from __future__ import annotations

from .config import get_api_key
from .llm import AriesLLMError, chat

GREETING = "Aries active. Ready to cook."

SYSTEM_PROMPT = (
    "You are Aries, a helpful PC desktop companion. "
    "Answer clearly and briefly. If you are unsure, say so."
)


def main() -> None:
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


if __name__ == "__main__":
    main()
