"""The one way in: ``python -m gshared_worker``, and the packaged executable.

Absolute rather than relative, because PyInstaller runs this file as ``__main__``
and a relative import has no package to be relative to there.

The packaged executable has no console, so it opens the status window; a run
from a terminal is the same :func:`gshared_worker.main.run` it always was.
"""

from gshared_worker.status_app import launch


if __name__ == "__main__":
    raise SystemExit(launch())
