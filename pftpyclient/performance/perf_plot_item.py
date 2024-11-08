from collections import deque
from datetime import datetime
import numpy as np
import copy
from typing import Optional
from multiprocessing import Queue
from loguru import logger
from .timer import Timer

class PerfPlotQueueItem(dict):
    def __init__(
            self, 
            process: str,
            track_duration: bool = True,
            track_count: bool = True,
            *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self['process'] = process
        self['timestamp'] = None
        self['elapsed'] = None
        self['data'] = {
            'duration': 0,
            'total': 0,
            'count': 0
        }

        if track_duration:
            self["data"].update({"total": None, "count": None})

        if track_count:
            self.total = 0
            self.count = 0

        self.timer = Timer()
        self.timer.start()

    def track(self, **kwargs) -> dict:
        """Record a new measurement"""
        if hasattr(self, "count"):
            self.total += 1
            self.count += 1

        self.timer.delta()  # First call establishes the start time
        return self._update()
    
    def end_track(self, **kwargs) -> dict:
        """End the measurement"""
        duration = self.timer.delta() * 1000
        self['data'].update({
            'duration': duration,
            'total': self.total,
            'count': self.count
        })
        logger.debug(f"Tracked duration for {self['process']}: {duration} ms")
        return self._update()
    
    def _update(self) -> dict:
        """Update internal state"""
        self["timestamp"] = datetime.now().timestamp()
        self["elapsed"] = self.timer.elapsed()

        if self["data"] is not None:
            if hasattr(self, "count"):
                self["data"]["total"] = self.total
                self["data"]["count"] = self.count

        return self
    
    def send_to_queue(self, queue: Queue):
        """Send current state to plotting queue"""
        self._update()
        queue.put(copy.deepcopy(self))
        size_after = queue.qsize()
        logger.debug(f"Put item in queue with id: {id(queue)}, size after: {size_after}")
        logger.debug(f"Sent to queue: process={self['process']}, data={self['data']}")
        self.reset()

    def reset(self):
        """Reset counters while preserving total"""
        if hasattr(self, "delays"):
            self.delays.clear()
        if hasattr(self, "count"):
            self.count = 0
        self._update()
        return self
    
