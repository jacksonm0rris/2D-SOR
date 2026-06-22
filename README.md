# Modularized 2D-SOR Demo

This folder contains a modularized version of `sor_demo_v19_26-6-2.py`.

Run it with:

```powershell
python launch_sor_demo.py
```

Run the benchmark suite with:

```powershell
python benchmark_scenarios.py
```

By default, the benchmark reads JSON files from the sibling `test scenarios`
folder and writes `benchmark_results.json` plus a matching `.txt` summary into
a new timestamped folder under `benchmark results`.

The package is split into focused files:

- `dependencies.py`: imports, optional hardware bindings, constants
- `numeric_utils.py`: numeric smoothing, interpolation, cycle/sweep helpers
- `analysis.py`: frame file sink and analysis engine
- `settings.py`: default settings and save/load
- `echem_data.py`: electrochemistry point/time helpers
- `workers.py`: live and synthetic acquisition workers
- `datasets.py`: dataset metadata persistence
- `dialogs.py`: setup dialog and traffic light widget
- `main_window.py`: main GUI window initialization and UI construction
- `image_display_mixin.py`: image display, rotation, color mapping, and frame slider behavior
- `cv_plot_mixin.py`: CV, ROI, smoothing, and power plotting
- `analysis_mixin.py`: PCA/clustering analysis workflow
- `acquisition_mixin.py`: live/test runs, loading, and incoming data handling
- `zones_mixin.py`: zone selection and zone analysis
- `export_mixin.py`: plot/frame export and close handling
- `app.py`: command-line entry point
