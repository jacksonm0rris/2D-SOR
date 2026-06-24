"""AcquisitionMixin methods for the main window.

This file is the bridge between the user interface and the data collection
pipeline. It coordinates what happens when the user clicks Run, Test, Load,
Stop, or Analyze.

The heavier device work happens in workers.py. The heavier math happens in
zones_mixin.py, cv_plot_mixin.py, analysis_mixin.py, and analysis.py. This
mixin mostly connects those pieces to the GUI.
"""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *

import queue as _queue


class _LiveFrameProcessor(QtCore.QThread):
    """Background ROI reducer for live frames.

    The camera can send many images per second. The GUI should not do expensive
    pixel math for every image on the main thread because that would make the
    window feel frozen.

    This thread receives a live camera frame, sums the selected zone pixels, and
    sends back a small ROI value for plotting. If frames arrive too fast, it
    discards older unprocessed frames and keeps the newest one. That is safe for
    live preview because the complete frame stack is still saved for full
    analysis after acquisition.
    """
    result_ready=QtCore.pyqtSignal(object)

    def __init__(self,max_queue=4,parent=None):
        super().__init__(parent)
        # Small queue by design: live display should stay current, not replay
        # every old frame if the computer briefly falls behind.
        self._max_queue=max(1,int(max_queue))
        self._queue=_queue.Queue(maxsize=self._max_queue)
        self._stop_evt=threading.Event()
        self._dropped_frames=0

    def submit(self,payload):
        # payload is a dictionary containing one frame plus metadata such as the
        # frame number, timestamp, power reading, and zone masks.
        if self._stop_evt.is_set():return
        try:
            self._queue.put_nowait(payload)
        except _queue.Full:
            # Keep the newest live frame instead of building a backlog.
            try:self._queue.get_nowait()
            except _queue.Empty:pass
            else:self._dropped_frames+=1
            try:self._queue.put_nowait(payload)
            except _queue.Full:pass

    def request_stop(self):
        # Signal the thread loop to end. The None item wakes it up if it is
        # currently waiting for work.
        self._stop_evt.set()
        try:self._queue.put_nowait(None)
        except _queue.Full:pass

    def run(self):
        # Main thread loop for live ROI preview work.
        while not self._stop_evt.is_set():
            try:payload=self._queue.get(timeout=0.05)
            except _queue.Empty:continue
            if payload is None:break
            try:
                t0=time.perf_counter()
                frame=np.asarray(payload["frame"])
                frame_flat=frame.ravel()
                roi_by_zone=[]
                # Masks are precomputed flat arrays from ZonesMixin; summing
                # them here gives a quick live ROI trace during acquisition.
                #
                # For an electrochemist: this is a live optical signal, formed
                # by adding up reflectance/intensity values inside the selected
                # region(s) on the camera image.
                for mask in payload["masks"]:
                    roi_by_zone.append(float(np.nansum(frame_flat[mask])))
                rv=float(np.nansum(roi_by_zone)) if roi_by_zone else np.nan
                elapsed_ms=(time.perf_counter()-t0)*1000.0
                # Send the compact result back to the GUI thread.
                self.result_ready.emit({
                    "frame_idx":payload["frame_idx"],
                    "t_wall":payload["t_wall"],
                    "power":payload["power"],
                    "roi":rv,
                    "roi_by_zone":roi_by_zone,
                    "zone_areas":payload.get("zone_areas",[]),
                    "generation":payload["generation"],
                    "worker_frame_ms":elapsed_ms,
                    "worker_queue_dropped_frames":self._dropped_frames,
                    "worker_queue_size":self._queue.qsize(),
                    "worker_queue_max":self._max_queue,
                })
            except Exception as e:
                # Report a failed frame as NaN instead of crashing the whole run.
                self.result_ready.emit({
                    "frame_idx":payload.get("frame_idx",-1),
                    "t_wall":payload.get("t_wall",np.nan),
                    "power":payload.get("power",np.nan),
                    "roi":np.nan,
                    "roi_by_zone":[],
                    "zone_areas":payload.get("zone_areas",[]),
                    "generation":payload.get("generation",-1),
                    "worker_frame_ms":np.nan,
                    "worker_queue_dropped_frames":self._dropped_frames,
                    "worker_queue_size":self._queue.qsize(),
                    "worker_queue_max":self._max_queue,
                    "error":str(e),
                })


