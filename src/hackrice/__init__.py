"""Console entry point for the project.

`uv run hackrice` starts the same development server as `hackrice/run.sh`. The source tree
is flat - `src/app.py` and its neighbours rather than an installed package - so this shim
puts `src/` on the import path before handing over to Flask.
"""

import os
import sys

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _add_source_to_path() -> None:
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)


def main() -> None:
    _add_source_to_path()
    from app import app

    app.run(debug=True, use_reloader=False)
