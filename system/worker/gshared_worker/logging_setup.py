"""Two streams, on purpose.

What the supplier reads is six lines telling them whether their machine is
earning; what whoever diagnoses a fault reads is every step and its outcome.
Mixing them serves neither, so everything diagnostic goes to a file beside the
executable and nothing here ever prints.

The file is next to the program rather than in a system log directory because
the one instruction that has to work is "send me that file", and a supplier can
only follow it for a file they can see.
"""

from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path

#: One logger for the whole Worker. Modules take it by name rather than passing
#: it around, so a component logs the same way wherever it is called from.
LOGGER_NAME = "gshared"

DEBUG_LOG_NAME = "gshared-debug.log"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: Marks the handler this module installed, so starting the log twice in one
#: process replaces its own handler instead of writing every line twice.
_OURS = "gshared_debug_log"

log = logging.getLogger(LOGGER_NAME)


def build_stamp() -> str:
    """When this copy of the program was put together.

    Written at the top of every log. Without it a report cannot be told from an
    older copy of the executable that was never replaced, and a fix that did not
    work cannot be told from a build that was never copied across.
    """
    try:
        if getattr(sys, "frozen", False):
            when = Path(sys.executable).stat().st_mtime
        else:
            when = Path(__file__).stat().st_mtime
        return datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "unrecorded"


#: Which build this is. Read once, so every line of one run names one build.
BUILD = build_stamp()


def start_debug_log(directory: Path) -> Path:
    """Send everything diagnostic to gshared-debug.log in the given directory.

    Returns the path, because the failure message has to name a file the
    supplier can find.
    """
    path = directory / DEBUG_LOG_NAME
    # The per-user folder does not exist on a machine that has never run the
    # packaged Worker. Opening a file in it would otherwise be the first fault.
    directory.mkdir(parents=True, exist_ok=True)
    _remove_our_handlers()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.set_name(_OURS)
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    # Diagnostics are not the supplier's screen: without this every line would
    # also reach the root logger and be printed over the six that matter.
    log.propagate = False
    return path


def stop_debug_log() -> None:
    """Close the log file. Only a caller that starts several runs needs this."""
    _remove_our_handlers()


def stamp_the_build() -> None:
    log.info("G-shared Worker built %s", BUILD)


def _remove_our_handlers() -> None:
    for handler in list(log.handlers):
        if handler.get_name() == _OURS:
            log.removeHandler(handler)
            handler.close()
