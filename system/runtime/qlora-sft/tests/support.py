"""Shared test helpers.

There is one, and it exists because of a mistake made more than once in this
repository: a test that greps a module's source for a word the module is not
supposed to contain, run against a module whose docstring explains at length
why it does not contain it. ``test_events`` asserted that ``events.py`` has no
"JOB_PROGRESS" in it, and failed on the sentence *"JOB_PROGRESS is task 8 and
is not implemented here."*

Strip the prose first, then search.
"""

from __future__ import annotations

import ast
import inspect


def code_only(module) -> str:
    """A module's source with every docstring removed."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)
