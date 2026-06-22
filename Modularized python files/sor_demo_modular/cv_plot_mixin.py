"""CVPlotMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class CVPlotMixin:
    def _update_lowess_color_btn(self):
        c=self._lowess_color
        luma=0.299*c.red()+0.587*c.green()+0.114*c.blue()
        fg="black" if luma>128 else "white"
        self.btn_lowess_color.setStyleSheet(
            f"QPushButton{{background:rgb({c.red()},{c.green()},{c.blue()});"
            f"color:{fg};font-size:11px;padding:2px 4px;border-radius:3px;}}")

    def _on_lowess_color_pick(self):
        c=QtWidgets.QColorDialog.getColor(self._lowess_color,self,"LOWESS curve colour")
        if c.isValid():
            self._lowess_color=c
            self._update_lowess_color_btn()
            self._apply_lowess_pen()
            self._update_roi_plots()

    def _on_lowess_style_changed(self,*_):
        self._apply_lowess_pen()
        self._update_roi_plots()

    def _on_lowess_frac_changed(self,*_):
        """Clear LOWESS cache when the window fraction changes so new values are computed."""
        self._lowess_cache={}
        self._update_roi_plots()

    def _lowess_pen(self):
        c=self._lowess_color
        w=getattr(self,'lowess_thickness',None)
        thickness=w.value() if w else 2
        return pg.mkPen(color=(c.red(),c.green(),c.blue()),width=thickness)

    def _apply_lowess_pen(self):
        pen=self._lowess_pen()
        if hasattr(self,'roi_t_lowess'):self.roi_t_lowess.setPen(pen)
        if hasattr(self,'roi_e_lowess'):self.roi_e_lowess.setPen(pen)

    def _on_smooth_mode_toggle(self,checked):
        self.smooth_sg_btn.setText("SG" if checked else "Boxcar")
        self._update_all_plots()

    def _apply_smooth(self,arr,window):
        if window<2:return arr
        use_sg=self.smooth_sg_btn.isChecked()
        return _smooth(arr,window,use_savgol=use_sg)

    def _sm(self,arr):
        return self._apply_smooth(arr,self.smooth_sp.value())

    def _get_upper_v(self):
        try:return float(self.cfg["potentiostat"].get("upper_v","0.25"))
        except:return None

    def _get_cycle_slice(self):
        cyc=self.cycle_sp.value()
        if cyc==0:return 0,min(self._frame_E.size,self._frame_t.size,self._roi_intensity.size)
        cycles=(self._cached_cycles
                or _detect_cycles(self._frame_E,upper_v=self._get_upper_v()))
        if cyc>len(cycles):cyc=len(cycles)
        if cyc<1:return 0,min(self._frame_E.size,self._frame_t.size)
        s,e=cycles[cyc-1];return s,e

    def _break_at_steps(self,xdata,ydata,t_wall=None):
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
        if live:
            self._update_cv_live(self._E_cv, self._I_cv, self._t_cv)
            return
        mode=self.cv_mode.currentText();sw=self.smooth_sp.value()
        MAX_CV=3000
        if mode=="I vs E":
            self.cv_plot.setLabel("bottom","Potential (V)")
            yvar=self.cv_y.currentText()
            self.cv_plot.setLabel("left",yvar)
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
            self.cv_plot.setLabel("bottom","Time (s)");self.cv_plot.setLabel("left","Potential (V)")
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
        MAX_PTS = 4000
        mode = self.cv_mode.currentText()
        if mode == "I vs E":
            self.cv_plot.setLabel("bottom", "Potential (V)")
            self.cv_plot.setLabel("left", "Current (A)")
            xdata = E
            ydata = I
        else:
            self.cv_plot.setLabel("bottom", "Time (s)")
            self.cv_plot.setLabel("left", "Potential (V)")
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

        if mode=='dR/dV vs E':
            valid=np.isfinite(E)&np.isfinite(roi)
            if np.sum(valid)<5:return E,np.full_like(E,np.nan),'Potential (V)','dR/dV'
            idx=np.argsort(E[valid]);Es=E[valid][idx];rs=roi[valid][idx]
            w=_sg_window(len(rs))
            drdv=_sg_deriv(rs,Es,w)
            y=np.full_like(roi,np.nan);y[np.where(valid)[0][idx]]=drdv
            return E,y,'Potential (V)','dR/dV'
        elif mode=='Optical Current vs E':
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
        return E,roi,'Potential (V)','Reflectance'

    def _lowess_cached(self, x, y, frac):
        """LOWESS with an in-memory cache keyed on (data identity, length, frac).
        Recomputing LOWESS on every slider move / plot resize is expensive
        (O(n²)).  The cache stores the smoothed y for the current dataset;
        it is cleared whenever new data is loaded, Analyze is re-run, or the
        window fraction spinbox changes.
        """
        if not hasattr(self, "_lowess_cache"):
            self._lowess_cache = {}
        key = (id(x), len(x), round(frac, 4))
        if key not in self._lowess_cache:
            self._lowess_cache[key] = _lowess_smooth(x, y, frac=frac)
        return self._lowess_cache[key]

    def _update_roi_plots(self,*_):
        sw=self.smooth_sp.value()
        _frac_t=getattr(self,'lowess_frac_t',None)
        _ft=_frac_t.value() if _frac_t else 0.05
        _frac_e=getattr(self,'lowess_frac_e',None)
        _fe=_frac_e.value() if _frac_e else 0.08
        rt_y=self._get_var(self.roi_t_y.currentText())
        n=min(rt_y.size,self._frame_t.size)
        if n>0:
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
            self.roi_t_plot.setLabel('left','Reflectance')
        else:
            self.roi_t_curve.setData([],[]);self.roi_t_smooth.setData([],[])
            self.roi_t_lowess.setData([],[])
        _e_mode=self.roi_e_mode.currentText()
        _e_axis_is_E=_e_mode not in ('dR/dt vs t','ΔR/R vs t')
        re_cb=getattr(self,"roi_e_cycle_cb",None)
        _re_sel=re_cb.currentText() if re_cb else "All"
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
        self.roi_e_plot.setLabel('bottom',xlabel);self.roi_e_plot.setLabel('left',ylabel)
        titles={'R vs E':'Reflectance vs Potential',
                'dR/dV vs E':'dReflectance/dV vs Potential',
                'Optical Current vs E':'Optical Current vs Potential',
                'dR/dt vs t':'dReflectance/dt vs Time',
                'ΔR/R vs t':'ΔR/R vs Time'}
        self.roi_e_plot.setTitle(titles.get(_e_mode,''))
        self._update_an_plots()

__all__ = [name for name in globals() if not name.startswith("__")]
