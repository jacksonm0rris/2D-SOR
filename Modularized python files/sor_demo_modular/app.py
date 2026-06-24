"""Application entry point.

This is the first package file called by launch_sor_demo.py. Its job is small:

* read optional command-line arguments,
* create the Qt application,
* create the main DemoWindow,
* optionally trigger Test or Load after the window appears.
"""
from .dependencies import *
from .main_window import DemoWindow
from .datasets import load_dataset_from_directory

def main():
    # --test starts a synthetic run automatically. If a scenario path is given,
    # that JSON scenario is used; otherwise the user can choose one.
    parser=argparse.ArgumentParser(description="2D-SOR Demo v8")
    parser.add_argument("--test",nargs="?",const=True,default=False,metavar="SCENARIO.json")
    # --load opens an existing saved dataset folder at startup.
    parser.add_argument("--load",type=str,default=None,metavar="DIR")
    args=parser.parse_args()
    # Qt applications must have exactly one QApplication before widgets exist.
    app=QtWidgets.QApplication(sys.argv);win=DemoWindow();win.show()
    if args.test is not False:
        sp=args.test if isinstance(args.test,str) else None
        # Delay until the event loop starts so the window is fully constructed.
        QtCore.QTimer.singleShot(100,lambda:win._on_test(sp=sp,preview_first=False))
    elif args.load:
        def _do():
            try:
                # Loading through the window keeps startup behavior identical to
                # clicking "Load dataset..." inside the GUI.
                ds=load_dataset_from_directory(args.load);win._clear_data()
                win._store_dir=args.load;win._load_ds(ds);win.status_lbl.setText(f"Loaded {win._n_frames}")
            except Exception as e:win.status_lbl.setText(f"Load fail: {e}")
        QtCore.QTimer.singleShot(100,_do)
    sys.exit(app.exec())

__all__ = [name for name in globals() if not name.startswith("__")]
