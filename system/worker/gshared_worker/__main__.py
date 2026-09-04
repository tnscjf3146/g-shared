"""The one way in: ``python -m gshared_worker``, and the packaged executable.

Absolute rather than relative, because PyInstaller runs this file as ``__main__``
and a relative import has no package to be relative to there.
"""

from gshared_worker.main import run


if __name__ == "__main__":
    raise SystemExit(run())
