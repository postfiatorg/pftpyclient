import multiprocessing
from pathlib import Path
from typing import Optional
from loguru import logger
from .perf_plotter import WalletPerformancePlotter
from .perf_plot_item import PerfPlotQueueItem
from functools import wraps
import sys

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
    def measure_time(operation: str):
        """Decorator that measures execution time and sends data to the plotter"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):

                monitor = PerformanceMonitor._instance
                
                # If no monitor is active, just run the function
                if monitor is None:
                    return func(*args, **kwargs)

                logger.debug(f"Starting measurement for {operation}")
                perf_item = monitor.monitors.get(operation)
                if perf_item is None:
                    # Create a new monitor if it doesn't exist
                    perf_item = monitor.create_monitor(operation)
                    monitor.monitors[operation] = perf_item

                perf_item.track()
                result = func(*args, **kwargs)
                perf_item.end_track()
                
                perf_item.send_to_queue(monitor.queue)
                logger.debug(f"Finished measurement for {operation}")

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
