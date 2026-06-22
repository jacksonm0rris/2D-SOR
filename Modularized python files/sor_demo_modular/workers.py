"""Live acquisition and test-data worker threads.

Generated from sor_demo_v19_26-6-2.py during modularization.
"""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .echem_data import *

class _BaseCVWorker(QtCore.QThread):
    new_echem  = QtCore.pyqtSignal(object)
    new_frame  = QtCore.pyqtSignal(object)
    status_msg = QtCore.pyqtSignal(str)
    progress   = QtCore.pyqtSignal(int, int)
    finished_ok  = QtCore.pyqtSignal(object)
    finished_err = QtCore.pyqtSignal(str)

    def __init__(self, cfg, sd, bin_path=None, parent=None):
        super().__init__(parent)
        self._stop = False
        self.cfg = cfg
        self.sd = sd
        self.bin_path = bin_path

    def request_stop(self):
        self._stop = True

    def _build_sequence(self, blp, bl, ocv):
        raise NotImplementedError

    def run(self):
        ok, ebl, blp, err = try_import_ebl()
        if not ok:
            self.finished_err.emit(f"easy-biologic:\n{err}"); return
        if not CAM_AVAILABLE:
            self.finished_err.emit(CAM_IMPORT_ERROR or "Camera missing"); return
        _ensure_dll()
        cam = self.cfg["camera"]; pmc = self.cfg["power_meter"]
        exp_us   = int(cam.get("exposure_us", "7000"))
        ci       = int(cam.get("camera_index", "0"))
        bf       = int(cam.get("bin_factor", "1"))
        bm       = cam.get("bin_mode", "mean")
        cap_int  = max(10, int(cam.get("capture_interval_ms", "33")))
        fw       = int(cam.get("frame_wait_ms", "500"))
        use_pm   = pmc.get("enabled", "false").lower() == "true"
        pmv      = pmc.get("visa", "")
        os.makedirs(self.sd, exist_ok=True)
        fp = self.bin_path if self.bin_path else os.path.join(self.sd, "frames_f32.bin")
        sink = FrameFileSink(fp); bl = None; pm = None
        Ea, Ia, ta, twe, Ephase = [], [], [], [], []
        ftw, fpw = [], []
        seq_exc = {"e": None}

        try:
            self.status_msg.emit("Connecting...")
            bl = ebl.BiologicDevice(self._get_bl_address())
            bl.connect(None, None)
            ocv = 0.0
            if self._use_ocv():
                try:
                    if hasattr(blp, "OCV"):
                        op = blp.OCV(bl, {"duration": 2.0}, channels=[0])
                        op.run("data")
                        ch = op.data.get(0, [])
                        if ch: ocv = safe_float(getattr(ch[-1], "voltage", None), 0.0)
                except: ocv = 0.0

            sequence = self._build_sequence(blp, bl, ocv)
            current_phase = {"label": "init"}

            def _run_sequence():
                try:
                    for label, prog in sequence:
                        if self._stop: break
                        if prog is None: continue
                        current_phase["label"] = label
                        self.status_msg.emit(label)
                        prog.run("data")
                    current_phase["label"] = "done"
                except Exception as e:
                    seq_exc["e"] = e

            seq_thread = threading.Thread(target=_run_sequence, daemon=True)

            with TLCameraSDK() as sdk:
                cams = sdk.discover_available_cameras()
                if not cams: raise RuntimeError("No cameras found")
                cid = cams[min(max(ci, 0), len(cams) - 1)]
                with sdk.open_camera(cid) as camera:
                    try: camera.exposure_time_us = exp_us
                    except: pass
                    camera.frames_per_trigger_zero_for_unlimited = 0
                    camera.image_poll_timeout_ms = 50
                    camera.arm(2)
                    if use_pm:
                        pm=None
                        try:pm=_TLPMPowerMeter(pmv)
                        except Exception as _e:self.status_msg.emit(f"Power meter open failed — {_e}")
                        if pm is not None:
                            self.status_msg.emit("Priming power meter...")
                            for _ in range(10):
                                try:pm.get_power()
                                except:pass
                                time.sleep(0.05)

                    seq_thread.start()
                    cf = 0; lee = 0.0; lft = 0.0

                    while seq_thread.is_alive() and not self._stop:
                        now = time.time()
                        for label, prog in sequence:
                            if prog is None: continue
                            t_now = time.time()
                            try: cd = prog.data.get(0, [])
                            except: continue
                            seen = getattr(prog, "_seen_pts", 0)
                            if len(cd) < seen:
                                prog._seen_pts = len(cd)
                                seen = len(cd)
                            if len(cd) > seen:
                                batch = cd[seen:]
                                finite_batch = []
                                for j, d in enumerate(batch):
                                    v, c = _extract_echem_point(d)
                                    if np.isfinite(v) and np.isfinite(c):
                                        finite_batch.append((j, d, v, c))
                                if finite_batch:
                                    dev_times, point_walls = _stamp_batch(
                                        [d for _, d, _, _ in finite_batch], twe, t_now)
                                    if "Initial" in label or "initial" in label:
                                        _ep = "hold_init"
                                    elif "Final" in label or "final" in label:
                                        _ep = "hold_final"
                                    else:
                                        _ep = "cv"
                                    for k, (j, d, v, c) in enumerate(finite_batch):
                                        Ea.append(v); Ia.append(c)
                                        ta.append(dev_times[k])
                                        twe.append(float(point_walls[k]))
                                        Ephase.append(_ep)
                                prog._seen_pts = len(cd)

                        if (now - lee) > 0.15 and Ea:
                            _Ea_arr = np.array(Ea, dtype=np.float64)
                            _Ia_arr = np.array(Ia, dtype=np.float64)
                            try:
                                _sv = float(self.cfg["potentiostat"].get("step_v", "0.001"))
                                _Ia_arr = _step_average_current(_Ea_arr, _Ia_arr, step_v=_sv)
                            except Exception:
                                pass
                            self.new_echem.emit({
                                "E": _Ea_arr,
                                "I": _Ia_arr,
                                "t": np.array(ta, dtype=np.float64),
                                "t_wall": np.array(twe, dtype=np.float64),
                                "phase": list(Ephase)})
                            lee = now

                        if (now - lft) * 1000 >= cap_int:
                            try: camera.issue_software_trigger()
                            except: pass
                            t0g = time.time()
                            while (time.time() - t0g) * 1000 < fw:
                                fr = camera.get_pending_frame_or_null()
                                if fr is not None:
                                    img = np.asarray(fr.image_buffer).astype(np.float32, copy=False)
                                    if bf > 1: img = _bin_2d(img, bf, bm)
                                    sink.append(img); tw = time.time(); pwr = np.nan
                                    if pm:
                                        try: pwr = float(pm.get_power())
                                        except: pass
                                    ftw.append(tw); fpw.append(pwr)
                                    _lbl = current_phase["label"]
                                    if "Initial" in _lbl or "initial" in _lbl:
                                        _phase = "hold_init"
                                    elif "Final" in _lbl or "final" in _lbl:
                                        _phase = "hold_final"
                                    else:
                                        _phase = "cv"
                                    self.new_frame.emit({"frame": img, "t_wall": tw,
                                                         "power": pwr, "frame_idx": cf,
                                                         "phase": _phase})
                                    cf += 1; self.progress.emit(len(Ea), cf); break
                                time.sleep(0.001)
                            lft = now
                        time.sleep(0.002)

                    if Ea:
                        _Ea_f = np.array(Ea, dtype=np.float64)
                        _Ia_f = np.array(Ia, dtype=np.float64)
                        try:
                            _sv = float(self.cfg["potentiostat"].get("step_v", "0.001"))
                            _Ia_f = _step_average_current(_Ea_f, _Ia_f, step_v=_sv)
                        except Exception:
                            pass
                        self.new_echem.emit({
                            "E": _Ea_f,
                            "I": _Ia_f,
                            "t": np.array(ta, dtype=np.float64),
                            "t_wall": np.array(twe, dtype=np.float64),
                            "phase": list(Ephase)})
                    try: camera.disarm()
                    except: pass

            sink.close()
            if seq_exc["e"]: raise RuntimeError(str(seq_exc["e"]))
            etw = np.array(twe, dtype=np.float64)
            eE  = np.array(Ea,  dtype=np.float64)
            eI  = np.array(Ia,  dtype=np.float64)
            et  = np.array(ta,  dtype=np.float64)
            try:
                _sv = float(self.cfg["potentiostat"].get("step_v", "0.001"))
                eI = _step_average_current(eE, eI, step_v=_sv)
            except Exception:
                pass
            f_tw = np.array(ftw, dtype=np.float64)
            self.finished_ok.emit({
                "frames_path":  fp,
                "frames_count": sink.count,
                "frames_hw":    sink.shape_hw,
                "frame_E":      _interp_echem(f_tw, etw, eE),
                "frame_I":      _interp_echem(f_tw, etw, eI),
                "frame_t":      _interp_echem(f_tw, etw, et),
                "frame_t_wall": f_tw,
                "frame_power":  np.array(fpw, dtype=np.float64),
                "E_all": eE, "I_all": eI, "t_all": et, "t_wall_echem": etw})

        except Exception as e:
            try: sink.close()
            except: pass
            self.finished_err.emit(f"{e}\n\n{traceback.format_exc()}")
        finally:
            if pm:
                try: pm.close()
                except: pass
            if bl:
                try: bl.disconnect()
                except: pass

    def _get_bl_address(self):
        return self.cfg["potentiostat"].get("bl_address", "192.168.0.9")

    def _use_ocv(self):
        return self.cfg["potentiostat"].get("vs_ocv", "false").lower() == "true"


