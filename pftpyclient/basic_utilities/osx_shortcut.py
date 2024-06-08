import os
import sys
import subprocess

def check_and_install_fileicon():
    try:
        # Check if fileicon is installed
        subprocess.run(['fileicon', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("fileicon is already installed.")
    except subprocess.CalledProcessError:
        print("fileicon not found. Attempting to install via Homebrew...")
        try:
            # Install Homebrew if not installed
            subprocess.run('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"', shell=True, check=True)
            # Install fileicon
            subprocess.run(['brew', 'install', 'fileicon'], check=True)
            print("fileicon installed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to install fileicon: {e}")
            print("Continuing without setting the icon.")
            return False
    return True

# Check and install fileicon if necessary
fileicon_installed = check_and_install_fileicon()

# Get the current location, adjust to be one directory up from the pftpyclient repo
current_location = os.getcwd().replace("/basic_utilities", "")
python_executable = sys.executable
pre_folder = current_location.split('pftpyclient')[0].split("/")[-2]
save_location = current_location.split(pre_folder)[0] + pre_folder
ico_location = current_location.split(pre_folder)[0] + pre_folder + '/pftpyclient/pftpyclient/images/simple_pf_logo.ico'
icns_location = os.path.join(save_location, 'simple_pf_logo.icns')

print(f"Current Location: {current_location}")
print(f"Save Location: {save_location}")
print(f"ICO Location: {ico_location}")
print(f"ICNS Location: {icns_location}")

# Prepare the script content
script = f"""
#!/bin/bash
source "{python_executable.replace('bin/python', 'bin/activate')}"
python "{current_location}/wallet_ux/prod_wallet.py"
"""

# Ensure the save directory exists
os.makedirs(save_location, exist_ok=True)

# Create the .sh file
sh_file_path = os.path.join(save_location, "run_prod_wallet.sh")
with open(sh_file_path, "w") as file:
    file.write(script)

# Make the .sh file executable
os.chmod(sh_file_path, 0o755)

# Create the .command file
command_file_path = os.path.join(save_location, "Post_Fiat_Wallet.command")
with open(command_file_path, "w") as file:
    file.write(f"#!/bin/bash\n{sh_file_path}\n")

# Make the .command file executable
os.chmod(command_file_path, 0o755)

print(f"Shortcut created: {command_file_path}")

# Check if icon file exists
if not os.path.exists(ico_location):
    print(f"Icon file does not exist: {ico_location}")
    sys.exit(1)

try:
    # Convert .ico to .icns using sips
    subprocess.run(['sips', '-s', 'format', 'icns', ico_location, '--out', icns_location], check=True)

    if fileicon_installed:
        # Use fileicon to set the icon
        subprocess.run(['fileicon', 'set', command_file_path, icns_location], check=True)
        print("Icon setting process completed.")
    else:
        print("fileicon is not installed. Skipping icon setting process.")

except subprocess.CalledProcessError as e:
    print(f"Error during processing: {e}")
    print("Make sure to install fileicon via Homebrew: brew install fileicon")
