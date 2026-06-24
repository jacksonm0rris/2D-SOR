"""Allows the package to be launched with ``python -m sor_demo_modular``."""
from .app import main

if __name__ == "__main__":
    # Delegate to the same entry point used by launch_sor_demo.py.
    main()
