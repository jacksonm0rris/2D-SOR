"""AcquisitionMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *

import queue as _queue


class _LiveFrameProcessor(QtCore.QThread):
    result_ready=QtCore.pyqtSignal(object)

    def __init__(self,max_queue=4,parent=None):
        super().__init__(parent)
        self._queue=_queue.Queue(maxsize=max(1,int(max_queue)))
        self._stop_evt=threading.Event()
        self._dropped_frames=0

    def submit(self,payload):
        if self._stop_evt.is_set():return
        try:
            self._queue.put_nowait(payload)
        except _queue.Full:
            try:self._queue.get_nowait()
            except _queue.Empty:pass
            else:self._dropped_frames+=1
            try:self._queue.put_nowait(payload)
            except _queue.Full:pass

    def request_stop(self):
        self._stop_evt.set()
        try:self._queue.put_nowait(None)
        except _queue.Full:pass

    def run(self):
        while not self._stop_evt.is_set():
            try:payload=self._queue.get(timeout=0.05)
            except _queue.Empty:continue
            if payload is None:break
            try:
                t0=time.perf_counter()
                frame=np.asarray(payload["frame"])
                frame_flat=frame.ravel()
                rv=0.0
                for mask in payload["masks"]:
                    rv+=float(np.nansum(frame_flat[mask]))
                elapsed_ms=(time.perf_counter()-t0)*1000.0
                self.result_ready.emit({
                    "frame_idx":payload["frame_idx"],
                    "t_wall":payload["t_wall"],
                    "power":payload["power"],
                    "roi":rv,
                    "generation":payload["generation"],
                    "worker_frame_ms":elapsed_ms,
                    "worker_queue_dropped_frames":self._dropped_frames,
                })
            except Exception as e:
                self.result_ready.emit({
                    "frame_idx":payload.get("frame_idx",-1),
                    "t_wall":payload.get("t_wall",np.nan),
                    "power":payload.get("power",np.nan),
                    "roi":np.nan,
                    "generation":payload.get("generation",-1),
                    "worker_frame_ms":np.nan,
                    "worker_queue_dropped_frames":self._dropped_frames,
                    "error":str(e),
                })


class AcquisitionMixin:
    def _start_frame_processor(self):
        self._stop_frame_processor()
        self._frame_processor=_LiveFrameProcessor(max_queue=4,parent=self)
        self._frame_processor.result_ready.connect(
            self._on_frame_processed,QtCore.Qt.ConnectionType.QueuedConnection)
        self._frame_processor.start()

    def _stop_frame_processor(self):
        proc=getattr(self,"_frame_processor",None)
        if proc is not None:
            proc.request_stop()
            proc.wait(1000)
            self._frame_processor=None

    def _ensure_live_capacity(self,n):
        if n<=self._live_ftw_arr.size:return
        new_size=max(n,self._live_ftw_arr.size*2)
        ftw=np.full(new_size,np.nan,dtype=np.float64)
        roi=np.full(new_size,np.nan,dtype=np.float64)
        ftw[:self._live_ftw_arr.size]=self._live_ftw_arr
        roi[:self._live_roi_arr.size]=self._live_roi_arr
        self._live_ftw_arr=ftw;self._live_roi_arr=roi

    def _clear_data(self):
        self._stop_frame_processor()
        self._frame_processor_generation+=1
        for a in ["_E_all","_I_all","_t_all","_t_wall_echem","_frame_E","_frame_I","_frame_t","_frame_t_wall","_frame_power","_roi_intensity","_drr_intensity"]:
            setattr(self,a,np.array([],dtype=np.float64))
        self._roi_cache_key=None;self._live_ftw=[];self._live_fpwr=[];self._live_roi=[]
        self._invalidate_zone_mask_cache()
        self._live_eE_arr=np.array([],dtype=np.float64)
        self._live_eI_arr=np.array([],dtype=np.float64)
        self._live_etw_arr=np.array([],dtype=np.float64)
        self._E_cv=np.array([],dtype=np.float64)
        self._I_cv=np.array([],dtype=np.float64)
        self._t_cv=np.array([],dtype=np.float64)
        self._tw_cv=np.array([],dtype=np.float64)
        self._live_ftw_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_roi_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_processed_n=0
        self._cv_last_draw=0.0
        self._frames_path=None;self._frames_hw=None;self._n_frames=0;self._memmap=None
        self._ref_frame=None;self._ref_roi_val=None;self._last_frame=None
        self._engine=None;self._cl_means=None;self._clear_an()
        # ── OPTIMISATION: clear caches on new data ────────────────────────────
        self._cached_cycles=None
        self._lowess_cache={}
        self.cl_overlay.setVisible(False);self.pca_overlay.setVisible(False);self.an_var.setText("")
        self.fslider.setRange(0,0);self.fslider_lbl.setText("0/0")
        self.cv_curve.setData([],[]);self.roi_t_curve.setData([],[]);self.roi_e_curve.setData([],[])
        for _attr in ("_cv_cyc_curves","_roi_e_cyc_curves","_roi_e_cyc_lowess"):
            if hasattr(self,_attr):
                _pl=self.cv_plot if "_cv_" in _attr else self.roi_e_plot
                for _c in getattr(self,_attr):_pl.removeItem(_c)
                setattr(self,_attr,[])
        self._cycle_colors={};self._n_cycles_detected=0
        self.cv_smooth.setData([],[]);self.roi_t_smooth.setData([],[]);self.roi_e_smooth.setData([],[])
        if hasattr(self,'roi_t_loess'):self.roi_t_lowess.setData([],[])
        if hasattr(self,'roi_e_loess'):self.roi_e_lowess.setData([],[])
        self.cv_mk.setData([],[]);self.roi_t_mk.setData([],[]);self.roi_e_mk.setData([],[])
        self.img_item.setImage(np.zeros((2,2),dtype=np.float32))
        self.btn_analyze.setEnabled(False)
        self._zone_intensity=[]
        self._undo_stack=[]
        if hasattr(self,"zone_lbl"):self._update_zone_label()

    def _set_btns(self,running):
        self.btn_run.setEnabled(not running);self.btn_stop.setEnabled(running)
        self.btn_setup.setEnabled(not running);self.btn_test.setEnabled(not running)
        self.btn_load.setEnabled(not running)
        self.btn_analyze.setEnabled(not running)

    def _on_run(self):
        if self._running:return
        exp=self.cfg["potentiostat"].get("experiment_type",EXP_CV)
        import datetime
        default_name=datetime.datetime.now().strftime(f"experiment_%Y%m%d_%H%M%S.json")
        json_path,_=QtWidgets.QFileDialog.getSaveFileName(
            self,"Save experiment data as",
            os.path.join(self._last_save_dir,default_name),
            "JSON (*.json);;All files (*)")
        if not json_path:return
        if not json_path.lower().endswith(".json"):json_path+=".json"
        save_dir=os.path.dirname(json_path)
        os.makedirs(save_dir,exist_ok=True)
        self._last_save_dir=save_dir
        self._pending_json_path=json_path
        json_stem=os.path.splitext(os.path.basename(json_path))[0]
        bin_path=os.path.join(save_dir,json_stem+"_frames.bin")
        self._clear_data();self._running=True;self._set_btns(True);self.traffic.setState(TrafficLight.RED)
        self._start_frame_processor()
        self.status_lbl.setText(f"Starting {exp}...");self.prog_lbl.setText("")
        self._store_dir=save_dir
        if exp==EXP_CVA:
            self._worker=LiveCVAWorker(self.cfg,self._store_dir,bin_path=bin_path)
        else:
            self._worker=LiveCVWorker(self.cfg,self._store_dir,bin_path=bin_path)
        self._wire();self._worker.start()

    def _on_test(self,sp=None):
        if self._running:return
        if isinstance(sp,bool):sp=None
        if sp is None:
            sd=os.path.join(os.path.dirname(os.path.abspath(__file__)),"test_scenarios")
            p,_=QtWidgets.QFileDialog.getOpenFileName(self,"Scenario",sd if os.path.isdir(sd) else "","JSON (*.json);;All (*)")
            sp=p if p else None
        self._clear_data();self._running=True;self._set_btns(True);self.traffic.setState(TrafficLight.RED)
        self._start_frame_processor()
        self.status_lbl.setText("Test...");self.prog_lbl.setText("")
        self._store_dir=tempfile.mkdtemp(prefix="sor_test_")
        import datetime as _dt
        _ts=_dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        _bin=os.path.join(self._store_dir,f"test_{_ts}_frames.bin")
        self._worker=TestWorker(self.cfg,self._store_dir,scenario_path=sp,bin_path=_bin)
        self._wire();self._worker.start()

    def _wire(self):
        w=self._worker
        w.new_echem.connect(self._on_echem,QtCore.Qt.ConnectionType.QueuedConnection)
        w.new_frame.connect(self._on_frame,QtCore.Qt.ConnectionType.QueuedConnection)
        w.status_msg.connect(lambda m:self.status_lbl.setText(m),QtCore.Qt.ConnectionType.QueuedConnection)
        w.progress.connect(self._on_prog,QtCore.Qt.ConnectionType.QueuedConnection)
        w.finished_ok.connect(self._on_done,QtCore.Qt.ConnectionType.QueuedConnection)
        w.finished_err.connect(self._on_err,QtCore.Qt.ConnectionType.QueuedConnection)

    def _on_stop(self):
        if self._worker:self.status_lbl.setText("Stopping...");self._worker.request_stop()
        self.btn_stop.setEnabled(False)

    def _on_done(self,ds):
        self._stop_frame_processor()
        self._running=False;self._worker=None;self._set_btns(False)
        self.traffic.setState(TrafficLight.YELLOW)
        QtWidgets.QApplication.processEvents()
        self._raw_ds=ds
        self._frames_path=ds["frames_path"];self._frames_hw=ds["frames_hw"]
        self._n_frames=ds["frames_count"]
        self._E_all=ds["E_all"];self._I_all=ds["I_all"];self._t_all=ds["t_all"]
        if "t_wall_echem" in ds:self._t_wall_echem=ds["t_wall_echem"]
        self._frame_E=ds["frame_E"];self._frame_I=ds["frame_I"]
        self._frame_t_wall=ds["frame_t_wall"];self._frame_power=ds["frame_power"]
        ftw=ds["frame_t_wall"]
        self._frame_t=ftw-ftw[0] if ftw.size>0 else ds["frame_t"]
        if self._n_frames>0 and self._frames_hw:
            try:
                h,w=self._frames_hw
                self._memmap=np.memmap(self._frames_path,dtype=np.float32,mode="r",shape=(self._n_frames,h,w))
                self._last_frame=np.asarray(self._memmap[-1],dtype=np.float32)
                self._show_frame(self._last_frame)
                self.fslider.setRange(0,max(0,self._n_frames-1))
                self.fslider.setValue(self._n_frames-1)
                self.fslider_lbl.setText(f"{self._n_frames-1}/{self._n_frames-1}")
            except Exception as e:self._memmap=None;self.status_lbl.setText(f"Memmap err: {e}")
        try:
            mp=save_dataset_metadata(ds,json_path=self._pending_json_path)
            self.status_lbl.setText(f"Done. {self._n_frames} frames. Click ⚙ Analyze to process.\n{mp}")
        except Exception as e:
            self.status_lbl.setText(f"Done. {self._n_frames} frames (save err: {e}). Click ⚙ Analyze.")
        self._pending_json_path=None
        self.btn_analyze.setEnabled(True)
        self.traffic.setState(TrafficLight.GREEN)

    def _on_err(self,msg):
        self._stop_frame_processor()
        self._running=False;self._worker=None;self._set_btns(False);self.traffic.setState(TrafficLight.GREEN)
        self.status_lbl.setText("Error.");QtWidgets.QMessageBox.warning(self,"Error",msg[:1000])

    def _on_analyze(self):
        """Run all deferred heavy processing after acquisition completes."""
        if self._memmap is None and self._n_frames==0:
            QtWidgets.QMessageBox.information(self,"Analyze","No data to analyze.");return
        self.btn_analyze.setEnabled(False)
        self.traffic.setState(TrafficLight.YELLOW)
        self.status_lbl.setText("Analyzing...")
        QtWidgets.QApplication.processEvents()
        # Clear caches so stale results don't survive a re-analyze
        self._cached_cycles=None
        self._lowess_cache={}
        try:
            self._analyze_zones()
            self._update_cv()
            # ── OPTIMISATION: detect cycles once and cache ────────────────────
            self._cached_cycles=_detect_cycles(self._frame_E,upper_v=self._get_upper_v())
            cycles=self._cached_cycles
            self.cycle_sp.setRange(0,len(cycles))
            n_cv=len(cycles)
            self.cycle_sp.setToolTip(
                "0 = all data\n"
                +(("1 = initial hold\n") if (cycles and cycles[0][0]==0
                    and self._frame_E.size>0
                    and abs(self._frame_E[cycles[0][0]]-self._frame_E[min(cycles[0][1]-1,self._frame_E.size-1)])<0.01) else "")
                +f"Cycles detected: {n_cv}")
            self._update_roi_plots()
            if self._an_active:
                self._run_an()
            self.status_lbl.setText(f"Analysis complete. {self._n_frames} frames, {n_cv} cycles.")
        except Exception as e:
            self.status_lbl.setText(f"Analysis error: {e}")
            QtWidgets.QMessageBox.warning(self,"Analyze",f"{e}\n\n{traceback.format_exc()}")
        self.btn_analyze.setEnabled(True)
        self.traffic.setState(TrafficLight.GREEN)

    def _load_ds(self,ds):
        self._frames_path=ds["frames_path"];self._frames_hw=ds["frames_hw"];self._n_frames=ds["frames_count"]
        self._E_all=ds["E_all"];self._I_all=ds["I_all"];self._t_all=ds["t_all"]
        if "t_wall_echem" in ds:self._t_wall_echem=ds["t_wall_echem"]
        self._frame_E=ds["frame_E"];self._frame_I=ds["frame_I"]
        self._frame_t_wall=ds["frame_t_wall"];self._frame_power=ds["frame_power"]
        ftw=ds["frame_t_wall"]
        self._frame_t=ftw-ftw[0] if ftw.size>0 else ds["frame_t"]
        if self._n_frames>0 and self._frames_hw and not ds.get("frames_missing"):
            try:
                h,w=self._frames_hw
                self._memmap=np.memmap(self._frames_path,dtype=np.float32,mode="r",shape=(self._n_frames,h,w))
                self._last_frame=np.asarray(self._memmap[-1],dtype=np.float32);self._show_frame(self._last_frame)
                self.fslider.setRange(0,max(0,self._n_frames-1));self.fslider.setValue(self._n_frames-1)
                self.fslider_lbl.setText(f"{self._n_frames-1}/{self._n_frames-1}")
            except Exception as e:self._memmap=None;self.status_lbl.setText(f"Memmap err: {e}")
        self._update_cv()

    def _on_load(self):
        if self._running:return
        path,_=QtWidgets.QFileDialog.getOpenFileName(
            self,"Open dataset","",
            "SOR dataset (*.json);;All files (*)")
        if not path:
            path=QtWidgets.QFileDialog.getExistingDirectory(
                self,"Or pick dataset directory","",
                QtWidgets.QFileDialog.Option.ShowDirsOnly)
        if not path:return
        try:
            if os.path.isfile(path) and path.lower().endswith(".json"):
                ds=load_dataset_from_json(path)
                store=os.path.dirname(path)
            else:
                ds=load_dataset_from_directory(path)
                store=path
        except Exception as e:QtWidgets.QMessageBox.critical(self,"Load",str(e));return
        self._clear_data();self._store_dir=store;self._load_ds(ds)
        self.status_lbl.setText(f"Loaded {self._n_frames} frames. Click ⚙ Analyze to process.")
        self.btn_analyze.setEnabled(True)

    def _on_prog(self,pts,frames):self.prog_lbl.setText(f"Echem: {pts:,} | Frames: {frames}")

    def _on_echem(self,data):
        E=data["E"];I=data["I"];t=data["t"]
        tw=data.get("t_wall",np.array([],dtype=np.float64))
        self._E_all=E;self._I_all=I;self._t_all=t
        if "t_wall" in data:self._t_wall_echem=tw
        if "phase" in data:
            _ph=np.asarray(data["phase"])
            _cv_mask=(_ph=="cv")
            self._E_cv=E[_cv_mask]
            self._I_cv=I[_cv_mask]
            self._t_cv=t[_cv_mask]
            self._tw_cv=tw[_cv_mask] if tw.size else np.array([],dtype=np.float64)
        else:
            self._E_cv=E;self._I_cv=I
            self._t_cv=t;self._tw_cv=tw
        self._live_eE_arr=E;self._live_eI_arr=I
        self._live_etw_arr=tw
        now=time.time()
        if now-self._cv_last_draw>=0.1:
            self._update_cv(live=True)
            self._cv_last_draw=now

    def _on_frame(self,data):
        frame=data["frame"];tw=data["t_wall"];pwr=data["power"];fidx=data["frame_idx"]
        self._last_frame=frame;self._n_frames=fidx+1
        if self._frames_hw is None:self._frames_hw=frame.shape[:2]
        self._live_ftw.append(tw);self._live_fpwr.append(pwr)
        n=fidx+1
        self._ensure_live_capacity(n)
        self.fslider.setRange(0,max(0,fidx));self.fslider.setValue(fidx)
        self.fslider_lbl.setText(f"{fidx}/{fidx}")
        masks=tuple(self._get_zone_mask_flats(frame.shape[:2]))
        proc=getattr(self,"_frame_processor",None)
        if proc is not None and proc.isRunning():
            proc.submit({
                "frame":frame,
                "masks":masks,
                "frame_idx":fidx,
                "t_wall":tw,
                "power":pwr,
                "generation":self._frame_processor_generation,
            })
        else:
            rv=0.0;frame_flat=frame.ravel()
            for _pmask in masks:rv+=float(np.nansum(frame_flat[_pmask]))
            self._on_frame_processed({
                "frame_idx":fidx,"t_wall":tw,"power":pwr,"roi":rv,
                "generation":self._frame_processor_generation,
                "worker_frame_ms":np.nan,
                "worker_queue_dropped_frames":0})

    def _on_frame_processed(self,data):
        if data.get("generation")!=self._frame_processor_generation:return
        fidx=int(data.get("frame_idx",-1))
        if fidx<0:return
        n=fidx+1
        self._ensure_live_capacity(n)
        self._live_ftw_arr[fidx]=float(data.get("t_wall",np.nan))
        self._live_roi_arr[fidx]=float(data.get("roi",np.nan))
        self._live_processed_n=max(self._live_processed_n,n)
        fa=self._live_ftw_arr[:self._live_processed_n]
        ra=self._live_roi_arr[:self._live_processed_n]
        if fa.size==0 or not np.isfinite(fa[0]):return
        te=fa-fa[0]
        MAX_LIVE=2000
        if self._live_processed_n>MAX_LIVE:
            idx=np.round(np.linspace(0,self._live_processed_n-1,MAX_LIVE)).astype(int)
            te_d=te[idx];ra_d=ra[idx]
        else:
            te_d=te;ra_d=ra
        m=np.isfinite(te_d)&np.isfinite(ra_d)
        self.roi_t_curve.setData(te_d[m],ra_d[m])
        self.roi_t_smooth.setData([],[])
        self.roi_e_curve.setData([],[])
        self.roi_e_smooth.setData([],[])

    def _open_setup(self):
        dlg=SetupDialog(self.cfg,parent=self)
        if dlg.exec()==QtWidgets.QDialog.DialogCode.Accepted:
            self.cmap_cb.setCurrentText(self.cfg["display"].get("colormap","viridis"))
            self.auto_lev.setChecked(self.cfg["display"].get("auto_levels","true").lower()=="true")
            self.lmin.setValue(float(self.cfg["display"].get("level_min","0")))
            self.lmax.setValue(float(self.cfg["display"].get("level_max","1000")))
            self._apply_cmap();self._refresh_exp_lbl();self.status_lbl.setText("Settings updated.")

__all__ = [name for name in globals() if not name.startswith("__")]
