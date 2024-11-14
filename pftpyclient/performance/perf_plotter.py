from datetime import datetime
import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore
import multiprocessing
from itertools import cycle
from loguru import logger
from queue import Empty
from pathlib import Path
import platform
from .metric_types import Metric

def configure_plotter_logger():
    """Configure logger specifically for the plotter process"""
    import sys
    from pathlib import Path
    from loguru import logger

    logger.remove()

    logger.add(sys.stderr, level="DEBUG")

    log_path = Path.cwd() / "pftpyclient" / "logs" / "perf_plotter_debug.log"
    logger.add(log_path, rotation="10 MB", retention="1 week", level="DEBUG")
    return logger

class TimeAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def tickStrings(self, values, scale, spacing):
        return [datetime.fromtimestamp(v).strftime('%H:%M:%S') for v in values]

class WalletPerformancePlotter:
    """Class to plot performance metrics in a live view"""

    PERF_LOG_COLUMNS = [
        'timestamp',
        'process',
        'metric_type',
        'metric_value',
        'metric_unit',
        'platform',
        'python_version',
        'session_id'
    ]

    def __init__(self, queue: multiprocessing.Queue, shutdown_event: multiprocessing.Event):
        self.logger = configure_plotter_logger()
        self.logger.debug("Initializing WalletPerformancePlotter")

        self.queue = queue
        self.shutdown_event = shutdown_event
        self.app = pg.mkQApp("PftPyClient Stats")
        
        self.win = pg.GraphicsLayoutWidget(show=True)
        self.win.resize(700, 800)
        self.win.setWindowTitle('PftPyClient Stats')

        self.win.closeEvent = self.handle_close
        self.win.setWindowFlags(self.win.windowFlags())

        self.duration_plot = self.win.addPlot()
        self.duration_bars = pg.BarGraphItem(x=[], height=[], width=0.5)
        self.duration_plot.addItem(self.duration_bars)

        self.win.ci.layout.setSpacing(40)

        self.count_plot = self.win.addPlot(row=2, col=0)
        self.count_bars = pg.BarGraphItem(x=[], height=[], width=0.5)
        self.count_plot.addItem(self.count_bars)

        self.duration_data = {}
        self.count_data = {}

        # Setup colors
        self.colors = cycle(['c', 'r', 'b', 'g', 'w', 'y', 'm'])
        self.bar_colors = {}

        self.timer = None
        self.closed = False

        self.logger = logger

        # Setup performance metrics logging with pandas
        metrics_dir = Path.cwd() / "pftpyclient" / "logs"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        self.perf_log_path = metrics_dir / "performance_metrics.csv"
        self.perf_df = self._init_perf_log()

        self.last_save_time = datetime.now()
        self.save_interval = 300  # seconds

    def _init_perf_log(self):
        """Initialize or load the performance log DataFrame"""

        if self.perf_log_path.exists():
            try:
                return pd.read_csv(self.perf_log_path)
            except Exception as e:
                self.logger.error(f"Error reading performance log: {e}")

        return pd.DataFrame(columns=self.PERF_LOG_COLUMNS)
    
    def _save_metrics(self, force: bool = False):
        """Save metrics to CSV if interval has elapsed or force is True"""
        now = datetime.now()
        if force or (now - self.last_save_time).total_seconds() >= self.save_interval:
            try:
                self.perf_df.to_csv(self.perf_log_path, index=False)
                self.last_save_time = now
                self.logger.debug(f"Saved performance log to {self.perf_log_path}")
            except Exception as e:
                self.logger.error(f"Error saving performance log: {e}")

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
                try:
                    data = self.queue.get_nowait()

                    if data:
                        process_name = data.get('process')
                        metrics = data.get('data')

                        if process_name and metrics:
                            metric_type = Metric.from_type_name(metrics.get('type'))
                            metric_value = float(metrics.get('value', 0))

                            new_row = pd.DataFrame([{
                                'timestamp': datetime.now(),
                                'process': process_name,
                                'metric_type': metric_type.type_name, 
                                'metric_value': metric_value,
                                'metric_unit': metric_type.unit,
                                'platform': platform.system(),
                                'python_version': platform.python_version(),
                                'session_id': id(self)
                            }], columns=self.PERF_LOG_COLUMNS)
                            self.perf_df = pd.concat([self.perf_df, new_row], ignore_index=True)
                        
                            # Update live plot data
                            match metric_type:
                                case Metric.DURATION:
                                    self.duration_data[process_name] = {
                                        'duration': metric_value,
                                        'timestamp': datetime.now()
                                    }
                                case Metric.COUNT:
                                    if process_name not in self.count_data:
                                        self.count_data[process_name] = {
                                            'count': 0,
                                            'timestamp': datetime.now()
                                        }
                                    self.count_data[process_name]['count'] += 1
                                    self.count_data[process_name]['timestamp'] = datetime.now()

                except Empty:
                    break

            self._update_plots()
            self._save_metrics()

        except Exception as e:
            self.logger.error(f"Error processing queue: {e}", exc_info=True)

    def _update_plots(self):
        """Update all plots"""
        try:
            if self.duration_data:
                self._update_metric_plot(Metric.DURATION)

            if self.count_data:
                self._update_metric_plot(Metric.COUNT)

        except Exception as e:
            self.logger.error(f"Error updating plots: {e}", exc_info=True)

    def _update_metric_plot(self, metric_type: Metric):
        """Update a metric's bar chart"""
        try:
            value_key = f'{metric_type.type_name}'
            title = f"{metric_type.type_name} ({metric_type.unit})"

            match metric_type:
                case Metric.DURATION:
                    data_dict = self.duration_data
                    plot = self.duration_plot
                    bars = self.duration_bars
                case Metric.COUNT:
                    data_dict = self.count_data
                    plot = self.count_plot
                    bars = self.count_bars
                case _:
                    raise ValueError(f"Invalid metric type: {metric_type}")
        
            if not data_dict:
                return
            
            sorted_ops = sorted(
                [(name, data[value_key], data['timestamp'])
                  for name, data in data_dict.items()],
                key=lambda x: x[1],
                reverse=True
            )

            base_names = []
            display_names = []
            values = []

            # Create labels with elapsed time
            for op_name, duration, timestamp in sorted_ops:
                elapsed = datetime.now() - timestamp
                if elapsed.total_seconds() < 60:
                    time_str = f"{elapsed.total_seconds():.0f}s ago"
                else:
                    time_str = f"{elapsed.total_seconds() // 60:.0f}m {elapsed.total_seconds() - (elapsed.total_seconds() // 60) * 60:.0f}s ago"
                base_names.append(op_name)
                display_names.append(f"{op_name} ({time_str})")
                values.append(duration)

            # Assign colors to new processes
            for process, _, _ in sorted_ops:
                if process not in self.bar_colors:
                    self.bar_colors[process] = next(self.colors)

            # Create y positions for horizontal bars
            y_pos = np.arange(len(display_names))

            # Remove old bars and create new horizontal ones
            plot.removeItem(bars)
            bars = pg.BarGraphItem(
                x0=0,                # Starts bars at 0
                y=y_pos,            # Y position for each bar
                width=np.array(values, dtype=float),    # Bar length
                height=0.5,         # Bar thickness
                brushes=[self.bar_colors[name] for name in base_names]  # Color for each bar
            )
            plot.addItem(bars)

            # Update plot properties
            plot.setTitle(title)

            # Update axis labels
            left_axis = plot.getAxis('left')
            left_axis.setTicks([list(enumerate(display_names))])
            left_axis.setLabel('Processes')

            bottom_axis = plot.getAxis('bottom')
            bottom_axis.setLabel(metric_type.type_name)
            
            # Force x-axis to start at 0 and give 10% margin on the right
            plot.setXRange(0, max(values) * 1.1)
            # Adjust y-axis to fit all bars
            plot.setYRange(-0.5, len(display_names) - 0.5)

            # Store bars reference
            match metric_type:
                case Metric.DURATION:
                    self.duration_bars = bars
                case Metric.COUNT:
                    self.count_bars = bars

        except Exception as e:
            self.logger.error(f"Error updating {metric_type.type_name} plot: {e}", exc_info=True)
    
    # def _update_duration_plot(self):
    #     """Update the duration bar chart"""
    #     try:
    #         # Sort processes by duration for better visualization
    #         sorted_ops = sorted(
    #             [(name, data['duration'], data['timestamp']) 
    #              for name, data in self.duration_data.items()],
    #             key=lambda x: x[1],
    #             reverse=True
    #         )

    #         base_names = []
    #         display_names = []
    #         durations = []

    #         # Create labels with elapsed time
    #         for op_name, duration, timestamp in sorted_ops:
    #             elapsed = datetime.now() - timestamp
    #             if elapsed.total_seconds() < 60:
    #                 time_str = f"{elapsed.total_seconds():.0f}s ago"
    #             else:
    #                 time_str = f"{elapsed.total_seconds() // 60:.0f}m {elapsed.total_seconds() - (elapsed.total_seconds() // 60) * 60:.0f}s ago"
    #             base_names.append(op_name)
    #             display_names.append(f"{op_name} ({time_str})")
    #             durations.append(duration)

    #         # Assign colors to new processes
    #         for process, _, _ in sorted_ops:
    #             if process not in self.bar_colors:
    #                 self.bar_colors[process] = next(self.colors)
            
    #         # Create y positions for horizontal bars
    #         y_pos = np.arange(len(display_names))

    #         # Remove old bars and create new horizontal ones
    #         self.duration_plot.removeItem(self.duration_bars)
    #         self.duration_bars = pg.BarGraphItem(
    #             x0=0,                # Starts bars at 0
    #             y=y_pos,            # Y position for each bar
    #             width=np.array(durations, dtype=float),    # Bar length
    #             height=0.5,         # Bar thickness
    #             brushes=[self.bar_colors[name] for name in base_names]  # Color for each bar
    #         )
    #         self.duration_plot.addItem(self.duration_bars)

    #         # Update axis with labels
    #         left_axis = self.duration_plot.getAxis('left')
    #         left_axis.setTicks([list(enumerate(display_names))])
    #         left_axis.setLabel('Processes')

    #         bottom_axis = self.duration_plot.getAxis('bottom')
    #         bottom_axis.setLabel('Duration (ms)')

    #         # Force x-axis to start at 0 and give 10% margin on the right
    #         self.duration_plot.setXRange(0, max(durations) * 1.1)
    #         # Adjust y-axis to fit all bars
    #         self.duration_plot.setYRange(-0.5, len(display_names) - 0.5)

    #     except Exception as e:
    #         self.logger.error(f"Error updating plot: {e}", exc_info=True)

    @staticmethod
    def _append(arr, value):
        """Append value to numpy array, shifting existing values left"""
        arr[:-1] = arr[1:]
        arr[-1] = value

    def handle_close(self, event):
        """Handle the close event of the plotter window"""
        self._save_metrics(force=True)

        # Stop the timer
        if hasattr(self, 'timer'):
            self.timer.stop()

        self.shutdown_event.set()
        self.closed = True
        event.accept()
        self.app.quit()
