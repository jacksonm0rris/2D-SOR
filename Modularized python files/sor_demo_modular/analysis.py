"""Frame storage and analysis engine.

This file contains the non-GUI analysis machinery:

* FrameFileSink writes camera frames to disk during acquisition.
* AnalysisEngine turns a movie into block-level reflectance traces.
* PCA reduces each trace to a few response-pattern coordinates.
* K-means groups regions that behaved similarly during the experiment.

For an electrochemist, the important idea is that the engine looks for spatial
regions whose optical response over time/potential has a similar shape.
"""
from .dependencies import *
from .numeric_utils import *

# Writes incoming image frames to a compact raw binary file so long runs do not
# need to keep every full-resolution frame in RAM.
class FrameFileSink:
    """Append-only writer for the camera frame binary file."""
    def __init__(self,path,dtype="auto",flush_every_frames=60,flush_interval_s=2.0):
        # Track the output file, the first observed frame shape, and frame count.
        self.path=path;self._fh=open(path,"wb");self.shape_hw=None;self.count=0
        self.requested_dtype=str(dtype or "auto").lower()
        self.dtype=None
        self.dtype_name=None
        self.flush_every_frames=max(1,int(flush_every_frames))
        self.flush_interval_s=max(0.1,float(flush_interval_s))
        self._last_flush=time.time()

    def _choose_dtype(self,fr):
        # Auto mode keeps integer camera frames as uint16 whenever their values
        # fit. Float inputs remain float32 because converting fractional images
        # would throw away derived precision.
        arr=np.asarray(fr)
        def _fits_u16(a):
            if not np.issubdtype(a.dtype,np.integer):return False
            try:return bool(a.size and int(np.nanmin(a))>=0 and int(np.nanmax(a))<=65535)
            except Exception:return False
        if self.requested_dtype=="auto":
            return np.dtype(np.uint16) if _fits_u16(arr) else np.dtype(np.float32)
        if self.requested_dtype in ("uint16","u2"):
            if _fits_u16(arr):return np.dtype(np.uint16)
            return np.dtype(np.float32)
        return np.dtype(np.float32)

    def _flush_if_needed(self):
        now=time.time()
        if self.count%self.flush_every_frames==0 or now-self._last_flush>=self.flush_interval_s:
            self._fh.flush()
            self._last_flush=now

    def append(self,fr):
        # Accept only 2D frames, enforce a stable shape, and append raw pixels.
        # Shape checks matter because a single binary file can only be reopened
        # correctly if every frame has the same height and width.
        if self.dtype is None:
            self.dtype=self._choose_dtype(fr)
            self.dtype_name=str(self.dtype)
        fr=np.asarray(fr,dtype=self.dtype,order="C")
        if fr.ndim!=2:raise ValueError("2D expected")
        if self.shape_hw is None:self.shape_hw=(int(fr.shape[0]),int(fr.shape[1]))
        if (fr.shape[0],fr.shape[1])!=self.shape_hw:raise ValueError("Shape changed")
        try:
            fr.tofile(self._fh)
            self.count+=1
            self._flush_if_needed()
        except OSError as e:
            free_msg=""
            try:
                du=shutil.disk_usage(os.path.dirname(os.path.abspath(self.path)) or ".")
                free_msg=f"\nFree space at save location: {du.free/1024**3:.2f} GB"
            except Exception:pass
            raise OSError(f"Could not write frame data to {self.path}.{free_msg}\nOriginal error: {e}") from e

    def close(self):
        # Best-effort close keeps shutdown robust even if acquisition already failed.
        try:self._fh.flush()
        except:pass
        try:self._fh.close()
        except:pass
    def memmap(self):
        # Reopen the raw frame file as an array without reading it all into memory.
        dtype=self.dtype or np.dtype(np.float32)
        if self.shape_hw is None:return np.memmap(self.path,dtype=dtype,mode="r",shape=(0,1,1))
        h,w=self.shape_hw;return np.memmap(self.path,dtype=dtype,mode="r",shape=(self.count,h,w))


