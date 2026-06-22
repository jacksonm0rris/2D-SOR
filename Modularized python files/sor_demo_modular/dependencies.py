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

try: import tifffile
except: tifffile=None
CUPY_AVAILABLE=False;cp=None
try: import cupy;cp=cupy;CUPY_AVAILABLE=True
except: pass
THORCAM_SDK_DIR=r"C:\Program Files\Thorlabs\Scientific Imaging\ThorCam"
if platform.system()=="Windows":
    try:
        if os.path.isdir(THORCAM_SDK_DIR):os.add_dll_directory(THORCAM_SDK_DIR)
    except:pass

CAM_AVAILABLE=True;CAM_IMPORT_ERROR=None
try: from thorlabs_tsi_sdk.tl_camera import TLCameraSDK
except Exception as e: CAM_AVAILABLE=False;CAM_IMPORT_ERROR=str(e)
try: from pylablib.devices import Thorlabs as ThorlabsPylablib
except: ThorlabsPylablib=None
import ctypes as _ctypes

_TLPM_DLL_PATH=r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPM_64.dll"

class _TLPMPowerMeter:
    def __init__(self,visa_hint=""):
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
        p=_ctypes.c_double(0.0)
        self._dll.TLPM_measPower(self._handle,_ctypes.byref(p))
        return float(p.value)
    def close(self):
        try:self._dll.TLPM_close(self._handle)
        except:pass
SETTINGS_FILE=os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd(),"sor_demo_settings.ini")
CLUSTER_COLORS=[(228,26,28),(55,126,184),(77,175,74),(152,78,163),(255,127,0),(255,255,51),
                (166,86,40),(247,129,191),(153,153,153),(0,210,210),(180,0,0),(0,0,180)]
PLOT_VARS=["I (A)","E (V)","R","ΔR/R","Power (W)","dE/dt","Frame #"]
ZONE_COLORS=[(255,80,80),(80,180,255),(80,255,120),(255,200,60),
             (220,80,255),(80,255,220),(255,140,40),(200,255,80)]
CV_HEADER_SPACER_PX=16

EXP_CV  = "CV"
EXP_CVA = "CV Advanced"

def _ensure_dll():
    if platform.system()=="Windows":
        try:
            if os.path.isdir(THORCAM_SDK_DIR):os.add_dll_directory(THORCAM_SDK_DIR)
        except:pass

def try_import_ebl():
    try:
        import easy_biologic as ebl;import easy_biologic.base_programs as blp;return True,ebl,blp,""
    except Exception as e:
        msg=str(e)
        if "pkg_resources" in msg:msg+='\nFix: pip install "setuptools<82" --force-reinstall'
        return False,None,None,f"{msg}\n\n{traceback.format_exc()}"

__all__ = [name for name in globals() if not name.startswith("__")]
