"""WiFi Space Mapper — real-time CSI visualizer (PyQtGraph + PySide6).

A modern, dark, tabbed desktop app that shows the Wi-Fi channel "move" in several
ways at once and runs the working motion detector — all on one shared streaming
engine (tools/csi_stream.py) and the validated detector (tools/detector.py).

Views
  Dashboard     raw amplitude + waterfall + motion + a big MOTION/STILL banner.
  Raw CSI       per-subcarrier amplitude with a fading trail, plus an I/Q constellation.
  Spectrogram   full scrolling waterfall (subcarrier x time, colour = amplitude).
  Motion        the detector: level vs threshold, phase, banner, Recalibrate.
  Radar         subcarriers around a circle, amplitude = radius — the "alive" view.
  Doppler       FFT of the motion signal -> dominant motion frequency.

Run
  python tools/wifi_visualizer.py                 # demo mode (synthetic, no hardware)
  python tools/wifi_visualizer.py --demo
  python tools/wifi_visualizer.py COM9 921600     # live ESP32 (laptop port)
  python tools/wifi_visualizer.py COM3 921600     # live ESP32 (desktop port)
  python tools/wifi_visualizer.py data/20260628/gon_20260628_take01.npz   # replay a capture

Close the ESP-IDF serial monitor first — only one program can hold the port. Opening
the port resets the board, so expect a few seconds of boot before frames in live mode.
"""

import sys
import time
from collections import deque

import numpy as np

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from csi_stream import CSIStream, make_source, SyntheticSource
from detector import MotionDetector

# --- palette (GitHub-dark-ish) -------------------------------------------------
BG       = "#0e1116"
PANEL    = "#161b22"
FG       = "#c9d1d9"
MUTED    = "#8b949e"
ACCENT   = "#6e4aff"   # project purple (matches the README badge)
CYAN     = "#39d0d8"
GREEN    = "#3fb950"
RED      = "#f85149"
AMBER    = "#d29922"
GRID     = "#21262d"

WF_TIME = 256          # waterfall width (time columns)
TRAIL   = 10           # ghost frames in the Raw CSI trail
MOT_HIST = 600         # motion-level points kept
SPEC_N   = 256         # samples used for the Doppler FFT

pg.setConfigOptions(antialias=True, background=BG, foreground=FG,
                    imageAxisOrder="row-major")


def cmap_lut(name="inferno", n=256):
    """Colormap lookup table, tolerant of pyqtgraph builds without matplotlib maps."""
    for cand in (name, "viridis", "CET-L9", "CET-L4"):
        try:
            return pg.colormap.get(cand).getLookupTable(0.0, 1.0, n)
        except Exception:
            continue
    return None


def plot_widget(title=None, xlabel=None, ylabel=None):
    pw = pg.PlotWidget()
    pw.setBackground(PANEL)
    pi = pw.getPlotItem()
    pi.showGrid(x=True, y=True, alpha=0.15)
    if title:
        pi.setTitle(title, color=MUTED, size="10pt")
    if xlabel:
        pi.setLabel("bottom", xlabel, color=MUTED)
    if ylabel:
        pi.setLabel("left", ylabel, color=MUTED)
    for ax in ("left", "bottom"):
        pi.getAxis(ax).setPen(GRID)
        pi.getAxis(ax).setTextPen(MUTED)
    return pw


def banner_label():
    lbl = QtWidgets.QLabel("—")
    lbl.setAlignment(QtCore.Qt.AlignCenter)
    f = lbl.font(); f.setPointSize(22); f.setBold(True); lbl.setFont(f)
    lbl.setMinimumHeight(56)
    return lbl


def set_banner(lbl, state):
    """Paint the MOTION/STILL/phase banner from a DetectorState."""
    if state is None or state.phase == "warmup":
        lbl.setText("WARMUP — measuring frame rate…")
        color = MUTED
    elif state.phase == "calibrate":
        lbl.setText(f"CALIBRATING — STAY STILL  ({state.calib_progress*100:.0f}%)")
        color = AMBER
    elif state.in_motion:
        lbl.setText(">>>  MOTION DETECTED  <<<")
        color = RED
    else:
        lbl.setText("still")
        color = GREEN
    lbl.setStyleSheet(f"color:{color}; background:{PANEL}; border-radius:8px;")


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
class View(QtWidgets.QWidget):
    title = "View"

    def update_view(self, s):
        """s is the SharedState snapshot built by the main window each tick."""
        raise NotImplementedError


