import os
import sys
import platform

def create_shortcut():
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
    source "{python_executable.replace('bin/python', 'bin/activate')}"
    python "{os.path.join(current_location, 'pftpyclient', 'wallet_ux', 'prod_wallet.py')}"
    """

    sh_file_path = os.path.join(save_location, 'run_prod_wallet.sh')
    with open(sh_file_path, 'w') as file:
        file.write(script)

    os.chmod(sh_file_path, 0o755)

    command_file_path = os.path.join(save_location, 'Post_Fiat_Wallet.command')
    with open(command_file_path, 'w') as file:
        file.write(f"#!/bin/bash\n{sh_file_path}\n")

    os.chmod(command_file_path, 0o755)

    print(f"MacOS shortcut created: {command_file_path}")

if __name__ == "__main__":
    create_shortcut()
