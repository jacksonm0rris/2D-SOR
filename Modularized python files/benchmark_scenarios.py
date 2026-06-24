#!/usr/bin/env python3
"""Run the bundled JSON test scenarios as a repeatable performance suite.

This is not part of normal interactive use. It is a developer/validation tool
that runs synthetic scenarios through the same GUI code path, measures timing
and memory, then writes a JSON report and a human-readable text summary.
"""
import argparse
import ctypes
import datetime as _dt
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
import traceback

SHOW_WINDOW = "--show-window" in sys.argv
if not SHOW_WINDOW:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from sor_demo_modular.main_window import DemoWindow
from sor_demo_modular.workers import TestWorker
from sor_demo_modular.dialogs import TrafficLight


def _process_memory_mb():
    """Return current process working-set memory in MB on Windows."""
    # The benchmark currently targets the Windows development environment. On
    # other platforms, memory is reported as NaN rather than failing the suite.
    if os.name != "nt":
        return math.nan

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("Psapi.dll", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        return math.nan
    return counters.WorkingSetSize / (1024 * 1024)


def _mean(values):
    # Statistics helpers ignore NaN/inf so failed measurements do not poison the
    # whole summary.
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.mean(vals) if vals else math.nan


def _std(values):
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.stdev(vals) if len(vals) >= 2 else 0.0


def _percentile(values, pct):
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals:
        return math.nan
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * pct / 100.0
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


class BenchmarkRunner(QtCore.QObject):
    """Runs scenarios one at a time and records performance measurements."""

    finished = QtCore.pyqtSignal(int)

    def __init__(self, window, scenarios, output_path, keep_temp=False,
                 parent=None):
        super().__init__(parent)
        self.window = window
        self.scenarios = list(scenarios)
        self.output_path = Path(output_path)
        self.keep_temp = keep_temp
        self.results = []
        self.errors = []
        self.index = -1
        self.current = None
        self.current_temp_dir = None
        self._orig_on_frame = window._on_frame
        self._orig_on_frame_processed = window._on_frame_processed
        self._orig_on_err = window._on_err
        self._mem_timer = QtCore.QTimer(self)
        self._mem_timer.setInterval(100)
        self._mem_timer.timeout.connect(self._sample_memory)

    def start(self):
        # Temporarily wrap selected DemoWindow methods so normal GUI behavior
        # still happens while timing information is collected.
        self.window._on_frame = self._timed_on_frame
        self.window._on_frame_processed = self._timed_on_frame_processed
        self.window._on_err = self._window_error
        self._run_next()

    def _run_next(self):
        # Start the next scenario. Each one gets a fresh temporary folder and
        # frame binary so results do not overlap.
        self.index += 1
        if self.index >= len(self.scenarios):
            self._finish()
            return

        scenario = self.scenarios[self.index]
        self.current_temp_dir = tempfile.mkdtemp(prefix="sor_benchmark_")
        bin_path = Path(self.current_temp_dir) / f"{scenario.stem}_frames.bin"
        self.current = {
            # Lists collect per-frame samples; _finalize_current turns them into
            # means, percentiles, max values, and totals.
            "scenario": scenario.name,
            "scenario_path": str(scenario),
            "frame_handler_ms": [],
            "worker_frame_ms": [],
            "worker_queue_dropped_frames": [],
            "frame_interval_ms": [],
            "memory_samples_mb": [],
            "frames": 0,
            "error": None,
            "temp_dir": self.current_temp_dir,
        }

        self.window._clear_data()
        self.window._start_frame_processor()
        self.window._running = True
        self.window._set_btns(True)
        self.window.traffic.setState(TrafficLight.RED)
        self.window.status_lbl.setText(f"Benchmarking {scenario.name}...")
        self.window.prog_lbl.setText("")
        self.window._store_dir = self.current_temp_dir
        self.window._worker = TestWorker(
            self.window.cfg,
            self.current_temp_dir,
            scenario_path=str(scenario),
            bin_path=str(bin_path),
        )
        self.window._wire()
        self.window._worker.finished_ok.connect(
            self._worker_finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.window._worker.finished_err.connect(
            self._worker_failed, QtCore.Qt.ConnectionType.QueuedConnection)
        self.current["acquisition_start_perf"] = time.perf_counter()
        self._mem_timer.start()
        self.window._worker.start()

    def _timed_on_frame(self, data):
        # Measure how long the normal frame handler takes, then let it continue
        # doing its usual GUI/live-preview work.
        if self.current is None:
            return self._orig_on_frame(data)
        prev_tw = self.current.get("_last_frame_wall")
        tw = data.get("t_wall")
        if prev_tw is not None and tw is not None:
            self.current["frame_interval_ms"].append((tw - prev_tw) * 1000.0)
        if tw is not None:
            self.current["_last_frame_wall"] = tw
        t0 = time.perf_counter()
        self._orig_on_frame(data)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.current["frame_handler_ms"].append(elapsed_ms)
        self.current["frames"] = max(self.current["frames"],
                                     int(data.get("frame_idx", -1)) + 1)

    def _timed_on_frame_processed(self, data):
        # Capture timing information produced by the live ROI frame processor.
        if self.current is not None:
            worker_ms = data.get("worker_frame_ms")
            if worker_ms is not None and math.isfinite(float(worker_ms)):
                self.current["worker_frame_ms"].append(float(worker_ms))
            dropped = data.get("worker_queue_dropped_frames")
            if dropped is not None:
                self.current["worker_queue_dropped_frames"].append(int(dropped))
        return self._orig_on_frame_processed(data)

    def _worker_finished(self, _ds):
        # After synthetic acquisition finishes, run the same Analyze workflow a
        # user would click manually.
        if self.current is None:
            return
        self.current["acquisition_seconds"] = (
            time.perf_counter() - self.current["acquisition_start_perf"])
        QtCore.QTimer.singleShot(0, self._run_analysis)

    def _worker_failed(self, msg):
        if self.current is not None:
            self.current["error"] = msg
        QtCore.QTimer.singleShot(0, self._finalize_current)

    def _window_error(self, msg):
        if self.current is not None:
            self.current["error"] = msg
        self.window._running = False
        self.window._worker = None
        self.window._set_btns(False)
        self.window.traffic.setState(TrafficLight.GREEN)
        self.window.status_lbl.setText("Benchmark scenario failed.")

    def _run_analysis(self):
        # Time the full deferred analysis pass, including ROI analysis and any
        # enabled PCA/K-means settings.
        if self.current is None or self.current.get("error"):
            self._finalize_current()
            return
        t0 = time.perf_counter()
        try:
            self.window._on_analyze()
            self.current["analyze_seconds"] = time.perf_counter() - t0
        except Exception as exc:
            self.current["error"] = f"{exc}\n\n{traceback.format_exc()}"
            self.current["analyze_seconds"] = time.perf_counter() - t0
        self._finalize_current()

    def _sample_memory(self):
        # Timer callback that samples process memory while the scenario runs.
        if self.current is None:
            return
        mb = _process_memory_mb()
        if math.isfinite(mb):
            self.current["memory_samples_mb"].append(mb)

    def _finalize_current(self):
        # Convert raw samples for one scenario into a compact result row.
        self._mem_timer.stop()
        if self.current is None:
            self._run_next()
            return

        frame_ms = self.current["frame_handler_ms"]
        worker_ms = self.current["worker_frame_ms"]
        dropped_samples = self.current["worker_queue_dropped_frames"]
        intervals = self.current["frame_interval_ms"]
        mem = self.current["memory_samples_mb"]
        frames = int(self.current["frames"])
        acquisition_seconds = float(self.current.get("acquisition_seconds", 0.0))

        result = {
            "scenario": self.current["scenario"],
            "scenario_path": self.current["scenario_path"],
            "frames": frames,
            "acquisition_seconds": acquisition_seconds,
            "effective_fps": frames / acquisition_seconds
            if acquisition_seconds > 0 else math.nan,
            "on_frame_mean_ms": _mean(frame_ms),
            "on_frame_p95_ms": _percentile(frame_ms, 95),
            "on_frame_max_ms": max(frame_ms) if frame_ms else math.nan,
            "worker_frame_mean_ms": _mean(worker_ms),
            "worker_frame_p95_ms": _percentile(worker_ms, 95),
            "worker_frame_max_ms": max(worker_ms) if worker_ms else math.nan,
            "worker_queue_dropped_frames": max(dropped_samples)
            if dropped_samples else 0,
            "frame_interval_mean_ms": _mean(intervals),
            "frame_interval_p95_ms": _percentile(intervals, 95),
            "frame_interval_max_ms": max(intervals) if intervals else math.nan,
            "analyze_seconds": float(self.current.get("analyze_seconds", math.nan)),
            "memory_sample_count": len(mem),
            "peak_memory_mb": max(mem) if mem else math.nan,
            "final_memory_mb": mem[-1] if mem else math.nan,
            "error": self.current.get("error"),
        }
        self.results.append(result)
        if result["error"]:
            self.errors.append(result)

        self.window._memmap = None
        if not self.keep_temp and self.current_temp_dir:
            shutil.rmtree(self.current_temp_dir, ignore_errors=True)
        self.current = None
        self.current_temp_dir = None
        QtCore.QTimer.singleShot(0, self._run_next)

    def _finish(self):
        # Restore the original window methods and write both machine-readable and
        # human-readable output files.
        self.window._on_frame = self._orig_on_frame
        self.window._on_frame_processed = self._orig_on_frame_processed
        self.window._on_err = self._orig_on_err
        payload = {
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "scenario_count": len(self.results),
            "scenarios": self.results,
            "summary": self._summary(),
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        summary_path = self.output_path.with_suffix(".txt")
        summary_path.write_text(self._summary_text(payload), encoding="utf-8")
        print(self._summary_text(payload))
        print(f"\nJSON results: {self.output_path}")
        print(f"Text summary: {summary_path}")
        self.finished.emit(1 if self.errors else 0)

    def _summary(self):
        # Average successful scenarios and count failures/dropped frames.
        numeric_fields = [
            "effective_fps",
            "on_frame_mean_ms",
            "on_frame_p95_ms",
            "on_frame_max_ms",
            "worker_frame_mean_ms",
            "worker_frame_p95_ms",
            "worker_frame_max_ms",
            "frame_interval_mean_ms",
            "frame_interval_p95_ms",
            "frame_interval_max_ms",
            "analyze_seconds",
            "memory_sample_count",
            "peak_memory_mb",
            "final_memory_mb",
        ]
        summary = {
            "frames_total": sum(int(r["frames"]) for r in self.results),
            "worker_queue_dropped_frames_total": sum(
                int(r.get("worker_queue_dropped_frames",0))
                for r in self.results),
            "errors": len(self.errors),
        }
        for field in numeric_fields:
            values = [r.get(field, math.nan) for r in self.results
                      if not r.get("error")]
            summary[f"avg_{field}"] = _mean(values)
            summary[f"std_{field}"] = _std(values)
        return summary

    def _summary_text(self, payload):
        # Plain-text table for quick reading without opening JSON.
        lines = [
            "2D-SOR benchmark suite",
            f"Created: {payload['created_at']}",
            f"Scenarios: {payload['scenario_count']}",
            "",
            "Per-scenario results:",
            "scenario\tframes\tfps\ton_frame_mean_ms\ton_frame_p95_ms\t"
            "worker_mean_ms\tworker_p95_ms\tworker_dropped\tanalyze_s\t"
            "mem_samples\tpeak_mb\terror",
        ]
        for r in payload["scenarios"]:
            err = "yes" if r.get("error") else "no"
            lines.append(
                f"{r['scenario']}\t{r['frames']}\t"
                f"{r['effective_fps']:.3f}\t"
                f"{r['on_frame_mean_ms']:.3f}\t"
                f"{r['on_frame_p95_ms']:.3f}\t"
                f"{r['worker_frame_mean_ms']:.3f}\t"
                f"{r['worker_frame_p95_ms']:.3f}\t"
                f"{r['worker_queue_dropped_frames']}\t"
                f"{r['analyze_seconds']:.3f}\t"
                f"{r['memory_sample_count']}\t"
                f"{r['peak_memory_mb']:.1f}\t{err}"
            )
        s = payload["summary"]
        lines += [
            "",
            "Suite averages:",
            f"frames_total: {s['frames_total']}",
            f"avg_effective_fps: {s['avg_effective_fps']:.3f}",
            f"avg_on_frame_mean_ms: {s['avg_on_frame_mean_ms']:.3f}",
            f"avg_on_frame_p95_ms: {s['avg_on_frame_p95_ms']:.3f}",
            f"avg_on_frame_max_ms: {s['avg_on_frame_max_ms']:.3f}",
            f"avg_worker_frame_mean_ms: {s['avg_worker_frame_mean_ms']:.3f}",
            f"avg_worker_frame_p95_ms: {s['avg_worker_frame_p95_ms']:.3f}",
            f"avg_worker_frame_max_ms: {s['avg_worker_frame_max_ms']:.3f}",
            f"worker_queue_dropped_frames_total: "
            f"{s['worker_queue_dropped_frames_total']}",
            f"avg_analyze_seconds: {s['avg_analyze_seconds']:.3f}",
            f"avg_memory_sample_count: {s['avg_memory_sample_count']:.1f}",
            f"avg_peak_memory_mb: {s['avg_peak_memory_mb']:.1f}",
            f"avg_final_memory_mb: {s['avg_final_memory_mb']:.1f}",
            f"errors: {s['errors']}",
        ]
        return "\n".join(lines)


def parse_args():
    # Default to the repository's bundled test scenarios and a new timestamped
    # output folder.
    base_dir = Path(__file__).resolve().parent
    default_scenarios = base_dir.parent / "test scenarios"
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    default_output_dir = base_dir / "benchmark results" / stamp
    parser = argparse.ArgumentParser(
        description="Run all 2D-SOR JSON scenarios and summarize performance.")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=default_scenarios,
        help="Folder containing JSON scenario files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_dir / "benchmark_results.json",
        help=(
            "Output JSON path. By default, each run creates a timestamped "
            "folder under 'benchmark results'. A .txt summary is written beside it."
        ),
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary frame files created during benchmarking.",
    )
    parser.add_argument(
        "--show-window",
        action="store_true",
        help="Show the GUI window during the benchmark.",
    )
    return parser.parse_args()


def main():
    # Create an offscreen Qt app by default, then drive the normal DemoWindow
    # workflow programmatically.
    args = parse_args()
    scenarios = sorted(args.scenarios.glob("*.json"))
    if not scenarios:
        raise SystemExit(f"No JSON scenarios found in {args.scenarios}")

    app = QtWidgets.QApplication(sys.argv)

    for name in ("information", "warning", "critical"):
        setattr(QtWidgets.QMessageBox, name, staticmethod(lambda *a, **k: None))

    window = DemoWindow()
    if args.show_window:
        window.show()

    runner = BenchmarkRunner(
        window,
        scenarios,
        args.output,
        keep_temp=args.keep_temp,
    )
    exit_code = {"value": 0}
    runner.finished.connect(lambda code: (exit_code.__setitem__("value", code),
                                          app.quit()))
    QtCore.QTimer.singleShot(0, runner.start)
    app.exec()
    raise SystemExit(exit_code["value"])


if __name__ == "__main__":
    main()
