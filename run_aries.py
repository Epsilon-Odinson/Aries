"""Entry point for the Windows .exe build (PyInstaller).

``python -m aries.main`` works when run from source, but a
PyInstaller one-file build needs a plain script entry point:
relative imports in ``aries/main.py`` do not work when it is the
top-level script. This tiny launcher is that entry point — it just
hands off to ``aries.main.main()``.

Build it on Windows with:
    pyinstaller --onefile --windowed --name Aries run_aries.py
(or let the GitHub Actions workflow do it — see
.github/workflows/build-windows.yml).
"""

from aries.main import main

if __name__ == "__main__":
    raise SystemExit(main())