class DashboardView(View):
    title = "Dashboard"

    def __init__(self):
        super().__init__()
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        self.banner = banner_label()
        grid.addWidget(self.banner, 0, 0, 1, 2)

        self.raw_pw = plot_widget("Raw CSI amplitude (current frame)",
                                  "subcarrier", "|H|")
        self.raw_curve = self.raw_pw.plot(pen=pg.mkPen(ACCENT, width=2))
        grid.addWidget(self.raw_pw, 1, 0)

        self.wf_pw = plot_widget("Spectrogram (subcarrier × time)", "time →", "subcarrier")
        self.wf_img = pg.ImageItem()
        lut = cmap_lut("inferno")
        if lut is not None:
            self.wf_img.setLookupTable(lut)
        self.wf_pw.addItem(self.wf_img)
        grid.addWidget(self.wf_pw, 1, 1)

        self.mot_pw = plot_widget("Motion level vs threshold", "recent frames", "level")
        self.mot_curve = self.mot_pw.plot(pen=pg.mkPen(GREEN, width=2))
        self.thr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(RED, style=QtCore.Qt.DashLine))
        self.thr_line.setVisible(False)
        self.mot_pw.addItem(self.thr_line)
        grid.addWidget(self.mot_pw, 2, 0, 1, 2)

        grid.setRowStretch(1, 3)
        grid.setRowStretch(2, 2)

    def update_view(self, s):
        set_banner(self.banner, s.det)
        if s.latest is not None:
            self.raw_curve.setData(s.latest.amp)
        if s.wf is not None:
            self.wf_img.setImage(s.wf, autoLevels=False, levels=(0, s.level_hi))
        if s.motion_hist:
            ys = np.array(s.motion_hist)
            self.mot_curve.setData(ys)
            in_motion = s.det is not None and s.det.in_motion
            self.mot_curve.setPen(pg.mkPen(RED if in_motion else GREEN, width=2))
        if s.det is not None and s.det.threshold is not None:
            self.thr_line.setVisible(True)
            self.thr_line.setValue(s.det.threshold)


class RawCSIView(View):
    title = "Raw CSI"

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        self.amp_pw = plot_widget("Per-subcarrier amplitude  (bold = now, faint = recent)",
                                  "subcarrier index", "|H|")
        # fading trail of recent frames behind the current one
        self.ghosts = []
        for i in range(TRAIL):
            a = int(25 + 60 * i / TRAIL)
            c = self.amp_pw.plot(pen=pg.mkPen(QtGui.QColor(110, 74, 255, a), width=1))
            self.ghosts.append(c)
        self.cur = self.amp_pw.plot(pen=pg.mkPen(CYAN, width=2.5))
        self._trail = deque(maxlen=TRAIL)
        lay.addWidget(self.amp_pw, 3)

        self.iq_pw = plot_widget("I/Q constellation  (real vs imag, per subcarrier)",
                                 "I (real)", "Q (imag)")
        self.iq_pw.setAspectLocked(True)
        self.iq = pg.ScatterPlotItem(size=6, pen=None, brush=pg.mkBrush(57, 208, 216, 160))
        self.iq_pw.addItem(self.iq)
        self.iq_note = pg.TextItem("I/Q not available for replay files", color=MUTED)
        self.iq_pw.addItem(self.iq_note)
        self.iq_note.setVisible(False)
        lay.addWidget(self.iq_pw, 2)

    def update_view(self, s):
        f = s.latest
        if f is None:
            return
        for ghost, past in zip(self.ghosts, list(self._trail)):
            ghost.setData(past)
        self.cur.setData(f.amp)
        self._trail.append(f.amp)
        if f.real is not None and f.imag is not None:
            self.iq.setData(f.real, f.imag)
            self.iq_note.setVisible(False)
        else:
            self.iq.setData([], [])
            self.iq_note.setVisible(True)


