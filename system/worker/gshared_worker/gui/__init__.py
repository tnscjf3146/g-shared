"""The only part of the Worker a person looks at.

Two files on purpose:

* :mod:`gshared_worker.gui.presenter` decides what happens when a button is
  pressed. It knows nothing about Tk and is tested without a window.
* :mod:`gshared_worker.gui.window` draws that and nothing else.

Neither runs a WSL command, a Docker command, DPAPI, or an HTTP call of its
own. The presenter calls the services; the window calls the presenter.
"""
