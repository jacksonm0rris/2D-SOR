"""Numerical and plotting helper functions.

These functions are intentionally small and independent. They support camera
binning, aligning electrochemistry data to camera frame times, smoothing noisy
signals, splitting CV sweeps, and computing LOWESS trend lines.
"""
from .dependencies import *

def safe_float(x,d=np.nan):
    # Convert device values to float; return NaN/default if the value is missing
    # or stored in an unexpected form.
    try:return float(x)
    except:return d

def _bin_2d(img,f,mode):
    # Camera binning combines f-by-f pixel blocks. "mean" preserves approximate
    # brightness scale; "sum" preserves total counts.
    if f<=1:return np.asarray(img,dtype=np.float32)
    i=np.asarray(img,dtype=np.float32);h,w=i.shape;h2=(h//f)*f;w2=(w//f)*f
    if h2==0 or w2==0:raise ValueError("Bin too large")
    c=i[:h2,:w2].reshape(h2//f,f,w2//f,f)
    return c.sum(axis=(1,3),dtype=np.float32) if mode=="sum" else c.mean(axis=(1,3),dtype=np.float32)

def _interp_echem(ftw, etw, ev):
    # Interpolate an electrochemistry value array onto camera frame wall times.
    # This is how each image frame receives matching E, I, and time values.
    if len(etw) < 1:
        return np.full(len(ftw), np.nan)
    etw = np.asarray(etw, dtype=np.float64)
    ev  = np.asarray(ev,  dtype=np.float64)
    valid = np.isfinite(ev) & np.isfinite(etw)
    if not np.any(valid):
        return np.full(len(ftw), np.nan)
    etw = etw[valid]
    ev  = ev[valid]
    if not np.all(np.diff(etw) >= 0):
        # np.interp expects sorted x values, so sort if device timestamps arrived
        # slightly out of order.
        order = np.argsort(etw, kind="stable")
        etw = etw[order]
        ev  = ev[order]
    return np.interp(ftw, etw, ev)

def _smooth_boxcar(arr,window):
    # Boxcar smoothing is a simple moving average. Edge padding prevents the
    # smoothed curve from shrinking.
    if window<2 or arr.size<window:return arr.copy() if hasattr(arr,'copy') else np.asarray(arr)
    arr=np.asarray(arr,dtype=np.float64)
    pad=window//2
    padded=np.concatenate([np.full(pad,arr[0]),arr,np.full(window-pad-1,arr[-1])])
    kernel=np.ones(window)/window
    return np.convolve(padded,kernel,mode='valid')

def _smooth_savgol(arr,window):
    # Savitzky-Golay smoothing fits a small polynomial window. It often preserves
    # peak shape better than a moving average.
    if window<2 or arr.size<window:return arr.copy() if hasattr(arr,'copy') else np.asarray(arr)
    arr=np.asarray(arr,dtype=np.float64)
    w=window if window%2==1 else window+1
    w=max(w,3)
    if arr.size<w:return arr.copy()
    polyorder=min(3,w-1)
    try:
        from scipy.signal import savgol_filter
        return savgol_filter(arr,w,polyorder)
    except ImportError:
        # Fallback implementation if SciPy is not installed.
        half=w//2;out=arr.copy()
        x=np.arange(w,dtype=np.float64)-half
        for i in range(half,arr.size-half):
            seg=arr[i-half:i+half+1]
            coeffs=np.polyfit(x,seg,polyorder)
            out[i]=np.polyval(coeffs,0.0)
        return out

def _smooth(arr,window,use_savgol=False):
    if use_savgol:return _smooth_savgol(arr,window)
    return _smooth_boxcar(arr,window)

def _detect_cycles(E, upper_v=None, cfg=None):
    # Split an E-vs-frame trace into initial hold, CV cycles, and final hold.
    # The algorithm looks for non-flat regions and crossings near the upper
    # voltage limit.
    if E.size < 3:
        return [(0, E.size)]
    if upper_v is None:
        upper_v = float(np.nanmax(E))
    dE_abs = np.abs(np.diff(E))
    cv_range = float(np.nanmax(E) - np.nanmin(E))
    # Treat very small voltage changes as "flat" hold regions.
    flat_thresh = max(cv_range * 0.02, 1e-6)
    cv_start = 0
    for i in range(len(dE_abs)):
        if dE_abs[i] > flat_thresh:
            cv_start = i
            break
    cv_end = E.size
    for i in range(len(dE_abs) - 1, -1, -1):
        if dE_abs[i] > flat_thresh:
            cv_end = i + 2
            break
    cycles = []
    if cv_start > 0:
        cycles.append((0, cv_start))
    tol = max(cv_range * 0.03, 1e-5)
    # Crossings near upper_v act as natural boundaries between CV sweeps.
    E_cv = E[cv_start:cv_end]
    crossings = []
    i = 0
    while i < len(E_cv) - 1:
        if abs(E_cv[i] - upper_v) <= tol:
            j = i
            while j < len(E_cv) - 1 and abs(E_cv[j] - upper_v) <= tol:
                j += 1
            crossings.append(cv_start + (i + j) // 2)
            i = j
        else:
            i += 1
    if len(crossings) == 0:
        cycles.append((cv_start, cv_end))
    else:
        if len(crossings) >= 2:
            cycles.append((cv_start, crossings[1] + 1))
            prev = crossings[1] + 1
        else:
            cycles.append((cv_start, crossings[0] + 1))
            prev = crossings[0] + 1
        for cx in crossings[2:]:
            cycles.append((prev, cx + 1))
            prev = cx + 1
        if prev < cv_end:
            cycles.append((prev, cv_end))
    if cv_end < E.size:
        cycles.append((cv_end, E.size))
    return cycles if cycles else [(0, E.size)]


def _split_sweeps(E, *arrays):
    # Split a potential trace at direction changes. This prevents forward and
    # reverse CV sweeps from being connected by straight lines in plots.
    n = len(E)
    if n < 2:
        return [(E,) + tuple(a for a in arrays)]
    dE = np.diff(E)
    sign = np.zeros(len(dE), dtype=np.int8)
    sign[dE > 0] = 1
    sign[dE < 0] = -1
    last = 0
    for i in range(len(sign)):
        if sign[i] != 0:
            last = sign[i]
        else:
            sign[i] = last
    reversals = np.where(np.diff(sign) != 0)[0] + 1
    cut = np.concatenate([[0], reversals, [n]])
    segments = []
    for s, e in zip(cut[:-1], cut[1:]):
        if e - s < 2:
            continue
        seg = (E[s:e],) + tuple(a[s:e] for a in arrays)
        segments.append(seg)
    return segments


def _lowess_smooth(x, y, frac=0.05):
    # LOWESS creates a local weighted trend line. For each x[i], it fits a small
    # quadratic model to nearby points, with closer points weighted more heavily.
    n = len(x)
    if n < 4:
        return y.copy()
    k = max(4, int(round(frac * n)))
    y_out = np.empty(n, dtype=np.float64)
    for i in range(n):
        dist = np.abs(x - x[i])
        idx  = np.argsort(dist)[:k]
        d    = dist[idx]
        h    = d[-1] if d[-1] > 0 else 1.0
        u    = d / h
        w    = (1.0 - u ** 3) ** 3
        xi   = x[idx]; yi = y[idx]
        X = np.column_stack([np.ones(k), xi, xi ** 2])
        W = np.diag(w)
        XtW  = X.T @ W
        XtWX = XtW @ X
        XtWy = XtW @ yi
        try:
            # Weighted least-squares quadratic fit evaluated at x[i].
            coeffs = np.linalg.solve(XtWX, XtWy)
            y_out[i] = coeffs[0] + coeffs[1] * x[i] + coeffs[2] * x[i] ** 2
        except np.linalg.LinAlgError:
            sw = np.sum(w)
            y_out[i] = np.sum(w * yi) / sw if sw > 1e-12 else y[i]
    return y_out

__all__ = [name for name in globals() if not name.startswith("__")]
