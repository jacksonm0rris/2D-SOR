"""Main 2D-SOR application window.

This file builds the visible GUI and initializes the shared state used by all
mixins. The mixins add behavior, but most widgets they manipulate are created
here.

Beginner map:

* __init__ creates empty data arrays and bookkeeping fields.
* _build_ui creates buttons, plots, sliders, image views, and analysis controls.
* The mixins handle what those widgets do after the user interacts with them.
"""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *
from .image_display_mixin import ImageDisplayMixin
from .cv_plot_mixin import CVPlotMixin
from .analysis_mixin import AnalysisMixin
from .acquisition_mixin import AcquisitionMixin
from .zones_mixin import ZonesMixin
from .export_mixin import ExportMixin


class _ConsoleProxyLabel(QtWidgets.QLabel):
    """Compatibility label that routes setText calls into the main console."""

    def __init__(self,owner,channel):
        super().__init__("")
        self._owner=owner
        self._channel=channel

    def setText(self,text):
        super().setText(text)
        owner=getattr(self,"_owner",None)
        if owner is not None and hasattr(owner,"_append_console_text"):
            owner._append_console_text(str(text),self._channel)


class DemoWindow(ImageDisplayMixin, CVPlotMixin, AnalysisMixin, AcquisitionMixin, ZonesMixin, ExportMixin, QtWidgets.QMainWindow):
    """Main window assembled from focused behavior mixins."""

    def __init__(self):
        super().__init__();self.setWindowTitle("2D-SOR Demo v8");pg.setConfigOptions(imageAxisOrder="row-major")
        self.cfg=load_settings()
        # Raw electrochemistry arrays and frame-aligned arrays start empty.
        # Workers populate them after acquisition or dataset loading.
        for a in ["_E_all","_I_all","_t_all","_t_wall_echem","_frame_E","_frame_I","_frame_t","_frame_t_wall","_frame_power","_roi_intensity","_drr_intensity"]:
            setattr(self,a,np.array([],dtype=np.float64))
        # Frame/dataset bookkeeping.
        self._roi_cache_key=None;self._frames_path=None;self._frames_hw=None;self._frames_dtype="float32";self._n_frames=0
        # Zone/ROI state. Zones are rectangular regions selected on the image.
        self._zones=[]
        self._zone_intensity=[]
        self._zone_intensity_per_area=[]
        self._roi_intensity_per_area=np.array([],dtype=np.float64)
        self._zone_areas=np.array([],dtype=np.float64)
        self._drr_intensity=np.array([],dtype=np.float64)
        self._zone_drr=[]
        self._undo_stack=[]
        self._zone_mask_cache={}
        self._memmap=None;self._store_dir=None
        # Live preview state used during acquisition before full analysis runs.
        self._live_ftw=[];self._live_fpwr=[];self._live_roi=[]
        self._frame_processor=None;self._frame_processor_generation=0
        self._live_processed_n=0
        self._last_live_frame_draw=0.0
        self._perf_stats={
            "saved_frames":0,
            "preview_skipped":0,
            "preview_queue":0,
            "preview_queue_max":4,
            "fps":0.0,
            "roi_ms":np.nan,
            "echem_points":0,
            "frame_missed_est":0,
            "echem_missed_est":0,
            "max_worker_stall_ms":0.0,
            "last_panel_update":0.0,
        }
        self._ref_frame=None;self._ref_roi_val=None;self._last_frame=None
        self._custom_drr_ref_frame=None
        self._last_ref_preview_frame=None
        self._ref_preview_active=False
        self._ref_preview_worker=None
        self._ocp_worker=None
        self._idle_ocp_voltage=np.nan
        self._running=False;self._worker=None
        self._last_save_dir=os.path.expanduser("~")
        self._pending_json_path=None
        self._pending_test_scenario_path=None
        self._pending_test_preview=False
        self._auto_analyze_on_done=False
        self._plot_axes_locked=False
        # Per-session caches are cleared whenever a new dataset is loaded.
        # ── OPTIMISATION: per-session caches cleared on new data ──────────────
        self._lowess_cache={}   # (x_id, len, frac) -> y_smooth
        self._cached_cycles=None  # result of _detect_cycles, cleared on new data
        # CV-only arrays exclude initial/final holds when the worker provides
        # phase labels.
        self._live_eE_arr=np.array([],dtype=np.float64)
        self._live_eI_arr=np.array([],dtype=np.float64)
        self._live_etw_arr=np.array([],dtype=np.float64)
        self._E_cv=np.array([],dtype=np.float64)
        self._I_cv=np.array([],dtype=np.float64)
        self._t_cv=np.array([],dtype=np.float64)
        self._tw_cv=np.array([],dtype=np.float64)
        self._engine=None;self._an_active=False;self._cl_sel=set()
        # PCA/K-means cluster plot state.
        self._cycle_colors={}
        self._n_cycles_detected=0
        self._show_all_cycles=True
        self._cl_curves_t={};self._cl_curves_e={};self._cl_means=None
        self._power_win=None
        self._cv_last_draw=0.0
        self._live_ftw_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_roi_arr=np.full(8192,np.nan,dtype=np.float64)
        self._live_zone_roi_arrs=[]
        self._live_zone_count=0
        self._live_zone_areas=np.array([],dtype=np.float64)
        self._lowess_color=QtGui.QColor(60,220,100)
        # Build widgets after all state fields exist; many widget callbacks refer
        # to these fields.
        self._build_ui();self._apply_cmap()
        self._zones=[self.roi]
        scr=QtWidgets.QApplication.primaryScreen()
        try:
            w=max(900,int(self.cfg["display"].get("window_w","1600")))
            h=max(650,int(self.cfg["display"].get("window_h","980")))
        except Exception:
            w,h=1600,980
        if scr:
            g=scr.availableGeometry();self.resize(min(w,g.width()),min(h,g.height()))
        else:self.resize(w,h)
        QtCore.QTimer.singleShot(0,self._restore_window_layout)

    def _append_console_text(self,text,channel="Status"):
        # Central log for status/progress text that used to live in the right
        # sidebar. Empty clears are ignored; fast frame counters are throttled.
        text=str(text).strip()
        if not text:return
        now=time.time()
        if channel=="Progress" and text.startswith("Echem:"):
            last_t=getattr(self,"_last_console_progress_t",0.0)
            if now-last_t<0.5:return
            self._last_console_progress_t=now
        key=(channel,text)
        if getattr(self,"_last_console_entry",None)==key:return
        self._last_console_entry=key
        if not hasattr(self,"console_out"):return
        stamp=time.strftime("%H:%M:%S")
        self.console_out.appendPlainText(f"[{stamp}] {channel}: {text}")
        sb=self.console_out.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _splitter_sizes_from_config(self,key,count):
        # Splitter sizes are stored as "123,456" strings in the display section.
        # Invalid or stale values are ignored so old settings files still work.
        try:
            parts=[int(float(p.strip())) for p in self.cfg["display"].get(key,"").split(",")]
        except Exception:
            return None
        if len(parts)!=count or any(p<=0 for p in parts):return None
        return parts

    def _restore_window_layout(self):
        # Restore the user's last graph/control-panel proportions after Qt has
        # finished laying out the widgets for the current screen size.
        for attr,key in (
                ("split_main","split_main"),
                ("split_rows","split_vertical"),
                ("split_top","split_top"),
                ("split_bottom","split_bottom")):
            sp=getattr(self,attr,None)
            if sp is None:continue
            sizes=self._splitter_sizes_from_config(key,sp.count())
            if sizes is None and key=="split_vertical" and sp.count()==3:
                sizes=[470,360,110]
            if sizes:
                total=sum(sizes)
                # If a saved pane was squeezed almost closed, reset that row to
                # an even split so the user is not trapped by old geometry.
                if key in ("split_top","split_bottom") and total>0 and min(sizes)/total<0.25:
                    sizes=[1 for _ in sizes]
                sp.setSizes(sizes)

    def _save_window_layout_to_config(self):
        # Remember main window size plus all internal graph/control splitters.
        d=self.cfg["display"]
        d["window_w"]=str(max(1,self.width()))
        d["window_h"]=str(max(1,self.height()))
        for attr,key in (
                ("split_main","split_main"),
                ("split_rows","split_vertical"),
                ("split_top","split_top"),
                ("split_bottom","split_bottom")):
            sp=getattr(self,attr,None)
            if sp is not None:
                d[key]=",".join(str(max(1,int(v))) for v in sp.sizes())

    def _build_ui(self):
        # Overall layout: image and CV plots on top, ROI plots on bottom, control
        # sidebar on the right.
        cw=QtWidgets.QWidget();self.setCentralWidget(cw);mh=QtWidgets.QHBoxLayout(cw);mh.setContentsMargins(4,4,4,4)
        self.split_rows=QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.split_top=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.split_bottom=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        for sp in (self.split_rows,self.split_top,self.split_bottom):
            sp.setChildrenCollapsible(False);sp.setHandleWidth(8)
        self.top_panel=QtWidgets.QWidget()
        self.top_panel_lay=QtWidgets.QVBoxLayout(self.top_panel)
        self.top_panel_lay.setContentsMargins(0,0,0,0)
        self.top_panel_lay.setSpacing(2)
        self.console_out=QtWidgets.QPlainTextEdit()
        self.console_out.setReadOnly(True)
        self.console_out.setMaximumBlockCount(1000)
        self.console_out.setPlaceholderText("Console output")
        self.console_out.setStyleSheet(
            "QPlainTextEdit{font-family:Consolas,monospace;font-size:11px;"
            "background:#111;color:#ddd;border:1px solid #444;}")
        self.console_out.setMinimumHeight(70)
        self.split_rows.addWidget(self.top_panel)
        self.split_rows.addWidget(self.split_bottom)
        self.split_rows.addWidget(self.console_out)
        self.split_rows.setStretchFactor(0,5)
        self.split_rows.setStretchFactor(1,4)
        self.split_rows.setStretchFactor(2,1)
        # Image panel: frame slider, display controls, ROI zones, and overlays.
        ic=QtWidgets.QWidget();ivl=QtWidgets.QVBoxLayout(ic);ivl.setContentsMargins(0,0,0,0)
        ic.setMinimumWidth(180)
        ic.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                         QtWidgets.QSizePolicy.Policy.Expanding)
        sl_r=QtWidgets.QHBoxLayout();sl_r.addWidget(QtWidgets.QLabel("Frame:"))
        self.fslider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);self.fslider.setRange(0,0)
        self.fslider.valueChanged.connect(self._on_slider);sl_r.addWidget(self.fslider)
        self.fslider_lbl=QtWidgets.QLabel("0/0");sl_r.addWidget(self.fslider_lbl);ivl.addLayout(sl_r)
        ctrl_w=QtWidgets.QWidget()
        ctrl_w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum,
                             QtWidgets.QSizePolicy.Policy.Fixed)
        ctrl=QtWidgets.QHBoxLayout(ctrl_w);ctrl.setContentsMargins(0,0,0,0)
        ctrl.addWidget(QtWidgets.QLabel("Cmap:"))
        self.cmap_cb=QtWidgets.QComboBox()
        self.cmap_cb.addItems(["gray","viridis","plasma","magma","inferno","cividis","turbo","jet"])
        self.cmap_cb.setCurrentText(self.cfg["display"].get("colormap","viridis"))
        self.cmap_cb.currentTextChanged.connect(self._apply_cmap);ctrl.addWidget(self.cmap_cb)
        ctrl.addWidget(QtWidgets.QLabel("Min:"));self.lmin=QtWidgets.QDoubleSpinBox();self.lmin.setDecimals(2);self.lmin.setRange(-1e30,1e30)
        self.lmin.setFixedWidth(100)
        self.lmin.setValue(float(self.cfg["display"].get("level_min","0")));self.lmin.valueChanged.connect(self._ml);ctrl.addWidget(self.lmin)
        ctrl.addWidget(QtWidgets.QLabel("Max:"));self.lmax=QtWidgets.QDoubleSpinBox();self.lmax.setDecimals(2);self.lmax.setRange(-1e30,1e30)
        self.lmax.setFixedWidth(100)
        self.lmax.setValue(float(self.cfg["display"].get("level_max","1000")));self.lmax.valueChanged.connect(self._ml);ctrl.addWidget(self.lmax)
        self.auto_lev=QtWidgets.QCheckBox("Auto");self.auto_lev.setChecked(self.cfg["display"].get("auto_levels","true").lower()=="true")
        self.auto_lev.toggled.connect(self._on_auto_lev_toggle)
        ctrl.addWidget(self.auto_lev)
        ctrl.addWidget(QtWidgets.QLabel("Preview compression:"))
        self.preview_bin_cb=QtWidgets.QComboBox()
        self.preview_bin_cb.addItems(["1x","2x","4x","8x","16x"])
        self.preview_bin_cb.setCurrentText(self.cfg["display"].get("preview_bin","4x"))
        self.preview_bin_cb.setToolTip(
            "Downsample the live camera preview only.\n"
            "Saved frames, ROI analysis, TIFF export, and video export stay full resolution.")
        self.preview_bin_cb.currentTextChanged.connect(self._on_preview_bin_changed)
        ctrl.addWidget(self.preview_bin_cb)
        self.btn_drr=QtWidgets.QPushButton("ΔR/R")
        self.btn_drr.setCheckable(True)
        self.btn_drr.setToolTip(
            "Toggle: show change in reflectance from the first frame\n"
            "(frame - frame[0]) / frame[0] instead of raw counts\n"
            "Auto levels: symmetric \u00b198th-percentile stretch so even\n"
            "sub-percent changes fill the full colormap range.")
        self.btn_drr.setStyleSheet(
            "QPushButton{padding:3px 10px;font-size:12px;font-weight:bold;"
            "border-radius:4px;border:1px solid #777;background:#555;color:#ddd;}"
            "QPushButton:hover{background:#666;}"
            "QPushButton:checked{background:#1f9d55;color:white;border:1px solid #28c76f;}"
            "QPushButton:checked:hover{background:#27ae60;}")
        self.btn_drr.toggled.connect(self._on_drr_toggle)
        ctrl.addWidget(self.btn_drr)
        ctrl.addWidget(QtWidgets.QLabel("Rot°:"))
        self.rot_sp=QtWidgets.QDoubleSpinBox()
        self.rot_sp.setDecimals(1);self.rot_sp.setRange(-180.0,180.0);self.rot_sp.setSingleStep(90.0)
        self.rot_sp.setValue(0.0);self.rot_sp.setFixedWidth(100)
        self.rot_sp.setToolTip(
            "Rotate the displayed image by this many degrees (counter-clockwise).\n"
            "±90 and ±180 are lossless; other angles use bilinear interpolation.")
        self.rot_sp.valueChanged.connect(self._on_rotation_changed)
        ctrl.addWidget(self.rot_sp)
        sep=QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        ctrl.addWidget(sep)
        ctrl.addWidget(QtWidgets.QLabel("CV:"))
        self.cv_mode=QtWidgets.QComboBox();self.cv_mode.addItems(["I vs E","E vs t"])
        self.cv_mode.currentTextChanged.connect(self._update_all_plots);ctrl.addWidget(self.cv_mode)
        ctrl.addWidget(QtWidgets.QLabel("Y:"));self.cv_y=QtWidgets.QComboBox();self.cv_y.addItems(PLOT_VARS)
        self.cv_y.setCurrentText(self.cfg["display"].get("cv_y","I (A)"))
        self.cv_y.currentTextChanged.connect(lambda *_: self._on_plot_y_axis_changed("cv"));ctrl.addWidget(self.cv_y)
        ctrl.addWidget(QtWidgets.QLabel("Cycle:"))
        self.cv_cycle_cb=QtWidgets.QComboBox()
        self.cv_cycle_cb.addItem("All")
        self.cv_cycle_cb.setToolTip("Select which cycle to display, or All for every cycle.")
        self.cv_cycle_cb.setFixedWidth(70)
        self.cv_cycle_cb.currentIndexChanged.connect(self._update_all_plots)
        ctrl.addWidget(self.cv_cycle_cb)
        self.cv_cycle_color_row=QtWidgets.QHBoxLayout()
        self.cv_cycle_color_row.setContentsMargins(0,0,0,0)
        self.cv_cycle_color_row.setSpacing(2)
        ctrl.addLayout(self.cv_cycle_color_row)
        ctrl_scroll=QtWidgets.QScrollArea()
        ctrl_scroll.setWidget(ctrl_w)
        ctrl_scroll.setWidgetResizable(False)
        ctrl_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        ctrl_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ctrl_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        ctrl_scroll.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                  QtWidgets.QSizePolicy.Policy.Fixed)
        ctrl_scroll.setFixedHeight(ctrl_w.sizeHint().height()+16)
        self.top_panel_lay.addWidget(ctrl_scroll)
        self.drr_smooth_row=QtWidgets.QWidget()
        drr_sr=QtWidgets.QHBoxLayout(self.drr_smooth_row)
        drr_sr.setContentsMargins(0,0,0,0)
        drr_sr.addWidget(QtWidgets.QLabel("ΔR/R smooth:"))
        self.drr_smooth_cb=QtWidgets.QComboBox()
        self.drr_smooth_cb.addItems(["None","Gaussian","Median","Bilateral"])
        self.drr_smooth_cb.setToolTip(
            "None: no spatial smoothing\n"
            "Gaussian: fast uniform blur, good for general use\n"
            "Median: removes salt-and-pepper noise, preserves edges\n"
            "Bilateral: preserves sharp feature boundaries (slowest)")
        self.drr_smooth_cb.currentTextChanged.connect(self._on_drr_toggle)
        drr_sr.addWidget(self.drr_smooth_cb)
        drr_sr.addWidget(QtWidgets.QLabel("Radius:"))
        self.drr_smooth_sp=QtWidgets.QSpinBox()
        self.drr_smooth_sp.setRange(1,20);self.drr_smooth_sp.setValue(2)
        self.drr_smooth_sp.setFixedWidth(100)
        self.drr_smooth_sp.setToolTip("Smoothing radius in pixels")
        self.drr_smooth_sp.valueChanged.connect(self._on_drr_toggle)
        drr_sr.addWidget(self.drr_smooth_sp)
        drr_sr.addStretch(1)
        self.drr_smooth_row.setVisible(False)
        ivl.addWidget(self.drr_smooth_row)
        self.img_view=pg.GraphicsLayoutWidget();self.img_plot=self.img_view.addPlot();self.img_plot.setAspectLocked(True)
        self.img_view.setMinimumWidth(80)
        self.img_item=pg.ImageItem();self.img_plot.addItem(self.img_item)
        # Cluster and PCA overlays sit above the camera image but stay hidden
        # until analysis produces results.
        self.cl_overlay=pg.ImageItem();self.cl_overlay.setZValue(5);self.cl_overlay.setOpacity(0.5);self.cl_overlay.setVisible(False);self.img_plot.addItem(self.cl_overlay)
        self.pca_overlay=pg.ImageItem();self.pca_overlay.setZValue(6);self.pca_overlay.setOpacity(0.6);self.pca_overlay.setVisible(False);self.img_plot.addItem(self.pca_overlay)
        zone_row=QtWidgets.QHBoxLayout()
        self.btn_zone_add=QtWidgets.QPushButton("+ Zone")
        self.btn_zone_add.setStyleSheet("font-size:11px;padding:3px 6px;")
        self.btn_zone_add.setToolTip("Add a new ROI zone")
        self.btn_zone_add.clicked.connect(self._on_zone_add)
        zone_row.addWidget(self.btn_zone_add)
        self.btn_zone_del=QtWidgets.QPushButton("- Zone")
        self.btn_zone_del.setStyleSheet("font-size:11px;padding:3px 6px;")
        self.btn_zone_del.setToolTip("Delete the most recently added zone")
        self.btn_zone_del.clicked.connect(self._on_zone_del)
        zone_row.addWidget(self.btn_zone_del)
        self.btn_zone_clear=QtWidgets.QPushButton("Clear All")
        self.btn_zone_clear.setStyleSheet("font-size:11px;padding:3px 6px;")
        self.btn_zone_clear.setToolTip("Remove all zones except the first")
        self.btn_zone_clear.clicked.connect(self._on_zone_clear)
        zone_row.addWidget(self.btn_zone_clear)
        self.btn_zone_undo=QtWidgets.QPushButton("Undo")
        self.btn_zone_undo.setStyleSheet("font-size:11px;padding:3px 6px;")
        self.btn_zone_undo.setToolTip("Undo last zone add or delete")
        self.btn_zone_undo.clicked.connect(self._on_zone_undo)
        zone_row.addWidget(self.btn_zone_undo)
        self.zone_lbl=QtWidgets.QLabel("Zone 1")
        self.zone_lbl.setStyleSheet("font-size:11px;color:#aaa;")
        zone_row.addWidget(self.zone_lbl)
        zone_row.addStretch(1)
        ivl.addLayout(zone_row)
        r,g,b=ZONE_COLORS[0]
        # The first ROI zone always exists and cannot be deleted.
        self.roi=pg.RectROI([10,10],[50,50],pen=pg.mkPen((r,g,b),width=2))
        self.roi.setZValue(10);self.img_plot.addItem(self.roi)
        ivl.addWidget(self.img_view,stretch=1);self.split_top.addWidget(ic)
        # CV plot panel: displays current vs potential or potential vs time.
        cv_c=QtWidgets.QWidget();cv_vl=QtWidgets.QVBoxLayout(cv_c);cv_vl.setContentsMargins(0,0,0,0)
        cv_c.setMinimumWidth(260)
        cv_c.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        cv_header_spacer=QtWidgets.QWidget()
        cv_header_spacer.setFixedHeight(sl_r.sizeHint().height()+zone_row.sizeHint().height())
        cv_vl.addWidget(cv_header_spacer)
        self.cv_plot=pg.PlotWidget(title="Current vs Potential")
        self.cv_plot.setLabel("bottom","Potential (V)")
        self.cv_plot.setLabel("left","Current (A)")
        self.cv_curve=self.cv_plot.plot([],[],pen=pg.mkPen(color='w',width=2))
        self.cv_smooth=self.cv_plot.plot([],[],pen=pg.mkPen(color=(255,180,0),width=2))
        self.cv_mk=pg.ScatterPlotItem(size=12,pen=pg.mkPen(None),brush=pg.mkBrush(255,255,0,220),symbol='o')
        self.cv_plot.addItem(self.cv_mk);self.cv_mk.setZValue(20);cv_vl.addWidget(self.cv_plot);self.split_top.addWidget(cv_c)
        self.top_panel_lay.addWidget(self.split_top,stretch=1)
        # ROI vs time panel: optical signal through time, plus optional cluster
        # mean traces on a secondary axis.
        rt_c=QtWidgets.QWidget();rt_vl=QtWidgets.QVBoxLayout(rt_c);rt_vl.setContentsMargins(0,0,0,0)
        rt_tr=QtWidgets.QHBoxLayout();rt_tr.addWidget(QtWidgets.QLabel("Y:"))
        self.roi_t_y=QtWidgets.QComboBox();self.roi_t_y.addItems(PLOT_VARS)
        _rv_t=self.cfg["display"].get("roi_t_y","R")
        _rv_t="R" if _rv_t in ("ROI Sum","R") else _rv_t
        _idx_t=self.roi_t_y.findText(_rv_t)
        self.roi_t_y.setCurrentIndex(_idx_t if _idx_t>=0 else self.roi_t_y.findText("R"))
        self.roi_t_y.currentTextChanged.connect(lambda *_: self._on_plot_y_axis_changed("roi_t"));rt_tr.addWidget(self.roi_t_y)
        rt_tr.addStretch(1)
        rt_tr.addWidget(QtWidgets.QLabel("LOWESS win:"))
        self.lowess_frac_t=QtWidgets.QDoubleSpinBox()
        self.lowess_frac_t.setDecimals(3);self.lowess_frac_t.setRange(0.01,0.5)
        self.lowess_frac_t.setSingleStep(0.01);self.lowess_frac_t.setValue(0.05)
        self.lowess_frac_t.setFixedWidth(100)
        self.lowess_frac_t.setToolTip("LOWESS window fraction for this plot (0.01–0.5).\nSmaller = tighter, less smooth; larger = wider, smoother.")
        self.lowess_frac_t.valueChanged.connect(self._on_lowess_frac_changed)
        rt_tr.addWidget(self.lowess_frac_t)
        rt_vl.addLayout(rt_tr)
        rt_cyc_row=QtWidgets.QHBoxLayout()
        rt_cyc_row.addWidget(QtWidgets.QLabel("Cycle:"))
        self.roi_t_cycle_cb=QtWidgets.QComboBox()
        self.roi_t_cycle_cb.addItem("All")
        self.roi_t_cycle_cb.setToolTip("Select which cycle to display on this plot.")
        self.roi_t_cycle_cb.setFixedWidth(70)
        self.roi_t_cycle_cb.currentIndexChanged.connect(self._update_roi_plots)
        rt_cyc_row.addWidget(self.roi_t_cycle_cb)
        rt_cyc_row.addStretch(1)
        rt_vl.addLayout(rt_cyc_row)
        self.roi_t_plot=pg.PlotWidget(title="Reflectance vs Time");self.roi_t_plot.setLabel("bottom","Time (s)")
        self.roi_t_curve=self.roi_t_plot.plot([],[],pen=pg.mkPen(color='w',width=2))
        self.roi_t_smooth=self.roi_t_plot.plot([],[],pen=pg.mkPen(color=(255,180,0),width=2))
        self.roi_t_lowess=self.roi_t_plot.plot([],[],pen=pg.mkPen(color=(60,220,100),width=2))
        self.roi_t_mk=pg.ScatterPlotItem(size=12,pen=pg.mkPen(None),brush=pg.mkBrush(255,255,0,220),symbol='o')
        self.roi_t_plot.addItem(self.roi_t_mk);self.roi_t_mk.setZValue(20)
        self.roi_t_vb2=pg.ViewBox();self.roi_t_plot.plotItem.scene().addItem(self.roi_t_vb2)
        self.roi_t_plot.plotItem.getAxis("right").linkToView(self.roi_t_vb2)
        self.roi_t_vb2.setXLink(self.roi_t_plot.plotItem)
        self.roi_t_plot.plotItem.showAxis("right");self.roi_t_plot.plotItem.getAxis("right").setLabel("Cluster")
        self.roi_t_plot.plotItem.hideAxis("right")
        self.roi_t_plot.plotItem.vb.sigResized.connect(lambda:self.roi_t_vb2.setGeometry(self.roi_t_plot.plotItem.vb.sceneBoundingRect()))
        rt_vl.addWidget(self.roi_t_plot);self.split_bottom.addWidget(rt_c)
        # ROI vs potential / derivative panel. This is where optical response is
        # compared directly with the electrochemical waveform.
        re_c=QtWidgets.QWidget();re_vl=QtWidgets.QVBoxLayout(re_c);re_vl.setContentsMargins(0,0,0,0)
        re_tr=QtWidgets.QHBoxLayout();re_tr.addWidget(QtWidgets.QLabel("Mode:"))
        self.roi_e_mode=QtWidgets.QComboBox()
        self.roi_e_mode.addItems(["R vs E","R / area vs E","dR/dV vs E","Optical Current vs E","dR/dt vs t","ΔR/R vs t"])
        self.roi_e_mode.currentTextChanged.connect(lambda *_: self._on_plot_y_axis_changed("roi_e"));re_tr.addWidget(self.roi_e_mode)
        re_tr.addWidget(QtWidgets.QLabel("Cycle:"))
        self.cycle_sp=QtWidgets.QSpinBox();self.cycle_sp.setRange(0,100);self.cycle_sp.setSpecialValueText("All")
        self.cycle_sp.setFixedWidth(100);self.cycle_sp.valueChanged.connect(self._update_all_plots);re_tr.addWidget(self.cycle_sp)
        re_tr.addStretch(1)
        re_tr.addWidget(QtWidgets.QLabel("LOWESS win:"))
        self.lowess_frac_e=QtWidgets.QDoubleSpinBox()
        self.lowess_frac_e.setDecimals(3);self.lowess_frac_e.setRange(0.01,0.5)
        self.lowess_frac_e.setSingleStep(0.01);self.lowess_frac_e.setValue(0.08)
        self.lowess_frac_e.setFixedWidth(100)
        self.lowess_frac_e.setToolTip("LOWESS window fraction for this plot (0.01–0.5).\nSmaller = tighter, less smooth; larger = wider, smoother.")
        self.lowess_frac_e.valueChanged.connect(self._on_lowess_frac_changed)
        re_tr.addWidget(self.lowess_frac_e)
        re_vl.addLayout(re_tr)
        re_cyc_row=QtWidgets.QHBoxLayout()
        re_cyc_row.addWidget(QtWidgets.QLabel("Cycle:"))
        self.roi_e_cycle_cb=QtWidgets.QComboBox()
        self.roi_e_cycle_cb.addItem("All")
        self.roi_e_cycle_cb.setToolTip("Select which cycle to display, or All for every cycle coloured individually.")
        self.roi_e_cycle_cb.setFixedWidth(70)
        self.roi_e_cycle_cb.currentIndexChanged.connect(self._update_roi_plots)
        re_cyc_row.addWidget(self.roi_e_cycle_cb)
        self.roi_e_cycle_color_row=QtWidgets.QHBoxLayout()
        re_cyc_row.addLayout(self.roi_e_cycle_color_row)
        re_cyc_row.addStretch(1)
        re_vl.addLayout(re_cyc_row)
        self.roi_e_plot=pg.PlotWidget(title="Reflectance vs Potential");self.roi_e_plot.setLabel("bottom","Potential (V)")
        self.roi_e_curve=self.roi_e_plot.plot([],[],pen=pg.mkPen(color='w',width=2))
        self.roi_e_smooth=self.roi_e_plot.plot([],[],pen=pg.mkPen(color=(255,180,0),width=2))
        self.roi_e_lowess=self.roi_e_plot.plot([],[],pen=pg.mkPen(color=(60,220,100),width=2))
        self.roi_e_mk=pg.ScatterPlotItem(size=12,pen=pg.mkPen(None),brush=pg.mkBrush(255,255,0,220),symbol='o')
        self.roi_e_plot.addItem(self.roi_e_mk);self.roi_e_mk.setZValue(20)
        self.roi_e_vb2=pg.ViewBox();self.roi_e_plot.plotItem.scene().addItem(self.roi_e_vb2)
        self.roi_e_plot.plotItem.getAxis("right").linkToView(self.roi_e_vb2)
        self.roi_e_vb2.setXLink(self.roi_e_plot.plotItem)
        self.roi_e_plot.plotItem.showAxis("right");self.roi_e_plot.plotItem.getAxis("right").setLabel("Cluster")
        self.roi_e_plot.plotItem.hideAxis("right")
        self.roi_e_plot.plotItem.vb.sigResized.connect(lambda:self.roi_e_vb2.setGeometry(self.roi_e_plot.plotItem.vb.sceneBoundingRect()))
        re_vl.addWidget(self.roi_e_plot);self.split_bottom.addWidget(re_c)
        # Right sidebar: run controls, status, setup, loading, analysis settings,
        # export, and traffic-light status.
        _sb_inner=QtWidgets.QWidget()
        sl=QtWidgets.QVBoxLayout(_sb_inner);sl.setContentsMargins(6,6,6,6);sl.setSpacing(4)
        sb=QtWidgets.QScrollArea()
        sb.setWidgetResizable(True);sb.setWidget(_sb_inner)
        sb.setMinimumWidth(240);sb.setMaximumWidth(900)
        sb.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        run_row=QtWidgets.QHBoxLayout()
        live_grp=QtWidgets.QGroupBox("Live")
        live_grp.setToolTip("Updates while an experiment is running.")
        live_gl=QtWidgets.QGridLayout(live_grp)
        live_gl.setContentsMargins(8,6,8,6);live_gl.setHorizontalSpacing(8);live_gl.setVerticalSpacing(3)
        live_gl.setColumnStretch(1,1)
        def _live_row(row,label,value):
            name=QtWidgets.QLabel(label)
            name.setStyleSheet("font-size:11px;color:#aaa;")
            val=QtWidgets.QLabel(value)
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight|QtCore.Qt.AlignmentFlag.AlignVCenter)
            val.setStyleSheet("font-size:13px;font-family:Consolas,monospace;font-weight:bold;")
            live_gl.addWidget(name,row,0)
            live_gl.addWidget(val,row,1)
            return val
        self.live_elapsed_lbl=_live_row(0,"Seconds","0.0 s")
        self.live_E_lbl=_live_row(1,"Potential","-- V")
        self.live_I_lbl=_live_row(2,"Current","-- A")
        self.live_hold_lbl=_live_row(3,"Hold","--")
        self.live_cycle_lbl=_live_row(4,"Cycle","--")
        run_row.addWidget(live_grp,1)
        self.btn_run=QtWidgets.QPushButton("  RUN  ")
        self.btn_run.setStyleSheet("QPushButton{background:#27ae60;color:white;font-size:28px;font-weight:bold;border-radius:12px;padding:18px 0;min-height:60px}QPushButton:hover{background:#2ecc71}QPushButton:disabled{background:#7f8c8d}")
        self.btn_run.clicked.connect(self._on_run);run_row.addWidget(self.btn_run,1)
        sl.addLayout(run_row)
        self.btn_stop=QtWidgets.QPushButton("STOP")
        self.btn_stop.setStyleSheet("QPushButton{background:#c0392b;color:white;font-size:20px;font-weight:bold;border-radius:8px;padding:10px 0}QPushButton:disabled{background:#7f8c8d}")
        self.btn_stop.clicked.connect(self._on_stop);self.btn_stop.setEnabled(False);sl.addWidget(self.btn_stop)
        self.btn_ref_capture=QtWidgets.QPushButton("Capture reference frame")
        self.btn_ref_capture.setStyleSheet("font-size:14px;padding:7px;background:#34495e;color:white;border-radius:6px;")
        self.btn_ref_capture.setToolTip(
            "First click: start a live camera preview without saving frames.\n"
            "Second click: use the latest preview frame as the image dR/R reference.\n"
            "If no reference is captured, dR/R uses frame 0 as before.")
        self.btn_ref_capture.clicked.connect(self._on_reference_capture)
        sl.addWidget(self.btn_ref_capture)
        self.exp_type_lbl=QtWidgets.QLabel("")
        self.exp_type_lbl.setStyleSheet("font-size:11px;color:#aaa;")
        self.exp_type_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.exp_type_lbl);self._refresh_exp_lbl()
        sl.addSpacing(10)
        self.status_lbl=_ConsoleProxyLabel(self,"Status");self.status_lbl.setWordWrap(True)
        self.prog_lbl=_ConsoleProxyLabel(self,"Progress");self.prog_lbl.setWordWrap(True)
        self.status_lbl.setText("Ready.")
        perf_grp=QtWidgets.QGroupBox("Performance")
        perf_grp.setToolTip(
            "Live acquisition health.\n"
            "Preview skipped means the live ROI preview skipped old frames to stay current.\n"
            "Frame/echem gap estimates come from worker-side timing gaps before preview processing.")
        perf_gl=QtWidgets.QGridLayout(perf_grp)
        perf_gl.setSpacing(4);perf_gl.setColumnStretch(1,1)
        def _perf_row(row,label,value):
            name=QtWidgets.QLabel(label)
            name.setStyleSheet("font-size:11px;color:#aaa;")
            val=QtWidgets.QLabel(value)
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight|QtCore.Qt.AlignmentFlag.AlignVCenter)
            val.setStyleSheet("font-size:11px;font-family:Consolas,monospace;")
            perf_gl.addWidget(name,row,0)
            perf_gl.addWidget(val,row,1)
            return val
        self.perf_frames_lbl=_perf_row(0,"Saved frames","0")
        self.perf_skipped_lbl=_perf_row(1,"Preview skipped","0")
        self.perf_queue_lbl=_perf_row(2,"Preview queue","0/4")
        self.perf_fps_lbl=_perf_row(3,"FPS","0.0")
        self.perf_roi_ms_lbl=_perf_row(4,"ROI ms","--")
        self.perf_echem_lbl=_perf_row(5,"Echem points","0")
        self.perf_frame_loss_lbl=_perf_row(6,"Frame gap est","0")
        self.perf_echem_loss_lbl=_perf_row(7,"Echem gap est","0")
        self.perf_stall_lbl=_perf_row(8,"Max loop gap","0 ms")
        sl.addWidget(perf_grp)
        self._update_perf_panel(force=True)
        sl.addSpacing(10)
        self.btn_setup=QtWidgets.QPushButton("Setup...");self.btn_setup.setStyleSheet("font-size:15px;padding:8px;");self.btn_setup.clicked.connect(self._open_setup);sl.addWidget(self.btn_setup)
        sl.addSpacing(8)
        self.btn_test=QtWidgets.QPushButton("Test (synthetic)")
        self.btn_test.setStyleSheet("font-size:14px;padding:6px;background:#2980b9;color:white;border-radius:6px;")
        self.btn_test.clicked.connect(self._on_test);sl.addWidget(self.btn_test)
        self.btn_load=QtWidgets.QPushButton("Load dataset...");self.btn_load.setStyleSheet("font-size:14px;padding:6px;");self.btn_load.clicked.connect(self._on_load);sl.addWidget(self.btn_load)
        self.btn_analyze=QtWidgets.QPushButton("⚙  Analyze")
        self.btn_analyze.setStyleSheet(
            "QPushButton{font-size:16px;padding:10px;background:#e67e22;color:white;"
            "border-radius:8px;font-weight:bold;}"
            "QPushButton:hover{background:#f39c12;}"
            "QPushButton:disabled{background:#7f8c8d;}")
        self.btn_analyze.setToolTip(
            "Run after acquisition:\n"
            "  • ROI normalization (power + ref frame)\n"
            "  • Smoothing overlays\n"
            "  • Cycle detection\n"
            "  • PCA / K-means clustering")
        self.btn_analyze.setEnabled(False)
        # Zone collections are reset here because the first ROI widget has just
        # been constructed.
        for zone in self._zones[1:]:
            try:self.img_plot.removeItem(zone)
            except:pass
        self._zones=[self.roi]
        self._zone_intensity=[]
        self._undo_stack=[]
        if hasattr(self,"zone_lbl"):self._update_zone_label()
        self.btn_analyze.clicked.connect(self._on_analyze)
        sl.addWidget(self.btn_analyze)
        self.btn_power=QtWidgets.QPushButton("Plot LED Power");self.btn_power.setStyleSheet("font-size:13px;padding:4px;");self.btn_power.clicked.connect(self._plot_power);sl.addWidget(self.btn_power)
        # Smoothing controls affect displayed curves, not the raw saved data.
        sm_grp=QtWidgets.QGroupBox("Smoothing")
        sm_gl=QtWidgets.QGridLayout(sm_grp);sm_gl.setSpacing(6);sm_gl.setColumnStretch(1,1)
        sm_gl.addWidget(QtWidgets.QLabel("Window:"),0,0)
        self.smooth_sp=QtWidgets.QSpinBox();self.smooth_sp.setRange(1,500)
        self.smooth_sp.setValue(int(self.cfg["display"].get("smooth_window","1")))
        self.smooth_sp.setSpecialValueText("Off");self.smooth_sp.valueChanged.connect(self._update_all_plots)
        sm_gl.addWidget(self.smooth_sp,0,1)
        self.smooth_sg_btn=QtWidgets.QPushButton("Boxcar")
        self.smooth_sg_btn.setCheckable(True)
        self.smooth_sg_btn.setChecked(self.cfg["display"].get("smooth_mode","boxcar")=="savgol")
        self.smooth_sg_btn.setToolTip("Toggle between Boxcar (moving average) and Savitzky-Golay smoothing")
        self.smooth_sg_btn.setStyleSheet(
            "QPushButton{padding:3px 6px;font-size:12px;}"
            "QPushButton:checked{background:#1a6fa8;color:white;border-radius:4px;}"
            "QPushButton:!checked{border-radius:4px;}")
        self.smooth_sg_btn.toggled.connect(self._on_smooth_mode_toggle)
        sm_gl.addWidget(self.smooth_sg_btn,0,2)
        sm_gl.addWidget(QtWidgets.QLabel("LOWESS colour:"),1,0)
        self.btn_lowess_color=QtWidgets.QPushButton("Colour")
        self.btn_lowess_color.setFixedWidth(80)
        self.btn_lowess_color.setToolTip("Pick the colour for the LOWESS smoothed curve on both ROI plots.")
        self._lowess_color=QtGui.QColor(60,220,100)
        self._update_lowess_color_btn()
        self.btn_lowess_color.clicked.connect(self._on_lowess_color_pick)
        sm_gl.addWidget(self.btn_lowess_color,1,1,1,2)
        sm_gl.addWidget(QtWidgets.QLabel("LOWESS thickness:"),2,0)
        self.lowess_thickness=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.lowess_thickness.setRange(1,8);self.lowess_thickness.setValue(2)
        self.lowess_thickness.setTickInterval(1)
        self.lowess_thickness.setToolTip("LOWESS line thickness (1–8 px)")
        self.lowess_thickness.valueChanged.connect(self._on_lowess_style_changed)
        sm_gl.addWidget(self.lowess_thickness,2,1,1,2)
        sm_gl.addWidget(QtWidgets.QLabel("LOWESS ROI:"),3,0)
        self.lowess_roi_cb=QtWidgets.QComboBox()
        self.lowess_roi_cb.setToolTip("Choose which ROI gets a LOWESS trend line, or All.")
        self.lowess_roi_cb.currentIndexChanged.connect(self._on_lowess_roi_changed)
        sm_gl.addWidget(self.lowess_roi_cb,3,1,1,2)
        self._refresh_lowess_roi_options()
        sl.addWidget(sm_grp)
        ag=QtWidgets.QGroupBox("Analysis (PCA → K-means)")
        ag_gl=QtWidgets.QGridLayout(ag);ag_gl.setSpacing(6);ag_gl.setColumnStretch(1,1)
        self.btn_an=QtWidgets.QPushButton("Enable");self.btn_an.setCheckable(True)
        self.btn_an.setStyleSheet("QPushButton{padding:4px;font-size:13px}QPushButton:checked{background:#8e44ad;color:white}")
        self.btn_an.toggled.connect(self._on_an_toggle)
        ag_gl.addWidget(self.btn_an,0,0,1,2)
        ag_gl.addWidget(QtWidgets.QLabel("K clusters:"),1,0)
        self.an_k=QtWidgets.QSpinBox();self.an_k.setRange(2,12);self.an_k.setValue(5)
        self.an_k.valueChanged.connect(self._on_an_setting_changed)
        ag_gl.addWidget(self.an_k,1,1)
        ag_gl.addWidget(QtWidgets.QLabel("PCA components:"),2,0)
        self.an_pca=QtWidgets.QSpinBox();self.an_pca.setRange(2,20);self.an_pca.setValue(5)
        self.an_pca.valueChanged.connect(self._on_an_setting_changed)
        ag_gl.addWidget(self.an_pca,2,1)
        ag_gl.addWidget(QtWidgets.QLabel("Frame stride:"),3,0)
        self.an_stride=QtWidgets.QSpinBox();self.an_stride.setRange(1,50);self.an_stride.setValue(1)
        self.an_stride.setSuffix(" fr")
        self.an_stride.setToolTip(
            "Frame stride for PCA / k-means.\n"
            "1 = every frame (slowest, most accurate).\n"
            "5 = every 5th frame (5× faster, negligible accuracy loss).\n"
            "Cluster means are always computed over ALL frames.")
        self.an_stride.valueChanged.connect(self._on_an_setting_changed)
        ag_gl.addWidget(self.an_stride,3,1)
        ag_gl.addWidget(QtWidgets.QLabel("Superpixel size:"),4,0)
        self.an_block=QtWidgets.QSpinBox();self.an_block.setRange(1,64);self.an_block.setValue(4)
        self.an_block.setSuffix(" px")
        self.an_block.setToolTip(
            "Superpixel (block) size in pixels.\n"
            "Larger = faster analysis, coarser spatial resolution.\n"
            "Smaller = slower analysis, finer spatial resolution.\n"
            "4 px is a good default for most cameras.")
        self.an_block.valueChanged.connect(self._on_an_setting_changed)
        ag_gl.addWidget(self.an_block,4,1)
        ag_gl.addWidget(QtWidgets.QLabel("Normalisation:"),5,0)
        self.an_norm=QtWidgets.QComboBox();self.an_norm.addItems(["zscore","dR/R","raw"])
        self.an_norm.currentTextChanged.connect(self._on_an_setting_changed)
        ag_gl.addWidget(self.an_norm,5,1)
        opt_row=QtWidgets.QHBoxLayout()
        self.an_rgb=QtWidgets.QCheckBox("PCA RGB");self.an_rgb.stateChanged.connect(self._on_rgb);opt_row.addWidget(self.an_rgb)
        self.an_roi_only=QtWidgets.QCheckBox("ROI only");self.an_roi_only.stateChanged.connect(self._on_an_setting_changed);opt_row.addWidget(self.an_roi_only)
        opt_row.addStretch(1)
        opt_w=QtWidgets.QWidget();opt_w.setLayout(opt_row)
        ag_gl.addWidget(opt_w,6,0,1,2)
        self.an_var=QtWidgets.QLabel("");self.an_var.setWordWrap(True)
        self.an_var.setStyleSheet("font-size:11px;color:#666;")
        ag_gl.addWidget(self.an_var,7,0,1,2)
        self.an_cw=QtWidgets.QWidget();self.an_cl_lay=QtWidgets.QHBoxLayout(self.an_cw)
        self.an_cl_lay.setContentsMargins(0,0,0,0);self.an_cl_lay.setSpacing(2)
        self.an_cbs=[];self._rebuild_cbs()
        ag_gl.addWidget(self.an_cw,8,0,1,2)
        sl.addSpacing(10);sl.addWidget(ag)
        sl.addSpacing(10);eg=QtWidgets.QGroupBox("Export");egl=QtWidgets.QVBoxLayout(eg)
        b=QtWidgets.QPushButton("Export all (ZIP)");b.clicked.connect(self._export_plots);egl.addWidget(b)
        b2=QtWidgets.QPushButton("Export frames (TIFF)");b2.clicked.connect(self._export_frames);egl.addWidget(b2)
        b3=QtWidgets.QPushButton("Export video");b3.clicked.connect(self._export_video);egl.addWidget(b3)
        sl.addWidget(eg);sl.addStretch(1)
        tl_r=QtWidgets.QHBoxLayout();tl_r.addStretch(1);self.traffic=TrafficLight();tl_r.addWidget(self.traffic);tl_r.addStretch(1);sl.addLayout(tl_r)
        self.split_main=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.split_main.setChildrenCollapsible(False);self.split_main.setHandleWidth(8)
        mh.removeWidget(self.split_rows);mh.removeWidget(sb);self.split_main.addWidget(self.split_rows);self.split_main.addWidget(sb)
        self.split_main.setStretchFactor(0,5);self.split_main.setStretchFactor(1,1);mh.addWidget(self.split_main)

__all__ = [name for name in globals() if not name.startswith("__")]
