from loguru import logger
from .timer import Timer
from .metric_types import Metric

class PerfPlotQueueItem(dict):
    def __init__(self, process: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self['process'] = process
        self['data'] = {
            'type': None,       # Metric type
            'value': 0,         # Metric value
            'unit': None        # Metric unit
        }

        self.timer = Timer()
        self.timer.start()

    def track(self, metric_type: Metric):
        """Record a new measurement"""
        match metric_type:
            case Metric.DURATION:
                self.timer.delta()  # First call establishes the start time
            case Metric.COUNT:
                pass
            case _:
                logger.error(f"Unsupported metric type: {metric_type}")
    
    def end_track(self, metric_type: Metric) -> float:
        """End the measurement"""
        match metric_type:
            case Metric.DURATION:
                value = self.timer.delta() * 1000
            case Metric.COUNT:
                value = 1  # Each call counts as 1
            case _:
                logger.error(f"Unsupported metric type: {metric_type}")
                value = 0

        # Plotter expects data in a specific format
        self['data'].update({
            'type': metric_type.type_name,
            'value': value,
            'unit': metric_type.unit
        })

        logger.debug(f"Tracked {metric_type.type_name} for {self['process']}: {value} {metric_type.unit}")
        return value
    
