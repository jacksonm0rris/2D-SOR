"""ImageDisplayMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class ImageDisplayMixin:
    def _refresh_exp_lbl(self):
        exp=self.cfg["potentiostat"].get("experiment_type",EXP_CV)
        self.exp_type_lbl.setText(f"Experiment: {exp}")

    def _rebuild_cbs(self):
        for cb in self.an_cbs:self.an_cl_lay.removeWidget(cb);cb.deleteLater()
        self.an_cbs=[];K=self.an_k.value()
        for k in range(K):
            r,g,b=CLUSTER_COLORS[k%len(CLUSTER_COLORS)]
            cb=QtWidgets.QCheckBox(f"C{k}")
            cb.setStyleSheet(f"QCheckBox{{color:rgb({r},{g},{b});font-weight:bold;font-size:11px}}")
            cb.setChecked(k in self._cl_sel);cb.stateChanged.connect(self._on_cl_sel)
            self.an_cl_lay.addWidget(cb);self.an_cbs.append(cb)

    def _get_var(self,name):
        if name=="I (A)":return self._frame_I
        elif name=="E (V)":return self._frame_E
        elif name in ("R","ROI Sum"):return self._roi_intensity
        elif name=="ΔR/R":return self._drr_intensity
        elif name=="Power (W)":return self._frame_power
        elif name=="dE/dt":
            if self._frame_E.size<2:return np.array([])
            return np.gradient(self._frame_E,self._frame_t) if self._frame_t.size==self._frame_E.size else np.array([])
        elif name=="Frame #":return np.arange(self._n_frames,dtype=np.float64)
        return np.array([])

    def _apply_cmap(self,name=None):
        nm=((name or self.cmap_cb.currentText())or"viridis").strip().lower()
        if nm=="grey":nm="gray"
        lut=None
        try:import matplotlib.cm as cm;m=cm.get_cmap(nm);x=np.linspace(0,1,256);lut=(m(x)*255).astype(np.ubyte)[:,:3].copy()
        except:pass
        if lut is None:
            try:
                cmap=pg.colormap.get(nm)
                try:lut=cmap.getLookupTable(0.0,1.0,256)
                except:lut=cmap.getLookupTable(256)
                lut=np.asarray(lut)
                if lut.dtype!=np.ubyte:lut=np.clip(lut,0,255).astype(np.ubyte)
                if lut.ndim==2 and lut.shape[1]==4:lut=lut[:,:3].copy()
            except:pass
        if lut is None:g=np.arange(256,dtype=np.ubyte);lut=np.stack([g,g,g],axis=1)
        self.img_item.setLookupTable(lut)

    def _ml(self):
        if self.auto_lev.isChecked():return
        lo,hi=float(self.lmin.value()),float(self.lmax.value())
        if hi>lo:self.img_item.setLevels([lo,hi])

    def _get_display_frame(self,frame):
        if not hasattr(self,"btn_drr") or not self.btn_drr.isChecked():
            return self._apply_rotation(frame)
        if self._memmap is None or self._n_frames < 1:
            return frame
        ref = np.asarray(self._memmap[0], dtype=np.float32)
        if ref.shape != frame.shape:
            return frame
        safe_ref = np.where(np.abs(ref) < 1e-10, np.ones_like(ref), ref)
        drr = (frame.astype(np.float32) - ref) / safe_ref
        return self._apply_rotation(self._spatial_smooth_drr(drr))

    def _rotated_shape(self, orig_hw):
        if not hasattr(self, "rot_sp"):
            return orig_hw
        deg = float(self.rot_sp.value()) % 360
        h, w = orig_hw
        if abs(deg) < 0.05:
            return (h, w)
        if abs(deg - 90) < 0.05 or abs(deg - 270) < 0.05:
            return (w, h)
        if abs(deg - 180) < 0.05:
            return (h, w)
        rad = np.deg2rad(deg)
        cos_a, sin_a = abs(np.cos(rad)), abs(np.sin(rad))
        nh = int(h * cos_a + w * sin_a + 0.5)
        nw = int(w * cos_a + h * sin_a + 0.5)
        return (nh, nw)

    def _zone_mask_in_original(self, zone, orig_hw):
        deg = float(self.rot_sp.value()) % 360 if hasattr(self, "rot_sp") else 0.0
        rot_hw = self._rotated_shape(orig_hw)
        ys, xs = self._zone_slices(zone, rot_hw)
        rot_mask = np.zeros(rot_hw, dtype=np.float32)
        rot_mask[ys, xs] = 1.0
        if abs(deg) < 0.05:
            orig_mask = rot_mask
        elif abs(deg - 90) < 0.05:
            orig_mask = np.rot90(rot_mask, -1)
        elif abs(deg - 180) < 0.05:
            orig_mask = np.rot90(rot_mask, 2)
        elif abs(deg - 270) < 0.05:
            orig_mask = np.rot90(rot_mask, 1)
        else:
            try:
                from scipy.ndimage import rotate as sci_rotate
                inv = sci_rotate(rot_mask, -deg, reshape=True, order=0,
                                 mode='constant', cval=0.0)
                ih, iw = orig_hw
                dh = (inv.shape[0] - ih) // 2
                dw = (inv.shape[1] - iw) // 2
                orig_mask = inv[dh:dh + ih, dw:dw + iw]
                orig_mask = orig_mask[:ih, :iw]
            except ImportError:
                rad = np.deg2rad(-deg)
                cos_a, sin_a = np.cos(rad), np.sin(rad)
                rh, rw = rot_hw
                ih, iw = orig_hw
                cy_r, cx_r = rh / 2, rw / 2
                cy_o, cx_o = ih / 2, iw / 2
                orig_mask = np.zeros(orig_hw, dtype=np.float32)
                oy = np.arange(ih, dtype=np.float32) - cy_o
                ox = np.arange(iw, dtype=np.float32) - cx_o
                gx, gy = np.meshgrid(ox, oy)
                rx = (cos_a * gx - sin_a * gy + cx_r).astype(np.int32)
                ry = (sin_a * gx + cos_a * gy + cy_r).astype(np.int32)
                valid = (rx >= 0) & (rx < rw) & (ry >= 0) & (ry < rh)
                orig_mask[valid] = rot_mask[ry[valid], rx[valid]]
        h, w = orig_hw
        orig_mask = orig_mask[:h, :w]
        return orig_mask > 0.5

    def _on_rotation_changed(self,*_):
        self._invalidate_zone_mask_cache()
        if self._memmap is None or self._n_frames==0:return
        val=self.fslider.value()
        if val<self._n_frames:
            self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))

    def _apply_rotation(self,img):
        if not hasattr(self,"rot_sp"):return img
        deg=float(self.rot_sp.value())
        deg=deg%360
        if abs(deg)<0.05:return img
        if abs(deg-90)<0.05:return np.rot90(img,1)
        if abs(deg-180)<0.05:return np.rot90(img,2)
        if abs(deg-270)<0.05:return np.rot90(img,3)
        try:
            from scipy.ndimage import rotate as sci_rotate
            return sci_rotate(img,deg,reshape=True,order=1,
                              mode='constant',cval=float(np.nanmedian(img))).astype(np.float32)
        except ImportError:pass
        rad=np.deg2rad(deg);cos_a=np.cos(rad);sin_a=np.sin(rad)
        h,w=img.shape
        cy,cx=h/2,w/2
        nh=int(abs(h*cos_a)+abs(w*sin_a)+0.5)
        nw=int(abs(w*cos_a)+abs(h*sin_a)+0.5)
        out=np.full((nh,nw),float(np.nanmedian(img)),dtype=np.float32)
        ncy,ncx=nh/2,nw/2
        ys=np.arange(nh,dtype=np.float32)-ncy
        xs=np.arange(nw,dtype=np.float32)-ncx
        gx,gy=np.meshgrid(xs,ys)
        src_x=(cos_a*gx+sin_a*gy+cx).astype(np.int32)
        src_y=(-sin_a*gx+cos_a*gy+cy).astype(np.int32)
        valid=(src_x>=0)&(src_x<w)&(src_y>=0)&(src_y<h)
        out[valid]=img[src_y[valid],src_x[valid]]
        return out

    def _spatial_smooth_drr(self,drr):
        if not hasattr(self,"drr_smooth_cb"):return drr
        mode=self.drr_smooth_cb.currentText()
        if mode=="None":return drr
        r=int(self.drr_smooth_sp.value())
        if mode=="Gaussian":
            try:
                from scipy.ndimage import gaussian_filter
                return gaussian_filter(drr,sigma=r).astype(np.float32)
            except ImportError:
                k=np.exp(-0.5*(np.arange(-r,r+1)/max(r/2,0.5))**2)
                k=k/k.sum()
                out=np.apply_along_axis(lambda x:np.convolve(x,k,"same"),0,drr)
                out=np.apply_along_axis(lambda x:np.convolve(x,k,"same"),1,out)
                return out.astype(np.float32)
        elif mode=="Median":
            try:
                from scipy.ndimage import median_filter
                return median_filter(drr,size=2*r+1).astype(np.float32)
            except ImportError:
                pass
        elif mode=="Bilateral":
            try:
                import cv2
                finite=np.nan_to_num(drr,nan=0.0,posinf=0.0,neginf=0.0)
                vmin,vmax=float(finite.min()),float(finite.max())
                span=max(vmax-vmin,1e-10)
                norm=((finite-vmin)/span).astype(np.float32)
                smoothed=cv2.bilateralFilter(norm,d=2*r+1,
                    sigmaColor=0.1,sigmaSpace=float(r))
                return (smoothed*span+vmin).astype(np.float32)
            except ImportError:
                try:
                    from scipy.ndimage import gaussian_filter
                    return gaussian_filter(drr,sigma=r).astype(np.float32)
                except ImportError:
                    pass
        return drr

    def _show_frame(self,frame):
        display=self._get_display_frame(np.asarray(frame,dtype=np.float32))
        self.img_item.setImage(display,autoLevels=False)
        if self.auto_lev.isChecked():
            v=display[np.isfinite(display)]
            if v.size>0:
                drr_mode=hasattr(self,"btn_drr") and self.btn_drr.isChecked()
                if drr_mode:
                    bound=float(np.percentile(np.abs(v),98))
                    if bound<1e-10:bound=float(np.max(np.abs(v))) or 1e-6
                    lo=-bound;hi=bound
                else:
                    lo=float(np.percentile(v,1));hi=float(np.percentile(v,99))
                if np.isfinite(lo) and np.isfinite(hi) and hi>lo:
                    self.img_item.setLevels([lo,hi])
                    self.lmin.blockSignals(True);self.lmax.blockSignals(True)
                    self.lmin.setValue(lo);self.lmax.setValue(hi)
                    self.lmin.blockSignals(False);self.lmax.blockSignals(False)
        else:self._ml()

    def _on_auto_lev_toggle(self,checked=None):
        if self._memmap is None or self._n_frames==0:return
        val=self.fslider.value()
        if val<self._n_frames:
            self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))

    def _on_drr_toggle(self,checked=None):
        is_on=hasattr(self,"btn_drr") and self.btn_drr.isChecked()
        if hasattr(self,"drr_smooth_row"):self.drr_smooth_row.setVisible(is_on)
        if self._memmap is None or self._n_frames==0:return
        val=self.fslider.value()
        if val<self._n_frames:
            self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))

    def _on_slider(self,val):
        if self._memmap is None or val>=self._n_frames:return
        self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))
        self.fslider_lbl.setText(f"{val}/{self._n_frames-1}");self._update_mk(val)

    def _update_mk(self,fi):
        E=self._frame_E[fi] if fi<self._frame_E.size else np.nan
        t=self._frame_t[fi] if fi<self._frame_t.size else np.nan
        cv_yv=self._get_var(self.cv_y.currentText())
        cv_y_val=cv_yv[fi] if fi<cv_yv.size else np.nan
        if fi<self._frame_t_wall.size and self._t_wall_echem.size>0:
            t_cv=self._frame_t_wall[fi]-self._t_wall_echem[0]
        else:t_cv=t
        mode=self.cv_mode.currentText()
        if mode=="I vs E" and np.isfinite(E) and np.isfinite(cv_y_val):self.cv_mk.setData([E],[cv_y_val])
        elif mode=="E vs t" and np.isfinite(t_cv) and np.isfinite(E):self.cv_mk.setData([t_cv],[E])
        else:self.cv_mk.setData([],[])
        rt_yv=self._get_var(self.roi_t_y.currentText());rt_val=rt_yv[fi] if fi<rt_yv.size else np.nan
        if np.isfinite(t) and np.isfinite(rt_val):self.roi_t_mk.setData([t],[rt_val])
        else:self.roi_t_mk.setData([],[])
        re_yv=self._roi_intensity;re_val=re_yv[fi] if fi<re_yv.size else np.nan
        if np.isfinite(E) and np.isfinite(re_val):self.roi_e_mk.setData([E],[re_val])
        else:self.roi_e_mk.setData([],[])

    def _update_all_plots(self,*_):
        self._update_cv();self._update_roi_plots()

__all__ = [name for name in globals() if not name.startswith("__")]
