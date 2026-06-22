"""Main 2D-SOR application window."""
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


class DemoWindow(ImageDisplayMixin, CVPlotMixin, AnalysisMixin, AcquisitionMixin, ZonesMixin, ExportMixin, QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle("2D-SOR Demo v8");pg.setConfigOptions(imageAxisOrder="row-major")
        self.cfg=load_settings()
        for a in ["_E_all","_I_all","_t_all","_t_wall_echem","_frame_E","_frame_I","_frame_t","_frame_t_wall","_frame_power","_roi_intensity","_drr_intensity"]:
            setattr(self,a,np.array([],dtype=np.float64))
        self._roi_cache_key=None;self._frames_path=None;self._frames_hw=None;self._n_frames=0
        self._zones=[]
        self._zone_intensity=[]
        self._drr_intensity=np.array([],dtype=np.float64)
        self._zone_drr=[]
        self._undo_stack=[]
        self._zone_mask_cache={}
        self._memmap=None;self._store_dir=None
        self._live_ftw=[];self._live_fpwr=[];self._live_roi=[]
        self._ref_frame=None;self._ref_roi_val=None;self._last_frame=None
        self._running=False;self._worker=None
        self._last_save_dir=os.path.expanduser("~")
        self._pending_json_path=None
        # ── OPTIMISATION: per-session caches cleared on new data ──────────────
        self._lowess_cache={}   # (x_id, len, frac) -> y_smooth
        self._cached_cycles=None  # result of _detect_cycles, cleared on new data
        self._live_eE_arr=np.array([],dtype=np.float64)
        self._live_eI_arr=np.array([],dtype=np.float64)
        self._live_etw_arr=np.array([],dtype=np.float64)
        self._E_cv=np.array([],dtype=np.float64)
        self._I_cv=np.array([],dtype=np.float64)
        self._t_cv=np.array([],dtype=np.float64)
        self._tw_cv=np.array([],dtype=np.float64)
        self._engine=None;self._an_active=False;self._cl_sel=set()
        self._cycle_colors={}
        self._n_cycles_detected=0
        self._show_all_cycles=True
        self._cl_curves_t={};self._cl_curves_e={};self._cl_means=None
        self._power_win=None
        self._cv_last_draw=0.0
        self._live_ftw_arr=np.empty(8192,dtype=np.float64)
        self._live_roi_arr=np.empty(8192,dtype=np.float64)
        self._lowess_color=QtGui.QColor(60,220,100)
        self._build_ui();self._apply_cmap()
        self._zones=[self.roi]
        scr=QtWidgets.QApplication.primaryScreen()
        if scr:g=scr.availableGeometry();self.resize(min(1600,g.width()),min(980,g.height()))
        else:self.resize(1600,980)

    def _build_ui(self):
        cw=QtWidgets.QWidget();self.setCentralWidget(cw);mh=QtWidgets.QHBoxLayout(cw);mh.setContentsMargins(4,4,4,4)
        ps=QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        tr=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        br=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        ps.addWidget(tr);ps.addWidget(br)
        ic=QtWidgets.QWidget();ivl=QtWidgets.QVBoxLayout(ic);ivl.setContentsMargins(0,0,0,0)
        sl_r=QtWidgets.QHBoxLayout();sl_r.addWidget(QtWidgets.QLabel("Frame:"))
        self.fslider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);self.fslider.setRange(0,0)
        self.fslider.valueChanged.connect(self._on_slider);sl_r.addWidget(self.fslider)
        self.fslider_lbl=QtWidgets.QLabel("0/0");sl_r.addWidget(self.fslider_lbl);ivl.addLayout(sl_r)
        ctrl=QtWidgets.QHBoxLayout();ctrl.addWidget(QtWidgets.QLabel("Cmap:"))
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
        self.btn_drr=QtWidgets.QPushButton("ΔR/R")
        self.btn_drr.setCheckable(True)
        self.btn_drr.setToolTip(
            "Toggle: show change in reflectance from the first frame\n"
            "(frame - frame[0]) / frame[0] instead of raw counts\n"
            "Auto levels: symmetric \u00b198th-percentile stretch so even\n"
            "sub-percent changes fill the full colormap range.")
        self.btn_drr.setStyleSheet(
            "QPushButton{padding:3px 8px;font-size:12px;}"
            "QPushButton:checked{background:#c0392b;color:white;border-radius:4px;}"
            "QPushButton:!checked{border-radius:4px;}")
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
        ivl.addLayout(ctrl)
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
        self.img_item=pg.ImageItem();self.img_plot.addItem(self.img_item)
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
        self.roi=pg.RectROI([10,10],[50,50],pen=pg.mkPen((r,g,b),width=2))
        self.roi.setZValue(10);self.img_plot.addItem(self.roi)
        ivl.addWidget(self.img_view,stretch=1);tr.addWidget(ic)
        cv_c=QtWidgets.QWidget();cv_vl=QtWidgets.QVBoxLayout(cv_c);cv_vl.setContentsMargins(0,0,0,0)
        cv_sp=QtWidgets.QWidget();cv_sp.setFixedHeight(CV_HEADER_SPACER_PX);cv_vl.addWidget(cv_sp)
        cv_tr=QtWidgets.QHBoxLayout()
        self.cv_mode=QtWidgets.QComboBox();self.cv_mode.addItems(["I vs E","E vs t"])
        self.cv_mode.currentTextChanged.connect(self._update_all_plots);cv_tr.addWidget(self.cv_mode)
        cv_tr.addWidget(QtWidgets.QLabel("Y:"));self.cv_y=QtWidgets.QComboBox();self.cv_y.addItems(PLOT_VARS)
        self.cv_y.setCurrentText(self.cfg["display"].get("cv_y","I (A)"))
        self.cv_y.currentTextChanged.connect(self._update_all_plots);cv_tr.addWidget(self.cv_y)
        cv_tr.addStretch(1);cv_vl.addLayout(cv_tr)
        cv_cyc_row=QtWidgets.QHBoxLayout()
        cv_cyc_row.addWidget(QtWidgets.QLabel("Cycle:"))
        self.cv_cycle_cb=QtWidgets.QComboBox()
        self.cv_cycle_cb.addItem("All")
        self.cv_cycle_cb.setToolTip("Select which cycle to display, or All for every cycle.")
        self.cv_cycle_cb.setFixedWidth(70)
        self.cv_cycle_cb.currentIndexChanged.connect(self._update_all_plots)
        cv_cyc_row.addWidget(self.cv_cycle_cb)
        self.cv_cycle_color_row=QtWidgets.QHBoxLayout()
        cv_cyc_row.addLayout(self.cv_cycle_color_row)
        cv_cyc_row.addStretch(1)
        cv_vl.addLayout(cv_cyc_row)
        self.cv_plot=pg.PlotWidget();self.cv_curve=self.cv_plot.plot([],[],pen=pg.mkPen(color='w',width=2))
        self.cv_smooth=self.cv_plot.plot([],[],pen=pg.mkPen(color=(255,180,0),width=2))
        self.cv_mk=pg.ScatterPlotItem(size=12,pen=pg.mkPen(None),brush=pg.mkBrush(255,255,0,220),symbol='o')
        self.cv_plot.addItem(self.cv_mk);self.cv_mk.setZValue(20);cv_vl.addWidget(self.cv_plot);tr.addWidget(cv_c)
        rt_c=QtWidgets.QWidget();rt_vl=QtWidgets.QVBoxLayout(rt_c);rt_vl.setContentsMargins(0,0,0,0)
        rt_tr=QtWidgets.QHBoxLayout();rt_tr.addWidget(QtWidgets.QLabel("Y:"))
        self.roi_t_y=QtWidgets.QComboBox();self.roi_t_y.addItems(PLOT_VARS)
        _rv_t=self.cfg["display"].get("roi_t_y","R")
        _rv_t="R" if _rv_t in ("ROI Sum","R") else _rv_t
        _idx_t=self.roi_t_y.findText(_rv_t)
        self.roi_t_y.setCurrentIndex(_idx_t if _idx_t>=0 else self.roi_t_y.findText("R"))
        self.roi_t_y.currentTextChanged.connect(self._update_all_plots);rt_tr.addWidget(self.roi_t_y)
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
        self.roi_t_plot.plotItem.vb.sigResized.connect(lambda:self.roi_t_vb2.setGeometry(self.roi_t_plot.plotItem.vb.sceneBoundingRect()))
        rt_vl.addWidget(self.roi_t_plot);br.addWidget(rt_c)
        re_c=QtWidgets.QWidget();re_vl=QtWidgets.QVBoxLayout(re_c);re_vl.setContentsMargins(0,0,0,0)
        re_tr=QtWidgets.QHBoxLayout();re_tr.addWidget(QtWidgets.QLabel("Mode:"))
        self.roi_e_mode=QtWidgets.QComboBox()
        self.roi_e_mode.addItems(["R vs E","dR/dV vs E","Optical Current vs E","dR/dt vs t","ΔR/R vs t"])
        self.roi_e_mode.currentTextChanged.connect(self._update_all_plots);re_tr.addWidget(self.roi_e_mode)
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
        self.roi_e_plot.plotItem.vb.sigResized.connect(lambda:self.roi_e_vb2.setGeometry(self.roi_e_plot.plotItem.vb.sceneBoundingRect()))
        re_vl.addWidget(self.roi_e_plot);br.addWidget(re_c)
        _sb_inner=QtWidgets.QWidget()
        sl=QtWidgets.QVBoxLayout(_sb_inner);sl.setContentsMargins(6,6,6,6);sl.setSpacing(4)
        sb=QtWidgets.QScrollArea()
        sb.setWidgetResizable(True);sb.setWidget(_sb_inner)
        sb.setMinimumWidth(260);sb.setMaximumWidth(540)
        sb.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.btn_run=QtWidgets.QPushButton("  RUN  ")
        self.btn_run.setStyleSheet("QPushButton{background:#27ae60;color:white;font-size:28px;font-weight:bold;border-radius:12px;padding:18px 0;min-height:60px}QPushButton:hover{background:#2ecc71}QPushButton:disabled{background:#7f8c8d}")
        self.btn_run.clicked.connect(self._on_run);sl.addWidget(self.btn_run)
        self.btn_stop=QtWidgets.QPushButton("STOP")
        self.btn_stop.setStyleSheet("QPushButton{background:#c0392b;color:white;font-size:20px;font-weight:bold;border-radius:8px;padding:10px 0}QPushButton:disabled{background:#7f8c8d}")
        self.btn_stop.clicked.connect(self._on_stop);self.btn_stop.setEnabled(False);sl.addWidget(self.btn_stop)
        self.exp_type_lbl=QtWidgets.QLabel("")
        self.exp_type_lbl.setStyleSheet("font-size:11px;color:#aaa;")
        self.exp_type_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.exp_type_lbl);self._refresh_exp_lbl()
        sl.addSpacing(10)
        self.status_lbl=QtWidgets.QLabel("Ready.");self.status_lbl.setWordWrap(True);self.status_lbl.setStyleSheet("font-size:13px;");sl.addWidget(self.status_lbl)
        self.prog_lbl=QtWidgets.QLabel("");self.prog_lbl.setWordWrap(True);sl.addWidget(self.prog_lbl)
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
        for zone in self._zones[1:]:
            try:self.img_plot.removeItem(zone)
            except:pass
        self._zones=[self.roi]
        self._zone_intensity=[]
        self._undo_stack=[]
        if hasattr(self,"zone_lbl"):self.zone_lbl.setText("1 zone")
        self.btn_analyze.clicked.connect(self._on_analyze)
        sl.addWidget(self.btn_analyze)
        self.btn_power=QtWidgets.QPushButton("Plot LED Power");self.btn_power.setStyleSheet("font-size:13px;padding:4px;");self.btn_power.clicked.connect(self._plot_power);sl.addWidget(self.btn_power)
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
        sl.addWidget(eg);sl.addStretch(1)
        tl_r=QtWidgets.QHBoxLayout();tl_r.addStretch(1);self.traffic=TrafficLight();tl_r.addWidget(self.traffic);tl_r.addStretch(1);sl.addLayout(tl_r)
        ms=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        mh.removeWidget(ps);mh.removeWidget(sb);ms.addWidget(ps);ms.addWidget(sb)
        ms.setStretchFactor(0,5);ms.setStretchFactor(1,1);mh.addWidget(ms)

__all__ = [name for name in globals() if not name.startswith("__")]