class SpectrogramView(View):
    title = "Spectrogram"

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.pw = plot_widget("Channel waterfall — each column is one frame; "
                              "colour = subcarrier amplitude", "time →", "subcarrier")
        self.img = pg.ImageItem()
        self.lut = cmap_lut("inferno")
        if self.lut is not None:
            self.img.setLookupTable(self.lut)
        self.pw.addItem(self.img)
        lay.addWidget(self.pw)
        # colour scale legend
        bar = pg.ColorBarItem(values=(0, 1), colorMap=pg.colormap.get("inferno")
                              if "inferno" in pg.colormap.listMaps() else None)
        try:
            bar.setImageItem(self.img, insert_in=self.pw.getPlotItem())
        except Exception:
            pass  # colorbar is cosmetic; never let it break the view

    def update_view(self, s):
        if s.wf is not None:
            self.img.setImage(s.wf, autoLevels=False, levels=(0, s.level_hi))


class MotionView(View):
    title = "Motion"

    def __init__(self, on_recalibrate=None):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        top = QtWidgets.QHBoxLayout()
        self.banner = banner_label()
        top.addWidget(self.banner, 1)
        self.recal_btn = QtWidgets.QPushButton("Recalibrate")
        self.recal_btn.setToolTip("Re-learn the still baseline, then stay still ~8 s")
        if on_recalibrate:
            self.recal_btn.clicked.connect(on_recalibrate)
        top.addWidget(self.recal_btn)
        lay.addLayout(top)

        self.pw = plot_widget("Motion level over time", "recent frames", "level")
        self.curve = self.pw.plot(pen=pg.mkPen(GREEN, width=2))
        self.thr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(RED, style=QtCore.Qt.DashLine),
                                        label="threshold", labelOpts={"color": RED})
        self.thr_line.setVisible(False)
        self.pw.addItem(self.thr_line)
        lay.addWidget(self.pw)

        self.info = QtWidgets.QLabel("—")
        self.info.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(self.info)

    def update_view(self, s):
        set_banner(self.banner, s.det)
        if s.motion_hist:
            ys = np.array(s.motion_hist)
            self.curve.setData(ys)
            in_motion = s.det is not None and s.det.in_motion
            self.curve.setPen(pg.mkPen(RED if in_motion else GREEN, width=2))
        d = s.det
        if d is not None and d.threshold is not None:
            self.thr_line.setVisible(True)
            self.thr_line.setValue(d.threshold)
        if d is not None:
            lvl = "—" if d.motion_level is None else f"{d.motion_level:.4f}"
            thr = "—" if d.threshold is None else f"{d.threshold:.4f}"
            fps = "—" if d.fps is None else f"{d.fps:.0f}"
            self.info.setText(f"phase: {d.phase}    level: {lvl}    "
                              f"threshold: {thr}    locked fps: {fps}    "
                              f"subcarriers: {d.target_len or '—'}")


class RadarView(View):
    title = "Radar"

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.pw = plot_widget("Radial CSI — subcarriers around the circle, "
                              "amplitude = radius")
        self.pw.setAspectLocked(True)
        self.pw.hideAxis("left")
        self.pw.hideAxis("bottom")
        # static guide circles
        th = np.linspace(0, 2 * np.pi, 100)
        for r in (0.33, 0.66, 1.0):
            self.pw.plot(r * np.cos(th), r * np.sin(th),
                         pen=pg.mkPen(GRID, width=1))
        self.fill = self.pw.plot(pen=pg.mkPen(ACCENT, width=2),
                                 fillLevel=0, brush=pg.mkBrush(110, 74, 255, 60))
        self.curve = self.pw.plot(pen=pg.mkPen(CYAN, width=2))
        self.dots = pg.ScatterPlotItem(size=4, pen=None, brush=pg.mkBrush(57, 208, 216, 200))
        self.pw.addItem(self.dots)

    def update_view(self, s):
        f = s.latest
        if f is None or len(f.amp) == 0:
            return
        amp = f.amp
        r = amp / (amp.max() + 1e-6)
        th = np.linspace(0, 2 * np.pi, len(r), endpoint=False)
        x, y = r * np.cos(th), r * np.sin(th)
        x = np.append(x, x[0]); y = np.append(y, y[0])    # close the loop
        self.curve.setData(x, y)
        self.dots.setData(x, y)


