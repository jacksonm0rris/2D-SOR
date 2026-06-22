"""Electrochemistry data extraction helpers.

Generated from sor_demo_v19_26-6-2.py during modularization.
"""
from .dependencies import *
from .numeric_utils import *

def _parse_er(t):
    if "\u00b11" in t and "2.5" not in t:return 1.0
    if "2.5" in t:return 2.5
    if "5" in t and "2.5" not in t:return 5.0
    if "10" in t:return 10.0
    return 1.0

def _extract_echem_point(d):
    try:
        if hasattr(d, "_asdict"):
            d_dict = d._asdict()
        elif isinstance(d, dict):
            d_dict = d
        else:
            d_dict = vars(d)
    except Exception:
        d_dict = {}
    v = d_dict.get("voltage", d_dict.get("Ewe", d_dict.get("Ece")))
    c = d_dict.get("<I>",
        d_dict.get("average_I",
            d_dict.get("I",
                d_dict.get("current", None))))
    return safe_float(v), safe_float(c)

def _extract_dev_time(d):
    tt=getattr(d,"time",None)
    if tt is None and isinstance(d,dict):tt=d.get("time",d.get("t"))
    return safe_float(tt,np.nan)

def _step_average_current(Ea, Ia, step_v=None):
    Ea = np.asarray(Ea, dtype=np.float64)
    Ia = np.asarray(Ia, dtype=np.float64)
    n = len(Ea)
    if n < 2:
        return Ia.copy()
    if step_v is None or step_v <= 0:
        dE = np.abs(np.diff(Ea))
        pos = dE[dE > 0]
        step_v = float(np.median(pos)) if pos.size > 0 else 1e-4
    half = step_v * 0.6
    Ia_avg = Ia.copy()
    i = 0
    while i < n:
        j = i + 1
        while j < n and abs(Ea[j] - Ea[i]) < half:
            j += 1
        if j > i + 1:
            finite_mask = np.isfinite(Ia[i:j])
            if np.any(finite_mask):
                avg = float(np.mean(Ia[i:j][finite_mask]))
                Ia_avg[i:j] = avg
        i = j
    return Ia_avg

def _stamp_batch(batch, twe, t_now):
    nb = len(batch)
    prev_tw = twe[-1] if twe else (t_now - 0.001 * nb)
    t_now = max(t_now, prev_tw + 1e-6 * nb)
    dev_times = [_extract_dev_time(d) for d in batch]
    dev_arr = np.array(dev_times, dtype=np.float64)
    wall_span = max(t_now - prev_tw, 1e-6)
    if np.all(np.isfinite(dev_arr)) and nb > 1:
        resets = np.where(np.diff(dev_arr) < 0)[0] + 1
    else:
        resets = np.array([], dtype=int)
    if resets.size == 0:
        if nb > 1 and np.all(np.isfinite(dev_arr)) and dev_arr[-1] > dev_arr[0]:
            span = dev_arr[-1] - dev_arr[0]
            offsets = (dev_arr - dev_arr[0]) / span * wall_span
            point_walls = prev_tw + offsets + (t_now - (prev_tw + offsets[-1]))
        else:
            point_walls = np.linspace(
                prev_tw + wall_span / max(nb, 1), t_now, nb)
    else:
        seg_starts = np.concatenate([[0], resets, [nb]])
        seg_sizes  = np.diff(seg_starts)
        point_walls = np.empty(nb, dtype=np.float64)
        seg_prev = prev_tw
        for s in range(len(seg_sizes)):
            s0 = seg_starts[s]
            s1 = seg_starts[s + 1]
            n_seg = seg_sizes[s]
            seg_wall = wall_span * n_seg / nb
            seg_t_now = seg_prev + seg_wall
            da = dev_arr[s0:s1]
            if n_seg > 1 and np.all(np.isfinite(da)) and da[-1] > da[0]:
                span = da[-1] - da[0]
                offsets = (da - da[0]) / span * seg_wall
                point_walls[s0:s1] = seg_prev + offsets + (seg_t_now - (seg_prev + offsets[-1]))
            else:
                point_walls[s0:s1] = np.linspace(
                    seg_prev + seg_wall / max(n_seg, 1), seg_t_now, n_seg)
            seg_prev = seg_t_now
    for i in range(1, nb):
        if point_walls[i] <= point_walls[i - 1]:
            point_walls[i] = point_walls[i - 1] + 1e-6
    return dev_times, point_walls

__all__ = [name for name in globals() if not name.startswith("__")]
