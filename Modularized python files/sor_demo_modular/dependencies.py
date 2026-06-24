#!/usr/bin/env python3
"""
2D-SOR Demo v8 — Variable pickers, cycle selector, smoothing, traffic light.
Supports CV (Cyclic Voltammetry) and CVA (CV Advanced) via easy-biologic.
Experiment type and hold times configured in Setup dialog.
"""
import sys,os,platform,time,traceback,tempfile,configparser,zipfile,io,json,argparse,threading
import numpy as np
from PyQt6 import QtWidgets,QtCore,QtGui
import pyqtgraph as pg

# This file is imported by almost every other module. It gathers shared imports,
# optional hardware bindings, common colors, and small wrappers in one place.
# That keeps the rest of the code focused on workflow instead of setup details.

try: import tifffile
except: tifffile=None
# CuPy is optional. If available, analysis.py can use the GPU for larger arrays;
# otherwise the project falls back to NumPy.
CUPY_AVAILABLE=False;cp=None
try: import cupy;cp=cupy;CUPY_AVAILABLE=True
except: pass
THORCAM_SDK_DIR=r"C:\Program Files\Thorlabs\Scientific Imaging\ThorCam"
if platform.system()=="Windows":
    try:
        # Make Thorlabs camera DLLs visible to Python on Windows.
        if os.path.isdir(THORCAM_SDK_DIR):os.add_dll_directory(THORCAM_SDK_DIR)
    except:pass

# Camera SDK import is optional at startup. If it is missing, the GUI can still
# open for loading/analyzing saved datasets, and live acquisition reports the
# stored error message later.
CAM_AVAILABLE=True;CAM_IMPORT_ERROR=None
try: from thorlabs_tsi_sdk.tl_camera import TLCameraSDK
except Exception as e: CAM_AVAILABLE=False;CAM_IMPORT_ERROR=str(e)
try: from pylablib.devices import Thorlabs as ThorlabsPylablib
except: ThorlabsPylablib=None
import ctypes as _ctypes

_TLPM_DLL_PATH=r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPM_64.dll"

class _TLPMPowerMeter:
    """Small wrapper around the Thorlabs power meter DLL."""

    def __init__(self,visa_hint=""):
        # Try the configured VISA address first. If that fails, ask the DLL for
        # the first available resource.
        self._dll=_ctypes.cdll.LoadLibrary(_TLPM_DLL_PATH)
        self._handle=_ctypes.c_uint32(0)
        addr=visa_hint.encode("ascii")
        ret=self._dll.TLPM_init(addr,_ctypes.c_bool(True),
                                _ctypes.c_bool(True),_ctypes.byref(self._handle))
        if ret!=0:
            buf=_ctypes.create_string_buffer(1024)
            self._dll.TLPM_findRsrc(None,_ctypes.byref(_ctypes.c_uint32(0)))
            self._dll.TLPM_getRsrcName(None,_ctypes.c_uint32(0),buf)
            addr=buf.value
            ret=self._dll.TLPM_init(addr,_ctypes.c_bool(True),
                                    _ctypes.c_bool(True),_ctypes.byref(self._handle))
            if ret!=0:raise RuntimeError(f"TLPM_init failed: {ret:#010x}")
    def get_power(self):
        # Return one optical power reading in watts.
        p=_ctypes.c_double(0.0)
        self._dll.TLPM_measPower(self._handle,_ctypes.byref(p))
        return float(p.value)
    def close(self):
        try:self._dll.TLPM_close(self._handle)
        except:pass
SETTINGS_FILE=os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd(),"sor_demo_settings.ini")
# Reused color palettes for cluster overlays and zone outlines.
CLUSTER_COLORS=[(228,26,28),(55,126,184),(77,175,74),(152,78,163),(255,127,0),(255,255,51),
                (166,86,40),(247,129,191),(153,153,153),(0,210,210),(180,0,0),(0,0,180)]
PLOT_VARS=["I (A)","E (V)","R","R / area","ΔR/R","Power (W)","dE/dt","Frame #"]
ZONE_COLORS=[(255,80,80),(80,180,255),(80,255,120),(255,200,60),
             (220,80,255),(80,255,220),(255,140,40),(200,255,80)]
CV_HEADER_SPACER_PX=16

EXP_CV  = "CV"
EXP_CVA = "CV Advanced"

def _ensure_dll():
    # Re-add the camera DLL directory just before live acquisition. This is a
    # defensive helper for Windows environments where DLL paths can be fragile.
    if platform.system()=="Windows":
        try:
            if os.path.isdir(THORCAM_SDK_DIR):os.add_dll_directory(THORCAM_SDK_DIR)
        except:pass

def try_import_ebl():
    # Import easy-biologic only when needed so the GUI can still open on systems
    # used for loading or analyzing saved datasets.
    try:
        import easy_biologic as ebl;import easy_biologic.base_programs as blp;return True,ebl,blp,""
    except Exception as e:
        msg=str(e)
        if "pkg_resources" in msg:msg+='\nFix: pip install "setuptools<82" --force-reinstall'
        return False,None,None,f"{msg}\n\n{traceback.format_exc()}"

__all__ = [name for name in globals() if not name.startswith("__")]