# Converts a frame stack into spatial blocks, runs PCA on the temporal traces,
# clusters pixels/blocks by PCA coordinates, and prepares overlay data for the UI.
class AnalysisEngine:
    """Run PCA/K-means analysis on a saved frame stack.

    The engine works on spatial blocks rather than individual pixels by default.
    Each block has a reflectance trace through time. PCA compresses those traces,
    and K-means assigns each block to a cluster of similar behavior.
    """
    NORM_RAW="raw";NORM_ZSCORE="zscore";NORM_DROR="dR/R"
    def __init__(self,frame_h,frame_w,block_size=4,K=5,n_pca=5,norm_mode="zscore",frame_stride=1):
        # Store analysis parameters and derive the coarse spatial grid dimensions.
        self.block_size=block_size;self.K=K;self.n_pca=n_pca;self.norm_mode=norm_mode
        self.frame_stride=max(1,int(frame_stride))
        self.sp_h=frame_h//block_size;self.sp_w=frame_w//block_size;self.n_sp=self.sp_h*self.sp_w
        self.frame_h=frame_h;self.frame_w=frame_w
        # Use CuPy if available for the larger array operations, otherwise NumPy.
        self._xp=cp if CUPY_AVAILABLE else np
        # Runtime fields are populated by load_from_memmap() and run().
        self._raw_data=None;self._n_frames=0;self._frame_idx=None
        self.pca_loadings=None;self.pca_scores=None;self.explained_var=None
        self.labels=None;self.cluster_means=None;self._sp_mask=None
    def set_roi_mask(self,roi_slices,frame_shape):
        # Convert a full-resolution ROI rectangle into the block-grid mask used by PCA.
        bs=self.block_size;ys,xs=roi_slices
        sp_y0=max(0,ys.start//bs);sp_y1=min(self.sp_h,(ys.stop+bs-1)//bs)
        sp_x0=max(0,xs.start//bs);sp_x1=min(self.sp_w,(xs.stop+bs-1)//bs)
        mask=np.zeros((self.sp_h,self.sp_w),dtype=bool);mask[sp_y0:sp_y1,sp_x0:sp_x1]=True
        self._sp_mask=mask.ravel()
    def clear_roi_mask(self):self._sp_mask=None
    def load_from_memmap(self,mm,n_frames,rotate_fn=None):
        # Read frames in batches, optionally rotate them, and average each block into
        # one temporal signal. The full stack is kept in block form; PCA can use a
        # strided subset for speed while cluster means still use all frames.
        xp=self._xp;bs=self.block_size
        all_idx=np.arange(n_frames)
        # frame_stride lets PCA use every nth frame for speed. Cluster means
        # still use all frames, so exported traces keep full time resolution.
        sub_idx=all_idx[::self.frame_stride]
        n_sub=len(sub_idx)

        def _load_batch(i0,i1):
            # Convert one contiguous frame slice into block-averaged signals.
            if rotate_fn is not None:
                # Rotation is applied before block averaging so analysis matches
                # the display orientation and ROI geometry.
                raw=np.asarray(mm[i0:i1],dtype=np.float32)
                rotated=[]
                for k in range(raw.shape[0]):
                    rf=rotate_fn(raw[k])
                    rotated.append(rf)
                raw=np.stack(rotated,axis=0)
                raw=raw[:,:self.sp_h*bs,:self.sp_w*bs]
                # Reshape into blocks: frame, block-row, pixel-row, block-col,
                # pixel-col. Averaging over the pixel axes gives one signal per
                # spatial block.
                return raw.reshape(i1-i0,self.sp_h,bs,self.sp_w,bs).mean(axis=(2,4)).reshape(i1-i0,self.n_sp).T
            raw=np.asarray(mm[i0:i1])
            if NUMBA_AVAILABLE and _block_mean_stack_numba is not None:
                try:
                    # Numba fuses uint16-to-float conversion and block averaging.
                    # That avoids making a full float32 copy of every frame chunk.
                    return _block_mean_stack_numba(np.ascontiguousarray(raw),
                                                   self.sp_h,self.sp_w,bs)
                except Exception:
                    pass
            raw=np.asarray(raw,dtype=np.float32)
            raw=raw[:,:self.sp_h*bs,:self.sp_w*bs]
            # Reshape into blocks: frame, block-row, pixel-row, block-col,
            # pixel-col. Averaging over the pixel axes gives one signal per
            # spatial block.
            sp=raw.reshape(i1-i0,self.sp_h,bs,self.sp_w,bs).mean(axis=(2,4)).reshape(i1-i0,self.n_sp).T
            return sp

        data_all=xp.zeros((self.n_sp,n_frames),dtype=xp.float32)
        for i0 in range(0,n_frames,50):
            i1=min(n_frames,i0+50)
            sp=_load_batch(i0,i1)
            data_all[:,i0:i1]=xp.asarray(sp,dtype=xp.float32)
        self._raw_data_all=data_all
        self._raw_data=data_all[:,sub_idx]
        self._n_frames=n_frames
        self._n_sub=n_sub
        self._frame_idx=sub_idx
    def _normalize(self,data):
        # Normalize each spatial trace before PCA so clustering reflects shape
        # changes rather than only absolute brightness differences.
        xp=self._xp
        if self.norm_mode==self.NORM_ZSCORE:
            # Z-score asks: does this region rise/fall like another region,
            # regardless of absolute brightness?
            mu=xp.mean(data,axis=1,keepdims=True);std=xp.std(data,axis=1,keepdims=True)
            std=xp.where(std<1e-10,xp.ones_like(std),std);return(data-mu)/std
        elif self.norm_mode==self.NORM_DROR:
            # dR/R asks: what fractional change did each region make relative
            # to its starting value?
            r0=data[:,0:1].copy();r0=xp.where(xp.abs(r0)<1e-10,xp.ones_like(r0),r0);return(data-r0)/r0
        return data
    @staticmethod
    def _rsvd(A, n_components, n_oversampling=10, n_power=2):
        # Randomized SVD gives a faster PCA approximation for wide frame matrices.
        rng=np.random.default_rng(42)
        n_rows,n_cols=A.shape
        k=min(n_components+n_oversampling, n_cols, n_rows)
        Omega=rng.standard_normal((n_cols,k)).astype(np.float32)
        # Random projection finds a smaller subspace that captures most of the
        # important variation before the exact SVD is run.
        Y=A@Omega
        for _ in range(n_power):
            Y=A@(A.T@Y)
        Q,_=np.linalg.qr(Y)
        B=(Q.T@A)
        U_hat,S,Vt=np.linalg.svd(B,full_matrices=False)
        U=Q@U_hat
        return U[:,:n_components],S[:n_components],Vt[:n_components,:]

    def run(self):
        # Main analysis pipeline: select active blocks, normalize traces, compute
        # PCA, cluster the PCA coordinates, and calculate mean traces per cluster.
        if self._raw_data is None or self._n_frames<3:return False
        xp=self._xp
        raw=self._raw_data
        raw_all=getattr(self,'_raw_data_all',raw)
        n_sub=raw.shape[1]
        # Restrict analysis to the user ROI when one is active.
        if self._sp_mask is not None:
            mask_xp=xp.asarray(self._sp_mask);active_idx=xp.where(mask_xp)[0]
            if len(active_idx)<3:return False
            raw_active=raw[active_idx]
        else:active_idx=None;raw_active=raw
        # Center normalized temporal signals and compute PCA components.
        n_active=raw_active.shape[0];normed=self._normalize(raw_active)
        # Subtract the average response at each time point so PCA focuses on
        # spatial differences, not only the global illumination/echem response.
        mu=xp.mean(normed,axis=0,keepdims=True);data_c=normed-mu
        n_pca=min(self.n_pca,n_sub-1,n_active)
        data_c_np=np.asarray(data_c,dtype=np.float32)
        try:
            U,S,Vt=self._rsvd(data_c_np,n_pca)
        except Exception:
            U,S,Vt=np.linalg.svd(data_c_np,full_matrices=False)
        U=U[:,:n_pca];S=S[:n_pca];Vt=Vt[:n_pca,:]
        U=xp.asarray(U,dtype=xp.float32)
        S=xp.asarray(S,dtype=xp.float32)
        Vt=xp.asarray(Vt,dtype=xp.float32)
        total_var=float(xp.sum(S**2));self.explained_var=S**2/max(total_var,1e-10)
        # Expand ROI-only PCA loadings back to the full block grid for display.
        full_loadings=xp.zeros((n_pca,self.n_sp),dtype=xp.float32)
        if active_idx is not None:
            for c in range(n_pca):full_loadings[c,active_idx]=U[:,c]
        else:full_loadings=U.T
        self.pca_loadings=full_loadings;self.pca_scores=xp.diag(S)@Vt
        # Initialize K-means with k-means++ style seeding in PCA space.
        # In plain language: pick starting cluster centers that are spread out,
        # then repeatedly assign each block to the nearest center.
        pca_coords=U;K=min(self.K,n_active)
        idx_km=xp.random.choice(n_active,size=1,replace=False);centroids=pca_coords[idx_km].copy()
        for _ in range(K-1):
            dists=xp.sum((pca_coords[:,None,:]-centroids[None,:,:])**2,axis=2);min_d=xp.min(dists,axis=1)
            probs_np=(cp.asnumpy(min_d) if CUPY_AVAILABLE else np.asarray(min_d)).astype(np.float64)
            probs_np/=max(probs_np.sum(),1e-10);ni=np.random.choice(n_active,p=probs_np)
            centroids=xp.concatenate([centroids,pca_coords[ni:ni+1]],axis=0)
        # Iterate K-means until the centroids stop moving or the iteration cap is hit.
        for _ in range(30):
            dists=xp.sum((pca_coords[:,None,:]-centroids[None,:,:])**2,axis=2)
            labels_active=xp.argmin(dists,axis=1).astype(xp.int32);nc=xp.zeros_like(centroids)
            for k in range(K):
                m=(labels_active==k);cnt=int(xp.sum(m))
                nc[k]=xp.mean(pca_coords[m],axis=0) if cnt>0 else centroids[k]
            if float(xp.max(xp.abs(nc-centroids)))<1e-6:centroids=nc;break
            centroids=nc
        # Map cluster labels back to the full block grid, using -1 outside the ROI.
        full_labels=xp.full(self.n_sp,-1,dtype=xp.int32)
        if active_idx is not None:full_labels[active_idx]=labels_active
        else:full_labels=labels_active
        self.labels=full_labels
        # Average the original block traces for each cluster for downstream plots.
        # These are the colored cluster traces shown against time and potential.
        self.cluster_means=xp.zeros((K,self._n_frames),dtype=xp.float32)
        for k in range(K):
            m=(full_labels==k)
            if int(xp.sum(m))>0:self.cluster_means[k]=xp.mean(raw_all[m],axis=0)
        return True
    def _to_np(self,x):
        # Convert CuPy arrays back to NumPy for plotting and Qt/pyqtgraph consumers.
        if x is None:return None
        return cp.asnumpy(x) if CUPY_AVAILABLE else np.asarray(x)
    def get_cluster_means_np(self):return self._to_np(self.cluster_means)
    def get_pca_scores_np(self):return self._to_np(self.pca_scores)
    def get_explained_var_np(self):return self._to_np(self.explained_var)
    def get_label_map(self):
        # Return cluster labels as a 2D block-grid image.
        lbl=self._to_np(self.labels)
        if lbl is None:return None
        return lbl.astype(np.int32).reshape(self.sp_h,self.sp_w)
    def get_cluster_overlay(self,alpha=80,highlight=None):
        # Build an RGBA overlay by expanding cluster labels from block grid to pixels.
        # Each cluster gets a color, making spatial domains visible on top of the
        # camera image.
        lbl=self.get_label_map()
        if lbl is None:return None
        K=self.K;bs=self.block_size;ov=np.zeros((self.sp_h*bs,self.sp_w*bs,4),dtype=np.uint8)
        for k in range(K):
            mask=(lbl==k)
            if not np.any(mask):continue
            r,g,b=CLUSTER_COLORS[k%len(CLUSTER_COLORS)]
            mf=np.repeat(np.repeat(mask,bs,axis=0),bs,axis=1)
            a=min(255,alpha*3) if (highlight and k in highlight) else alpha
            ov[mf,0]=r;ov[mf,1]=g;ov[mf,2]=b;ov[mf,3]=a
        return ov
    def get_pca_rgb_overlay(self,alpha=160):
        # Convert the first three PCA loading maps into RGB channels for visualizing
        # the dominant spatial response patterns.
        # Unlike K-means, this is continuous: mixed colors mean mixed PCA
        # response patterns.
        if self.pca_loadings is None:return None
        n_comp=min(3,self.pca_loadings.shape[0]);bs=self.block_size
        h_out=self.sp_h*bs;w_out=self.sp_w*bs;ov=np.zeros((h_out,w_out,4),dtype=np.uint8)
        for c in range(n_comp):
            lmap=np.abs(self._to_np(self.pca_loadings[c]).reshape(self.sp_h,self.sp_w))
            vmin,vmax=float(np.percentile(lmap,2)),float(np.percentile(lmap,98))
            if vmax-vmin<1e-10:vmax=vmin+1
            ln=np.clip((lmap-vmin)/(vmax-vmin),0,1);lf=np.repeat(np.repeat(ln,bs,axis=0),bs,axis=1)
            ov[:,:,c]=(lf*255).astype(np.uint8)
        ov[:,:,3]=alpha;return ov
    @property
    def n_frames(self):return self._n_frames

__all__ = [name for name in globals() if not name.startswith("__")]
