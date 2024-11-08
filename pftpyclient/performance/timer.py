import time
from datetime import datetime


def datetime_formatter(func):

    def in_datetime(seconds: float, utc: bool = False) -> datetime:
        dt = datetime.utcnow().timestamp() if utc else datetime.now().timestamp()
        delta = time.perf_counter() - seconds
        return datetime.fromtimestamp(dt - delta)

    def wrapper(*args, **kwargs):
        __time = func(*args, **kwargs)
        __format = kwargs.get("_format")
        match __format:
            case "datetime":
                return in_datetime(__time)
            case "datetime_utc":
                return in_datetime(__time, utc=True)
            case _:
                return __time

    return wrapper


def time_formatter(func):

    def in_hms(seconds: float) -> str:
        hours, rem = divmod(seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{int(hours):0>2}:{int(minutes):0>2}:{float(seconds):05.2f}"

    def wrapper(*args, **kwargs):
        __time = func(*args, **kwargs)
        __format = kwargs.get("_format")
        match __format:
            case "hms":
                return in_hms(__time)
            case _:
                return __time

    return wrapper


class Timer:
    def __init__(self) -> None:
        self.__start_time = None
        self.__last_time = None

    def __check_started(self) -> None:
        if self.__start_time is None:
            raise TimerError(f"Timer is not running yet. Use .start() to start it")

    def __check_startable(self) -> None:
        if self.__start_time is not None:
            raise TimerError(f"Timer is already running. Use .stop() to stop it")

    @datetime_formatter
    def get_start_time(self, *args, **kwargs):
        return self.__start_time

    @datetime_formatter
    def get_last_time(self, *args, **kwargs):
        return self.__last_time

    def start(self) -> None:
        """Start timer"""
        self.__check_startable()
        self.__start_time = time.perf_counter()

    def reset(self) -> None:
        """Reset timer"""
        self.__start_time = time.perf_counter()

    @time_formatter
    def lap(self, *args, **kwargs) -> float:
        """Returns time elapsed and resets timer."""
        self.__check_started()
        elapsed_time = time.perf_counter() - self.__start_time
        self.reset()
        return elapsed_time

    @time_formatter
    def elapsed(self, *args, **kwargs) -> float:
        """Return time elapsed from start."""
        self.__check_started()
        elapsed_time = time.perf_counter() - self.__start_time
        return elapsed_time

    @time_formatter
    def delta(self, *args, **kwargs) -> float:
        """Return time elapsed from last time this method was called.
        If first time calling this method, returns elapsed."""
        self.__check_started()
        now = time.perf_counter()

        if self.__last_time is None:
            delta = now - self.__start_time
        else:
            delta = now - self.__last_time

        self.__last_time = now

        return delta


class TimerError(Exception):
    """A custom exception used to report errors in use of Timer class"""
    pass


if __name__ == '__main__':
    wait = 0.1

    print("Timer 1 Test - elapsed and lap methods")
    timer1 = Timer()
    timer1.start()
    print("Timer 1 started. ", end='')
    print(f"timer1.get_start_time() = {timer1.get_start_time()}")
    time.sleep(0.1)
    print(f"Waited {wait} seconds.")
    print(f"timer1.elapsed() = {timer1.elapsed()}")
    print(f"timer1.elapsed(_format='hms') = {timer1.elapsed(_format='hms')}")
    print(f"timer1.lap() = {timer1.lap()}")
    time.sleep(0.1)
    print(f"Waited {wait} seconds.")
    print(f"timer1.lap(_format='hms') = {timer1.lap(_format='hms')}")
    time.sleep(0.1)
    print(f"Waited {wait} seconds.")
    print(f"timer1.delta() = {timer1.delta()}")
    time.sleep(0.1)
    print(f"Waited {wait} seconds.")
    print(f"timer1.delta(_format='hms') = {timer1.delta(_format='hms')}")
    print(f"timer1.get_start_time() = {timer1.get_start_time()}")
    print(f"timer1.get_start_time(_format='datetime') = {timer1.get_start_time(_format='datetime')}")


    print()
    print("Timer 2 Test - test exceptions")
    timer2 = Timer()

    try:
        timer2.elapsed()
    except TimerError as e:
        print(e)

    try:
        timer2.delta()
    except TimerError as e:
        print(e)

    try:
        timer2.lap()
    except TimerError as e:
        print(e)

    timer2.start()
    try:
        timer2.start()
    except TimerError as e:
        print(e)
