"""CVPlotMixin methods for the main window.

This mixin turns stored arrays into the plots the user sees:

* electrochemical CV plots,
* optical ROI vs time,
* optical ROI vs potential,
* derivative-style plots such as dR/dV and dR/dt,
* smoothing and cycle-specific display.
"""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class CVPlotMixin:
    def _update_lowess_color_btn(self):
        # Update the button color so it previews the LOWESS curve color.
        c=self._lowess_color
        luma=0.299*c.red()+0.587*c.green()+0.114*c.blue()
        fg="black" if luma>128 else "white"
        self.btn_lowess_color.setStyleSheet(
            f"QPushButton{{background:rgb({c.red()},{c.green()},{c.blue()});"
            f"color:{fg};font-size:11px;padding:2px 4px;border-radius:3px;}}")

    def _on_lowess_color_pick(self):
        # Let the user choose the trend-line color for ROI plots.
        c=QtWidgets.QColorDialog.getColor(self._lowess_color,self,"LOWESS curve colour")
        if c.isValid():
            self._lowess_color=c
            self._update_lowess_color_btn()
            self._apply_lowess_pen()
            self._update_roi_plots()

    def _on_lowess_style_changed(self,*_):
        # Thickness/color changes only affect the displayed curve style.
        self._apply_lowess_pen()
        self._update_roi_plots()

    def _on_lowess_frac_changed(self,*_):
        """Clear LOWESS cache when the window fraction changes so new values are computed."""
        self._lowess_cache={}
        self._update_roi_plots()

    def _on_lowess_roi_changed(self,*_):
        # Redraw ROI plots when the user chooses which ROI should receive a
        # LOWESS trend line.
        self._update_roi_plots()

    def _refresh_lowess_roi_options(self):
        # Keep the LOWESS ROI selector aligned with the current zone count.
        cb=getattr(self,"lowess_roi_cb",None)
        if cb is None:return
        current=cb.currentData()
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("All",None)
        n=max(1,len(getattr(self,"_zones",[]) or []))
        for idx in range(n):
            cb.addItem(f"ROI {idx+1}",idx)
        if current is None:
            cb.setCurrentIndex(0)
        else:
            idx=cb.findData(current)
            cb.setCurrentIndex(idx if idx>=0 else 0)
        cb.blockSignals(False)

    def _selected_lowess_zone(self):
        # Return None for "All" or the zero-based ROI index for a specific zone.
        cb=getattr(self,"lowess_roi_cb",None)
        if cb is None:return None
        data=cb.currentData()
        return None if data is None else int(data)

    def _show_lowess_for_zone(self,z_idx):
        selected=self._selected_lowess_zone()
        return selected is None or selected==z_idx

    def _zone_lowess_pen(self,z_idx):
        # A selected single ROI gets the global LOWESS style; "All" keeps each
        # trend line near its ROI color so traces remain distinguishable.
        if self._selected_lowess_zone()==z_idx:
            return self._lowess_pen()
        r,g,b=self._zone_color(z_idx)
        return pg.mkPen(color=(r,g,b,180),width=1,
                        style=QtCore.Qt.PenStyle.DotLine)

    def _lowess_pen(self):
        # Build a pyqtgraph pen using the current LOWESS color and thickness.
        c=self._lowess_color
        w=getattr(self,'lowess_thickness',None)
        thickness=w.value() if w else 2
        return pg.mkPen(color=(c.red(),c.green(),c.blue()),width=thickness)

    def _apply_lowess_pen(self):
        pen=self._lowess_pen()
        if hasattr(self,'roi_t_lowess'):self.roi_t_lowess.setPen(pen)
        if hasattr(self,'roi_e_lowess'):self.roi_e_lowess.setPen(pen)

    def _on_smooth_mode_toggle(self,checked):
        # Switch between moving-average smoothing and Savitzky-Golay smoothing.
        self.smooth_sg_btn.setText("SG" if checked else "Boxcar")
        self._update_all_plots()

    def _apply_smooth(self,arr,window):
        if window<2:return arr
        use_sg=self.smooth_sg_btn.isChecked()
        return _smooth(arr,window,use_savgol=use_sg)

    def _sm(self,arr):
        return self._apply_smooth(arr,self.smooth_sp.value())

    def _get_upper_v(self):
        # Cycle detection needs the configured upper CV vertex.
        try:return float(self.cfg["potentiostat"].get("upper_v","0.25"))
        except:return None

    def _plot_var_label(self,name):
        # Turn compact dropdown names into labels that make plot titles clearer.
        labels={
            "I (A)":"Current",
            "E (V)":"Potential",
            "R":"Reflectance",
            "ROI Sum":"Reflectance",
            "R / area":"Reflectance / pixel",
            "Power (W)":"Power",
            "dE/dt":"dE/dt",
            "Frame #":"Frame #",
        }
        return labels.get(name,name)

    def _is_drr_name(self,name):
        # Accept both the intended delta symbol and the older mojibake form that
        # can appear in this file on some Windows terminals.
        return name in ("ΔR/R","Î”R/R") or str(name).endswith("R/R")

    def _is_area_name(self,name):
        # Area-normalized reflectance means each ROI sum divided by the number
        # of pixels in that same ROI.
        return str(name).strip()=="R / area"

    def _zone_area_array(self,n_zones=None,live=False):
        # Return valid ROI areas. Missing/zero areas become 1 so division never
        # crashes; that fallback preserves the raw signal shape.
        src=(getattr(self,"_live_zone_areas",np.array([],dtype=np.float64))
             if live else getattr(self,"_zone_areas",np.array([],dtype=np.float64)))
        arr=np.asarray(src,dtype=np.float64)
        if n_zones is None:n_zones=arr.size
        n_zones=max(0,int(n_zones))
        out=np.ones(n_zones,dtype=np.float64)
        take=min(out.size,arr.size)
        if take>0:out[:take]=arr[:take]
        return np.where(np.isfinite(out)&(out>0),out,1.0)

    def _divide_zone_traces_by_area(self,traces,live=False):
        # Make one area-normalized trace per ROI zone.
        areas=self._zone_area_array(len(traces),live=live)
        return [np.asarray(trace,dtype=np.float64)/areas[i]
                for i,trace in enumerate(traces)]

    def _total_area(self,live=False):
        # Total area is useful for the legacy single summed trace and frame
        # marker fallback.
        return float(np.sum(self._zone_area_array(None,live=live))) or 1.0

    def _roi_zone_traces_for_name(self,name):
        # Return one trace per ROI zone for optical variables. Non-optical plot
        # variables such as current, power, or frame number stay as single traces.
        if name in ("R","ROI Sum"):
            return list(getattr(self,"_zone_intensity",[]) or [])
        if self._is_area_name(name):
            if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
            traces=list(getattr(self,"_zone_intensity_per_area",[]) or [])
            if traces:return traces
            traces=list(getattr(self,"_zone_intensity",[]) or [])
            return self._divide_zone_traces_by_area(traces)
        if self._is_drr_name(name):
            return list(getattr(self,"_zone_drr",[]) or [])
        return []

    def _clear_dynamic_roi_curves(self):
        # Per-zone traces are created dynamically after Analyze, so reset them
        # before redrawing or when a new run starts.
        for attr,plot_name in (
                ("_roi_t_zone_curves","roi_t_plot"),
                ("_roi_t_zone_smooth","roi_t_plot"),
                ("_roi_t_zone_lowess","roi_t_plot"),
                ("_roi_e_zone_curves","roi_e_plot"),
                ("_roi_e_zone_lowess","roi_e_plot")):
            if hasattr(self,attr) and hasattr(self,plot_name):
                plot=getattr(self,plot_name)
                for curve in getattr(self,attr):
                    try:plot.removeItem(curve)
                    except Exception:pass
                setattr(self,attr,[])

    def _get_cycle_slice(self):
        # Return frame-index bounds for the selected cycle. Cycle 0 means all
        # data, matching the GUI's "All" behavior.
        cyc=self.cycle_sp.value()
        if cyc==0:return 0,min(self._frame_E.size,self._frame_t.size,self._roi_intensity.size)
        cycles=(self._cached_cycles
                or _detect_cycles(self._frame_E,upper_v=self._get_upper_v()))
        if cyc>len(cycles):cyc=len(cycles)
        if cyc<1:return 0,min(self._frame_E.size,self._frame_t.size)
        s,e=cycles[cyc-1];return s,e

    def _break_at_steps(self,xdata,ydata,t_wall=None):
        # Insert NaNs at large potential jumps so plots do not draw misleading
        # straight connector lines across discontinuities.
        n=min(xdata.size,ydata.size)
        if n<3:return xdata[:n].copy(),ydata[:n].copy()
        E=self._E_all[:n] if self._E_all.size>=n else xdata[:n]
        if E.size<2:return xdata[:n].copy(),ydata[:n].copy()
        dE=np.abs(np.diff(E))
        median_step=np.median(dE[dE>0]) if np.any(dE>0) else 1e-9
        seams=np.where(dE>20*median_step)[0]+1
        if seams.size==0:return xdata[:n].copy(),ydata[:n].copy()
        xout=[];yout=[]
        prev=0
        for s in seams:
            xout.append(xdata[prev:s]);xout.append([np.nan])
            yout.append(ydata[prev:s]);yout.append([np.nan])
            prev=s
        xout.append(xdata[prev:n]);yout.append(ydata[prev:n])
        if not xout:return xdata[:n].copy(),ydata[:n].copy()
        return np.concatenate(xout),np.concatenate(yout)

    def _update_cv(self,live=False):
        # Draw the electrochemistry plot. In live mode, keep it lightweight; in
        # post-analysis mode, support cycle selection and smoothing.
        if live:
            self._update_cv_live(self._E_cv, self._I_cv, self._t_cv)
            return
        mode=self.cv_mode.currentText();sw=self.smooth_sp.value()
        MAX_CV=3000
        if mode=="I vs E":
            # Standard CV view: current or selected y-variable vs potential.
            self.cv_plot.setLabel("bottom","Potential (V)")
            yvar=self.cv_y.currentText()
            self.cv_plot.setLabel("left",yvar)
            self.cv_plot.setTitle(f"{self._plot_var_label(yvar)} vs Potential")
            cv_cb=getattr(self,"cv_cycle_cb",None)
            _cv_sel=cv_cb.currentText() if cv_cb else "All"
            if not hasattr(self,"_cv_cyc_curves"):self._cv_cyc_curves=[]
            for c in self._cv_cyc_curves:self.cv_plot.removeItem(c)
            self._cv_cyc_curves=[]
            self.cv_curve.setData([],[]);self.cv_smooth.setData([],[])
            if self._frame_E.size>0:
                yv=self._get_var(yvar)
                _all_cv_cyc=(self._cached_cycles
                             or (_detect_cycles(self._frame_E,upper_v=self._get_upper_v())
                                 if self._frame_E.size>2 else []))
                if _cv_sel=="All":
                    _cv_ranges=[(i+1,s,e) for i,(s,e) in enumerate(_all_cv_cyc)] if _all_cv_cyc else [(1,0,min(self._frame_E.size,yv.size))]
                elif _cv_sel.isdigit():
                    _ci=int(_cv_sel)-1
                    _cv_ranges=[(_ci+1,)+_all_cv_cyc[_ci]] if 0<=_ci<len(_all_cv_cyc) else []
                else:
                    _cv_ranges=[(1,0,min(self._frame_E.size,yv.size))]
                if not _cv_ranges:
                    self.cv_curve.setData([],[]);self.cv_smooth.setData([],[])
                for cyc_i,cs,ce in _cv_ranges:
                    # Plot each detected cycle separately so colors/selection
                    # can distinguish repeated scans.
                    n=min(self._frame_E.size,yv.size,ce)-cs
                    if n<2:continue
                    xdata=self._frame_E[cs:cs+n];ydata=yv[cs:cs+n]
                    xr,yr=self._break_at_steps(xdata,ydata)
                    if xr.size>MAX_CV:
                        idx=np.round(np.linspace(0,xr.size-1,MAX_CV)).astype(int)
                        xr=xr[idx];yr=yr[idx]
                    pen=self._cycle_pen(cyc_i,width=2) if self._cycle_colors else pg.mkPen(color='w',width=2)
                    c=self.cv_plot.plot(xr,yr,pen=pen);self._cv_cyc_curves.append(c)
                    yr_f=yr[np.isfinite(yr)]
                    if sw>1 and yr_f.size>=sw:
                        sc=self.cv_plot.plot(xr[np.isfinite(yr)],self._apply_smooth(yr_f,sw),
                                             pen=pg.mkPen(color=(255,180,0),width=2))
                        self._cv_cyc_curves.append(sc)
            else:
                if yvar=="I (A)":xdata=self._E_all;ydata=self._I_all
                elif yvar=="E (V)":xdata=self._E_all;ydata=self._E_all
                else:xdata=self._E_all;ydata=self._I_all
                n=min(xdata.size,ydata.size)
                if n>0:
                    xr,yr=self._break_at_steps(xdata[:n],ydata[:n])
                    if xr.size>MAX_CV:
                        idx=np.round(np.linspace(0,xr.size-1,MAX_CV)).astype(int)
                        xr=xr[idx];yr=yr[idx]
                    self.cv_curve.setData(xr,yr)
                    yr_f=yr[np.isfinite(yr)]
                    if sw>1 and yr_f.size>=sw:
                        self.cv_smooth.setData(xr[np.isfinite(yr)],self._apply_smooth(yr_f,sw))
                    else:self.cv_smooth.setData([],[])
                else:self.cv_curve.setData([],[]);self.cv_smooth.setData([],[])
        else:
            # Alternative view: potential vs time.
            self.cv_plot.setLabel("bottom","Time (s)");self.cv_plot.setLabel("left","Potential (V)")
            self.cv_plot.setTitle("Potential vs Time")
            if self._t_wall_echem.size>0:tp=self._t_wall_echem-self._t_wall_echem[0]
            else:tp=self._t_all
            n=min(tp.size,self._E_all.size)
            if n>0:
                xr,yr=self._break_at_steps(tp[:n],self._E_all[:n])
                self.cv_curve.setData(xr,yr)
                yr_f=yr[np.isfinite(yr)]
                if sw>1 and yr_f.size>=sw:
                    self.cv_smooth.setData(xr[np.isfinite(yr)],self._apply_smooth(yr_f,sw))
                else:self.cv_smooth.setData([],[])
            else:self.cv_curve.setData([],[]);self.cv_smooth.setData([],[])

    def _update_cv_live(self, E, I, t):
        # Fast live CV plotting from worker-emitted arrays. No smoothing here so
        # acquisition remains responsive.
        MAX_PTS = 4000
        mode = self.cv_mode.currentText()
        if mode == "I vs E":
            self.cv_plot.setLabel("bottom", "Potential (V)")
            self.cv_plot.setLabel("left", "Current (A)")
            self.cv_plot.setTitle("Current vs Potential")
            xdata = E
            ydata = I
        else:
            self.cv_plot.setLabel("bottom", "Time (s)")
            self.cv_plot.setLabel("left", "Potential (V)")
            self.cv_plot.setTitle("Potential vs Time")
            xdata = t - t[0] if t.size > 0 else t
            ydata = E
        if xdata.size < 2:
            self.cv_curve.setData([], [])
            self.cv_smooth.setData([], [])
            return
        if xdata.size > MAX_PTS:
            idx = np.round(np.linspace(0, xdata.size - 1, MAX_PTS)).astype(int)
            xdata = xdata[idx]
            ydata = ydata[idx]
        self.cv_curve.setData(xdata, ydata)
        self.cv_smooth.setData([], [])

    def _plot_power(self):
        # Show LED/power-meter readings if the power meter was enabled.
        if self._frame_power.size==0 or self._frame_t.size==0:
            QtWidgets.QMessageBox.information(self,"Power","No power data.");return
        n=min(self._frame_power.size,self._frame_t.size)
        t=self._frame_t[:n];p=self._frame_power[:n]
        mask=np.isfinite(t)&np.isfinite(p)
        if not np.any(mask):
            QtWidgets.QMessageBox.information(self,"Power",
                "All power values are NaN.\n\n"
                "The power meter is either disabled or not connected.\n"
                "Enable it under Setup \u2192 Camera \u2192 Power Meter.");return
        pw=pg.PlotWidget(title="LED Power vs Time");pw.setLabel("bottom","Time (s)");pw.setLabel("left","Power (W)")
        pw.plot(t[mask],p[mask],pen=pg.mkPen("y",width=2))
        pw.resize(600,300);pw.show();self._power_win=pw

    def _roi_slices(self,shape=None):
        return self._zone_slices(self.roi,shape)

    def _roi_key(self):
        parts=[]
        shape=self._frames_hw
        for zone in self._zones:
            ys,xs=self._zone_slices(zone,shape)
            parts+=[xs.start,ys.start,xs.stop-xs.start,ys.stop-ys.start]
        return tuple(parts)

    def _stabilize_power(self,pwr):
        # Power meters may read low while stabilizing. Replace early low values
        # with the later stable median so normalization does not exaggerate early
        # frames.
        pwr=pwr.copy()
        valid=np.isfinite(pwr)&(pwr>0)
        if np.sum(valid)<6:return pwr
        n=pwr.size
        second_half=pwr[valid][len(pwr[valid])//2:]
        if second_half.size==0:return pwr
        stable_median=float(np.median(second_half))
        if stable_median<=0:return pwr
        threshold=0.95*stable_median
        stabilized_at=0
        for i in range(n):
            if np.isfinite(pwr[i]) and pwr[i]>=threshold:
                stabilized_at=i
                break
        if stabilized_at>0:
            pwr[:stabilized_at]=stable_median
        return pwr

    def _compute_roi_e_data(self,roi,E,t):
        # Convert an ROI trace into the selected x/y representation for the
        # bottom-right plot.
        mode=self.roi_e_mode.currentText()

        def _sg_window(n):
            w=max(11,int(round(0.01*n))|1)
            if w%2==0:w+=1
            return min(w,n if n%2==1 else n-1)

        def _sg_smooth(y, w, poly=3):
            try:
                from scipy.signal import savgol_filter
                return savgol_filter(y,window_length=w,polyorder=min(poly,w-1))
            except Exception:
                return np.convolve(y,np.ones(w)/w,mode='same')

        def _sg_deriv(y, x, w, poly=3):
            try:
                from scipy.signal import savgol_filter
                dx=float(np.median(np.abs(np.diff(x))))
                if dx<=0:dx=1.0
                return savgol_filter(y,window_length=w,polyorder=min(poly,w-1),
                                     deriv=1,delta=dx)
            except Exception:
                ys=_sg_smooth(y,w,poly)
                return np.gradient(ys,x)

        if str(mode).endswith("R/R vs t"):
            # The caller passes either the total dR/R trace or one zone's dR/R
            # trace, so use the provided roi array instead of the summed trace.
            if roi.size<2:return t,np.full_like(t,np.nan),'Time (s)','ΔR/R'
            n2=min(t.size,roi.size)
            return t[:n2],roi[:n2],'Time (s)','ΔR/R'
        if mode=='dR/dV vs E':
            # Optical derivative with respect to potential.
            valid=np.isfinite(E)&np.isfinite(roi)
            if np.sum(valid)<5:return E,np.full_like(E,np.nan),'Potential (V)','dR/dV'
            idx=np.argsort(E[valid]);Es=E[valid][idx];rs=roi[valid][idx]
            w=_sg_window(len(rs))
            drdv=_sg_deriv(rs,Es,w)
            y=np.full_like(roi,np.nan);y[np.where(valid)[0][idx]]=drdv
            return E,y,'Potential (V)','dR/dV'
        elif mode=='Optical Current vs E':
            # "Optical current" here is dR/dt plotted against potential.
            valid=np.isfinite(t)&np.isfinite(roi)
            if np.sum(valid)<5:return E,np.full_like(E,np.nan),'Potential (V)','dR/dt'
            ts=t[valid];rs=roi[valid]
            idx=np.argsort(ts);ts=ts[idx];rs=rs[idx]
            w=_sg_window(len(rs))
            drdt_s=_sg_deriv(rs,ts,w)
            drdt=np.full_like(roi,np.nan)
            drdt[np.where(valid)[0][idx]]=drdt_s
            return E,drdt,'Potential (V)','dR/dt'
        elif mode=='dR/dt vs t':
            # Time derivative of the optical signal.
            valid=np.isfinite(t)&np.isfinite(roi)
            if np.sum(valid)<5:return t,np.full_like(t,np.nan),'Time (s)','dR/dt'
            ts=t[valid];rs=roi[valid]
            idx=np.argsort(ts);ts=ts[idx];rs=rs[idx]
            w=_sg_window(len(rs))
            drdt_s=_sg_deriv(rs,ts,w)
            drdt=np.full_like(roi,np.nan)
            drdt[np.where(valid)[0][idx]]=drdt_s
            return t,drdt,'Time (s)','dR/dt'
        elif mode=='ΔR/R vs t':
            drr=self._drr_intensity
            if drr.size<2:return t,np.full_like(t,np.nan),'Time (s)','ΔR/R'
            n2=min(t.size,drr.size)
            return t[:n2],drr[:n2],'Time (s)','ΔR/R'
        if mode=="R / area vs E":
            return E,roi,'Potential (V)','Reflectance / pixel'
        return E,roi,'Potential (V)','Reflectance'

    def _lowess_cached(self, x, y, frac):
        """LOWESS with an in-memory cache keyed on (x/y identity, length, frac).
        Recomputing LOWESS on every slider move / plot resize is expensive
        (O(n²)).  The cache stores the smoothed y for the current dataset;
        it is cleared whenever new data is loaded, Analyze is re-run, or the
        window fraction spinbox changes.
        """
        if not hasattr(self, "_lowess_cache"):
            self._lowess_cache = {}
        key = (id(x), id(y), len(x), round(frac, 4))
        if key not in self._lowess_cache:
            self._lowess_cache[key] = _lowess_smooth(x, y, frac=frac)
        return self._lowess_cache[key]

    def _update_roi_plots(self,*_):
        # Update both ROI plots after analysis, variable selection, smoothing,
        # cycle selection, or LOWESS settings change.
        sw=self.smooth_sp.value()
        _frac_t=getattr(self,'lowess_frac_t',None)
        _ft=_frac_t.value() if _frac_t else 0.05
        _frac_e=getattr(self,'lowess_frac_e',None)
        _fe=_frac_e.value() if _frac_e else 0.08
        self._clear_dynamic_roi_curves()
        rt_name=self.roi_t_y.currentText()
        rt_label=self._plot_var_label(rt_name)
        self.roi_t_plot.setTitle(f"{rt_label} vs Time")
        self.roi_t_plot.setLabel('left',rt_name)
        rt_y=self._get_var(rt_name)
        n=min(rt_y.size,self._frame_t.size)
        if n>0:
            # Top/bottom-left plot: selected optical variable vs time.
            m=np.isfinite(self._frame_t[:n])&np.isfinite(rt_y[:n])
            xr=self._frame_t[:n][m];yr=rt_y[:n][m]
            self.roi_t_curve.setData(xr,yr)
            if sw>1 and yr.size>=sw:self.roi_t_smooth.setData(xr,self._apply_smooth(yr,sw))
            else:self.roi_t_smooth.setData([],[])
            if xr.size>=4:
                _frac_t=getattr(self,"lowess_frac_t",None)
                _ft=_frac_t.value() if _frac_t else 0.05
                try:
                    self.roi_t_lowess.setPen(self._lowess_pen())
                    self.roi_t_lowess.setData(xr, self._lowess_cached(xr, yr, _ft))
                except Exception:self.roi_t_lowess.setData([],[])
            else:self.roi_t_lowess.setData([],[])
        else:
            self.roi_t_curve.setData([],[]);self.roi_t_smooth.setData([],[])
            self.roi_t_lowess.setData([],[])
        zone_rt=self._roi_zone_traces_for_name(rt_name)
        if zone_rt:
            # For optical ROI variables, draw each ROI zone separately instead
            # of plotting the summed total trace.
            self.roi_t_curve.setData([],[]);self.roi_t_smooth.setData([],[]);self.roi_t_lowess.setData([],[])
            self._roi_t_zone_curves=[];self._roi_t_zone_smooth=[];self._roi_t_zone_lowess=[]
            for z_idx,rt_y in enumerate(zone_rt):
                n=min(rt_y.size,self._frame_t.size)
                if n<=0:continue
                m=np.isfinite(self._frame_t[:n])&np.isfinite(rt_y[:n])
                xr=self._frame_t[:n][m];yr=rt_y[:n][m]
                if xr.size<1:continue
                r,g,b=self._zone_color(z_idx)
                self._roi_t_zone_curves.append(
                    self.roi_t_plot.plot(xr,yr,pen=pg.mkPen(color=(r,g,b),width=2)))
                if sw>1 and yr.size>=sw:
                    self._roi_t_zone_smooth.append(
                        self.roi_t_plot.plot(xr,self._apply_smooth(yr,sw),
                                             pen=pg.mkPen(color=(r,g,b,150),width=1,
                                                          style=QtCore.Qt.PenStyle.DashLine)))
                if xr.size>=4 and self._show_lowess_for_zone(z_idx):
                    try:
                        self._roi_t_zone_lowess.append(
                            self.roi_t_plot.plot(xr,self._lowess_cached(xr,yr,_ft),
                                                 pen=self._zone_lowess_pen(z_idx)))
                    except Exception:pass
        _e_mode=self.roi_e_mode.currentText()
        _e_axis_is_E=_e_mode not in ('dR/dt vs t','ΔR/R vs t')
        _e_axis_is_E=not (_e_mode=='dR/dt vs t' or str(_e_mode).endswith("R/R vs t"))
        re_cb=getattr(self,"roi_e_cycle_cb",None)
        _re_sel=re_cb.currentText() if re_cb else "All"
        _e_area_mode=(_e_mode=="R / area vs E")
        if str(_e_mode).endswith("R/R vs t"):
            re_base=self._drr_intensity
        elif _e_area_mode:
            if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
            area_base=getattr(self,"_roi_intensity_per_area",np.array([],dtype=np.float64))
            re_base=(area_base if area_base.size else
                     (self._roi_intensity/self._total_area()
                      if self._roi_intensity.size else self._roi_intensity))
        else:
            re_base=self._roi_intensity
        # ── OPTIMISATION: use cached cycle detection result ───────────────────
        _all_cycles=(self._cached_cycles
                     or (_detect_cycles(self._frame_E,upper_v=self._get_upper_v())
                         if self._frame_E.size>2 else []))
        if _re_sel=="All":
            _cyc_ranges=[(i+1,s,e) for i,(s,e) in enumerate(_all_cycles)] if _all_cycles else [(1,0,min(re_base.size,self._frame_E.size,self._frame_t.size))]
        elif _re_sel.isdigit():
            _ci=int(_re_sel)-1
            _cyc_ranges=[(_ci+1,)+_all_cycles[_ci]] if 0<=_ci<len(_all_cycles) else []
        else:
            _cyc_ranges=[(1,0,min(re_base.size,self._frame_E.size,self._frame_t.size))]
        if not hasattr(self,"_roi_e_cyc_curves"):self._roi_e_cyc_curves=[]
        if not hasattr(self,"_roi_e_cyc_lowess"):self._roi_e_cyc_lowess=[]
        for c in self._roi_e_cyc_curves:self.roi_e_plot.removeItem(c)
        for c in self._roi_e_cyc_lowess:self.roi_e_plot.removeItem(c)
        self._roi_e_cyc_curves=[];self._roi_e_cyc_lowess=[]
        self.roi_e_curve.setData([],[]);self.roi_e_smooth.setData([],[]);self.roi_e_lowess.setData([],[])
        xlabel="Potential (V)";ylabel="Reflectance"
        if not _cyc_ranges:
            self.roi_e_plot.setLabel('bottom',xlabel);self.roi_e_plot.setLabel('left',ylabel)
        for cyc_i,cs,ce in _cyc_ranges:
            # Bottom-right plot: draw each selected cycle separately and split
            # forward/reverse sweeps when using potential as x-axis.
            ne=min(re_base.size,self._frame_E.size,self._frame_t.size,ce)-cs
            if ne<2:continue
            E_sl=self._frame_E[cs:cs+ne];t_sl=self._frame_t[cs:cs+ne];roi_sl=re_base[cs:cs+ne]
            ex,ey,xlabel,ylabel=self._compute_roi_e_data(roi_sl,E_sl,t_sl)
            m=np.isfinite(ex)&np.isfinite(ey);xr=ex[m];yr=ey[m]
            if xr.size<2:continue
            pen=self._cycle_pen(cyc_i,width=2) if self._cycle_colors else pg.mkPen(color='w',width=2)
            if _e_axis_is_E:
                segs=_split_sweeps(xr,yr)
                if not segs:continue
                xcat=np.concatenate([np.append(s[0],np.nan) for s in segs])
                ycat=np.concatenate([np.append(s[1],np.nan) for s in segs])
                curve=self.roi_e_plot.plot(xcat,ycat,pen=pen)
                self._roi_e_cyc_curves.append(curve)
                xl_c=[];yl_c=[]
                for s in segs:
                    if s[0].size>=4:
                        try:
                            xl_c.append(np.append(s[0],np.nan))
                            yl_c.append(np.append(self._lowess_cached(s[0],s[1],_fe),np.nan))
                        except Exception:pass
                if xl_c:
                    lc=self.roi_e_plot.plot(np.concatenate(xl_c),np.concatenate(yl_c),pen=self._lowess_pen())
                    self._roi_e_cyc_lowess.append(lc)
            else:
                curve=self.roi_e_plot.plot(xr,yr,pen=pen)
                self._roi_e_cyc_curves.append(curve)
                if xr.size>=4:
                    try:
                        lc=self.roi_e_plot.plot(xr,self._lowess_cached(xr,yr,_fe),pen=self._lowess_pen())
                        self._roi_e_cyc_lowess.append(lc)
                    except Exception:pass
        if str(_e_mode).endswith("R/R vs t"):
            zone_re=list(getattr(self,"_zone_drr",[]) or [])
        elif _e_area_mode:
            if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
            zone_re=list(getattr(self,"_zone_intensity_per_area",[]) or [])
            if not zone_re:
                zone_re=self._divide_zone_traces_by_area(
                    list(getattr(self,"_zone_intensity",[]) or []))
        else:
            zone_re=list(getattr(self,"_zone_intensity",[]) or [])
        if zone_re:
            # Replace the summed ROI-vs-potential/time curve with one curve per
            # ROI zone. Each trace uses the same color as its rectangle.
            for c in self._roi_e_cyc_curves:
                try:self.roi_e_plot.removeItem(c)
                except Exception:pass
            for c in self._roi_e_cyc_lowess:
                try:self.roi_e_plot.removeItem(c)
                except Exception:pass
            self._roi_e_cyc_curves=[];self._roi_e_cyc_lowess=[]
            for z_idx,trace in enumerate(zone_re):
                r,g,b=self._zone_color(z_idx)
                pen=pg.mkPen(color=(r,g,b),width=2)
                lpen=pg.mkPen(color=(r,g,b,180),width=1,style=QtCore.Qt.PenStyle.DotLine)
                for _cyc_i,cs,ce in _cyc_ranges:
                    ne=min(trace.size,self._frame_E.size,self._frame_t.size,ce)-cs
                    if ne<2:continue
                    E_sl=self._frame_E[cs:cs+ne];t_sl=self._frame_t[cs:cs+ne];roi_sl=trace[cs:cs+ne]
                    ex,ey,xlabel,ylabel=self._compute_roi_e_data(roi_sl,E_sl,t_sl)
                    m=np.isfinite(ex)&np.isfinite(ey);xr=ex[m];yr=ey[m]
                    if xr.size<2:continue
                    if _e_axis_is_E:
                        segs=_split_sweeps(xr,yr)
                        if not segs:continue
                        xcat=np.concatenate([np.append(s[0],np.nan) for s in segs])
                        ycat=np.concatenate([np.append(s[1],np.nan) for s in segs])
                        self._roi_e_cyc_curves.append(self.roi_e_plot.plot(xcat,ycat,pen=pen))
                        xl_c=[];yl_c=[]
                        if self._show_lowess_for_zone(z_idx):
                            for s in segs:
                                if s[0].size>=4:
                                    try:
                                        xl_c.append(np.append(s[0],np.nan))
                                        yl_c.append(np.append(self._lowess_cached(s[0],s[1],_fe),np.nan))
                                    except Exception:pass
                            if xl_c:
                                self._roi_e_cyc_lowess.append(
                                    self.roi_e_plot.plot(np.concatenate(xl_c),np.concatenate(yl_c),
                                                         pen=self._zone_lowess_pen(z_idx)))
                    else:
                        self._roi_e_cyc_curves.append(self.roi_e_plot.plot(xr,yr,pen=pen))
                        if xr.size>=4 and self._show_lowess_for_zone(z_idx):
                            try:self._roi_e_cyc_lowess.append(self.roi_e_plot.plot(xr,self._lowess_cached(xr,yr,_fe),pen=self._zone_lowess_pen(z_idx)))
                            except Exception:pass
        self.roi_e_plot.setLabel('bottom',xlabel);self.roi_e_plot.setLabel('left',ylabel)
        titles={'R vs E':'Reflectance vs Potential',
                'R / area vs E':'Reflectance / Area vs Potential',
                'dR/dV vs E':'dReflectance/dV vs Potential',
                'Optical Current vs E':'Optical Current vs Potential',
                'dR/dt vs t':'dReflectance/dt vs Time',
                'ΔR/R vs t':'ΔR/R vs Time'}
        self.roi_e_plot.setTitle(titles.get(_e_mode,''))
        self._update_an_plots()

__all__ = [name for name in globals() if not name.startswith("__")]