class LiveCVWorker(_BaseCVWorker):
    def _build_sequence(self, blp, bl, ocv):
        pot = self.cfg["potentiostat"]
        vs  = self._use_ocv()
        exp = pot.get("experiment_type", EXP_CV)

        start_v = float(pot.get("start_v", "0"))    + (ocv if vs else 0)
        upper_v = float(pot.get("upper_v", "0.25")) + (ocv if vs else 0)
        lower_v = float(pot.get("lower_v", "-0.1")) + (ocv if vs else 0)
        end_v   = float(pot.get("end_v",   "0"))    + (ocv if vs else 0)

        t_init  = float(pot.get("hold_t_init",  "0.0"))
        t_final = float(pot.get("hold_t_final", "0.0"))

        def _ca(voltage, duration, label):
            if duration <= 0:
                return label, None
            prog = blp.CA(
                bl,
                {"voltages": [voltage], "durations": [duration],
                 "time_interval": 0.1, "current_interval": 1e-3},
                channels=[0],
                autoconnect=False,
            )
            return label, prog

        cv_params = {
            "start":   start_v,
            "end":     upper_v,
            "E2":      lower_v,
            "Ef":      end_v,
            "rate":    float(pot.get("scan_rate", "0.02")),
            "step":    float(pot.get("step_v",    "0.001")),
            "N_Cycles":          int(pot.get("cycles", "1")),
            "Begin_measuring_I": float(pot.get("cva_begin_measuring_i", "0.5")),
            "End_measuring_I":   float(pot.get("cva_end_measuring_i",   "1.0")),
            "average": True,
        }
        cv_prog = blp.CV(bl, cv_params, channels=[0], autoconnect=False)

        return [
            _ca(start_v, t_init,  "Initial hold..."),
            ("CV scan",            cv_prog),
            _ca(end_v,   t_final, "Final hold..."),
        ]