class AcquisitionMixin:
    """GUI-side coordinator for acquisition, loading, and analysis.

    DemoWindow inherits this mixin, so every method here becomes a method on the
    main window. These methods depend on widgets and state created in
    main_window.py.
    """

    def _reset_perf_stats(self):
        # Performance stats are intentionally small and acquisition-focused.
        # "Preview skipped" means the live ROI preview skipped old frames to stay
        # responsive; it does not mean the saved frame file lost those frames.
        self._perf_stats={
            "saved_frames":0,
            "preview_skipped":0,
            "preview_queue":0,
            "preview_queue_max":4,
            "fps":0.0,
            "roi_ms":np.nan,
            "echem_points":0,
            "last_panel_update":0.0,
        }
        self._update_perf_panel(force=True)

    def _update_perf_panel(self,force=False):
        # Throttle label updates so the performance panel does not become a new
        # source of GUI overhead during fast acquisition.
        labels=[
            "perf_frames_lbl","perf_skipped_lbl","perf_queue_lbl",
            "perf_fps_lbl","perf_roi_ms_lbl","perf_echem_lbl",
        ]
        if not all(hasattr(self,name) for name in labels):return
        stats=getattr(self,"_perf_stats",None)
        if not stats:return
        now=time.time()
        if not force and now-stats.get("last_panel_update",0.0)<0.25:return
        stats["last_panel_update"]=now
        self.perf_frames_lbl.setText(f"{int(stats.get('saved_frames',0)):,}")
        self.perf_skipped_lbl.setText(f"{int(stats.get('preview_skipped',0)):,}")
        q=int(stats.get("preview_queue",0))
        qmax=int(stats.get("preview_queue_max",4) or 4)
        self.perf_queue_lbl.setText(f"{q}/{qmax}")
        fps=float(stats.get("fps",0.0) or 0.0)
        self.perf_fps_lbl.setText(f"{fps:.1f}")
        roi_ms=float(stats.get("roi_ms",np.nan))
        self.perf_roi_ms_lbl.setText("--" if not np.isfinite(roi_ms) else f"{roi_ms:.2f}")
        self.perf_echem_lbl.setText(f"{int(stats.get('echem_points',0)):,}")

    def _start_frame_processor(self):
        # Restart the live ROI worker so results from a prior run cannot leak
        # into the current dataset.
        self._stop_frame_processor()
        self._frame_processor=_LiveFrameProcessor(max_queue=4,parent=self)
        self._frame_processor.result_ready.connect(
            self._on_frame_processed,QtCore.Qt.ConnectionType.QueuedConnection)
        self._frame_processor.start()

    def _stop_frame_processor(self):
        # Shut down the live ROI helper thread, if it exists.
        proc=getattr(self,"_frame_processor",None)
        if proc is not None:
            proc.request_stop()
            proc.wait(1000)
            self._frame_processor=None

    def _ensure_live_capacity(self,n):
        # Grow live arrays geometrically so long runs can keep direct frame-index
        # writes without frequent reallocations.
        #
        # n is the number of frame slots needed. The arrays hold live preview
        # timestamps and live ROI values while acquisition is still running.
        if n<=self._live_ftw_arr.size:return
        new_size=max(n,self._live_ftw_arr.size*2)
        # Fill new slots with NaN so missing or unprocessed values are explicit.
        ftw=np.full(new_size,np.nan,dtype=np.float64)
        roi=np.full(new_size,np.nan,dtype=np.float64)
        ftw[:self._live_ftw_arr.size]=self._live_ftw_arr
        roi[:self._live_roi_arr.size]=self._live_roi_arr
        zone_arrs=[]
        for arr in getattr(self,"_live_zone_roi_arrs",[]):
            za=np.full(new_size,np.nan,dtype=np.float64)
            za[:arr.size]=arr
            zone_arrs.append(za)
        self._live_ftw_arr=ftw;self._live_roi_arr=roi
        self._live_zone_roi_arrs=zone_arrs

    def _ensure_live_zone_capacity(self,n,n_zones):
        # Keep one live ROI array per visible ROI zone. The total live ROI trace
        # is still stored separately for backward compatibility.
        self._ensure_live_capacity(n)
        if not hasattr(self,"_live_zone_roi_arrs"):
            self._live_zone_roi_arrs=[]
        while len(self._live_zone_roi_arrs)<n_zones:
            self._live_zone_roi_arrs.append(np.full(self._live_ftw_arr.size,np.nan,dtype=np.float64))
        for i,arr in enumerate(self._live_zone_roi_arrs):
            if arr.size<self._live_ftw_arr.size:
                za=np.full(self._live_ftw_arr.size,np.nan,dtype=np.float64)
                za[:arr.size]=arr
                self._live_zone_roi_arrs[i]=za

    def _ensure_live_roi_curves(self,n_zones):
        # Reuse the same dynamic curve list used by analyzed per-zone traces.
        if not hasattr(self,"_roi_t_zone_curves"):
            self._roi_t_zone_curves=[]
        while len(self._roi_t_zone_curves)>n_zones:
            curve=self._roi_t_zone_curves.pop()
            try:self.roi_t_plot.removeItem(curve)
            except Exception:pass
        while len(self._roi_t_zone_curves)<n_zones:
            z_idx=len(self._roi_t_zone_curves)
            r,g,b=self._zone_color(z_idx)
            curve=self.roi_t_plot.plot([],[],pen=pg.mkPen(color=(r,g,b),width=2))
            self._roi_t_zone_curves.append(curve)

    def _ensure_live_roi_e_curves(self,n_zones):
        # Live reflectance-vs-potential traces, one curve per ROI zone.
        if not hasattr(self,"_roi_e_zone_curves"):
            self._roi_e_zone_curves=[]
        while len(self._roi_e_zone_curves)>n_zones:
            curve=self._roi_e_zone_curves.pop()
            try:self.roi_e_plot.removeItem(curve)
            except Exception:pass
        while len(self._roi_e_zone_curves)<n_zones:
            z_idx=len(self._roi_e_zone_curves)
            r,g,b=self._zone_color(z_idx)
            curve=self.roi_e_plot.plot([],[],pen=pg.mkPen(color=(r,g,b),width=2))
            self._roi_e_zone_curves.append(curve)

    def _clear_data(self):
        # Reset acquisition, plotting, ROI, and analysis state before a new run
        # or load. The generation counter invalidates late worker callbacks.
        self._stop_frame_processor()
        self._frame_processor_generation+=1
        self._clear_test_preview_state()
        self._unlock_plot_axes()
        self._reset_perf_stats()
        # Clear raw electrochemistry arrays and frame-aligned arrays.
        # E/I/t arrays are potentiostat samples; frame_E/frame_I/frame_t are
        # interpolated values matched to camera frame timestamps.
        for a in ["_E_all","_I_all","_t_all","_t_wall_echem","_frame_E","_frame_I","_frame_t","_frame_t_wall","_frame_power","_roi_intensity","_roi_intensity_per_area","_drr_intensity"]:
            setattr(self,a,np.array([],dtype=np.float64))
        # Clear cached ROI state and simple live preview lists.
        self._roi_cache_key=None;self._live_ftw=[];self._live_fpwr=[];self._live_roi=[]
        self._invalidate_zone_mask_cache()
        # Live electrochemistry arrays are used while a run is in progress.
        self._live_eE_arr=np.array([],dtype=np.float64)
        self._live_eI_arr=np.array([],dtype=np.float64)
        self._live_etw_arr=np.array([],dtype=np.float64)
        # These arrays hold only the CV phase when the worker labels initial and
        # final holds separately. That keeps live CV plots focused on the scan.
        self._E_cv=np.array([],dtype=np.float64)
        self._I_cv=np.array([],dtype=np.float64)
        self._t_cv=np.array([],dtype=np.float64)
        self._tw_cv=np.array([],dtype=np.float64)
        # Preallocate live preview arrays. They can grow later if needed.
        self._live_ftw_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_roi_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_zone_roi_arrs=[]
        self._live_zone_count=0
        self._live_zone_areas=np.array([],dtype=np.float64)
        self._live_processed_n=0
        self._last_live_frame_draw=0.0
        self._cv_last_draw=0.0
        # Frame stack information. _memmap is a NumPy view of the binary frame
        # file on disk, not a copy of all frames in memory.
        self._frames_path=None;self._frames_hw=None;self._n_frames=0;self._memmap=None
        # Clear reference-frame, last-frame, PCA, and cluster results.
        self._ref_frame=None;self._ref_roi_val=None;self._last_frame=None
        self._engine=None;self._cl_means=None;self._clear_an()
        # Clear cached cycle detection and smoothing results because they belong
        # to the previous dataset.
        self._cached_cycles=None
        self._lowess_cache={}
        # Reset visible analysis overlays and plot controls.
        self.cl_overlay.setVisible(False);self.pca_overlay.setVisible(False);self.an_var.setText("")
        self.fslider.setRange(0,0);self.fslider_lbl.setText("0/0")
        self.cv_curve.setData([],[]);self.roi_t_curve.setData([],[]);self.roi_e_curve.setData([],[])
        # Remove per-cycle plot curves that were created dynamically.
        for _attr in ("_cv_cyc_curves","_roi_e_cyc_curves","_roi_e_cyc_lowess",
                      "_roi_t_zone_curves","_roi_t_zone_smooth","_roi_t_zone_lowess",
                      "_roi_e_zone_curves","_roi_e_zone_lowess"):
            if hasattr(self,_attr):
                _pl=(self.cv_plot if "_cv_" in _attr else
                     self.roi_t_plot if "_roi_t_" in _attr else
                     self.roi_e_plot)
                for _c in getattr(self,_attr):_pl.removeItem(_c)
                setattr(self,_attr,[])
        self._cycle_colors={};self._n_cycles_detected=0
        self.cv_smooth.setData([],[]);self.roi_t_smooth.setData([],[]);self.roi_e_smooth.setData([],[])
        # Clear LOWESS/trend traces too. These are separate curve objects from
        # the raw and simple-smoothed traces, so they must be reset explicitly.
        if hasattr(self,'roi_t_lowess'):self.roi_t_lowess.setData([],[])
        if hasattr(self,'roi_e_lowess'):self.roi_e_lowess.setData([],[])
        self.cv_mk.setData([],[]);self.roi_t_mk.setData([],[]);self.roi_e_mk.setData([],[])
        self.img_item.setImage(np.zeros((2,2),dtype=np.float32))
        self.btn_analyze.setEnabled(False)
        self._zone_intensity=[]
        self._zone_intensity_per_area=[]
        self._zone_areas=np.array([],dtype=np.float64)
        self._undo_stack=[]
        if hasattr(self,"zone_lbl"):self._update_zone_label()

    def _set_test_preview_state(self,scenario_path=None):
        # After previewing a synthetic scenario, the next Test click should start
        # the run instead of opening the scenario picker again.
        self._pending_test_scenario_path=scenario_path
        self._pending_test_preview=True
        if hasattr(self,"btn_test"):
            self.btn_test.setText("Start previewed test")

    def _clear_test_preview_state(self):
        self._pending_test_scenario_path=None
        self._pending_test_preview=False
        if hasattr(self,"btn_test"):
            self.btn_test.setText("Test (synthetic)")

    def _preview_test_scenario(self,sp):
        # Render one synthetic frame before the run so the user can position the
        # ROI around the feature they care about.
        sc=load_test_scenario(sp) if sp and os.path.isfile(sp) else default_test_scenario()
        h,w=(256,320)
        yy,xx=np.mgrid[0:h,0:w]
        try:E0=float(self.cfg["potentiostat"].get("start_v","0"))
        except:E0=0.0
        frame=render_scenario_frame(sc,E0,0.0,h,w,yy,xx)
        self._clear_data()
        self._frames_hw=(h,w)
        self._n_frames=1
        self._last_frame=frame
        self.fslider.setRange(0,0);self.fslider.setValue(0);self.fslider_lbl.setText("0/0")
        self._show_frame(frame)
        self._set_test_preview_state(sp)
        name=os.path.basename(sp) if sp else "default synthetic scenario"
        self.status_lbl.setText(
            f"Previewing {name}. Move/resize the ROI, then click Start previewed test.")
        self.prog_lbl.setText("")
        self.traffic.setState(TrafficLight.GREEN)

    def _start_test_worker(self,sp):
        # Synthetic runs are stored in a temporary folder because they are mainly
        # for testing and demonstrating the pipeline.
        self._clear_test_preview_state()
        self._clear_data();self._running=True;self._set_btns(True);self.traffic.setState(TrafficLight.RED)
        self._start_frame_processor()
        self.status_lbl.setText("Test...");self.prog_lbl.setText("")
        self._store_dir=tempfile.mkdtemp(prefix="sor_test_")
        import datetime as _dt
        _ts=_dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        _bin=os.path.join(self._store_dir,f"test_{_ts}_frames.bin")
        self._worker=TestWorker(self.cfg,self._store_dir,scenario_path=sp,bin_path=_bin)
        self._wire();self._worker.start()

    def _set_btns(self,running):
        # Disable actions that should not happen mid-run, such as loading a
        # different dataset while a camera/potentiostat worker is active.
        self.btn_run.setEnabled(not running);self.btn_stop.setEnabled(running)
        self.btn_setup.setEnabled(not running);self.btn_test.setEnabled(not running)
        self.btn_load.setEnabled(not running)
        self.btn_analyze.setEnabled(not running)

    def _on_run(self):
        # Start a hardware-backed experiment. The worker writes frames to a
        # binary file while emitting live electrochemistry and frame updates.
        if self._running:return
        exp=self.cfg["potentiostat"].get("experiment_type",EXP_CV)
        # Ask the user where to save the metadata JSON. The frame binary is
        # saved beside it with a matching name.
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
        # Remember this folder so the next save dialog opens in the same place.
        self._last_save_dir=save_dir
        self._pending_json_path=json_path
        json_stem=os.path.splitext(os.path.basename(json_path))[0]
        # Keep the frame stack beside the selected JSON metadata path.
        bin_path=os.path.join(save_dir,json_stem+"_frames.bin")
        # Move the GUI into "running" state before starting device work.
        self._clear_data();self._running=True;self._set_btns(True);self.traffic.setState(TrafficLight.RED)
        self._start_frame_processor()
        self.status_lbl.setText(f"Starting {exp}...");self.prog_lbl.setText("")
        self._store_dir=save_dir
        # LiveCVAWorker is currently an alias of LiveCVWorker, but this branch
        # keeps the UI ready for separate CV/CVA behavior.
        if exp==EXP_CVA:
            self._worker=LiveCVAWorker(self.cfg,self._store_dir,bin_path=bin_path)
        else:
            self._worker=LiveCVWorker(self.cfg,self._store_dir,bin_path=bin_path)
        # Connect worker signals to GUI handlers, then start acquisition.
        self._wire();self._worker.start()

    def _on_test(self,sp=None,preview_first=True):
        # Start a synthetic acquisition path that exercises the same GUI, signal,
        # storage, and analysis flow as live hardware.
        if self._running:return
        if isinstance(sp,bool):sp=None
        if preview_first and getattr(self,"_pending_test_preview",False):
            self._start_test_worker(getattr(self,"_pending_test_scenario_path",None))
            return
        if sp is None:
            # If no scenario was passed from the command line, ask the user to
            # choose one. If they cancel, TestWorker uses its default scenario.
            sd=os.path.join(os.path.dirname(os.path.abspath(__file__)),"test_scenarios")
            p,_=QtWidgets.QFileDialog.getOpenFileName(self,"Scenario",sd if os.path.isdir(sd) else "","JSON (*.json);;All (*)")
            sp=p if p else None
        if preview_first:
            self._preview_test_scenario(sp)
        else:
            self._start_test_worker(sp)

    def _wire(self):
        # Worker signals arrive on the Qt event loop, keeping all widget updates
        # on the main thread.
        w=self._worker
        # new_echem carries arrays of voltage, current, and time.
        w.new_echem.connect(self._on_echem,QtCore.Qt.ConnectionType.QueuedConnection)
        # new_frame carries one camera frame and frame-specific metadata.
        w.new_frame.connect(self._on_frame,QtCore.Qt.ConnectionType.QueuedConnection)
        # Status/progress update small text labels in the GUI.
        w.status_msg.connect(lambda m:self.status_lbl.setText(m),QtCore.Qt.ConnectionType.QueuedConnection)
        w.progress.connect(self._on_prog,QtCore.Qt.ConnectionType.QueuedConnection)
        # A worker either completes with a dataset dictionary or reports an error.
        w.finished_ok.connect(self._on_done,QtCore.Qt.ConnectionType.QueuedConnection)
        w.finished_err.connect(self._on_err,QtCore.Qt.ConnectionType.QueuedConnection)

    def _on_stop(self):
        # Ask the worker to stop cooperatively. Device cleanup happens inside
        # the worker; the GUI just disables Stop to avoid repeated requests.
        if self._worker:self.status_lbl.setText("Stopping...");self._worker.request_stop()
        self.btn_stop.setEnabled(False)

    def _on_done(self,ds):
        # Normalize the completed worker payload into the main-window state used
        # by plotting, ROI analysis, PCA/clustering, and export.
        self._stop_frame_processor()
        # Restore the GUI from "running" mode before updating result widgets.
        self._running=False;self._worker=None;self._set_btns(False)
        self.traffic.setState(TrafficLight.YELLOW)
        QtWidgets.QApplication.processEvents()
        self._raw_ds=ds
        # ds is the finished dataset dictionary from workers.py. It contains the
        # saved frame path, image shape, raw echem arrays, and echem values
        # interpolated onto each frame time.
        self._frames_path=ds["frames_path"];self._frames_hw=ds["frames_hw"]
        self._n_frames=ds["frames_count"]
        self._E_all=ds["E_all"];self._I_all=ds["I_all"];self._t_all=ds["t_all"]
        if "t_wall_echem" in ds:self._t_wall_echem=ds["t_wall_echem"]
        self._frame_E=ds["frame_E"];self._frame_I=ds["frame_I"]
        self._frame_t_wall=ds["frame_t_wall"];self._frame_power=ds["frame_power"]
        ftw=ds["frame_t_wall"]
        # Convert absolute wall-clock frame times into run-relative seconds for
        # easier plotting on the x-axis.
        self._frame_t=ftw-ftw[0] if ftw.size>0 else ds["frame_t"]
        if self._n_frames>0 and self._frames_hw:
            try:
                h,w=self._frames_hw
                # Memory-map the frame stack rather than loading the whole movie
                # into RAM; downstream analysis can stream or slice it as needed.
                self._memmap=np.memmap(self._frames_path,dtype=np.float32,mode="r",shape=(self._n_frames,h,w))
                self._last_frame=np.asarray(self._memmap[-1],dtype=np.float32)
                self._show_frame(self._last_frame)
                self.fslider.setRange(0,max(0,self._n_frames-1))
                self.fslider.setValue(self._n_frames-1)
                self.fslider_lbl.setText(f"{self._n_frames-1}/{self._n_frames-1}")
            except Exception as e:self._memmap=None;self.status_lbl.setText(f"Memmap err: {e}")
        try:
            # Save lightweight metadata so the dataset can be loaded later
            # without rerunning the experiment.
            mp=save_dataset_metadata(ds,json_path=self._pending_json_path)
            self.status_lbl.setText(f"Done. {self._n_frames} frames. Click ⚙ Analyze to process.\n{mp}")
        except Exception as e:
            self.status_lbl.setText(f"Done. {self._n_frames} frames (save err: {e}). Click ⚙ Analyze.")
        self._pending_json_path=None
        self.btn_analyze.setEnabled(True)
        self.traffic.setState(TrafficLight.GREEN)
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["saved_frames"]=self._n_frames
            stats["echem_points"]=int(self._E_all.size)
            self._update_perf_panel(force=True)

    def _on_err(self,msg):
        # Worker errors arrive here. Reset the GUI and show a concise message.
        self._stop_frame_processor()
        self._running=False;self._worker=None;self._set_btns(False);self.traffic.setState(TrafficLight.GREEN)
        self.status_lbl.setText("Error.");QtWidgets.QMessageBox.warning(self,"Error",msg[:1000])
        self._update_perf_panel(force=True)

    def _on_analyze(self):
        """Run all deferred heavy processing after acquisition completes."""
        if self._memmap is None and self._n_frames==0:
            QtWidgets.QMessageBox.information(self,"Analyze","No data to analyze.");return
        # Disable Analyze while the analysis pass is running to prevent two
        # overlapping passes from writing to the same plot/state variables.
        self.btn_analyze.setEnabled(False)
        self.traffic.setState(TrafficLight.YELLOW)
        self.status_lbl.setText("Analyzing...")
        QtWidgets.QApplication.processEvents()
        # Clear caches so stale results don't survive a re-analyze
        self._cached_cycles=None
        self._lowess_cache={}
        try:
            # Zone analysis computes full ROI traces from the saved frame stack.
            # It is deferred so live acquisition stays lightweight.
            self._analyze_zones()
            # Refresh CV plots before ROI/cycle-specific plot updates.
            self._update_cv()
            # Detect CV cycles once and reuse the result for all plots in this
            # analysis pass.
            self._cached_cycles=_detect_cycles(self._frame_E,upper_v=self._get_upper_v())
            cycles=self._cached_cycles
            # The cycle selector uses 0 for "all data" and 1..N for individual
            # detected segments of the CV/hold sequence.
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
                # Optional PCA/K-means analysis lives in AnalysisMixin but is
                # triggered here so Analyze remains one workflow.
                self._run_an()
            self._lock_plot_axes()
            self.status_lbl.setText(f"Analysis complete. {self._n_frames} frames, {n_cv} cycles.")
        except Exception as e:
            self.status_lbl.setText(f"Analysis error: {e}")
            QtWidgets.QMessageBox.warning(self,"Analyze",f"{e}\n\n{traceback.format_exc()}")
        self.btn_analyze.setEnabled(True)
        self.traffic.setState(TrafficLight.GREEN)

    def _load_ds(self,ds):
        # Hydrate main-window state from metadata loaded by datasets.py. This is
        # the same shape produced by workers when acquisition finishes.
        #
        # Loading a dataset should leave the GUI in almost the same state as a
        # just-finished acquisition: frames are memory-mapped, echem arrays are
        # available, and Analyze can be clicked.
        self._frames_path=ds["frames_path"];self._frames_hw=ds["frames_hw"];self._n_frames=ds["frames_count"]
        self._E_all=ds["E_all"];self._I_all=ds["I_all"];self._t_all=ds["t_all"]
        if "t_wall_echem" in ds:self._t_wall_echem=ds["t_wall_echem"]
        self._frame_E=ds["frame_E"];self._frame_I=ds["frame_I"]
        self._frame_t_wall=ds["frame_t_wall"];self._frame_power=ds["frame_power"]
        ftw=ds["frame_t_wall"]
        # Use seconds since the first frame when wall-clock frame times exist.
        self._frame_t=ftw-ftw[0] if ftw.size>0 else ds["frame_t"]
        if self._n_frames>0 and self._frames_hw and not ds.get("frames_missing"):
            try:
                h,w=self._frames_hw
                # Loaded datasets use the same memory-map path as newly acquired
                # datasets, so analysis and export do not care where data came from.
                self._memmap=np.memmap(self._frames_path,dtype=np.float32,mode="r",shape=(self._n_frames,h,w))
                # Show the final frame as a useful preview of the loaded dataset.
                self._last_frame=np.asarray(self._memmap[-1],dtype=np.float32);self._show_frame(self._last_frame)
                self.fslider.setRange(0,max(0,self._n_frames-1));self.fslider.setValue(self._n_frames-1)
                self.fslider_lbl.setText(f"{self._n_frames-1}/{self._n_frames-1}")
            except Exception as e:self._memmap=None;self.status_lbl.setText(f"Memmap err: {e}")
        # Loading should at least refresh the CV plot; ROI and PCA work still
        # happens when the user clicks Analyze.
        self._update_cv()
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["saved_frames"]=self._n_frames
            stats["echem_points"]=int(self._E_all.size)
            self._update_perf_panel(force=True)

    def _on_load(self):
        # Let the user pick either the metadata JSON directly or the containing
        # dataset folder; datasets.py resolves the matching frame binary.
        if self._running:return
        # First try opening a JSON file. If the user cancels, offer a folder
        # picker for workflows where they think of the dataset as a directory.
        path,_=QtWidgets.QFileDialog.getOpenFileName(
            self,"Open dataset","",
            "SOR dataset (*.json);;All files (*)")
        if not path:
            path=QtWidgets.QFileDialog.getExistingDirectory(
                self,"Or pick dataset directory","",
                QtWidgets.QFileDialog.Option.ShowDirsOnly)
        if not path:return
        try:
            # Both loaders return the same dictionary shape, so _load_ds can use
            # one common path afterward.
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

    def _on_prog(self,pts,frames):
        self.prog_lbl.setText(f"Echem: {pts:,} | Frames: {frames}")
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["saved_frames"]=int(frames)
            stats["echem_points"]=int(pts)
            self._update_perf_panel()

    def _on_echem(self,data):
        # Store the latest electrochemistry stream. Phase labels let the live CV
        # plot ignore initial/final holds when a worker provides that context.
        #
        # E is potential, I is current, t is device/run time, and t_wall is the
        # computer timestamp used to align echem data with camera frames.
        E=data["E"];I=data["I"];t=data["t"]
        tw=data.get("t_wall",np.array([],dtype=np.float64))
        self._E_all=E;self._I_all=I;self._t_all=t
        if "t_wall" in data:self._t_wall_echem=tw
        if "phase" in data:
            # Keep only CV-labeled points for the live CV curve when the worker
            # labels initial and final holds separately.
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
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["echem_points"]=int(E.size)
            self._update_perf_panel()
        now=time.time()
        if now-self._cv_last_draw>=0.1:
            # Throttle redraws to about 10 per second so plotting does not
            # dominate acquisition time.
            self._update_cv(live=True)
            self._cv_last_draw=now

    def _on_frame(self,data):
        # Handle a live frame signal quickly: update frame state and send only
        # the ROI reduction work to the background processor.
        #
        # The worker already saved the full frame to disk. This slot is mainly
        # responsible for keeping the GUI preview and frame slider current.
        frame=data["frame"];tw=data["t_wall"];pwr=data["power"];fidx=data["frame_idx"]
        self._last_frame=frame;self._n_frames=fidx+1
        # Record frame dimensions the first time a frame arrives.
        if self._frames_hw is None:self._frames_hw=frame.shape[:2]
        self._live_ftw.append(tw);self._live_fpwr.append(pwr)
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["saved_frames"]=fidx+1
            recent=np.asarray(self._live_ftw[-60:],dtype=np.float64)
            recent=recent[np.isfinite(recent)]
            if recent.size>=2 and recent[-1]>recent[0]:
                stats["fps"]=(recent.size-1)/(recent[-1]-recent[0])
            self._update_perf_panel()
        n=fidx+1
        self._ensure_live_capacity(n)
        # Keep the frame slider pinned to the newest frame during acquisition.
        self.fslider.setRange(0,max(0,fidx));self.fslider.setValue(fidx)
        self.fslider_lbl.setText(f"{fidx}/{fidx}")
        now=time.time()
        if now-getattr(self,"_last_live_frame_draw",0.0)>=1.0/15.0:
            # Show a throttled live camera preview. The worker has already saved
            # every frame, so this display rate can be lower than acquisition
            # rate without losing data.
            self._show_frame(frame)
            self._last_live_frame_draw=now
        # Convert the current zone selections into masks for this frame shape.
        masks=tuple(self._get_zone_mask_flats(frame.shape[:2]))
        zone_areas=[max(1,int(np.count_nonzero(_pmask))) for _pmask in masks]
        proc=getattr(self,"_frame_processor",None)
        if proc is not None and proc.isRunning():
            # Include the generation id so late results from previous runs can be
            # ignored safely by _on_frame_processed.
            proc.submit({
                "frame":frame,
                "masks":masks,
                "zone_areas":zone_areas,
                "frame_idx":fidx,
                "t_wall":tw,
                "power":pwr,
                "generation":self._frame_processor_generation,
            })
        else:
            # Synchronous fallback for shutdown races or unusual test harnesses
            # where the background processor is not running.
            frame_flat=frame.ravel()
            roi_by_zone=[float(np.nansum(frame_flat[_pmask])) for _pmask in masks]
            rv=float(np.nansum(roi_by_zone)) if roi_by_zone else np.nan
            self._on_frame_processed({
                "frame_idx":fidx,"t_wall":tw,"power":pwr,"roi":rv,
                "roi_by_zone":roi_by_zone,
                "zone_areas":zone_areas,
                "generation":self._frame_processor_generation,
                "worker_frame_ms":np.nan,
                "worker_queue_dropped_frames":0,
                "worker_queue_size":0,
                "worker_queue_max":1})

    def _on_frame_processed(self,data):
        # Accept only results from the current acquisition generation; stale
        # queued results are harmlessly ignored after a clear/load/new run.
        if data.get("generation")!=self._frame_processor_generation:return
        fidx=int(data.get("frame_idx",-1))
        if fidx<0:return
        n=fidx+1
        self._ensure_live_capacity(n)
        self._live_ftw_arr[fidx]=float(data.get("t_wall",np.nan))
        self._live_roi_arr[fidx]=float(data.get("roi",np.nan))
        roi_by_zone=list(data.get("roi_by_zone",[]) or [])
        zone_areas=np.asarray(data.get("zone_areas",[]),dtype=np.float64)
        if zone_areas.size:
            self._live_zone_areas=np.where(np.isfinite(zone_areas)&(zone_areas>0),
                                           zone_areas,1.0)
        if roi_by_zone:
            self._live_zone_count=len(roi_by_zone)
            self._ensure_live_zone_capacity(n,len(roi_by_zone))
            for z_idx,rv in enumerate(roi_by_zone):
                self._live_zone_roi_arrs[z_idx][fidx]=float(rv)
            for z_idx in range(len(roi_by_zone),len(self._live_zone_roi_arrs)):
                self._live_zone_roi_arrs[z_idx][fidx]=np.nan
        self._live_processed_n=max(self._live_processed_n,n)
        stats=getattr(self,"_perf_stats",None)
        if stats is not None:
            stats["preview_skipped"]=int(data.get("worker_queue_dropped_frames",stats.get("preview_skipped",0)))
            stats["preview_queue"]=int(data.get("worker_queue_size",stats.get("preview_queue",0)))
            stats["preview_queue_max"]=int(data.get("worker_queue_max",stats.get("preview_queue_max",4)))
            stats["roi_ms"]=float(data.get("worker_frame_ms",stats.get("roi_ms",np.nan)))
            self._update_perf_panel()
        # Build the live time axis as seconds since the first processed frame.
        fa=self._live_ftw_arr[:self._live_processed_n]
        ra=self._live_roi_arr[:self._live_processed_n]
        if fa.size==0 or not np.isfinite(fa[0]):return
        te=fa-fa[0]
        MAX_LIVE=2000
        if self._live_processed_n>MAX_LIVE:
            # Downsample the live trace for plotting only. Saved frames and full
            # post-run analysis still use all frames.
            idx=np.round(np.linspace(0,self._live_processed_n-1,MAX_LIVE)).astype(int)
            te_d=te[idx];ra_d=ra[idx]
        else:
            te_d=te;ra_d=ra
        # During acquisition, only the simple live ROI-vs-time plot is updated.
        # Voltage-aligned ROI plots wait until Analyze, when frame_E is final.
        live_zone_count=int(getattr(self,"_live_zone_count",0))
        live_zone_arrs=(getattr(self,"_live_zone_roi_arrs",[]) or [])[:live_zone_count]
        live_area_mode=(hasattr(self,"roi_t_y")
                        and self.roi_t_y.currentText()=="R / area")
        self.roi_t_plot.setTitle("Reflectance / Area vs Time" if live_area_mode else "Reflectance vs Time")
        self.roi_t_plot.setLabel("left","Reflectance / pixel" if live_area_mode else "Reflectance")
        live_plot_arrs=(self._divide_zone_traces_by_area(live_zone_arrs,live=True)
                        if live_area_mode else live_zone_arrs)
        if live_plot_arrs:
            self.roi_t_curve.setData([],[])
            self.roi_t_lowess.setData([],[])
            self._ensure_live_roi_curves(len(live_plot_arrs))
            for z_idx,arr in enumerate(live_plot_arrs):
                za=arr[:self._live_processed_n]
                za_d=za[idx] if self._live_processed_n>MAX_LIVE else za
                m=np.isfinite(te_d)&np.isfinite(za_d)
                self._roi_t_zone_curves[z_idx].setData(te_d[m],za_d[m])
        else:
            self._ensure_live_roi_curves(0)
            ra_t_d=(ra_d/self._total_area(live=True) if live_area_mode else ra_d)
            m=np.isfinite(te_d)&np.isfinite(ra_t_d)
            self.roi_t_curve.setData(te_d[m],ra_t_d[m])
        self.roi_t_smooth.setData([],[])
        self.roi_e_smooth.setData([],[])
        self.roi_e_lowess.setData([],[])
        e_mode=self.roi_e_mode.currentText() if hasattr(self,"roi_e_mode") else "R vs E"
        e_area_mode=(e_mode=="R / area vs E")
        can_live_e=(e_mode in ("R vs E","R / area vs E")
                    and self._live_etw_arr.size>=1 and self._live_eE_arr.size>=1)
        if can_live_e:
            frame_E=_interp_echem(fa,self._live_etw_arr,self._live_eE_arr)
            E_d=frame_E[idx] if self._live_processed_n>MAX_LIVE else frame_E
            live_e_arrs=(self._divide_zone_traces_by_area(live_zone_arrs,live=True)
                         if e_area_mode else live_zone_arrs)
            if live_e_arrs:
                self.roi_e_curve.setData([],[])
                self._ensure_live_roi_e_curves(len(live_e_arrs))
                for z_idx,arr in enumerate(live_e_arrs):
                    za=arr[:self._live_processed_n]
                    za_d=za[idx] if self._live_processed_n>MAX_LIVE else za
                    m=np.isfinite(E_d)&np.isfinite(za_d)
                    self._roi_e_zone_curves[z_idx].setData(E_d[m],za_d[m])
            else:
                self._ensure_live_roi_e_curves(0)
                ra_e_d=(ra_d/self._total_area(live=True) if e_area_mode else ra_d)
                m=np.isfinite(E_d)&np.isfinite(ra_e_d)
                self.roi_e_curve.setData(E_d[m],ra_e_d[m])
            self.roi_e_plot.setTitle("Reflectance / Area vs Potential" if e_area_mode else "Reflectance vs Potential")
            self.roi_e_plot.setLabel("bottom","Potential (V)")
            self.roi_e_plot.setLabel("left","Reflectance / pixel" if e_area_mode else "Reflectance")
        else:
            self._ensure_live_roi_e_curves(0)
            self.roi_e_curve.setData([],[])

    def _open_setup(self):
        # The setup dialog mutates self.cfg in place; after it closes, mirror the
        # relevant display settings back into visible controls.
        dlg=SetupDialog(self.cfg,parent=self)
        if dlg.exec()==QtWidgets.QDialog.DialogCode.Accepted:
            self.cmap_cb.setCurrentText(self.cfg["display"].get("colormap","viridis"))
            self.auto_lev.setChecked(self.cfg["display"].get("auto_levels","true").lower()=="true")
            self.lmin.setValue(float(self.cfg["display"].get("level_min","0")))
            self.lmax.setValue(float(self.cfg["display"].get("level_max","1000")))
            self._apply_cmap();self._refresh_exp_lbl();self.status_lbl.setText("Settings updated.")

__all__ = [name for name in globals() if not name.startswith("__")]
