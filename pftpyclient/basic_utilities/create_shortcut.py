import os
import sys
import platform
import subprocess

def create_shortcut():
    print("Creating shortcut...")
    current_location = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python_executable = sys.executable
    save_location = os.path.dirname(current_location)
    ico_location = os.path.join(current_location, 'images', 'simple_pf_logo.ico')

    if platform.system() == 'Windows':
        create_windows_shortcut(current_location, python_executable, save_location, ico_location)
    elif platform.system() == 'Darwin':
        create_macos_shortcut(current_location, python_executable, save_location, ico_location)
    else:
        print("Unsupported operating system. Cannot create shortcut.")

def create_windows_shortcut(current_location, python_executable, save_location, ico_location):
    from win32com.client import Dispatch

    shortcut_path = os.path.join(save_location, 'Post Fiat Wallet.lnk')

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.Targetpath = python_executable
    shortcut.Arguments = '-m pftpyclient.wallet_ux.prod_wallet'
    shortcut.WorkingDirectory = save_location
    shortcut.IconLocation = ico_location
    shortcut.save()

    print(f"Windows shortcut created: {shortcut_path}")

def create_macos_shortcut(current_location, python_executable, save_location, ico_location):

    script = f"""
#!/bin/bash
set -e

# Function to find and activate virtual environment
activate_venv() {{
    possible_venv_paths=(
        "{os.path.dirname(os.path.dirname(python_executable))}/bin/activate"
        "{os.path.dirname(current_location)}/venv/bin/activate"
        "{os.path.dirname(current_location)}/.venv/bin/activate"
        "{os.path.dirname(current_location)}/pftest/bin/activate"
    )

    for venv_path in "${{possible_venv_paths[@]}}"; do
        if [ -f "$venv_path" ]; then
            echo "Activating virtual environment: $venv_path"
            source "$venv_path"
            return 0
        fi
    done

    echo "No virtual environment found. Using system Python."
    return 1
}}

echo "Starting Post Fiat Wallet..."

# Change to the repository root directory
cd "{os.path.dirname(current_location)}"

# Try to activate virtual environment
activate_venv

# Run the application
python -m pftpyclient.wallet_ux.prod_wallet

echo "Post Fiat Wallet closed."
read -p "Press Enter to exit..."
"""

    command_file_path = os.path.join(save_location, 'Post Fiat Wallet.command')
    with open(command_file_path, 'w') as file:
        file.write(script)

    os.chmod(command_file_path, 0o755)

    print(f"MacOS shortcut created: {command_file_path}")

    add_icon_to_macos_shortcut(command_file_path, ico_location)

def add_icon_to_macos_shortcut(command_file_path, ico_location):

    def check_and_install_fileicon():
        try:
            # Check if fileicon is installed
            subprocess.run(['fileicon', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("fileicon is already installed.")
            return True
        except FileNotFoundError:
            print("fileicon not found. It's required to set custom icons.")
            user_input = input("Would you like to install Homebrew and fileicon? (y/n): ").lower()
            if user_input == 'y':
                try:
                    print("Installing Homebrew...")
                    subprocess.run('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"', shell=True, check=True)
                    print("Installing fileicon...")
                    subprocess.run(['brew', 'install', 'fileicon'], check=True)
                    print("fileicon installed successfully.")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"Failed to install fileicon: {e}")
            print("Continuing without setting the icon.")
            return False
        
    if not os.path.exists(ico_location):
        print(f"Warning: Icon file does not exist: {ico_location}")
        print("Continuing without setting the icon.")
        return
    
    # Check and install fileicon if necessary
    fileicon_installed = check_and_install_fileicon()

    if fileicon_installed:
        try:
            icns_location = ico_location.rsplit('.', 1)[0] + '.icns'
            # Convert .ico to .icns using sips
            subprocess.run(['sips', '-s', 'format', 'icns', ico_location, '--out', icns_location], check=True)

            # Use fileicon to set the icon
            subprocess.run(['fileicon', 'set', command_file_path, icns_location], check=True)
            print("Icon setting process completed.")

            # Clean up the temporary .icns file
            os.remove(icns_location)
        except subprocess.CalledProcessError as e:
            print(f"Error during icon processing: {e}")
            print("Icon setting failed. The shortcut will use the default icon.")
    else:
        print("fileicon is not installed. Skipping icon setting process.")

if __name__ == "__main__":
    create_shortcut()