LiveCVAWorker = LiveCVWorker

def load_test_scenario(path):
    with open(path,"r",encoding="utf-8") as f:return json.load(f)

def render_scenario_frame(sc,E,tf,h,w,yy,xx):
    bg=float(sc.get("background",200));noise=float(sc.get("noise_std",10))
    frame=np.full((h,w),bg,dtype=np.float32)
    for reg in sc.get("regions",[]):
        amp=float(reg.get("amplitude",500));mod=_cmod(reg.get("modulation",{}),E,tf)
        rt=reg.get("type","gaussian")
        if rt=="gaussian":
            cxf=float(reg.get("cx",0.5));cyf=float(reg.get("cy",0.5))
            sig=float(reg.get("sigma",0.1))*min(h,w)
            cx=cxf*w;cy=cyf*h;mv=reg.get("movement",{})
            if mv:cx+=float(mv.get("dx_per_E",0))*E*w;cy+=float(mv.get("dy_per_E",0))*E*h
            r2=((xx-cx)**2+(yy-cy)**2).astype(np.float32);frame+=amp*mod*np.exp(-r2/(2*sig**2))
        elif rt=="rect":
            x0=max(0,min(w,int(float(reg.get("x0",0))*w)));y0=max(0,min(h,int(float(reg.get("y0",0))*h)))
            x1=max(0,min(w,int(float(reg.get("x1",1))*w)));y1=max(0,min(h,int(float(reg.get("y1",1))*h)))
            frame[y0:y1,x0:x1]+=amp*mod
        elif rt=="voronoi":pass
    vr=[r for r in sc.get("regions",[]) if r.get("type")=="voronoi"]
    if vr:
        ck=f"_vc_{h}_{w}"
        if ck not in sc:
            seeds=np.array([(float(r.get("cy",0.5))*h,float(r.get("cx",0.5))*w) for r in vr],dtype=np.float32)
            dy_=yy[None,:,:]-seeds[:,0,None,None];dx_=xx[None,:,:]-seeds[:,1,None,None]
            sc[ck]=np.argmin(dy_**2+dx_**2,axis=0)
        labels=sc[ck]
        for i,r in enumerate(vr):
            frame[labels==i]+=float(r.get("amplitude",500))*_cmod(r.get("modulation",{}),E,tf)
    if noise>0:frame+=np.random.normal(0,noise,(h,w)).astype(np.float32)
    return frame

