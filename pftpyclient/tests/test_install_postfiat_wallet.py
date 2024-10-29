import subprocess
import platform
import sys
import os
from pathlib import Path
import shutil

def get_git_root():
    try: 
        git_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).strip().decode("utf-8")
        return Path(git_root)
    except subprocess.CalledProcessError:
        raise RuntimeError("Failed to determine the root of the git repository")

def create_virtual_environment(env_name):
    try:
        subprocess.check_call([sys.executable, "-m", "venv", env_name])
        print(f"Virtual environment {env_name} created")
    except subprocess.CalledProcessError as e:
        print(f"Failed to create virtual environment: {e}")
        sys.exit(1)

def activate_virtual_environment_and_install(env_name):
    os_type = platform.system()
    venv_activate = None

    project_root = get_git_root()

    extras = "[windows]" if os_type == "Windows" else ""

    if os_type == "Darwin" or os_type == "Linux":
        venv_activate = Path(env_name) / "bin" / "activate"
        command = f"source {venv_activate} && pip install -e {project_root}"
        subprocess.check_call(command, shell=True, executable="/bin/bash")

    elif os_type == "Windows":
        venv_activate = Path(env_name) / "Scripts" / "activate"
        command = f"{venv_activate} && pip install -e {project_root}{extras}"
        subprocess.check_call(command, shell=True)

    print(f"Virtual environment {env_name} activated on {os_type}")

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

        print("Wallet installation verified successfully with commands 'pft-shortcut' and 'pft'")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Verification failed: {e}")
        return False

def destroy_virtual_environment(env_name):
    try:
        shutil.rmtree(env_name)
        print(f"Virtual environment {env_name} destroyed")
    except Exception as e:
        print(f"Failed to destroy virtual environment: {e}")

def install_wallet_local(local_path, env_name):
    create_virtual_environment(env_name)
    activate_virtual_environment_and_install(env_name)

def main():
    env_name = "pft_test_env"
    local_path = Path.cwd()

    print(f"Running installation test using local package at {local_path}...")

    install_wallet_local(local_path, env_name)

    print("Running verification of wallet installation...")
    if verify_wallet_installation(env_name):
        print("Wallet installation verified successfully")
    else:
        print("Wallet installation verification failed")

    destroy_virtual_environment(env_name)

if __name__ == "__main__":
    main()
