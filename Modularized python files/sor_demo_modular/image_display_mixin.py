"""ImageDisplayMixin methods for the main window.

This mixin controls how camera frames are displayed. It handles colormaps,
manual/automatic intensity levels, optional display-only dR/R, image rotation,
and the yellow marker that shows the current frame on plots.
"""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class ImageDisplayMixin:
    def _refresh_exp_lbl(self):
        # Show the currently selected experiment type in the sidebar.
        exp=self.cfg["potentiostat"].get("experiment_type",EXP_CV)
        self.exp_type_lbl.setText(f"Experiment: {exp}")

    def _rebuild_cbs(self):
        # Rebuild cluster checkboxes when K changes. These checkboxes control
        # which cluster mean traces are shown on the ROI plots.
        for cb in self.an_cbs:self.an_cl_lay.removeWidget(cb);cb.deleteLater()
        self.an_cbs=[];K=self.an_k.value()
        for k in range(K):
            r,g,b=CLUSTER_COLORS[k%len(CLUSTER_COLORS)]
            cb=QtWidgets.QCheckBox(f"C{k}")
            cb.setStyleSheet(f"QCheckBox{{color:rgb({r},{g},{b});font-weight:bold;font-size:11px}}")
            cb.setChecked(k in self._cl_sel);cb.stateChanged.connect(self._on_cl_sel)
            self.an_cl_lay.addWidget(cb);self.an_cbs.append(cb)

    def _get_var(self,name):
        # Translate a user-facing plot variable name into the matching NumPy
        # array. Empty arrays mean "not available yet."
        if name=="I (A)":return self._frame_I
        elif name=="E (V)":return self._frame_E
        elif name in ("R","ROI Sum"):return self._roi_intensity
        elif name=="R / area":
            if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
            area_base=getattr(self,"_roi_intensity_per_area",np.array([],dtype=np.float64))
            return (area_base if area_base.size else
                    (self._roi_intensity/self._total_area()
                     if self._roi_intensity.size else np.array([])))
        elif self._is_drr_name(name):return self._drr_intensity
        elif name=="Power (W)":return self._frame_power
        elif name=="dE/dt":
            if self._frame_E.size<2:return np.array([])
            return np.gradient(self._frame_E,self._frame_t) if self._frame_t.size==self._frame_E.size else np.array([])
        elif name=="Frame #":return np.arange(self._n_frames,dtype=np.float64)
        return np.array([])

    def _apply_cmap(self,name=None):
        # Build a 256-color lookup table for the image display. Matplotlib is
        # tried first, then pyqtgraph, then grayscale fallback.
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
        # Apply manual min/max image levels when Auto is off.
        if self.auto_lev.isChecked():return
        lo,hi=float(self.lmin.value()),float(self.lmax.value())
        if hi>lo:self.img_item.setLevels([lo,hi])

    def _get_display_frame(self,frame):
        # This affects only the image shown in the GUI. It does not modify the
        # saved raw frames or the analysis data.
        if not hasattr(self,"btn_drr") or not self.btn_drr.isChecked():
            return self._apply_rotation(frame)
        # A user-captured reference frame is preferred. If it is absent or has a
        # different shape from the current data, fall back to the first saved
        # frame, which preserves the original behavior.
        ref = getattr(self,"_custom_drr_ref_frame",None)
        if ref is not None:
            ref=np.asarray(ref,dtype=np.float32)
        if ref is None or ref.shape != frame.shape:
            if self._memmap is None or self._n_frames < 1:
                return self._apply_rotation(frame)
            ref = np.asarray(self._memmap[0], dtype=np.float32)
        if ref.shape != frame.shape:
            return self._apply_rotation(frame)
        safe_ref = np.where(np.abs(ref) < 1e-10, np.ones_like(ref), ref)
        # Display dR/R relative to the captured reference frame when present,
        # otherwise relative to the first saved frame:
        # (current frame - reference frame) / reference frame.
        drr = (frame.astype(np.float32) - ref) / safe_ref
        return self._apply_rotation(self._spatial_smooth_drr(drr))

    def _rotated_shape(self, orig_hw):
        # Predict the image shape after rotation. Rectangular frames swap height
        # and width for 90/270 degree rotations.
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
        # Zones are drawn on the displayed image, which may be rotated. Analysis
        # needs masks in the original unrotated camera coordinates, so this
        # converts a displayed ROI back to original frame pixels.
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
        # Rotation changes zone geometry, so cached masks are no longer valid.
        self._invalidate_zone_mask_cache()
        if self._refresh_current_display_frame():return
        val=self.fslider.value()
        if val<self._n_frames:
            self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))

    def _apply_rotation(self,img):
        # Fast exact rotations for 90-degree multiples; interpolation only for
        # arbitrary angles.
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
        # Optional smoothing for display dR/R images. This makes small optical
        # changes easier to see but does not change saved data.
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

    def _preview_bin_factor(self):
        # Parse the live-preview bin dropdown. Invalid or missing values fall
        # back to 1x, meaning full-resolution preview.
        cb=getattr(self,"preview_bin_cb",None)
        txt=cb.currentText() if cb is not None else "1x"
        try:return max(1,int(str(txt).lower().replace("x","").strip()))
        except Exception:return 1

    def _downsample_preview_frame(self,img,factor):
        # Average small blocks for display speed. This is only used for the
        # live preview image; the saved frame stack and analysis arrays are not
        # touched.
        if factor<=1:return img
        h,w=img.shape[:2]
        h2=(h//factor)*factor
        w2=(w//factor)*factor
        if h2<factor or w2<factor:return img
        cropped=np.asarray(img[:h2,:w2],dtype=np.float32)
        return cropped.reshape(h2//factor,factor,w2//factor,factor).mean(axis=(1,3)).astype(np.float32)

    def _set_image_rect(self,shape_hw):
        # ImageItem coordinates normally match pixel coordinates. When the live
        # preview is binned, the image has fewer pixels, but it should still
        # occupy the same displayed width/height so ROI boxes stay aligned with
        # the original full-resolution frame.
        try:
            h,w=shape_hw[:2]
            self.img_item.setRect(QtCore.QRectF(0,0,float(w),float(h)))
        except Exception:pass

    def _live_preview_downsample_active(self):
        # Live acquisition and reference-frame preview are the two places where
        # the image display is intentionally compressed for speed.
        return bool(getattr(self,"_running",False) or getattr(self,"_ref_preview_active",False))

    def _refresh_current_display_frame(self):
        # Display-control changes such as ΔR/R, auto-levels, rotation, or
        # preview compression should redraw the current live/reference preview
        # with compression still applied. Static saved frames stay full-res.
        if self._live_preview_downsample_active() and getattr(self,"_last_frame",None) is not None:
            self._show_frame(self._last_frame,preview_downsample=True)
            return True
        if self._memmap is None or self._n_frames==0:return False
        val=self.fslider.value()
        if val<self._n_frames:
            self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))
            return True
        return False

    def _show_frame(self,frame,preview_downsample=False):
        # Central display routine: prepare the frame, show it, and update color
        # levels either automatically or from manual controls.
        # Important order: ΔR/R normalization and rotation happen first, then
        # live-preview compression is applied to the finished display image.
        display=self._get_display_frame(np.asarray(frame,dtype=np.float32))
        display_hw=display.shape[:2]
        shown=display
        if preview_downsample:
            shown=self._downsample_preview_frame(display,self._preview_bin_factor())
        self.img_item.setImage(shown,autoLevels=False)
        self._set_image_rect(display_hw if preview_downsample else shown.shape[:2])
        if self.auto_lev.isChecked():
            v=shown[np.isfinite(shown)]
            if v.size>0:
                drr_mode=hasattr(self,"btn_drr") and self.btn_drr.isChecked()
                if drr_mode:
                    # dR/R benefits from symmetric color limits around zero so
                    # positive and negative changes are visually balanced.
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

    def _on_preview_bin_changed(self,*_):
        # Changing the preview bin factor should affect only the current live
        # preview. Static loaded frames stay full resolution so the user can
        # inspect details after acquisition.
        self.cfg["display"]["preview_bin"]=self.preview_bin_cb.currentText()
        self._refresh_current_display_frame()

    def _on_auto_lev_toggle(self,checked=None):
        # Re-render the current frame when auto-level mode changes.
        self._refresh_current_display_frame()

    def _on_drr_toggle(self,checked=None):
        # Re-render the current frame when display dR/R mode or smoothing changes.
        is_on=hasattr(self,"btn_drr") and self.btn_drr.isChecked()
        if hasattr(self,"drr_smooth_row"):self.drr_smooth_row.setVisible(is_on)
        self._refresh_current_display_frame()

    def _on_slider(self,val):
        # Display the selected frame from the memory-mapped frame stack.
        if self._memmap is None or val>=self._n_frames:return
        self._show_frame(np.asarray(self._memmap[val],dtype=np.float32))
        self.fslider_lbl.setText(f"{val}/{self._n_frames-1}");self._update_mk(val)

    def _update_mk(self,fi):
        # Move yellow markers on the CV/ROI plots to the currently displayed
        # frame. This helps connect the image with its plot position.
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
        rt_zone=self._roi_zone_traces_for_name(self.roi_t_y.currentText())
        if rt_zone and np.isfinite(t):
            xs=[];ys=[];brushes=[]
            for z_idx,arr in enumerate(rt_zone):
                val=arr[fi] if fi<arr.size else np.nan
                if np.isfinite(val):
                    r,g,b=self._zone_color(z_idx);xs.append(t);ys.append(val);brushes.append(pg.mkBrush(r,g,b,230))
            self.roi_t_mk.setData(xs,ys,brush=brushes) if xs else self.roi_t_mk.setData([],[])
        else:
            rt_yv=self._get_var(self.roi_t_y.currentText());rt_val=rt_yv[fi] if fi<rt_yv.size else np.nan
            if np.isfinite(t) and np.isfinite(rt_val):self.roi_t_mk.setData([t],[rt_val])
            else:self.roi_t_mk.setData([],[])
        e_mode=self.roi_e_mode.currentText()
        if str(e_mode).endswith("R/R vs t"):
            re_zone=list(getattr(self,"_zone_drr",[]) or [])
        elif e_mode=="R / area vs E":
            if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
            re_zone=list(getattr(self,"_zone_intensity_per_area",[]) or [])
            if not re_zone:
                re_zone=self._divide_zone_traces_by_area(
                    list(getattr(self,"_zone_intensity",[]) or []))
        else:
            re_zone=list(getattr(self,"_zone_intensity",[]) or [])
        if re_zone:
            xs=[];ys=[];brushes=[]
            for z_idx,arr in enumerate(re_zone):
                n=min(arr.size,self._frame_E.size,self._frame_t.size)
                if fi>=n:continue
                ex,ey,_,_=self._compute_roi_e_data(arr[:n],self._frame_E[:n],self._frame_t[:n])
                marker_x=ex[fi] if fi<ex.size else np.nan
                val=ey[fi] if fi<ey.size else np.nan
                if np.isfinite(marker_x) and np.isfinite(val):
                    r,g,b=self._zone_color(z_idx);xs.append(marker_x);ys.append(val);brushes.append(pg.mkBrush(r,g,b,230))
            self.roi_e_mk.setData(xs,ys,brush=brushes) if xs else self.roi_e_mk.setData([],[])
        else:
            if str(e_mode).endswith("R/R vs t"):
                re_yv=self._drr_intensity
            elif e_mode=="R / area vs E":
                if hasattr(self,"_ensure_area_traces"):self._ensure_area_traces()
                area_base=getattr(self,"_roi_intensity_per_area",np.array([],dtype=np.float64))
                re_yv=(area_base if area_base.size else
                       (self._roi_intensity/self._total_area()
                        if self._roi_intensity.size else self._roi_intensity))
            else:
                re_yv=self._roi_intensity
            n=min(re_yv.size,self._frame_E.size,self._frame_t.size)
            if fi<n:
                ex,ey,_,_=self._compute_roi_e_data(re_yv[:n],self._frame_E[:n],self._frame_t[:n])
                marker_x=ex[fi] if fi<ex.size else np.nan
                re_val=ey[fi] if fi<ey.size else np.nan
            else:
                marker_x=np.nan;re_val=np.nan
            if np.isfinite(marker_x) and np.isfinite(re_val):self.roi_e_mk.setData([marker_x],[re_val])
            else:self.roi_e_mk.setData([],[])

    def _plot_view_boxes(self,target=None):
        # Main plot view boxes plus secondary cluster-trace axes.
        boxes=[]
        names={
            "cv":("cv_plot",),
            "roi_t":("roi_t_plot",),
            "roi_e":("roi_e_plot",),
        }.get(target,("cv_plot","roi_t_plot","roi_e_plot"))
        for name in names:
            if hasattr(self,name):
                try:boxes.append(getattr(self,name).getViewBox())
                except Exception:pass
        secondary={
            "roi_t":("roi_t_vb2",),
            "roi_e":("roi_e_vb2",),
        }.get(target,("roi_t_vb2","roi_e_vb2") if target is None else ())
        for name in secondary:
            if hasattr(self,name):boxes.append(getattr(self,name))
        return boxes

    def _main_plot_widgets(self,target=None):
        # These are the plots whose x/y ranges should visibly refit when the
        # plotted variable changes.
        plots=[]
        names={
            "cv":("cv_plot",),
            "roi_t":("roi_t_plot",),
            "roi_e":("roi_e_plot",),
        }.get(target,("cv_plot","roi_t_plot","roi_e_plot"))
        for name in names:
            if hasattr(self,name):plots.append(getattr(self,name))
        return plots

    def _unlock_plot_axes(self,target=None):
        # Let plots auto-scale while new data or a new variable selection is
        # being drawn. The lock is restored after plot updates when needed.
        self._plot_axes_locked=False
        for vb in self._plot_view_boxes(target):
            try:vb.enableAutoRange()
            except Exception:pass

    def _lock_plot_axes(self,target=None):
        # Freeze the current visible ranges so moving frame markers cannot force
        # pyqtgraph to rescale when they approach an axis boundary.
        for vb in self._plot_view_boxes(target):
            try:
                xr,yr=vb.viewRange()
                if len(xr)==2 and len(yr)==2:
                    vb.setRange(xRange=xr,yRange=yr,padding=0)
                vb.disableAutoRange()
            except Exception:pass
        self._plot_axes_locked=True

    def _rescale_plot_axes(self,target=None):
        # Refit plots to the currently displayed data. This is used when the
        # user changes a plotted Y variable, because the old locked range may be
        # much too large or too small for the new units.
        for plot in self._main_plot_widgets(target):
            if self._fit_plot_to_curve_data(plot):
                continue
            try:
                plot.enableAutoRange()
                plot.autoRange()
                continue
            except Exception:pass
            try:
                vb=plot.getViewBox()
                vb.enableAutoRange()
                vb.autoRange()
            except Exception:pass

    def _fit_plot_to_curve_data(self,plot):
        # pyqtgraph autoRange can be affected by previous range locks and extra
        # marker/overlay items. Compute the range from the actual plotted curve
        # data so small traces such as R / area do not appear pinned to zero.
        try:items=plot.listDataItems()
        except Exception:return False
        xs=[];ys=[]
        for item in items:
            try:x,y=item.getData()
            except Exception:continue
            if x is None or y is None:continue
            x=np.asarray(x,dtype=np.float64);y=np.asarray(y,dtype=np.float64)
            n=min(x.size,y.size)
            if n<1:continue
            m=np.isfinite(x[:n])&np.isfinite(y[:n])
            if np.any(m):
                xs.append(x[:n][m]);ys.append(y[:n][m])
        if not xs or not ys:return False
        xcat=np.concatenate(xs);ycat=np.concatenate(ys)
        if xcat.size<1 or ycat.size<1:return False
        xmin,xmax=float(np.nanmin(xcat)),float(np.nanmax(xcat))
        ymin,ymax=float(np.nanmin(ycat)),float(np.nanmax(ycat))
        if not all(np.isfinite(v) for v in (xmin,xmax,ymin,ymax)):return False
        def _pad(lo,hi):
            span=hi-lo
            if not np.isfinite(span) or abs(span)<1e-12:
                base=max(abs(lo),abs(hi),1.0)
                pad=0.05*base
            else:
                pad=0.04*span
            return lo-pad,hi+pad
        xr=_pad(xmin,xmax);yr=_pad(ymin,ymax)
        try:
            vb=plot.getViewBox()
            vb.setRange(xRange=xr,yRange=yr,padding=0)
            return True
        except Exception:return False

    def _on_plot_y_axis_changed(self,target=None,*_):
        # Y-axis changes should get a fresh scale even after Analyze has locked
        # axes to protect against frame-marker rescaling. Only the plot whose
        # control changed should refit; the others keep their current ranges.
        relock=getattr(self,"_plot_axes_locked",False)
        if relock:self._unlock_plot_axes(target)
        self._update_cv();self._update_roi_plots()
        if self._n_frames>0:
            self._update_mk(min(self.fslider.value(),self._n_frames-1))
        QtWidgets.QApplication.processEvents()
        self._rescale_plot_axes(target)
        QtWidgets.QApplication.processEvents()
        if relock:self._lock_plot_axes(target)

    def _update_all_plots(self,*_):
        # Convenience refresh used by many controls that affect both CV and ROI
        # plots.
        relock=getattr(self,"_plot_axes_locked",False)
        if relock:self._unlock_plot_axes()
        self._update_cv();self._update_roi_plots()
        if relock:self._lock_plot_axes()

__all__ = [name for name in globals() if not name.startswith("__")]