class DopplerView(View):
    title = "Doppler"

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.pw = plot_widget("Motion spectrum — frequency content of the motion signal",
                              "frequency (Hz)", "power")
        self.curve = self.pw.plot(pen=pg.mkPen(CYAN, width=2),
                                  fillLevel=0, brush=pg.mkBrush(57, 208, 216, 70))
        self.peak = pg.InfiniteLine(angle=90, pen=pg.mkPen(AMBER, style=QtCore.Qt.DashLine))
        self.peak.setVisible(False)
        self.pw.addItem(self.peak)
        self.label = QtWidgets.QLabel("Collecting motion history…")
        self.label.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(self.pw)
        lay.addWidget(self.label)

    def update_view(self, s):
        hist = s.motion_hist
        fps = s.det.fps if (s.det and s.det.fps) else None
        if not fps or len(hist) < 64:
            return
        x = np.array(hist[-SPEC_N:], dtype=float)
        x = x - x.mean()
        x = x * np.hanning(len(x))
        mag = np.abs(np.fft.rfft(x))
        freqs = np.fft.rfftfreq(len(x), d=1.0 / fps)
        self.curve.setData(freqs, mag)
        if len(mag) > 2:
            k = 1 + int(np.argmax(mag[1:]))   # skip DC
            self.peak.setVisible(True)
            self.peak.setValue(freqs[k])
            self.label.setText(f"dominant motion frequency ≈ {freqs[k]:.2f} Hz "
                               f"(human motion is ~0.5–10 Hz)")


