import multiprocessing
from pathlib import Path
from typing import Optional
from loguru import logger
from .perf_plotter import WalletPerformancePlotter
from .perf_plot_item import PerfPlotQueueItem
from .metric_types import Metric
from functools import wraps

class PerformanceMonitor:
    _instance = None

    def __init__(self, output_dir: Optional[Path] = None):
        self.queue = multiprocessing.Queue()
        self.plotter = None
        self.plotter_process = None
        self.output_dir = output_dir or Path.cwd() / "performance_logs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.monitors = {}
        self.stopped = False
        self.shutdown_event = multiprocessing.Event()

    @staticmethod
    def measure(process: str, *metrics: Metric):
        """Generic decorator that measures multiple metrics and sends data to the plotter
        
        Args:
            process: name of the process to measure
            *metrics: variable number of metrics to measure
        """

        metrics = metrics or (Metric.DURATION, Metric.COUNT,)

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                monitor = PerformanceMonitor._instance

                # If no monitor is active, just run the function
                if monitor is None:
                    return func(*args, **kwargs)
                
                logger.debug(f"Starting measurement for {process} ({[m.type_name for m in metrics]})")
                perf_item = monitor.monitors.get(process)

                # Create a new monitor if it doesn't exist
                if perf_item is None:
                    perf_item = monitor.create_monitor(process)
                    monitor.monitors[process] = perf_item

                # Start measurement
                for metric in metrics:
                    perf_item.track(metric)

                # Execute the function
                result = func(*args, **kwargs)

                # End measurement and send data to plotter queue
                for metric in metrics:
                    value = perf_item.end_track(metric)
                    monitor.queue.put({
                        'process': process,
                        'data': {
                            'type': metric.type_name,
                            'value': value,
                            'unit': metric.unit
                        }
                    })

                return result
            return wrapper
        return decorator

    def start(self):
        """Start the performance monitoring process"""
        if self.plotter_process is None:
            try:
                logger.debug("Starting plotter process")
                # Only pass the queue to the new process
                self.plotter_process = multiprocessing.Process(
                    target=self._start_plotter,
                    args=(self.queue,),
                    daemon=True
                )
                self.plotter_process.start()
                logger.info("Performance monitor started")
            except Exception as e:
                logger.error(f"Error starting plotter: {e}", exc_info=True)
                raise

    def _start_plotter(self, queue):
        """Start plotter in separate process"""
        try:
            plotter = WalletPerformancePlotter(queue, self.shutdown_event)
            plotter.start()
        except Exception as e:
            logger.error(f"Error in plotter process: {e}", exc_info=True)

    def stop(self):
        """Stop the performance monitoring process"""
        if self.plotter_process:
            try:
                self.queue.put(None)
                self.plotter_process.join(timeout=1.0)
                if self.plotter_process.is_alive():
                    self.plotter_process.terminate()
            except Exception as e:
                logger.error(f"Error stopping performance plotter: {e}")
            finally:
                self.plotter_process = None
                logger.info("Performance monitor stopped")

    def create_monitor(self, process: str) -> PerfPlotQueueItem:
        """Create a new performance monitor for a specific process"""
        return PerfPlotQueueItem(process=process)
