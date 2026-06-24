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

The package is split into focused files. A useful way to read the project is to
separate the plain Python modules from the mixin files:

- Regular modules contain reusable logic, data structures, hardware helpers,
  file loading/saving, math, and worker-thread code. They can usually be
  understood without knowing much about the GUI.
- Mixin files contain groups of methods that are added onto the main
  `DemoWindow` class. They keep the large GUI class manageable by separating
  related behavior into files such as acquisition, plotting, image display,
  zone selection, exporting, and analysis.

For example, `analysis.py` contains the reusable PCA/K-means analysis engine,
while `analysis_mixin.py` connects that engine to buttons, checkboxes, cached
GUI state, and displayed overlays. Likewise, `workers.py` does the actual data
collection work in background threads, while `acquisition_mixin.py` starts and
stops those workers and receives their incoming data for the GUI.

Regular modules:

- `dependencies.py`: imports, optional hardware bindings, constants
- `numeric_utils.py`: numeric smoothing, interpolation, cycle/sweep helpers
- `analysis.py`: frame file sink and analysis engine
- `settings.py`: default settings and save/load
- `echem_data.py`: electrochemistry point/time helpers
- `workers.py`: live and synthetic acquisition workers
- `datasets.py`: dataset metadata persistence
- `dialogs.py`: setup dialog and traffic light widget
- `main_window.py`: main GUI window initialization and UI construction
- `app.py`: command-line entry point

Mixin modules:

- `image_display_mixin.py`: image display, rotation, color mapping, and frame slider behavior
- `cv_plot_mixin.py`: CV, ROI, smoothing, and power plotting
- `analysis_mixin.py`: PCA/clustering analysis workflow
- `acquisition_mixin.py`: live/test runs, loading, and incoming data handling
- `zones_mixin.py`: zone selection and zone analysis
- `export_mixin.py`: plot/frame export and close handling
