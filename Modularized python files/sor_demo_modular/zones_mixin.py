"""ZonesMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class ZonesMixin:
    def _zone_color(self,idx):
        r,g,b=ZONE_COLORS[idx%len(ZONE_COLORS)];return (r,g,b)

    def _zone_slices(self,zone,shape=None):
        if shape is not None:
            try:
                dummy=np.empty(shape[:2],dtype=np.float32)
                slices,_=zone.getArraySlice(dummy,self.img_item)
                ys,xs=slices[0],slices[1]
                h,w=shape[:2]
                y0=int(np.clip(ys.start or 0,0,max(0,h-1)))
                y1=int(np.clip(ys.stop  or h,y0+1,max(1,h)))
                x0=int(np.clip(xs.start or 0,0,max(0,w-1)))
                x1=int(np.clip(xs.stop  or w,x0+1,max(1,w)))
                return slice(y0,y1),slice(x0,x1)
            except Exception:
                pass
        pos=zone.pos();sz=zone.size()
        x0=int(np.floor(pos.x()));y0=int(np.floor(pos.y()))
        x1=int(np.ceil(pos.x()+sz.x()));y1=int(np.ceil(pos.y()+sz.y()))
        if shape:
            h,w=shape[:2]
            x0=int(np.clip(x0,0,max(0,w-1)));x1=int(np.clip(x1,x0+1,max(1,w)))
            y0=int(np.clip(y0,0,max(0,h-1)));y1=int(np.clip(y1,y0+1,max(1,h)))
        return slice(y0,y1),slice(x0,x1)

    def _invalidate_zone_mask_cache(self):
        self._zone_mask_cache={}

    def _zone_mask_cache_key(self,shape):
        deg=round(float(self.rot_sp.value())%360,3) if hasattr(self,"rot_sp") else 0.0
        zones=[]
        for zone in self._zones:
            pos=zone.pos();sz=zone.size()
            zones.append((
                round(float(pos.x()),3),round(float(pos.y()),3),
                round(float(sz.x()),3),round(float(sz.y()),3)))
        return (tuple(shape[:2]),deg,tuple(zones))

    def _get_zone_mask_flats(self,shape):
        key=self._zone_mask_cache_key(shape)
        cache=getattr(self,"_zone_mask_cache",{})
        if key not in cache:
            cache.clear()
            cache[key]=[
                self._zone_mask_in_original(zone,shape).ravel()
                for zone in self._zones
            ]
            self._zone_mask_cache=cache
        return cache[key]

    def _update_zone_label(self):
        n=len(self._zones)
        self.zone_lbl.setText(f"{n} zone{'s' if n!=1 else ''}")

    def _on_zone_add(self):
        idx=len(self._zones)
        r,g,b=self._zone_color(idx)
        prev=self._zones[-1];pp=prev.pos();ps=prev.size()
        new_pos=[pp.x()+20,pp.y()+20]
        zone=pg.RectROI(new_pos,[ps.x(),ps.y()],pen=pg.mkPen((r,g,b),width=2))
        zone.setZValue(10+idx)
        self.img_plot.addItem(zone)
        self._zones.append(zone)
        self._undo_stack.append(("add",zone))
        self._update_zone_label()
        self._roi_cache_key=None;self._invalidate_zone_mask_cache()

    def _remove_zone(self,zone):
        if zone is self._zones[0]:return
        self.img_plot.removeItem(zone)
        if zone in self._zones:self._zones.remove(zone)
        self._roi_cache_key=None;self._invalidate_zone_mask_cache()
        self._update_zone_label()

    def _on_zone_del(self):
        if len(self._zones)<=1:
            self.status_lbl.setText("Cannot delete the only zone.");return
        zone=self._zones[-1]
        self._undo_stack.append(("delete",{
            "pos":(zone.pos().x(),zone.pos().y()),
            "size":(zone.size().x(),zone.size().y()),
            "idx":len(self._zones)-1}))
        self._remove_zone(zone)

    def _on_zone_clear(self):
        if len(self._zones)<=1:return
        removed=[]
        for zone in self._zones[1:]:
            removed.append({"pos":(zone.pos().x(),zone.pos().y()),
                             "size":(zone.size().x(),zone.size().y()),
                             "idx":self._zones.index(zone)})
            self.img_plot.removeItem(zone)
        self._zones=[self._zones[0]]
        self._undo_stack.append(("clear",removed))
        self._roi_cache_key=None;self._invalidate_zone_mask_cache()
        self._update_zone_label()

    def _on_zone_undo(self):
        if not self._undo_stack:
            self.status_lbl.setText("Nothing to undo.");return
        action,data=self._undo_stack.pop()
        if action=="add":self._remove_zone(data)
        elif action=="delete":
            idx=data["idx"];r,g,b=self._zone_color(idx)
            zone=pg.RectROI(list(data["pos"]),list(data["size"]),pen=pg.mkPen((r,g,b),width=2))
            zone.setZValue(10+idx);self.img_plot.addItem(zone)
            self._zones.insert(idx,zone)
            self._roi_cache_key=None;self._invalidate_zone_mask_cache();self._update_zone_label()
        elif action=="clear":
            for d in data:
                idx=d["idx"];r,g,b=self._zone_color(idx)
                zone=pg.RectROI(list(d["pos"]),list(d["size"]),pen=pg.mkPen((r,g,b),width=2))
                zone.setZValue(10+idx);self.img_plot.addItem(zone)
                self._zones.insert(idx,zone)
            self._roi_cache_key=None;self._invalidate_zone_mask_cache();self._update_zone_label()

    def _analyze_zones(self):
        """Compute per-zone ROI intensity arrays and summed total.
        Called by _on_analyze. Populates self._zone_intensity (list of arrays,
        one per zone) and updates self._roi_intensity to their sum.

        Performance notes
        -----------------
        * Zone masks are computed once per zone (not per frame).  The memmap
          is reshaped to (N, H*W) once, then each zone is extracted with a
          single vectorised nansum over columns — no Python loop over frames.
        * For large datasets the reshape creates a view (not a copy) because
          memmap supports it; only the selected columns are read from disk.
        * Power correction and reference normalisation are vectorised over the
          full frame axis.
        * ROI extraction is chunked (CHUNK frames at a time) to bound peak
          RAM usage regardless of dataset size or ROI area.
        """
        if self._memmap is None or self._n_frames == 0:
            return
        
        CHUNK = 500

        use_pwr = self.cfg["power_meter"].get("normalize_roi", "false").lower() == "true"
        use_ref = self.cfg["reference"].get("normalize_by_ref_frame", "false").lower() == "true"
        ri = int(np.clip(int(self.cfg["reference"].get("ref_frame_index", "0")),
                         0, max(0, self._n_frames - 1)))
        pwr_corrected = (self._stabilize_power(self._frame_power)
                         if use_pwr and self._frame_power.size == self._n_frames
                         else None)

        # Precompute one boolean mask per zone (H*W flat) — done once, not per frame
        h, w = self._frames_hw
        flat_masks = self._get_zone_mask_flats(self._frames_hw)

        # Reshape memmap to (N_frames, H*W) — view, no copy on most platforms
        try:
            flat_mm = self._memmap.reshape(self._n_frames, h * w)
        except Exception:
            flat_mm = None  # fall back to per-frame loop below

        def _drr_vec(arr):
            """Vectorised ΔR/R: out[i] = (arr[i] - arr[i-1]) / arr[i-1]."""
            out = np.full_like(arr, np.nan, dtype=np.float64)
            if arr.size < 2:
                return out
            prev = arr[:-1]
            safe = np.where(np.abs(prev) < 1e-10, np.ones_like(prev), prev)
            out[1:] = (arr[1:] - prev) / safe
            return out

        rr_list = [np.zeros(self._n_frames, dtype=np.float64) for _ in flat_masks]

        if flat_mm is not None:
            for i0 in range(0, self._n_frames, CHUNK):
                i1 = min(self._n_frames, i0 + CHUNK)
                chunk = flat_mm[i0:i1, :]          # single disk read per chunk
                for z_idx, pmask_flat in enumerate(flat_masks):
                    rr_list[z_idx][i0:i1] = np.nansum(chunk[:, pmask_flat], axis=1)
        else:
            for i in range(self._n_frames):
                frame = np.asarray(self._memmap[i], dtype=np.float32)
                for z_idx, pmask_flat in enumerate(flat_masks):
                    rr_list[z_idx][i] = float(np.nansum(frame[pmask_flat.reshape(h, w)]))

        zone_arrays = []
        for rr in rr_list:
            roi = rr.copy()
            if pwr_corrected is not None:
                g = np.isfinite(pwr_corrected) & (pwr_corrected != 0)
                roi = np.where(g, roi / pwr_corrected, roi)
            if use_ref:
                rv = rr[ri]
                if pwr_corrected is not None and ri < pwr_corrected.size:
                    p = pwr_corrected[ri]
                    if np.isfinite(p) and p != 0:
                        rv /= p
                if np.isfinite(rv) and rv != 0:
                    roi /= rv
            zone_arrays.append(roi)

        self._zone_intensity = zone_arrays
        self._roi_intensity = (np.sum(zone_arrays, axis=0)
                               if zone_arrays
                               else np.array([], dtype=np.float64))
        self._roi_cache_key = None
        self._zone_drr = [_drr_vec(za) for za in zone_arrays]
        self._drr_intensity = (_drr_vec(self._roi_intensity)
                               if self._roi_intensity.size > 0
                               else np.array([], dtype=np.float64))

__all__ = [name for name in globals() if not name.startswith("__")]