# --------------------------------------------------------------------------- #
# Shared per-tick state
# --------------------------------------------------------------------------- #
class SharedState:
    __slots__ = ("latest", "det", "wf", "level_hi", "motion_hist", "fps", "rssi", "C")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, stream, refresh_ms=33):
        super().__init__()
        self.stream = stream
        self.det = MotionDetector()
        self.C = None
        self.wf = None
        self.level_hi = 1.0
        self.motion_hist = deque(maxlen=MOT_HIST)
        self.paused = False

        self.setWindowTitle("WiFi Space Mapper — CSI Visualizer")
        self.resize(1200, 760)
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{BG}; color:{FG};
                font-family:'Segoe UI','Helvetica Neue',sans-serif; }}
            QTabWidget::pane {{ border:1px solid {GRID}; }}
            QTabBar::tab {{ background:{PANEL}; color:{MUTED}; padding:7px 16px;
                border:1px solid {GRID}; border-bottom:none; }}
            QTabBar::tab:selected {{ color:{FG}; border-top:2px solid {ACCENT}; }}
            QPushButton {{ background:{PANEL}; color:{FG}; border:1px solid {GRID};
                border-radius:6px; padding:6px 14px; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
            QToolBar {{ background:{PANEL}; border-bottom:1px solid {GRID}; spacing:6px; }}
        """)

        self._build_toolbar()
        self._build_telemetry()

        self.tabs = QtWidgets.QTabWidget()
        self.views = [
            DashboardView(),
            RawCSIView(),
            SpectrogramView(),
            MotionView(on_recalibrate=self.det.recalibrate),
            RadarView(),
            DopplerView(),
        ]
        for v in self.views:
            self.tabs.addTab(v, v.title)

        central = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self.telemetry)
        col.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(refresh_ms)

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.pause_act = QtGui.QAction("⏸ Pause", self, checkable=True)
        self.pause_act.toggled.connect(self._toggle_pause)
        tb.addAction(self.pause_act)
        recal = QtGui.QAction("⟳ Recalibrate", self)
        recal.triggered.connect(self.det.recalibrate)
        tb.addAction(recal)

    def _build_telemetry(self):
        self.telemetry = QtWidgets.QFrame()
        self.telemetry.setStyleSheet(f"background:{PANEL}; border-bottom:1px solid {GRID};")
        row = QtWidgets.QHBoxLayout(self.telemetry)
        row.setContentsMargins(14, 6, 14, 6)

        def chip(text):
            l = QtWidgets.QLabel(text)
            l.setStyleSheet(f"color:{MUTED}; font-size:12px;")
            return l

        self.dot = QtWidgets.QLabel("●")
        self.dot.setStyleSheet(f"color:{RED}; font-size:14px;")
        self.src_lbl = chip(f"source: {self.stream.label}")
        self.fps_lbl = chip("fps: —")
        self.rssi_lbl = chip("rssi: —")
        self.sc_lbl = chip("subcarriers: —")
        self.state_lbl = QtWidgets.QLabel("WARMUP")
        self.state_lbl.setStyleSheet(f"color:{MUTED}; font-weight:bold;")

        for w in (self.dot, self.src_lbl, self.fps_lbl, self.rssi_lbl, self.sc_lbl):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.state_lbl)

    def _toggle_pause(self, on):
        self.paused = on
        self.pause_act.setText("▶ Resume" if on else "⏸ Pause")

    def _ingest(self, frames):
        """Feed drained frames into the detector + waterfall + motion history."""
        for f in frames:
            st = self.det.update(f.amp, f.t)
            c = len(f.amp)
            if self.C != c:                       # (re)size buffers to the frame width
                self.C = c
                self.wf = np.zeros((c, WF_TIME), dtype=float)
            self.wf = np.roll(self.wf, -1, axis=1)
            self.wf[:, -1] = f.amp
            if st.motion_level is not None:
                self.motion_hist.append(st.motion_level)
        if frames and self.wf is not None:
            hi = float(np.percentile(self.wf, 99)) or 1.0
            self.level_hi = 0.9 * self.level_hi + 0.1 * max(hi, 1e-3)

    def _snapshot(self):
        s = SharedState()
        s.latest = self.stream.latest()
        s.det = self.det._snapshot()
        s.wf = self.wf
        s.level_hi = self.level_hi
        s.motion_hist = list(self.motion_hist)
        s.fps = self.stream.fps
        s.rssi = self.stream.rssi
        s.C = self.C
        return s

    def _tick(self):
        self._ingest(self.stream.drain())
        s = self._snapshot()

        # telemetry (always, even when paused, so the header stays honest)
        live = s.fps > 1.0
        self.dot.setStyleSheet(f"color:{GREEN if live else RED}; font-size:14px;")
        self.fps_lbl.setText(f"fps: {s.fps:.0f}")
        self.rssi_lbl.setText(f"rssi: {s.rssi if s.rssi is not None else '—'} dBm")
        self.sc_lbl.setText(f"subcarriers: {s.C if s.C else '—'}")
        d = s.det
        if d.phase == "detect":
            txt, col = (("MOTION", RED) if d.in_motion else ("STILL", GREEN))
        elif d.phase == "calibrate":
            txt, col = "CALIBRATING", AMBER
        else:
            txt, col = "WARMUP", MUTED
        self.state_lbl.setText(txt)
        self.state_lbl.setStyleSheet(f"color:{col}; font-weight:bold;")

        if not self.paused:
            self.tabs.currentWidget().update_view(s)

    def closeEvent(self, e):
        self.timer.stop()
        self.stream.stop()
        super().closeEvent(e)


def main():
    args = [a for a in sys.argv[1:] if a != "--smoketest"]
    smoketest = "--smoketest" in sys.argv

    spec = args[0] if args else "--demo"
    baud = int(args[1]) if len(args) > 1 else 921600

    app = QtWidgets.QApplication(sys.argv)

    try:
        source = make_source(spec, baud)
    except RuntimeError as exc:
        QtWidgets.QMessageBox.warning(None, "Serial error",
                                      f"{exc}\n\nFalling back to demo mode.")
        source = SyntheticSource()

    stream = CSIStream(source).start()
    win = MainWindow(stream)
    win.show()

    if smoketest:
        # Headless construction + a few update ticks, then quit (CI / no-display check).
        QtCore.QTimer.singleShot(2500, app.quit)

    code = app.exec()
    stream.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
