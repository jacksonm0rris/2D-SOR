"""Modularized 2D-SOR demo package.

Most project behavior lives in focused modules:

* app.py starts the GUI.
* main_window.py builds the main window.
* *_mixin.py files add behavior to that main window.
* workers.py talks to hardware or generates synthetic test data.
* analysis.py contains the PCA/K-means analysis engine.

This file marks the folder as a Python package and exposes main() for callers
that want to start the app programmatically.
"""

from .app import main

__all__ = ["main"]
