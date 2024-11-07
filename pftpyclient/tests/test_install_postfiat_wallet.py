import subprocess
import platform
import sys
import os
from pathlib import Path
import shutil
import logging
import json
from datetime import datetime

def configure_test_logging(output_directory: Path, log_filename: str, level: str = "INFO"):
    """Configure logging for installation testing using standard library"""
    log_dir = output_directory / "logs"
    log_dir.mkdir(exist_ok=True)

    # Configure logging
    log_path = log_dir / log_filename
    logging.basicConfig(
        level=level,
        format='%(asctime)s.%(msecs)03d --- %(levelname)s | Thread %(thread)d %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def get_system_info():
    """Collect system environment information"""
    return {
        'os_name': platform.system(),
        'os_version': platform.version(),
        'os_release': platform.release(),
        'python_version': sys.version,
        'platform_architecture': platform.architecture(),
        'processor': platform.processor()
    }

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

def get_git_root():
    try: 
        git_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).strip().decode("utf-8")
        logging.info(f"Git root: {git_root}")
        return Path(git_root)
    except subprocess.CalledProcessError:
        raise RuntimeError("Failed to determine the root of the git repository")

def create_virtual_environment(env_name):
    try:
        subprocess.check_call([sys.executable, "-m", "venv", env_name])
        logging.info(f"Virtual environment {env_name} created")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create virtual environment: {e}")
        sys.exit(1)

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

def verify_wallet_installation(env_name):
    os_type = platform.system()
    try:
        if os_type == "Darwin" or os_type == "Linux":
            venv_activate = Path(env_name) / "bin" / "activate"
            command = f"source {venv_activate} && pft-shortcut &&pft"
            subprocess.check_call(command, shell=True, executable="/bin/bash")

        elif os_type == "Windows":
            venv_activate = Path(env_name) / "Scripts" / "activate"
            command = f"{venv_activate} && pft-shortcut && pft"
            subprocess.check_call(command, shell=True)

        logging.info("Wallet installation verified successfully with commands 'pft-shortcut' and 'pft'")
        return True

    except subprocess.CalledProcessError as e:
        logging.error(f"Verification failed: {e}")
        return False

def destroy_virtual_environment(env_name):
    try:
        shutil.rmtree(env_name)
        logging.info(f"Virtual environment {env_name} destroyed")
    except Exception as e:
        logging.error(f"Failed to destroy virtual environment: {e}")

def install_wallet_local(local_path, env_name):
    create_virtual_environment(env_name)
    activate_virtual_environment_and_install(env_name)

def main():
    env_name = "pft_test_env"
    local_path = Path.cwd()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_filename = f"test_install_postfiat_wallet_{timestamp}.log"
    configure_test_logging(
        output_directory=local_path,
        log_filename=log_filename,
        level="DEBUG"
    )

    # Log system information
    sys_info = get_system_info()
    logging.info(f"System information:\n" + json.dumps(sys_info, indent=2))

    logging.info(f"Running installation test using local package at {local_path}...")

    try:
        install_wallet_local(local_path, env_name)

        logging.info("Running verification of wallet installation...")
        if verify_wallet_installation(env_name):
            logging.info("Wallet installation verified successfully")
        else:
            logging.error("Wallet installation verification failed")

    except Exception as e:
        logging.error(f"Installation test failed: {e}")

    finally:
        destroy_virtual_environment(env_name)
        logging.info("Installation test completed")

if __name__ == "__main__":
    main()