def _cmod(cfg,E,tf):
    mt=cfg.get("type","constant")
    if mt=="constant":return float(cfg.get("value",1.0))
    elif mt=="sine":return float(cfg.get("offset",1))+float(cfg.get("amplitude",0.3))*np.sin(2*np.pi*float(cfg.get("frequency",1))*tf+float(cfg.get("phase",0)))
    elif mt=="step":return float(cfg.get("after",1.5)) if tf>=float(cfg.get("t_step",0.5)) else float(cfg.get("before",1))
    elif mt=="faradaic":return float(cfg.get("baseline",1))+float(cfg.get("peak_amplitude",0.3))*np.exp(-((E-float(cfg.get("E_peak",0.1)))**2)/(2*float(cfg.get("width",0.02))**2))
    elif mt=="ramp":return float(cfg.get("start",1))+(float(cfg.get("end",1.5))-float(cfg.get("start",1)))*tf
    return 1.0


class TestWorker(QtCore.QThread):
    new_echem=QtCore.pyqtSignal(object);new_frame=QtCore.pyqtSignal(object)
    status_msg=QtCore.pyqtSignal(str);progress=QtCore.pyqtSignal(int,int)
    finished_ok=QtCore.pyqtSignal(object);finished_err=QtCore.pyqtSignal(str)
    def __init__(self,cfg,sd,scenario_path=None,n_points=500,frame_hw=(256,320),frame_rate_hz=30.0,bin_path=None,parent=None):
        super().__init__(parent);self._stop=False;self.cfg=cfg;self.sd=sd;self.sp=scenario_path
        self.bin_path=bin_path
        self.np_=n_points;self.fhw=frame_hw;self.fr=frame_rate_hz
    def request_stop(self):self._stop=True
    def run(self):
        try:
            sc=None
            if self.sp and os.path.isfile(self.sp):
                sc=load_test_scenario(self.sp);self.status_msg.emit(f"Scenario: {os.path.basename(self.sp)}")
            if sc is None:
                sc={"background":200,"noise_std":10,"regions":[{"type":"gaussian","cx":0.5,"cy":0.5,"sigma":0.12,"amplitude":800,
                    "modulation":{"type":"faradaic","E_peak":0.1,"width":0.02,"baseline":1.0,"peak_amplitude":0.3},
                    "movement":{"dx_per_E":0.8}}]}
                self.status_msg.emit("Test (default)...")
            os.makedirs(self.sd,exist_ok=True)
            fp=self.bin_path if self.bin_path else os.path.join(self.sd,"frames_f32.bin")
            sink=FrameFileSink(fp)
            pot=self.cfg["potentiostat"];sv=float(pot.get("start_v","0"));uv=float(pot.get("upper_v","0.25"))
            lv=float(pot.get("lower_v","-0.1"));step=max(float(pot.get("step_v","0.001")),1e-6)
            sr=max(float(pot.get("scan_rate","0.02")),1e-6)
            seg=np.concatenate([np.arange(sv,uv,step),np.arange(uv,lv,-step),np.arange(lv,sv,step)])
            if seg.size==0:seg=np.linspace(-0.1,0.25,self.np_)
            if seg.size<self.np_:seg=np.tile(seg,int(np.ceil(self.np_/seg.size)))
            Ew=seg[:self.np_];dt=step/sr;tw=np.arange(self.np_)*dt
            dE=np.gradient(Ew,tw)
            Iw=20e-6*dE+50e-6*np.exp(-((Ew-0.1)**2)/(2*0.02**2))*np.sign(dE)+np.random.normal(0,1e-7,self.np_)
            Ea,Ia,ta,twe=[],[],[],[];ftw_,fpw_=[],[]
            h,w=self.fhw;yy,xx=np.mgrid[0:h,0:w];cf=0;t0=time.time();fi=1.0/max(self.fr,1.0);ei=0;lee=0.0;lft=0.0
            while ei<self.np_ and not self._stop:
                now=time.time();elapsed=now-t0;bs_=ei
                while ei<self.np_ and ei*dt<=elapsed:
                    Ea.append(float(Ew[ei]));Ia.append(float(Iw[ei]));ta.append(float(tw[ei]));ei+=1
                bc=ei-bs_
                if bc>0:
                    for j in range(bc):twe.append(t0+float(tw[bs_+j]))
                if (now-lee)>0.10 and Ea:
                    self.new_echem.emit({"E":np.array(Ea,dtype=np.float64),"I":np.array(Ia,dtype=np.float64),
                        "t":np.array(ta,dtype=np.float64),"t_wall":np.array(twe,dtype=np.float64)});lee=now
                if (now-lft)>=fi and Ea:
                    frame=render_scenario_frame(sc,Ea[-1],ei/max(self.np_,1),h,w,yy,xx)
                    sink.append(frame);twn=time.time();pwr=0.5+0.01*np.random.randn()
                    ftw_.append(twn);fpw_.append(pwr)
                    self.new_frame.emit({"frame":frame,"t_wall":twn,"power":pwr,"frame_idx":cf,"phase":"cv"})
                    cf+=1;self.progress.emit(ei,cf);lft=now
                time.sleep(0.001)
            if Ea:self.new_echem.emit({"E":np.array(Ea,dtype=np.float64),"I":np.array(Ia,dtype=np.float64),
                "t":np.array(ta,dtype=np.float64),"t_wall":np.array(twe,dtype=np.float64)})
            sink.close()
            etw=np.array(twe,dtype=np.float64);eE=np.array(Ea,dtype=np.float64)
            eI=np.array(Ia,dtype=np.float64);et=np.array(ta,dtype=np.float64);f_tw=np.array(ftw_,dtype=np.float64)
            self.finished_ok.emit({"frames_path":fp,"frames_count":sink.count,"frames_hw":sink.shape_hw,
                "frame_E":_interp_echem(f_tw,etw,eE),"frame_I":_interp_echem(f_tw,etw,eI),
                "frame_t":_interp_echem(f_tw,etw,et),"frame_t_wall":f_tw,
                "frame_power":np.array(fpw_,dtype=np.float64),"E_all":eE,"I_all":eI,"t_all":et,"t_wall_echem":etw})
        except Exception as e:self.finished_err.emit(f"{e}\n\n{traceback.format_exc()}")

__all__ = [name for name in globals() if not name.startswith("__")]
