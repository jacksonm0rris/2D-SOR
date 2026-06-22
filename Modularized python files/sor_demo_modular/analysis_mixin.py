"""AnalysisMixin methods for the main window."""
from .dependencies import *
from .numeric_utils import *
from .analysis import *
from .settings import *
from .workers import *
from .datasets import *
from .dialogs import *


class AnalysisMixin:
    def _on_an_toggle(self,checked):
        if isinstance(checked,bool) and not checked:
            self._an_active=False
            self.cl_overlay.setOpacity(0.5);self.pca_overlay.setOpacity(0.6)
            self.cl_overlay.setVisible(False);self.pca_overlay.setVisible(False)
            self._clear_an();return
        self._an_active=True
        if self._engine is not None:self._run_an()
        else:self.an_var.setText("(click Analyze to run clustering)")

    def _on_an_setting_changed(self,*_):
        self._engine=None
        self._rebuild_cbs()
        if self._an_active:
            self.cl_overlay.setOpacity(0.15)
            self.pca_overlay.setOpacity(0.15)
            self.an_var.setText("(settings changed — click Analyze to update)")

    def _on_an_p(self,*_):
        self._on_an_setting_changed()

    def _on_cl_sel(self):
        self._cl_sel={i for i,cb in enumerate(self.an_cbs) if cb.isChecked()}
        self._update_an_plots()
        if self._engine is not None and self._an_active:
            ov=self._engine.get_cluster_overlay(alpha=80,highlight=self._cl_sel)
            if ov is not None:self.cl_overlay.setImage(ov)

    def _on_rgb(self):
        if self._an_active and self._engine is not None:
            if self.an_rgb.isChecked():
                rgb=self._engine.get_pca_rgb_overlay(alpha=140)
                if rgb is not None:self.pca_overlay.setImage(rgb);self.pca_overlay.setVisible(True)
            else:self.pca_overlay.setVisible(False)

    def _clear_an(self):
        for c in self._cl_curves_t.values():self.roi_t_vb2.removeItem(c)
        for c in self._cl_curves_e.values():self.roi_e_vb2.removeItem(c)
        self._cl_curves_t.clear();self._cl_curves_e.clear()

    def _ensure_an(self,K):
        for k in list(self._cl_curves_t):
            if k not in self._cl_sel:
                self.roi_t_vb2.removeItem(self._cl_curves_t.pop(k))
                self.roi_e_vb2.removeItem(self._cl_curves_e.pop(k))
        for k in self._cl_sel:
            if k>=K:continue
            if k not in self._cl_curves_t:
                r,g,b=CLUSTER_COLORS[k%len(CLUSTER_COLORS)];pen=pg.mkPen(color=(r,g,b),width=2)
                self._cl_curves_t[k]=pg.PlotDataItem([],[],pen=pen);self.roi_t_vb2.addItem(self._cl_curves_t[k])
                self._cl_curves_e[k]=pg.PlotDataItem([],[],pen=pen);self.roi_e_vb2.addItem(self._cl_curves_e[k])

    def _run_an(self):
        if not self._an_active or self._frames_hw is None or self._n_frames<3:return
        self.traffic.setState(TrafficLight.YELLOW);QtWidgets.QApplication.processEvents()
        h,w=self._frames_hw;K=self.an_k.value();np_=self.an_pca.value();nm=self.an_norm.currentText()
        stride=getattr(self,'an_stride',None);stride=stride.value() if stride else 1
        _bs=getattr(self,'an_block',None);_bs=_bs.value() if _bs else 4
        if (self._engine is None or self._engine.K!=K or self._engine.n_pca!=np_
                or self._engine.norm_mode!=nm or self._engine.frame_stride!=stride
                or self._engine.block_size!=_bs):
            self._engine=AnalysisEngine(h,w,block_size=_bs,K=K,n_pca=np_,norm_mode=nm,
                                        frame_stride=stride)
        eng=self._engine
        if self.an_roi_only.isChecked():
            _rmask=self._zone_mask_in_original(self.roi,self._frames_hw)
            _rows=np.where(np.any(_rmask,axis=1))[0]
            _cols=np.where(np.any(_rmask,axis=0))[0]
            if _rows.size and _cols.size:
                _rsl=slice(int(_rows[0]),int(_rows[-1])+1)
                _csl=slice(int(_cols[0]),int(_cols[-1])+1)
                eng.set_roi_mask((_rsl,_csl),(h,w))
            else:
                eng.clear_roi_mask()
        else:eng.clear_roi_mask()
        if self._memmap is not None:
            _orig_hw=self._frames_hw
            _rot_hw=self._rotated_shape(_orig_hw)
            stride=getattr(self,'an_stride',None);stride=stride.value() if stride else 1
            _bs=getattr(self,'an_block',None);_bs=_bs.value() if _bs else 4
            if (eng.frame_h!=_rot_hw[0] or eng.frame_w!=_rot_hw[1]):
                eng=AnalysisEngine(_rot_hw[0],_rot_hw[1],block_size=_bs,
                                   K=K,n_pca=np_,norm_mode=nm,frame_stride=stride)
                self._engine=eng
                if self.an_roi_only.isChecked():
                    _rmask=self._zone_mask_in_original(self.roi,self._frames_hw)
                    _rows=np.where(np.any(_rmask,axis=1))[0]
                    _cols=np.where(np.any(_rmask,axis=0))[0]
                    if _rows.size and _cols.size:
                        eng.set_roi_mask((slice(int(_rows[0]),int(_rows[-1])+1),
                                          slice(int(_cols[0]),int(_cols[-1])+1)),_rot_hw)
                    else:eng.clear_roi_mask()
            eng.load_from_memmap(self._memmap,self._n_frames,
                                 rotate_fn=self._apply_rotation)
        ok=eng.run()
        if not ok:self.traffic.setState(TrafficLight.GREEN);return
        self.cl_overlay.setOpacity(0.5);self.pca_overlay.setOpacity(0.6)
        ov=eng.get_cluster_overlay(alpha=80,highlight=self._cl_sel)
        if ov is not None:self.cl_overlay.setImage(ov);self.cl_overlay.setVisible(True)
        if self.an_rgb.isChecked():
            rgb=eng.get_pca_rgb_overlay(alpha=140)
            if rgb is not None:self.pca_overlay.setImage(rgb);self.pca_overlay.setVisible(True)
        else:self.pca_overlay.setVisible(False)
        ev=eng.get_explained_var_np()
        if ev is not None:
            self.an_var.setText("Var: "+"  ".join(f"PC{i}:{ev[i]*100:.1f}%" for i in range(min(5,len(ev)))))
        self._cl_means=eng.get_cluster_means_np();self._update_an_plots()
        self.traffic.setState(TrafficLight.GREEN)

    def _update_an_plots(self):
        means=self._cl_means
        if means is None or not self._an_active:return
        K=means.shape[0];self._ensure_an(K)
        cs,ce=self._get_cycle_slice()
        n=min(means.shape[1],self._frame_t.size,self._frame_E.size,ce)-cs
        if n<=0:return
        for k in self._cl_sel:
            if k>=K:continue
            y=means[k,cs:cs+n].astype(np.float64)
            if k in self._cl_curves_t:
                t=self._frame_t[cs:cs+n];m1=np.isfinite(t)&np.isfinite(y);self._cl_curves_t[k].setData(t[m1],y[m1])
                e=self._frame_E[cs:cs+n];m2=np.isfinite(e)&np.isfinite(y);self._cl_curves_e[k].setData(e[m2],y[m2])
        self.roi_t_vb2.enableAutoRange();self.roi_e_vb2.enableAutoRange()

__all__ = [name for name in globals() if not name.startswith("__")]
