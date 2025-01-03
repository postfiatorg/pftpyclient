import os
import sys
import platform
import subprocess
import tempfile
from pathlib import Path

def create_shortcut():
    print("Creating shortcut...")
    current_location = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python_executable = sys.executable
    save_location = os.path.dirname(current_location)
    ico_location = os.path.join(current_location, 'images', 'simple_pf_logo.ico')
    png_location = os.path.join(current_location, 'images', 'simple_pf_logo.png')

    if platform.system() == 'Windows':
        create_windows_shortcut(current_location, python_executable, save_location, ico_location)
    elif platform.system() == 'Darwin':
        create_macos_shortcut(current_location, python_executable, save_location, png_location)
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

def set_macos_custom_icon(file_path: str, png_path: str) -> bool:
    """
    Set custom icon for a file on macOS using a PNG file.
    
    Args:
        file_path: Path to the file that needs a custom icon
        png_path: Path to the PNG icon file
        
    Returns:
        bool: True if successful, False otherwise
    """
    
    if not os.path.exists(file_path) or not os.path.exists(png_path):
        print(f"Either file {file_path} or icon {png_path} does not exist")
        return False
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Use sips to directly work with the PNG
            subprocess.run([
                '/usr/bin/sips', 
                '-i', 
                png_path
            ], check=True)
            
            # Extract icon resource
            subprocess.run([
                '/usr/bin/DeRez', 
                '-only', 
                'icns', 
                png_path
            ], stdout=open(tmp_path / 'icon.rsrc', 'w'), check=True)
            
            # Apply the resource to the target file
            subprocess.run([
                '/usr/bin/Rez', 
                '-append', 
                tmp_path / 'icon.rsrc', 
                '-o', 
                file_path
            ], check=True)
            
            # Set custom icon bit
            subprocess.run([
                '/usr/bin/SetFile', 
                '-a', 
                'C', 
                file_path
            ], check=True)
            
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error setting custom icon: {e}")
        if hasattr(e, 'stderr') and e.stderr:
            print(f"Error details: {e.stderr}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

def add_icon_to_macos_shortcut(command_file_path, icon_location):
    """
    Add custom icon to a macOS shortcut file.
    
    Args:
        command_file_path: Path to the .command file
        icon_location: Path to the icon file (PNG)
    """
    if not os.path.exists(icon_location):
        print(f"Warning: Icon file does not exist: {icon_location}")
        print("Continuing without setting the icon.")
        return
        
    success = set_macos_custom_icon(command_file_path, icon_location)
    if success:
        print(f"Successfully set custom icon for {command_file_path}")
    else:
        print("Failed to set custom icon. Continuing without icon.")

if __name__ == "__main__":
    create_shortcut()
