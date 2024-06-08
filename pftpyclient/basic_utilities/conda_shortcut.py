import os
import sys
import winshell

## For reference this is the current location. could we make this script more robust so it doesnt hardcode Github it is just 1 directory up from the pftpyclient repo
## (ladyofpain) C:\Users\gooda\OneDrive\Documents\GitHub\pftpyclient\pftpyclient\basic_utilities>

current_location = os.getcwd().replace("\\basic_utilities", "")
python_executable = sys.executable
pre_folder = current_location.split('pftpyclient')[0].split("\\")[-2]
print(pre_folder)
save_location = current_location.split(pre_folder)[0] + pre_folder
ico_location = current_location.split(pre_folder)[0] + pre_folder+ r'\pftpyclient\pftpyclient\images\simple_pf_logo.ico'
#print(ico_location)

python_executable = sys.executable
script = f"""
@echo off
set "CONDA_PYTHON={python_executable}"
set "SCRIPT_PATH={current_location}\\wallet_ux\\prod_wallet.py"
if exist "%CONDA_PYTHON%" (
    "%CONDA_PYTHON%" "%SCRIPT_PATH%"
) else (
    echo Anaconda Python executable not found. Please check the installation path.
)
pause
"""

os.makedirs(save_location, exist_ok=True)
bat_file_path = os.path.join(save_location, "run_prod_wallet.bat")
with open(bat_file_path, "w") as file:
    file.write(script)

# Create a shortcut (.lnk) file for the batch script
shortcut_path = os.path.join(save_location, "Post Fiat Wallet.lnk")
with winshell.shortcut(shortcut_path) as shortcut:
    shortcut.path = bat_file_path
    shortcut.description = "Shortcut to run the Post Fiat Wallet Bash Script"
    shortcut.icon_location = (ico_location, 0)

#print(f"Shortcut created: {shortcut_path}")