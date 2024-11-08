from datetime import datetime, timedelta
import numpy as np
from collections import defaultdict
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore
import platform
import psutil
import multiprocessing
from itertools import cycle
from loguru import logger
from typing import Optional, Dict, Any
from queue import Empty
import os
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

SYNC_OPERATIONS = [
    "update_account_info",
    "run_bg_job",
    "update_account",
    "update_tokens",
    "update_data",
    "update_grid",
    "populate_grid_generic",
    "populate_summary_grid",
    "on_force_update",
]

TASK_OPERATIONS = [
    "on_request_task",
    "on_accept_task",
    "on_refuse_task",
    "on_submit_for_verification",
    "on_submit_verification_details",
    "on_log_pomodoro",
    "on_submit_memo",
    "on_submit_xrp_payment",
    "on_submit_pft_payment",
]

def configure_plotter_logger():
    """Configure logger specifically for the plotter process"""
    import sys
    from pathlib import Path
    from loguru import logger

    logger.remove()

    logger.add(sys.stderr, level="DEBUG")

    log_path = Path.cwd() / "pftpyclient" / "logs" / "perf_plotter.log"
    logger.add(log_path, rotation="10 MB", retention="1 week", level="DEBUG")
    return logger

class TimeAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def tickStrings(self, values, scale, spacing):
        return [datetime.fromtimestamp(v).strftime('%H:%M:%S') for v in values]

class WalletPerformancePlotter:
    def __init__(self, queue: multiprocessing.Queue, shutdown_event: multiprocessing.Event):
        self.logger = configure_plotter_logger()
        self.logger.debug("Initializing WalletPerformancePlotter")

        self.queue = queue
        self.shutdown_event = shutdown_event
        self.app = pg.mkQApp("PftPyClient Stats")
        
        self.win = pg.GraphicsLayoutWidget(show=True)
        self.win.resize(600, 400)
        self.win.setWindowTitle('PftPyClient Stats')

        self.win.closeEvent = self.handle_close
        self.win.setWindowFlags(self.win.windowFlags())

        self.plot = self.win.addPlot()
        self.plot.setTitle("Durations (ms)")

        self.bars = pg.BarGraphItem(x=[], height=[], width=0.5, brush='r')
        self.plot.addItem(self.bars)

        self.data = {}

        # Setup colors
        self.colors = cycle(['c', 'r', 'b', 'g', 'w', 'y', 'm'])
        self.bar_colors = {}

        self.timer = None
        self.closed = False

        self.logger = logger

    def start(self):
        """Start the plotter and Qt event loop"""
        self.logger.debug(f"Starting plotter")

        # Create and start the update timer
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._process_queue)
        self.timer.start(1000)  # 1 Hz update rate
        self.win.show()
        self.app.exec_()

    def _process_queue(self):
        """Process the performance data queue"""
        try:
            while not self.queue.empty():
                # self.logger.debug(f"Queue size: {self.queue.qsize()}. Processing...")
                try:
                    data = self.queue.get_nowait()
                    # self.logger.debug(f"Received data: {data}")

                    if data:
                        process_name = data.get('process')
                        metrics = data.get('data')
                        # self.logger.debug(f"Processing {process_name}: {metrics}")

                        if process_name and metrics:
                            self.data[process_name] = {
                                'duration': float(metrics.get('duration', 0)),
                                'timestamp': datetime.now()
                            }

                except Empty:
                    break

            self._update_plot()

        except Exception as e:
            self.logger.error(f"Error processing queue: {e}", exc_info=True)

    def _update_plot(self):
        """Update the bar chart with current data"""
        try:
            if not self.data:
                return

            # Sort operations by duration for better visualization
            sorted_ops = sorted([(name, data['duration'], data['timestamp']) for name, data in self.data.items()],
                key=lambda x: x[1],
                reverse=True
            )    

            base_names = []
            display_names = []
            durations = []

            # Create labels with elapsed time
            for op_name, duration, timestamp in sorted_ops:
                elapsed = datetime.now() - timestamp
                if elapsed.total_seconds() < 60:
                    time_str = f"{elapsed.total_seconds():.0f}s ago"
                else:
                    time_str = f"{elapsed.total_seconds() // 60:.0f}m {elapsed.total_seconds() - (elapsed.total_seconds() // 60) * 60:.0f}s ago"
                base_names.append(op_name)
                display_names.append(f"{op_name} ({time_str})")
                durations.append(duration)

            # Assign colors to new operations
            for operation, _, _ in sorted_ops:
                if operation not in self.bar_colors:
                    self.bar_colors[operation] = next(self.colors)
            
            # Create y positions for horizontal bars
            y_pos = np.arange(len(display_names))

            # Remove old bars and create new horizontal ones
            self.plot.removeItem(self.bars)
            self.bars = pg.BarGraphItem(
                x0=0,                # Starts bars at 0
                y=y_pos,            # Y position for each bar
                width=np.array(durations, dtype=float),    # Bar length
                height=0.5,         # Bar thickness
                brushes=[self.bar_colors[name] for name in base_names]  # Color for each bar
            )
            self.plot.addItem(self.bars)

            # Update axis with labels
            left_axis = self.plot.getAxis('left')
            left_axis.setTicks([list(enumerate(display_names))])
            left_axis.setLabel('Operations')

            bottom_axis = self.plot.getAxis('bottom')
            bottom_axis.setLabel('Duration (ms)')

            # Force x-axis to start at 0 and give 10% margin on the right
            self.plot.setXRange(0, max(durations) * 1.1)
            # Adjust y-axis to fit all bars
            self.plot.setYRange(-0.5, len(display_names) - 0.5)

        except Exception as e:
            self.logger.error(f"Error updating plot: {e}", exc_info=True)

    def _update_mem_plot(self):
        """Update memory usage plot"""
        self.mem_plot.clear()
        if "system" in self.data:
            self.mem_plot.plot(
                self.data["system"]["timestamp"],
                self.data["system"]["data"]["memory_percent"],
                pen='g'
            )

    def _update_cpu_plot(self):
        """Update CPU usage plot"""
        self.cpu_plot.clear()
        if "system" in self.data:
            self.cpu_plot.plot(
                self.data["system"]["timestamp"],
                self.data["system"]["data"]["cpu_percent"],
                pen='r'
            )

    @staticmethod
    def _append(arr, value):
        """Append value to numpy array, shifting existing values left"""
        arr[:-1] = arr[1:]
        arr[-1] = value

    def handle_close(self, event):
        """Handle the close event of the plotter window"""
        # Stop the timer
        if hasattr(self, 'timer'):
            self.timer.stop()

        self.shutdown_event.set()
        self.closed = True
        event.accept()
        self.app.quit()
