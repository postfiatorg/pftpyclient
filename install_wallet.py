import subprocess
import platform
import sys
import os
from pathlib import Path
import shutil
import logging
import argparse
from datetime import datetime

REPO_URL = "https://github.com/postfiatorg/pftpyclient.git"

def configure_logging(level: str = "INFO"):
    """Configure logging for installation using standard library"""
    logging.basicConfig(
        level=level,
        format='%(asctime)s.%(msecs)03d --- %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def get_git_root():
    try: 
        git_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).strip().decode("utf-8")
        logging.info(f"Git root: {git_root}")
        return Path(git_root)
    except subprocess.CalledProcessError:
        raise RuntimeError("Failed to determine the root of the git repository")

def get_package_root(local_path: Path = None) -> Path:
    """
    Get the root directory of the package, whether installed via pip or in development

    Args:
        local_path: Optional path to the package directory. If None, attempts to find it.

    Returns:
        Path to the package root directory
    """
    if local_path:
        return local_path
    
    try:
        # First try to find package in site-packages (pip installed)
        logging.info("Trying to find package in site-packages (pip installed)")
        import pftpyclient
        return Path(pftpyclient.__file__).parent.parent
    except ImportError:
        # Fallback to git root for development
        logging.info("Trying to find package in git root (development)")
        try:
            return get_git_root()
        except Exception as e:
            logging.warning(f"Failed to determine git root: {e}")
            pass

    # If all else fails, use correct working directory
    logging.warning("Failed to determine package root, using current working directory")
    return Path.cwd()

def activate_virtual_environment_and_install(env_name):
    os_type = platform.system()
    venv_activate = None

    project_root = get_package_root()
    logging.info(f"Installing package from {project_root}")

    extras = "[windows]" if os_type == "Windows" else ""

    try:
        if os_type == "Darwin" or os_type == "Linux":
            venv_activate = Path(env_name) / "bin" / "activate"
            command = f"source {venv_activate} && pip install -e {project_root}"
            subprocess.check_call(command, shell=True, executable="/bin/bash")

        elif os_type == "Windows":
            venv_activate = Path(env_name) / "Scripts" / "activate"
            command = f"{venv_activate} && pip install -e {project_root}{extras}"
            subprocess.check_call(command, shell=True)

        logging.info(f"Virtual environment {env_name} activated on {os_type} and installed {project_root}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Installation failed: {e}")
        raise

def get_desktop_path() -> Path:
    """Get the correct path to the user's desktop across different OS and configurations"""
    if platform.system() == "Windows":
        # On Windows, use the registry to get the correct Desktop path
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
    else:
        # On Unix-like systems, use XDG_DESKTOP_DIR if available, else fallback to ~/Desktop
        desktop_config = Path.home() / ".config/user-dirs.dirs"
        if desktop_config.exists():
            with open(desktop_config, 'r') as f:
                for line in f:
                    if line.startswith('XDG_DESKTOP_DIR'):
                        # Parse the XDG config line and expand ~ if present
                        desktop_path = line.split('=')[1].strip('"').strip("'").strip()
                        desktop_path = desktop_path.replace('$HOME', str(Path.home()))
                        desktop = Path(desktop_path)
                        break
                else:
                    desktop = Path.home() / "Desktop"
        else:
            desktop = Path.home() / "Desktop"
    
    logging.info(f"Found desktop path: {desktop}")
    return desktop

def move_shortcut_to_desktop(root: Path):
    """Move the generated shortcut to the desktop"""
    desktop = get_desktop_path()
    repo_path = root
    
    try:
        # Find the shortcut file (extension depends on OS)
        if platform.system() == "Windows":
            shortcut = next(repo_path.glob("*.lnk"))
            dest = desktop / shortcut.name
        else:
            shortcut = next(repo_path.glob("*.command"))
            dest = desktop / shortcut.name

        logging.info(f"Moving shortcut to desktop: {shortcut} -> {dest}")
            
        shutil.move(str(shortcut), str(dest))
        if platform.system() != "Windows":
            os.chmod(dest, 0o755)  # Make executable on Unix-like systems
        logging.info(f"Shortcut moved to desktop: {dest}")
    except StopIteration:
        logging.error("Shortcut file not found")
        raise
    except Exception as e:
        logging.error(f"Failed to move shortcut: {e}")
        raise

def destroy_virtual_environment(env_name):
    try:
        shutil.rmtree(env_name)
        logging.info(f"Virtual environment {env_name} destroyed")
    except Exception as e:
        logging.error(f"Failed to destroy virtual environment: {e}")

def create_virtual_environment(env_name):
    try:
        subprocess.check_call([sys.executable, "-m", "venv", env_name])
        logging.info(f"Virtual environment {env_name} created")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create virtual environment: {e}")
        sys.exit(1)

def create_shortcut(env_name):
    os_type = platform.system()
    try:
        if os_type == "Darwin" or os_type == "Linux":
            venv_activate = Path(env_name) / "bin" / "activate"
            command = f"source {venv_activate} && pft-shortcut"
            subprocess.check_call(command, shell=True, executable="/bin/bash")

        elif os_type == "Windows":
            venv_activate = Path(env_name) / "Scripts" / "activate"
            command = f"{venv_activate} && pft-shortcut"
            subprocess.check_call(command, shell=True)

        logging.info("Shortcut created successfully")

        return True

    except subprocess.CalledProcessError as e:
        logging.error(f"Verification failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Install Post Fiat Wallet")
    args = parser.parse_args()

    logger = configure_logging(level="DEBUG")
    env_name = "venv"

    root = get_package_root()

    try:
        # Remove existing environment if present
        if Path(env_name).exists():
            destroy_virtual_environment(env_name)
        
        # Create and activate virtual environment, then install
        create_virtual_environment(env_name)
        activate_virtual_environment_and_install(env_name)
        
        # Run shortcut creation and move to desktop
        create_shortcut(env_name)
        move_shortcut_to_desktop(root)
        
        logging.info("Installation completed successfully!")
        
    except Exception as e:
        logging.error(f"Installation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()