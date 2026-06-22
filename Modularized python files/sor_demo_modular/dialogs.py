"""Setup dialog and status widgets.

Generated from sor_demo_v19_26-6-2.py during modularization.
"""
from .dependencies import *
from .settings import *

class SetupDialog(QtWidgets.QDialog):
    def __init__(self,cfg,parent=None):
        super().__init__(parent);self.setWindowTitle("Setup");self.setMinimumWidth(520);self.cfg=cfg
        self._build();self._load_cfg();self._on_exp_radio()

    def _build(self):
        lo=QtWidgets.QVBoxLayout(self);tabs=QtWidgets.QTabWidget();lo.addWidget(tabs)

        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True)
        pw=QtWidgets.QWidget();g=QtWidgets.QGridLayout(pw);g.setVerticalSpacing(6);r=0

        g.addWidget(QtWidgets.QLabel("BioLogic address"),r,0)
        self.bl_addr=QtWidgets.QLineEdit()
        self.bl_addr.setToolTip("IP address (e.g. 192.168.0.9) or USB identifier")
        self.bl_addr.setPlaceholderText("IP or USB identifier");g.addWidget(self.bl_addr,r,1)
        btn_row=QtWidgets.QHBoxLayout()
        self.btn_tc=QtWidgets.QPushButton("Test");self.btn_tc.clicked.connect(self._tc);btn_row.addWidget(self.btn_tc)
        self.btn_scan=QtWidgets.QPushButton("Scan");self.btn_scan.clicked.connect(self._scan);btn_row.addWidget(self.btn_scan)
        btn_w=QtWidgets.QWidget();btn_w.setLayout(btn_row);btn_row.setContentsMargins(0,0,0,0)
        g.addWidget(btn_w,r,2);r+=1

        exp_grp=QtWidgets.QGroupBox("Experiment Type");exp_lay=QtWidgets.QHBoxLayout(exp_grp)
        self.rb_cv =QtWidgets.QRadioButton("CV (Standard)")
        self.rb_cva=QtWidgets.QRadioButton("CV Advanced (CVA)")
        self.rb_cv.setChecked(True)
        self.rb_cv.toggled.connect(self._on_exp_radio)
        self.rb_cva.toggled.connect(self._on_exp_radio)
        exp_lay.addWidget(self.rb_cv);exp_lay.addWidget(self.rb_cva)
        g.addWidget(exp_grp,r,0,1,3);r+=1

        sep=QtWidgets.QFrame();sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("color:#555");g.addWidget(sep,r,0,1,3);r+=1
        g.addWidget(QtWidgets.QLabel("<b>Waveform</b>"),r,0,1,3);r+=1

        self.cv_start=self._dbl(g,r,"Start (V)",-10,10,6,0);r+=1
        self.cv_upper=self._dbl(g,r,"Upper vertex (V)",-10,10,6,0.25);r+=1
        self.cv_lower=self._dbl(g,r,"Lower vertex (V)",-10,10,6,-0.1);r+=1
        self.cv_end  =self._dbl(g,r,"End (V)",-10,10,6,0);r+=1
        self.cv_rate =self._dbl(g,r,"Scan rate (V/s)",1e-6,10,6,0.02);r+=1
        self.cv_step =self._dbl(g,r,"Step (V)",1e-6,1,6,0.001);r+=1
        g.addWidget(QtWidgets.QLabel("Cycles"),r,0)
        self.cv_cyc=QtWidgets.QSpinBox();self.cv_cyc.setRange(1,10000);g.addWidget(self.cv_cyc,r,1);r+=1

        ht_grp=QtWidgets.QGroupBox("Hold Times (s)");ht_lay=QtWidgets.QGridLayout(ht_grp);hr=0
        self.ht_init =self._dbl(ht_lay,hr,"Initial hold",0,3600,3,0.0);hr+=1
        self.ht_final=self._dbl(ht_lay,hr,"Final hold",  0,3600,3,0.0)
        g.addWidget(ht_grp,r,0,1,3);r+=1

        sep2=QtWidgets.QFrame();sep2.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#555");g.addWidget(sep2,r,0,1,3);r+=1
        g.addWidget(QtWidgets.QLabel("<b>Instrument</b>"),r,0,1,3);r+=1

        for lbl,attr,items in [
            ("BW","cv_bw",[f"BW{i}" for i in range(1,9)]),
            ("E range","cv_er",["\u00b11 V","\u00b12.5 V","\u00b15 V","\u00b110 V"]),
            ("Filter","cv_fl",["None","5 Hz","1 Hz","10 Hz","50 Hz","100 Hz","1 kHz"]),
            ("I range","cv_ir",["auto","1 nA","10 nA","100 nA","1 \u00b5A","10 \u00b5A","100 \u00b5A",
                                "1 mA","10 mA","100 mA","1 A"])]:
            g.addWidget(QtWidgets.QLabel(lbl),r,0)
            cb=QtWidgets.QComboBox();cb.addItems(items);setattr(self,attr,cb);g.addWidget(cb,r,1);r+=1
        self.cv_ocv=QtWidgets.QCheckBox("vs OCV");g.addWidget(self.cv_ocv,r,0,1,3);r+=1

        self.cva_grp=QtWidgets.QGroupBox("CV Advanced — Extra Parameters")
        cva_g=QtWidgets.QGridLayout(self.cva_grp);cr=0
        self.cva_dE =self._dbl(cva_g,cr,"Record every dE (V)",1e-6,1,6,0.001);cr+=1
        cva_g.addWidget(QtWidgets.QLabel("Average over dE"),cr,0)
        self.cva_avg=QtWidgets.QCheckBox();cva_g.addWidget(self.cva_avg,cr,1);cr+=1
        self.cva_bi =self._dbl(cva_g,cr,"Begin measuring I (0–1)",0.0,1.0,3,0.5);cr+=1
        self.cva_ei2=self._dbl(cva_g,cr,"End measuring I (0–1)",  0.0,1.0,3,1.0)
        g.addWidget(self.cva_grp,r,0,1,3);r+=1

        self.tst=QtWidgets.QLabel("");self.tst.setWordWrap(True);g.addWidget(self.tst,r,0,1,3)

        scroll.setWidget(pw);tabs.addTab(scroll,"Potentiostat")

        cw=QtWidgets.QWidget();cg=QtWidgets.QGridLayout(cw);r=0
        for lbl,attr,lo_,hi_,val_ in [("Cam idx","ci",0,16,0),("Exp \u00b5s","ce",1,10000000,7000),
            ("Cap ms","cint",10,60000,33),("Wait ms","cfw",10,60000,500)]:
            cg.addWidget(QtWidgets.QLabel(lbl),r,0)
            s=QtWidgets.QSpinBox();s.setRange(lo_,hi_);s.setValue(val_);setattr(self,attr,s);cg.addWidget(s,r,1);r+=1
        cg.addWidget(QtWidgets.QLabel("Bin"),r,0)
        self.cb_=QtWidgets.QComboBox();self.cb_.addItems(["1","2","4","8","16"]);cg.addWidget(self.cb_,r,1);r+=1
        cg.addWidget(QtWidgets.QLabel("Bin mode"),r,0)
        self.cbm=QtWidgets.QComboBox();self.cbm.addItems(["mean","sum"]);cg.addWidget(self.cbm,r,1);r+=1
        pmg=QtWidgets.QGroupBox("Power Meter");pml=QtWidgets.QGridLayout(pmg)
        self.pm_en=QtWidgets.QCheckBox("Enable");pml.addWidget(self.pm_en,0,0,1,2)
        pml.addWidget(QtWidgets.QLabel("VISA"),1,0);self.pm_v=QtWidgets.QLineEdit();pml.addWidget(self.pm_v,1,1)
        self.pm_n=QtWidgets.QCheckBox("Norm ROI");pml.addWidget(self.pm_n,2,0,1,2);cg.addWidget(pmg,r,0,1,2);r+=1
        rfg=QtWidgets.QGroupBox("Ref Frame");rfl=QtWidgets.QGridLayout(rfg)
        self.rf_en=QtWidgets.QCheckBox("Norm by ref");rfl.addWidget(self.rf_en,0,0,1,2)
        rfl.addWidget(QtWidgets.QLabel("Idx"),1,0)
        self.rf_i=QtWidgets.QSpinBox();self.rf_i.setRange(0,10000000);rfl.addWidget(self.rf_i,1,1)
        cg.addWidget(rfg,r,0,1,2);tabs.addTab(cw,"Camera")

        dw=QtWidgets.QWidget();dg=QtWidgets.QGridLayout(dw);r=0
        dg.addWidget(QtWidgets.QLabel("Cmap"),r,0)
        self.cmap=QtWidgets.QComboBox()
        self.cmap.addItems(["gray","viridis","plasma","magma","inferno","cividis","turbo","jet"])
        dg.addWidget(self.cmap,r,1);r+=1
        self.al=QtWidgets.QCheckBox("Auto levels");self.al.setChecked(True);dg.addWidget(self.al,r,0,1,2);r+=1
        self.dlmin=self._dbl(dg,r,"Min",-1e30,1e30,2,0);r+=1
        self.dlmax=self._dbl(dg,r,"Max",-1e30,1e30,2,1000)
        tabs.addTab(dw,"Display")

        br=QtWidgets.QHBoxLayout()
        b1=QtWidgets.QPushButton("Save && Close");b1.clicked.connect(self._sv);br.addWidget(b1)
        b2=QtWidgets.QPushButton("Cancel");b2.clicked.connect(self.reject);br.addWidget(b2)
        lo.addLayout(br)

    def _on_exp_radio(self):
        self.cva_grp.setVisible(self.rb_cva.isChecked())

    def _dbl(self,g,r,l,lo,hi,d,v):
        g.addWidget(QtWidgets.QLabel(l),r,0)
        s=QtWidgets.QDoubleSpinBox();s.setDecimals(d);s.setRange(lo,hi);s.setValue(v)
        g.addWidget(s,r,1);return s

    def _load_cfg(self):
        p=self.cfg["potentiostat"];c=self.cfg["camera"]
        pm=self.cfg["power_meter"];rf=self.cfg["reference"];d=self.cfg["display"]
        exp=p.get("experiment_type",EXP_CV)
        self.rb_cva.setChecked(exp==EXP_CVA);self.rb_cv.setChecked(exp!=EXP_CVA)
        self.bl_addr.setText(p.get("bl_address","192.168.0.9"))
        self.cv_start.setValue(float(p.get("start_v","0")))
        self.cv_upper.setValue(float(p.get("upper_v","0.25")))
        self.cv_lower.setValue(float(p.get("lower_v","-0.1")))
        self.cv_end.setValue(float(p.get("end_v","0")))
        self.cv_rate.setValue(float(p.get("scan_rate","0.02")))
        self.cv_step.setValue(float(p.get("step_v","0.001")))
        self.cv_cyc.setValue(int(p.get("cycles","1")))
        self.ht_init.setValue(float(p.get("hold_t_init","0.0")))
        self.ht_final.setValue(float(p.get("hold_t_final","0.0")))
        self.cv_bw.setCurrentText(p.get("bandwidth","BW8"))
        self.cv_er.setCurrentText(p.get("e_range","\u00b11 V"))
        self.cv_fl.setCurrentText(p.get("filter","5 Hz"))
        self.cv_ir.setCurrentText(p.get("i_range","auto"))
        self.cv_ocv.setChecked(p.get("vs_ocv","false").lower()=="true")
        self.cva_dE.setValue(float(p.get("cva_record_every_de","0.001")))
        self.cva_avg.setChecked(p.get("cva_average_over_de","false").lower()=="true")
        self.cva_bi.setValue(float(p.get("cva_begin_measuring_i","0.5")))
        self.cva_ei2.setValue(float(p.get("cva_end_measuring_i","1.0")))
        self.ci.setValue(int(c.get("camera_index","0")));self.ce.setValue(int(c.get("exposure_us","7000")))
        self.cb_.setCurrentText(c.get("bin_factor","1"));self.cbm.setCurrentText(c.get("bin_mode","mean"))
        self.cint.setValue(int(c.get("capture_interval_ms","33")));self.cfw.setValue(int(c.get("frame_wait_ms","500")))
        self.pm_en.setChecked(pm.get("enabled","false").lower()=="true");self.pm_v.setText(pm.get("visa",""))
        self.pm_n.setChecked(pm.get("normalize_roi","false").lower()=="true")
        self.rf_en.setChecked(rf.get("normalize_by_ref_frame","false").lower()=="true")
        self.rf_i.setValue(int(rf.get("ref_frame_index","0")))
        self.cmap.setCurrentText(d.get("colormap","viridis"))
        self.al.setChecked(d.get("auto_levels","true").lower()=="true")
        self.dlmin.setValue(float(d.get("level_min","0")))
        self.dlmax.setValue(float(d.get("level_max","1000")))

    def _sv(self):
        p=self.cfg["potentiostat"];c=self.cfg["camera"]
        pm=self.cfg["power_meter"];rf=self.cfg["reference"];d=self.cfg["display"]
        p["experiment_type"]=EXP_CVA if self.rb_cva.isChecked() else EXP_CV
        p["bl_address"]=self.bl_addr.text().strip()
        p["start_v"]=str(self.cv_start.value());p["upper_v"]=str(self.cv_upper.value())
        p["lower_v"]=str(self.cv_lower.value());p["end_v"]=str(self.cv_end.value())
        p["scan_rate"]=str(self.cv_rate.value());p["step_v"]=str(self.cv_step.value())
        p["cycles"]=str(self.cv_cyc.value())
        p["hold_t_init"]=str(self.ht_init.value());p["hold_t_final"]=str(self.ht_final.value())
        p["bandwidth"]=self.cv_bw.currentText();p["e_range"]=self.cv_er.currentText()
        p["filter"]=self.cv_fl.currentText();p["i_range"]=self.cv_ir.currentText()
        p["vs_ocv"]=str(self.cv_ocv.isChecked()).lower()
        p["cva_record_every_de"]=str(self.cva_dE.value())
        p["cva_average_over_de"]=str(self.cva_avg.isChecked()).lower()
        p["cva_begin_measuring_i"]=str(self.cva_bi.value())
        p["cva_end_measuring_i"]=str(self.cva_ei2.value())
        c["camera_index"]=str(self.ci.value());c["exposure_us"]=str(self.ce.value())
        c["bin_factor"]=self.cb_.currentText();c["bin_mode"]=self.cbm.currentText()
        c["capture_interval_ms"]=str(self.cint.value());c["frame_wait_ms"]=str(self.cfw.value())
        pm["enabled"]=str(self.pm_en.isChecked()).lower();pm["visa"]=self.pm_v.text().strip()
        pm["normalize_roi"]=str(self.pm_n.isChecked()).lower()
        rf["normalize_by_ref_frame"]=str(self.rf_en.isChecked()).lower()
        rf["ref_frame_index"]=str(self.rf_i.value())
        d["colormap"]=self.cmap.currentText();d["auto_levels"]=str(self.al.isChecked()).lower()
        d["level_min"]=str(self.dlmin.value());d["level_max"]=str(self.dlmax.value())
        save_settings(self.cfg);self.accept()

    def _tc(self):
        a=self.bl_addr.text().strip()
        if not a:self.tst.setText("Enter address");return
        self.tst.setText("Testing...");self.btn_tc.setEnabled(False);QtWidgets.QApplication.processEvents()
        ok,ebl,_,err=try_import_ebl()
        if not ok:self.tst.setText(f"Fail: {err[:200]}");self.btn_tc.setEnabled(True);return
        try:dev=ebl.BiologicDevice(a);dev.connect(None,None);dev.disconnect();self.tst.setText("OK.")
        except Exception as e:self.tst.setText(f"Fail: {e}")
        self.btn_tc.setEnabled(True)

    def _scan(self):
        self.tst.setText("Scanning...");self.btn_scan.setEnabled(False);QtWidgets.QApplication.processEvents()
        ok,ebl,_,err=try_import_ebl()
        if not ok:self.tst.setText(f"Scan fail: {err[:200]}");self.btn_scan.setEnabled(True);return
        found=[]
        try:
            if hasattr(ebl,"find_devices"):
                result=ebl.find_devices()
            else:
                from easy_biologic import find_devices as fd_mod
                result=fd_mod.find_devices() if hasattr(fd_mod,"find_devices") else None
            if result is None:found=[]
            elif isinstance(result,dict):found=[str(k) for k in result.keys()]
            elif isinstance(result,(list,tuple)):
                for item in result:
                    if isinstance(item,str):found.append(item)
                    elif isinstance(item,(list,tuple)) and len(item)>0:found.append(str(item[0]))
                    else:found.append(str(item))
            else:found=[str(result)]
        except Exception as e:
            self.tst.setText(f"Scan fail: {e}");self.btn_scan.setEnabled(True);return
        if not found:
            self.tst.setText("No devices found.")
        elif len(found)==1:
            self.bl_addr.setText(found[0]);self.tst.setText(f"Found: {found[0]}")
        else:
            item,ok_sel=QtWidgets.QInputDialog.getItem(self,"Select device","Multiple devices found:",found,0,False)
            if ok_sel and item:
                self.bl_addr.setText(item);self.tst.setText(f"Selected: {item}")
            else:
                self.tst.setText(f"Found {len(found)} devices, none selected")
        self.btn_scan.setEnabled(True)


class TrafficLight(QtWidgets.QWidget):
    RED=0;YELLOW=1;GREEN=2
    def __init__(self,parent=None):
        super().__init__(parent);self._state=self.GREEN;self.setFixedSize(90,36)
    def setState(self,state):self._state=state;self.update()
    def paintEvent(self,event):
        p=QtGui.QPainter(self);p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        colors=[(200,50,50),(220,200,50),(50,200,50)]
        for i,c in enumerate(colors):
            x=6+i*28;r,g,b=c
            if i==self._state:p.setBrush(QtGui.QColor(r,g,b));p.setPen(QtGui.QColor(r,g,b))
            else:p.setBrush(QtGui.QColor(80,80,80));p.setPen(QtGui.QColor(60,60,60))
            p.drawEllipse(x,6,24,24)
        p.end()

__all__ = [name for name in globals() if not name.startswith("__")]
