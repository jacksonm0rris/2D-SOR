"""Application settings defaults and persistence.

The GUI stores user-editable experiment settings in an INI file. These defaults
are used the first time the program runs, or whenever a setting is missing from
the saved file.
"""
from .dependencies import *

DEFAULT_SETTINGS={
    # Potentiostat settings describe the CV waveform and optional holds.
    "potentiostat":{
        "bl_address":"192.168.0.9","experiment_type":"CV",
        "start_v":"0.0","upper_v":"0.25","lower_v":"-0.1","end_v":"0.0",
        "scan_rate":"0.02","step_v":"0.001","cycles":"1",
        "hold_t_init":"0.0","hold_t_final":"0.0",
        "bandwidth":"BW8","e_range":"\u00b11 V","filter":"5 Hz","i_range":"auto","vs_ocv":"false",
        "cva_record_every_de":"0.001","cva_average_over_de":"false",
        "cva_begin_measuring_i":"0.5","cva_end_measuring_i":"1.0",
    },
    # Camera settings control exposure and how often frames are requested.
    "camera":{"camera_index":"0","exposure_us":"7000","bin_factor":"1","bin_mode":"mean",
        "capture_interval_ms":"33","frame_wait_ms":"500","frame_dtype":"uint16"},
    # Display settings remember plot variables and smoothing preferences.
    "display":{"colormap":"viridis","level_min":"0.0","level_max":"1000.0","auto_levels":"true",
        "cv_y":"I (A)","roi_t_y":"R","roi_e_y":"R","smooth_window":"1","smooth_mode":"boxcar",
        "preview_bin":"4x","window_w":"1600","window_h":"980",
        "split_main":"1200,320","split_vertical":"470,360,110",
        "split_top":"760,760","split_bottom":"760,760"},
    # Power meter data can optionally normalize optical ROI intensity.
    "power_meter":{"enabled":"false","visa":"USB0::0x1313::0x8076::M01240030::INSTR","normalize_roi":"false"},
    # Reference-frame normalization divides ROI traces by a selected frame.
    "reference":{"normalize_by_ref_frame":"false","ref_frame_index":"0"},
}

def load_settings():
    # Start with defaults, then overlay any saved values from disk.
    cfg=configparser.ConfigParser()
    for s,kv in DEFAULT_SETTINGS.items():cfg[s]=dict(kv)
    if os.path.isfile(SETTINGS_FILE):
        try:cfg.read(SETTINGS_FILE,encoding="utf-8")
        except:pass
    # Older versions stored CVA settings in a separate section; current code
    # keeps them under "potentiostat".
    if cfg.has_section("cva"):cfg.remove_section("cva")
    return cfg

def save_settings(cfg):
    # Best-effort persistence: a save failure should not crash acquisition.
    try:
        with open(SETTINGS_FILE,"w",encoding="utf-8") as f:cfg.write(f)
    except Exception as e:print(f"Settings save failed: {e}")

__all__ = [name for name in globals() if not name.startswith("__")]
