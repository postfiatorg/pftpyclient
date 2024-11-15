import os
import re
import datetime
import glob
from platform import system
from pathlib import Path
from pftpyclient.postfiatsecurity import hash_tools as pwl
from loguru import logger

def datetime_current_EST():
    '''EST should be used for all timestamps'''
    now = datetime.datetime.now()
    eastern_time = now.astimezone(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
    return eastern_time

def get_datadump_directory_path():
    '''Returns the path to the datadump directory, creating it if it does not exist'''
    home_dir = Path.home()
    datadump_dir = home_dir / "datadump"
    data_dir = datadump_dir / "data"
    
    datadump_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    
    return datadump_dir

# TODO: Change this from constant to variable
DATADUMP_DIRECTORY_PATH = get_datadump_directory_path()

def convert_directory_tuple_to_filename(directory_tuple):
    '''Converts a tuple of directory paths to a single path string'''
    string_list = []
    for item in directory_tuple:
        if isinstance(item, list):
            string_list.extend(item)
        else:
            string_list.append(item)
    
    return '/'.join(string_list)
