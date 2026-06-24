"""Small launcher for the modular 2D-SOR application.

Run this file when you want the normal GUI:

    python launch_sor_demo.py

It simply imports the real entry point from the package and calls it. Keeping
this tiny wrapper at the top level makes the app easier to start from the
project folder.
"""
from sor_demo_modular.app import main

if __name__ == "__main__":
    # Only start the GUI when this file is executed directly, not when imported.
    main()
