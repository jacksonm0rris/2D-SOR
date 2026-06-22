"""ExportMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class ExportMixin:
    def _cycle_pen(self, cyc_i, width=2):
        """Return a pen for cycle cyc_i, using stored cycle colours if available."""
        if cyc_i in self._cycle_colors:
            c = self._cycle_colors[cyc_i]
            return pg.mkPen(color=(c.red(), c.green(), c.blue()), width=width)
        return pg.mkPen(color='w', width=width)

    def _export_plots(self):
        if self._frame_E.size==0 and self._E_all.size==0:
            QtWidgets.QMessageBox.information(self,"Export","No data.");return
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Export","sor_export.zip","ZIP (*.zip);;All (*)")
        if not path:return
        try:import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
        except:QtWidgets.QMessageBox.critical(self,"Export","pip install matplotlib");return
        self.traffic.setState(TrafficLight.YELLOW);QtWidgets.QApplication.processEvents()
        try:
            with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as zf:
                def _sf(fig,name):
                    buf=io.BytesIO();fig.savefig(buf,format="png",dpi=200,bbox_inches="tight")
                    plt.close(fig);zf.writestr(name,buf.getvalue())
                _E_exp = self._E_cv if self._E_cv.size > 0 else self._E_all
                _I_exp = self._I_cv if self._I_cv.size > 0 else self._I_all
                _t_exp = self._t_cv if self._t_cv.size > 0 else self._t_all
                fig,ax=plt.subplots(figsize=(8,6));m=np.isfinite(_E_exp)&np.isfinite(_I_exp)
                ax.plot(_E_exp[m],_I_exp[m],"b-",lw=1);ax.set_xlabel("E (V)");ax.set_ylabel("I (A)");ax.set_title("CV")
                ax.ticklabel_format(axis="y",style="sci",scilimits=(-3,3));_sf(fig,"cv.png")
                lines=["E_V\tI_A\tt_s\n"]
                for i in range(len(_E_exp)):
                    lines.append(f"{_E_exp[i]:.10g}\t{_I_exp[i]:.10g}\t{_t_exp[i]:.10g}\n")
                zf.writestr("cv_data.txt","".join(lines))
                lf=self._last_frame
                if lf is None and self._memmap is not None and self._n_frames>0:
                    lf=np.asarray(self._memmap[-1],dtype=np.float32)
                if lf is not None:
                    fig,ax=plt.subplots(figsize=(8,6));im=ax.imshow(lf,aspect="equal",cmap=self.cmap_cb.currentText())
                    plt.colorbar(im,ax=ax);ax.set_title("Last frame");_sf(fig,"camera.png")
                roi=self._roi_intensity;n=min(roi.size,self._frame_t.size,self._frame_E.size)
                if n>0:
                    _t_m=np.isfinite(self._frame_t[:n])&np.isfinite(roi[:n])
                    _xt=self._frame_t[:n][_t_m];_yt=roi[:n][_t_m]
                    fig,ax=plt.subplots(figsize=(8,6))
                    plt.rcParams.update({"font.size":12})
                    ax.plot(_xt,_yt,"-",color="gray",lw=1,alpha=0.35)
                    if _xt.size>=4:
                        _yl_t=_lowess_smooth(_xt,_yt,frac=0.05)
                        ax.plot(_xt,_yl_t,"-",color="#2471a3",lw=2)
                    ax.set_xlabel("Time (s)",fontsize=12)
                    ax.set_ylabel("Normalized Reflectance",fontsize=12)
                    _sf(fig,"roi_t.png")
                    _e_m=np.isfinite(self._frame_E[:n])&np.isfinite(roi[:n])
                    _xe=self._frame_E[:n][_e_m];_ye=roi[:n][_e_m]
                    fig,ax=plt.subplots(figsize=(8,6))
                    plt.rcParams.update({"font.size":12})
                    if _xe.size>=2:
                        _segs_e=_split_sweeps(_xe,_ye)
                        if _segs_e:
                            for _seg in _segs_e:
                                ax.plot(_seg[0],_seg[1],"-",color="#2471a3",lw=1.5)
                        else:
                            ax.plot(_xe,_ye,"-",color="#2471a3",lw=1.5)
                    else:
                        ax.plot(_xe,_ye,"-",color="#2471a3",lw=1.5)
                    ax.set_xlabel("Potential (V)",fontsize=12)
                    ax.set_ylabel("Normalized Reflectance",fontsize=12)
                    _sf(fig,"roi_e.png")
                    roi_raw=roi[:n]
                    ref_val=roi_raw[0] if (roi_raw.size>0 and np.isfinite(roi_raw[0]) and roi_raw[0]!=0) else np.nan
                    roi_norm=roi_raw/ref_val if np.isfinite(ref_val) else np.full(n,np.nan)
                    t_col=self._frame_t[:n]
                    E_col=self._frame_E[:n]
                    I_col=self._frame_I[:n] if self._frame_I.size>=n else np.full(n,np.nan)
                    _pwr_raw=self._frame_power if self._frame_power.size>=n else np.full(self._frame_power.size,np.nan)
                    pwr_col=self._stabilize_power(_pwr_raw)[:n] if _pwr_raw.size>=n else np.full(n,np.nan)
                    def _sg_w(m):w=max(11,int(round(0.01*m))|1);return min(w if w%2==1 else w+1,m if m%2==1 else m-1)
                    def _sg_d(y,x,w):
                        try:
                            from scipy.signal import savgol_filter
                            dx=float(np.median(np.abs(np.diff(x))));dx=dx if dx>0 else 1.0
                            return savgol_filter(y,window_length=w,polyorder=min(3,w-1),deriv=1,delta=dx)
                        except Exception:return np.gradient(y,x)
                    drdv_col=np.full(n,np.nan)
                    valid_e=np.isfinite(E_col)&np.isfinite(roi_raw)
                    if np.sum(valid_e)>=5:
                        idx_e=np.argsort(E_col[valid_e])
                        Es=E_col[valid_e][idx_e];rs=roi_raw[valid_e][idx_e]
                        drdv_col[np.where(valid_e)[0][idx_e]]=_sg_d(rs,Es,_sg_w(len(rs)))
                    drdt_col=np.full(n,np.nan)
                    valid_t=np.isfinite(t_col)&np.isfinite(roi_raw)
                    if np.sum(valid_t)>=5:
                        idx_t=np.argsort(t_col[valid_t])
                        ts=t_col[valid_t][idx_t];rs_t=roi_raw[valid_t][idx_t]
                        drdt_col[np.where(valid_t)[0][idx_t]]=_sg_d(rs_t,ts,_sg_w(len(rs_t)))
                    n_zones=len(self._zone_intensity)
                    zone_cols=[zi[:n] if zi.size>=n else np.full(n,np.nan)
                               for zi in self._zone_intensity]
                    zone_drr_cols=[zd[:n] if zd.size>=n else np.full(n,np.nan)
                                   for zd in self._zone_drr]
                    drr_summed=self._drr_intensity[:n] if self._drr_intensity.size>=n else np.full(n,np.nan)
                    _lowess_t_col=np.full(n,np.nan);_lowess_e_col=np.full(n,np.nan)
                    if np.sum(valid_t)>=4:
                        _lx=t_col[valid_t];_ly=roi_raw[valid_t]
                        _lsm=_lowess_smooth(_lx,_ly,frac=0.05)
                        _lowess_t_col[np.where(valid_t)[0]]=_lsm
                    if np.sum(valid_e)>=4:
                        _lxi=np.argsort(E_col[valid_e]);_lxe=E_col[valid_e][_lxi];_lye=roi_raw[valid_e][_lxi]
                        _lsme=_lowess_smooth(_lxe,_lye,frac=0.08)
                        _lowess_e_col[np.where(valid_e)[0][_lxi]]=_lsme
                    hdr_parts=["R_raw","R_norm_to_first",
                               "R_dRR",
                               "dR_dV","dR_dt",
                               "R_LOWESS_vs_t","R_LOWESS_vs_E",
                               "time_s","E_V","I_A","Power_W"]
                    for k in range(n_zones):hdr_parts.append(f"Zone{k+1}_R")
                    for k in range(n_zones):hdr_parts.append(f"Zone{k+1}_dRR")
                    ref_lines=["\t".join(hdr_parts)+"\n"]
                    for i in range(n):
                        row=(
                            f"{roi_raw[i]:.10g}\t{roi_norm[i]:.10g}\t"
                            f"{drr_summed[i]:.10g}\t"
                            f"{drdv_col[i]:.10g}\t{drdt_col[i]:.10g}\t"
                            f"{_lowess_t_col[i]:.10g}\t{_lowess_e_col[i]:.10g}\t"
                            f"{t_col[i]:.10g}\t{E_col[i]:.10g}\t"
                            f"{I_col[i]:.10g}\t{pwr_col[i]:.10g}")
                        for zc in zone_cols:row+=f"\t{zc[i]:.10g}"
                        for zd in zone_drr_cols:row+=f"\t{zd[i]:.10g}"
                        ref_lines.append(row+"\n")
                    zf.writestr("reflectance_data.txt","".join(ref_lines))
                if self._cl_means is not None and self._engine is not None:
                    eng=self._engine;means=self._cl_means;K=means.shape[0];nf=min(means.shape[1],n)
                    lbl=eng.get_label_map()
                    if lbl is not None:
                        lines=["# Cluster labels\n"]
                        for row in lbl:lines.append("\t".join(str(int(x)) for x in row)+"\n")
                        zf.writestr("cluster_labels.txt","".join(lines))
                    hdr="frame_t\tframe_E\t"+"\t".join(f"C{k}" for k in range(K))+"\n";lines=[hdr]
                    for i in range(nf):
                        tv=self._frame_t[i] if i<self._frame_t.size else np.nan
                        ev=self._frame_E[i] if i<self._frame_E.size else np.nan
                        vals="\t".join(f"{means[k,i]:.10g}" for k in range(K))
                        lines.append(f"{tv:.10g}\t{ev:.10g}\t{vals}\n")
                    zf.writestr("cluster_means.txt","".join(lines))
                    ov=eng.get_cluster_overlay(alpha=180)
                    if ov is not None:
                        fig,ax=plt.subplots(figsize=(8,6));ax.imshow(ov);ax.set_title(f"K={K}");_sf(fig,"clusters.png")
                    ev=eng.get_explained_var_np()
                    if ev is not None:
                        lines=["PC\tvar\n"]+[f"{i}\t{ev[i]:.10g}\n" for i in range(len(ev))]
                        zf.writestr("pca_var.txt","".join(lines))
                    rgb=eng.get_pca_rgb_overlay(alpha=200)
                    if rgb is not None:
                        fig,ax=plt.subplots(figsize=(8,6));ax.imshow(rgb);ax.set_title("PCA RGB");_sf(fig,"pca_rgb.png")
            QtWidgets.QMessageBox.information(self,"Export",f"Saved:\n{path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self,"Export",f"{e}\n\n{traceback.format_exc()}")
        self.traffic.setState(TrafficLight.GREEN)

    def _export_frames(self):
        if self._memmap is None or self._n_frames==0:
            QtWidgets.QMessageBox.information(self,"Export","No frames.");return
        if tifffile is None:QtWidgets.QMessageBox.critical(self,"Export","pip install tifffile");return
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Frames","sor_frames_normalised.tif","TIFF (*.tif);;All (*)")
        if not path:return
        self.traffic.setState(TrafficLight.YELLOW);QtWidgets.QApplication.processEvents()
        try:
            base=os.path.splitext(path)[0]
            raw_norm_path=path
            drr_norm_path=base+"_dRR_normalised.tif"
            self.status_lbl.setText("Computing statistics...")
            QtWidgets.QApplication.processEvents()
            n_stat=min(50,self._n_frames)
            stat_idx=np.linspace(0,self._n_frames-1,n_stat,dtype=int)
            raw_vals=np.asarray(self._memmap[stat_idx],dtype=np.float32).ravel()
            raw_vals=raw_vals[np.isfinite(raw_vals)]
            raw_lo=float(np.percentile(raw_vals,1)) if raw_vals.size>0 else 0.0
            raw_hi=float(np.percentile(raw_vals,99)) if raw_vals.size>0 else 1.0
            raw_span=max(raw_hi-raw_lo,1e-10)
            if self._n_frames>1:
                ref_s=np.asarray(self._memmap[0],dtype=np.float32)
                safe_s=np.where(np.abs(ref_s)<1e-10,np.ones_like(ref_s),ref_s)
                curr_s=np.asarray(self._memmap[stat_idx],dtype=np.float32)
                drr_vals=((curr_s-ref_s[np.newaxis])/safe_s[np.newaxis]).ravel()
                drr_vals=drr_vals[np.isfinite(drr_vals)]
                drr_lo=float(np.percentile(drr_vals,1)) if drr_vals.size>0 else -0.1
                drr_hi=float(np.percentile(drr_vals,99)) if drr_vals.size>0 else  0.1
            else:
                drr_lo=-0.1;drr_hi=0.1
            drr_span=max(drr_hi-drr_lo,1e-10)
            self.status_lbl.setText(
                f"Raw [{raw_lo:.4g}, {raw_hi:.4g}]  dR/R [{drr_lo:.4g}, {drr_hi:.4g}]  writing...")
            QtWidgets.QApplication.processEvents()
            if self._n_frames > 1:
                with tifffile.TiffWriter(raw_norm_path,bigtiff=True) as tw_raw, \
                     tifffile.TiffWriter(drr_norm_path,bigtiff=True) as tw_drr:
                    ref_frame=np.asarray(self._memmap[0],dtype=np.float32)
                    safe_ref=np.where(np.abs(ref_frame)<1e-10,np.ones_like(ref_frame),ref_frame)
                    for i0 in range(0,self._n_frames,50):
                        i1=min(self._n_frames,i0+50)
                        curr=np.asarray(self._memmap[i0:i1],dtype=np.float32)
                        drr=(curr-ref_frame[np.newaxis])/safe_ref[np.newaxis]
                        raw_n=np.clip((curr-raw_lo)/raw_span,0.0,1.0)
                        drr_n=np.clip((drr-drr_lo)/drr_span,0.0,1.0)
                        for k in range(curr.shape[0]):
                            tw_raw.write(raw_n[k])
                            tw_drr.write(drr_n[k])
                        if (i0//50)%5==0:
                            self.status_lbl.setText(f"Writing {i1}/{self._n_frames}")
                            QtWidgets.QApplication.processEvents()
                QtWidgets.QMessageBox.information(self,"Export",
                    f"Normalised raw [{raw_lo:.4g}, {raw_hi:.4g}]:\n{raw_norm_path}\n\n"
                    f"Normalised dR/R [{drr_lo:.4g}, {drr_hi:.4g}]:\n{drr_norm_path}")
            else:
                with tifffile.TiffWriter(raw_norm_path,bigtiff=True) as tw_raw:
                    blk0=np.asarray(self._memmap[0:1],dtype=np.float32)
                    tw_raw.write(np.clip((blk0[0]-raw_lo)/raw_span,0.0,1.0))
                QtWidgets.QMessageBox.information(self,"Export",
                    f"Normalised raw:\n{raw_norm_path}")
            self.status_lbl.setText("Done.")
        except Exception as e:QtWidgets.QMessageBox.critical(self,"Export",str(e))
        self.traffic.setState(TrafficLight.GREEN)

    def closeEvent(self,event):
        if self._worker and self._worker.isRunning():self._worker.request_stop();self._worker.wait(3000)
        self._stop_frame_processor()
        self._memmap=None
        d=self.cfg["display"];d["colormap"]=self.cmap_cb.currentText()
        d["auto_levels"]=str(self.auto_lev.isChecked()).lower()
        d["level_min"]=str(self.lmin.value());d["level_max"]=str(self.lmax.value())
        d["cv_y"]=self.cv_y.currentText();d["roi_t_y"]="R" if self.roi_t_y.currentText() in ("ROI Sum","R") else self.roi_t_y.currentText()
        d["smooth_window"]=str(self.smooth_sp.value())
        d["smooth_mode"]="savgol" if self.smooth_sg_btn.isChecked() else "boxcar"
        save_settings(self.cfg);super().closeEvent(event)

__all__ = [name for name in globals() if not name.startswith("__")]
