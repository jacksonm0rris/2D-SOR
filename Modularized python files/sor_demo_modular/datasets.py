"""Dataset metadata save/load helpers.

Generated from sor_demo_v19_26-6-2.py during modularization.
"""
from .dependencies import *

def save_dataset_metadata(ds,json_path=None,directory=None):
    fp=ds["frames_path"]
    if json_path is None:
        if directory is None:directory=os.path.dirname(fp)
        json_path=os.path.join(directory,"sor_dataset.json")
    meta={"frames_path":os.path.basename(fp),"frames_count":int(ds["frames_count"]),
          "frames_hw":list(ds["frames_hw"]) if ds["frames_hw"] else None}
    for k in ["frame_E","frame_I","frame_t","frame_t_wall","frame_power","E_all","I_all","t_all"]:
        meta[k]=ds[k].tolist()
    if "t_wall_echem" in ds:meta["t_wall_echem"]=ds["t_wall_echem"].tolist()
    with open(json_path,"w",encoding="utf-8") as f:json.dump(meta,f,indent=1)
    return json_path

def load_dataset_from_json(json_path):
    if not os.path.isfile(json_path):raise FileNotFoundError(f"Not found: {json_path}")
    with open(json_path,"r",encoding="utf-8") as f:meta=json.load(f)
    d=os.path.dirname(json_path)
    ffp=os.path.join(d,meta["frames_path"])
    if not os.path.isfile(ffp):raise FileNotFoundError(f"Missing frames file: {ffp}")
    ds={"frames_path":ffp,"frames_count":int(meta["frames_count"]),
        "frames_hw":tuple(meta["frames_hw"]) if meta["frames_hw"] else None}
    for k in ["frame_E","frame_I","frame_t","frame_t_wall","frame_power","E_all","I_all","t_all"]:
        ds[k]=np.array(meta[k],dtype=np.float64)
    if "t_wall_echem" in meta:ds["t_wall_echem"]=np.array(meta["t_wall_echem"],dtype=np.float64)
    return ds

def load_dataset_from_directory(d):
    mp=os.path.join(d,"sor_dataset.json")
    if not os.path.isfile(mp):raise FileNotFoundError(f"No sor_dataset.json in {d}")
    with open(mp,"r",encoding="utf-8") as f:meta=json.load(f)
    ffp=os.path.join(d,meta["frames_path"])
    if not os.path.isfile(ffp):raise FileNotFoundError(f"Missing: {ffp}")
    ds={"frames_path":ffp,"frames_count":int(meta["frames_count"]),
        "frames_hw":tuple(meta["frames_hw"]) if meta["frames_hw"] else None}
    for k in ["frame_E","frame_I","frame_t","frame_t_wall","frame_power","E_all","I_all","t_all"]:
        ds[k]=np.array(meta[k],dtype=np.float64)
    if "t_wall_echem" in meta:ds["t_wall_echem"]=np.array(meta["t_wall_echem"],dtype=np.float64)
    return ds

__all__ = [name for name in globals() if not name.startswith("__")]
