"""Application entry point.

Generated from sor_demo_v19_26-6-2.py during modularization.
"""
from .dependencies import *
from .main_window import DemoWindow
from .datasets import load_dataset_from_directory

def main():
    parser=argparse.ArgumentParser(description="2D-SOR Demo v8")
    parser.add_argument("--test",nargs="?",const=True,default=False,metavar="SCENARIO.json")
    parser.add_argument("--load",type=str,default=None,metavar="DIR")
    args=parser.parse_args()
    app=QtWidgets.QApplication(sys.argv);win=DemoWindow();win.show()
    if args.test is not False:
        sp=args.test if isinstance(args.test,str) else None
        QtCore.QTimer.singleShot(100,lambda:win._on_test(sp=sp))
    elif args.load:
        def _do():
            try:
                ds=load_dataset_from_directory(args.load);win._clear_data()
                win._store_dir=args.load;win._load_ds(ds);win.status_lbl.setText(f"Loaded {win._n_frames}")
            except Exception as e:win.status_lbl.setText(f"Load fail: {e}")
        QtCore.QTimer.singleShot(100,_do)
    sys.exit(app.exec())

__all__ = [name for name in globals() if not name.startswith("__")]
