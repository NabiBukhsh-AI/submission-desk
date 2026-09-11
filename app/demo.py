"""``make demo``: the reviewer interface with demo mode on.

A launcher and nothing else. It exists because the Makefile must not set an
environment variable inline — that syntax works in bash and not in cmd.exe,
which is what make uses from PowerShell — so demo mode is switched on here,
in the process that is about to read it, and Streamlit is started the way its
own command line would start it.

    python -m app.demo                 # demo mode, port 8501
    python -m app.demo --port 8600     # any further arguments go to streamlit
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from streamlit.web import cli as streamlit_cli

MAIN = Path(__file__).resolve().parent / "main.py"


def main(argv: list[str] | None = None) -> int:
    os.environ["DEMO_MODE"] = "true"
    arguments = list(sys.argv[1:] if argv is None else argv)
    sys.argv = ["streamlit", "run", str(MAIN), *arguments]
    return int(streamlit_cli.main() or 0)


if __name__ == "__main__":
    sys.exit(main())
