from loguru import logger
import sys
import datetime
from pathlib import Path
import wx

def wx_sink(message):
    if wx_sink.text_ctrl:
        wx.CallAfter(wx_sink.text_ctrl.AppendText, message)

wx_sink.text_ctrl = None

def configure_logger(
        log_to_file: bool = False,
        output_directory: Path = None,
        log_filename: str = None,
        level: str = None,
        text_ctrl = None,
):
    logger.remove()  # remove default logger

    if level not in {"CRITICAL", "WARNING", "INFO", "DEBUG", "TRACE"}:
        level = "INFO"

    # add file logger set to DEBUG level
    if log_to_file:
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        log_filename = f"log_{timestamp}.log" if log_filename is None else log_filename
        output_directory = Path.cwd() / "logs" if output_directory is None else output_directory / "logs"
        output_filepath = output_directory.joinpath(log_filename)
        logger.add(
            output_filepath, level="DEBUG", rotation="1 MB"
        )

    wx_sink.text_ctrl = text_ctrl
    logger.add(wx_sink, level=level)

    # add console logger with formatting
    logger_format = "<white>{time:YYYY-MM-DD HH:mm:ss.SSSSSS}</white> "
    logger_format += "--- <level>{level}</level> | Thread {thread} <level>{message}</level>"
    logger.add(
        sys.stdout, level=level,
        format=logger_format,
    )

    return wx_sink

def update_wx_sink(text_ctrl):
    wx_sink.text_ctrl = text_ctrl
