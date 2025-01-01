import shutil
import stat
import subprocess
import wx
import time
import sys
import os
import platform
import traceback
from pathlib import Path
from loguru import logger
from typing import Optional, Dict
from pftpyclient.wallet_ux.dialog_parent import WalletDialogParent

REPO_URL = "https://github.com/postfiatorg/pftpyclient"

def get_commit_details(branch: str) -> Optional[Dict[str, str]]:
    """Fetch detailed information about the latest remote commit"""
    try:
        # Fetch the latest changes
        subprocess.run(['git', 'fetch'], check=True)

        # Get the commit message and details
        result = subprocess.run(
            ['git', 'log', '-1', f'origin/{branch}', '--pretty=format:%h%n%an%n%ad%n%s%n%b'],
            capture_output=True,
            text=True,
            check=True
        )

        # Parse the output
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 4:
            return {
                'hash': lines[0],
                'author': lines[1],
                'date': lines[2],
                'subject': lines[3],
                'body': '\n'.join(lines[4:]) if len(lines) > 4 else ''
            }
        return None
    except subprocess.CalledProcessError:
        return None

class UpdateDialog(wx.Dialog):
    def __init__(self, parent: WalletDialogParent, commit_details: Dict[str, str], branch: str):
        super().__init__(parent, title="Update Available", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.commit_details = commit_details
        self.branch = branch
        self.setup_ui()
        self.Center()

    def setup_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        version_tag = 'dev ' if self.branch == 'dev' else ''

        # Create HTML content
        html_content = f"""
        <html>
        <body>
        <h3>A new {version_tag}version of PftPyClient is available</h3>
        <p>Latest update details:</p>
        <pre>
Commit: {self.commit_details['hash']}
Author: {self.commit_details['author']}
Date: {self.commit_details['date']}

{self.commit_details['subject']}

{self.commit_details['body']}
        </pre>
        <p>Would you like to update now?</p>
        </body>
        </html>
        """

        # HTML window for content
        self.html_window = wx.html.HtmlWindow(
            self,
            style=wx.html.HW_SCROLLBAR_AUTO,
            size=(500, 300)
        )
        self.html_window.SetPage(html_content)
        main_sizer.Add(self.html_window, 1, wx.EXPAND | wx.ALL, 10)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        update_btn = wx.Button(self, wx.ID_YES, "Update Now")
        update_btn.Bind(wx.EVT_BUTTON, self.on_update)
        skip_btn = wx.Button(self, wx.ID_NO, "Skip")
        skip_btn.Bind(wx.EVT_BUTTON, self.on_skip)
        
        btn_sizer.Add(update_btn, 0, wx.ALL, 5)
        btn_sizer.Add(skip_btn, 0, wx.ALL, 5)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        self.SetSizer(main_sizer)
        main_sizer.Fit(self)

    def on_update(self, event):
        """Handle update button click"""
        self.EndModal(wx.ID_YES)

    def on_skip(self, event):
        """Handle skip button click"""
        self.EndModal(wx.ID_NO)

    def on_close(self, event):
        """Handle window close button (X)"""
        self.EndModal(wx.ID_NO)

def get_current_branch() -> Optional[str]:
    try:
        repo_path = Path(__file__).parent.parent.parent
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_path)
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
    
def branch_exists_locally(branch: str) -> bool:
    try:
        repo_path = Path(__file__).parent.parent.parent
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', branch],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            # Don't use check=True here as we're checking existence
        )
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False

def switch_to_branch(branch: str) -> bool:
    try:
        repo_path = Path(__file__).parent.parent.parent
        # First ensure we have the latest refs
        subprocess.run(['git', 'fetch'], check=True, cwd=str(repo_path))
        
        if branch_exists_locally(branch):
            # If branch exists locally, just checkout
            subprocess.run(['git', 'checkout', branch], check=True, cwd=str(repo_path))
        else:
            # If branch doesn't exist locally, create it tracking the remote
            subprocess.run(
                ['git', 'checkout', '-b', branch, f'origin/{branch}'],
                check=True,
                cwd=str(repo_path)
            )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to switch to branch {branch}: {str(e)}")
        return False

def get_current_commit_hash():
    try:
        repo_path = Path(__file__).parent.parent.parent  # Gets the root directory
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], 
            capture_output=True, 
            text=True, 
            check=True,
            cwd=str(repo_path)
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
    
def get_remote_commit_hash(branch: str) -> Optional[str]:
    try:
        repo_path = Path(__file__).parent.parent.parent  # Gets the root directory
        # Fetch latest changes without merging
        subprocess.run(['git', 'fetch'], check=True)

        # Get the latest commit hash from origin/branch
        result = subprocess.run(
            ['git', 'rev-parse', f'origin/{branch}'],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(repo_path)
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
    
def update_available(branch: str) -> bool:
    current_branch = get_current_branch()

    # If we're on a branch other than main or dev, assume it's a developer branch and skip update check
    if current_branch and current_branch not in ['main', 'dev']:
        logger.debug(f"Current branch '{current_branch}' is a development branch. Skipping update check.")
        return False
    
    # If we're not on the target branch, try to switch
    if current_branch != branch:
        logger.debug(f"Currently on {current_branch}, attempting to switch to {branch}")
        if not switch_to_branch(branch):
            logger.error(f"Failed to switch to branch {branch}")
            return False

    # Now check for updates on the current branch
    current = get_current_commit_hash()
    logger.debug(f"Current commit hash: {current}")
    remote = get_remote_commit_hash(branch)
    logger.debug(f"Remote commit hash for {branch}: {remote}")
    return current != remote and current is not None and remote is not None

def handle_remove_error(func, path, excinfo):
    """Error handler for shutil.rmtree that handles readonly files"""
    try:
        os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777
        func(path)  # Try again
    except Exception as e:
        print(f"Error handling removal of {path}: {e}")
    
def remove_with_retry(path: Path) -> bool:
    """Remove a file or directory with retries and permission fixes"""
    if not path.exists():
        return True

    try:
        if path.is_file():
            os.chmod(path, 0o777)  # Make file writable
            path.unlink(missing_ok=True)
        elif path.is_dir():
            # Make all files and directories writable
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    try:
                        os.chmod(Path(root) / d, 0o777)
                    except Exception:
                        pass
                for f in files:
                    try:
                        os.chmod(Path(root) / f, 0o777)
                    except Exception:
                        pass
            
            # Attempt removal
            shutil.rmtree(path, onexc=handle_remove_error)

        return not path.exists()
    except Exception as e:
        print(f"Failed to remove {path}: {e}")
        return False

def backup_git_directory(repo_path: Path) -> Path:
    """
    Backup .git directory to a temporary location.
    Returns the backup path.
    """
    git_dir = repo_path / '.git'
    if not git_dir.exists():
        return None
    
    # Create backup in parent directory
    backup_dir = repo_path.parent / f'.git_backup_{int(time.time())}'
    print(f"Backing up .git directory to {backup_dir}")
    shutil.copytree(git_dir, backup_dir)
    return backup_dir

def restore_git_directory(backup_dir: Path, repo_path: Path) -> bool:
    """
    Restore .git directory from backup, skipping files that can't be copied.
    Returns True if at least some files were restored.
    """
    if not backup_dir or not backup_dir.exists():
        return False
    
    try:
        git_dir = repo_path / '.git'
        if not git_dir.exists():
            git_dir.mkdir(exist_ok=True)
        
        print(f"Restoring .git directory from {backup_dir}")
        files_restored = 0
        errors = []

        # Walk through the backup directory and copy files individually
        for src_path in backup_dir.rglob('*'):
            if not src_path.is_file():
                continue
                
            # Calculate destination path
            rel_path = src_path.relative_to(backup_dir)
            dst_path = git_dir / rel_path
            
            # Create parent directories if they don't exist
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                # Only copy if destination doesn't exist or is writable
                if not dst_path.exists() or os.access(dst_path, os.W_OK):
                    shutil.copy2(src_path, dst_path)
                    files_restored += 1
            except Exception as e:
                errors.append(f"{rel_path}: {str(e)}")
                continue

        # Report results
        if errors:
            print(f"Failed to restore {len(errors)} files:")
            for error in errors:
                print(f"  {error}")
        
        print(f"Successfully restored {files_restored} files")
        
        # Clean up backup if we restored at least some files
        if files_restored > 0:
            if not remove_with_retry(backup_dir):
                print(f"Warning: Failed to remove backup directory: {backup_dir}")
        
        return files_restored > 0

    except Exception as e:
        print(f"Failed to restore .git directory: {e}")
        return False
    
def move_venv_directory(repo_path: Path) -> Path:
    """
    Move venv directory to a temporary location.
    Returns the backup path.
    """
    venv_dir = repo_path / 'venv'
    if not venv_dir.exists():
        return None
    
    # Create backup in parent directory
    backup_dir = repo_path.parent / f'venv_backup_{int(time.time())}'
    print(f"Moving venv directory to {backup_dir}")
    shutil.move(str(venv_dir), str(backup_dir))  # Use str() for Windows compatibility
    return backup_dir

def restore_venv_directory(backup_dir: Path, repo_path: Path) -> bool:
    """
    Move venv directory back from backup.
    Returns True if successful.
    """
    if not backup_dir or not backup_dir.exists():
        return False
    
    try:
        venv_dir = repo_path / 'venv'
        print(f"Moving venv directory back from {backup_dir}")
        if venv_dir.exists():
            print("Warning: venv directory already exists at destination")
            return False
            
        shutil.move(str(backup_dir), str(venv_dir))  # Use str() for Windows compatibility
        return True
    except Exception as e:
        print(f"Failed to restore venv directory: {e}")
        return False
    
def get_python_requirement() -> tuple[int, int]:
    """Get minimum Python version from project configuration"""
    repo_path = Path(__file__).parent.parent.parent
    setup_path = repo_path / "setup.py"

    try:
        if setup_path.exists():
            # Fall back to parsing setup.py
            with open(setup_path, 'r') as f:
                content = f.read()
                import re
                if match := re.search(r'python_requires\s*=\s*[\'"]>=\s*(\d+)\.(\d+)[\'"]', content):
                    return (int(match.group(1)), int(match.group(2)))
        
        raise RuntimeError("Could not determine Python version requirement from project files")
    
    except Exception as e:
        logger.error(f"Failed to read Python version requirement: {e}")
        raise RuntimeError(f"Could not determine Python version requirement: {e}")
    
def get_system_python() -> str:
    """Get the path to the system Python executable"""
    # Try multiple methods to find Python on all platforms
    best_version = (0, 0)
    best_path = None

    try:
        min_version = get_python_requirement()
        logger.debug(f"Required Python version: >={min_version[0]}.{min_version[1]}")
    except Exception as e:
        logger.error(str(e))
        raise

    # Different paths to try based on platform
    if platform.system() == "Windows":
        possible_paths = [
            "python",
            sys.executable  # Current Python interpreter path
        ]
    else:
        possible_paths = [
            "python3",
            "python",
            sys.executable
        ]
    
    for path in possible_paths:
        try:
            # Test if this Python works and get its version
            result = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                capture_output=True,
                text=True,
                check=True
            )
            major, minor = map(int, result.stdout.strip().split())
            version = (major, minor)
            
            # Update best version if this one is newer
            if version > best_version:
                best_version = version
                best_path = path
                
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.debug(f"Failed to check {path}: {str(e)}")
            continue
    
    if best_path:
        if best_version >= min_version:
            logger.info(f"Selected Python {best_version[0]}.{best_version[1]} at {best_path}")
            return best_path
        else:
            raise RuntimeError(
                f"Found Python {best_version[0]}.{best_version[1]}, but version "
                f"{min_version[0]}.{min_version[1]} or higher is required"
            )

    # Fallback to system paths if PATH-based Python not found
    if platform.system() == "Darwin":  # macOS
        return "/usr/bin/python3"
    elif platform.system() == "Windows":
        raise RuntimeError(
            "Could not find Python installation. Please ensure Python 3 is installed "
            "and either in PATH or in a standard installation location."
        )
    else:  # Linux
        return "/usr/bin/python3"

def perform_update(branch: str) -> Optional[bool]:
    repo_url = REPO_URL
    repo_path = Path(__file__).parent.parent.parent  # Gets the root directory
    git_backup = None
    venv_backup = None
    
    try:
        system_python = get_system_python()

        # Remove all existing sinks
        logger.remove()

        # Try to clean up git
        git_dir = repo_path / '.git'
        if git_dir.exists():
            print("Cleaning up git...")
            # Backup .git directory first
            git_backup = backup_git_directory(repo_path)
            
            # Set Git environment to avoid prompts
            git_env = os.environ.copy()
            git_env['GIT_ASK_YESNO'] = 'false'

            # Force git to release locks
            subprocess.run(['git', 'gc'], cwd=repo_path, check=False, env=git_env)
            subprocess.run(['git', 'prune'], cwd=repo_path, check=False, env=git_env)
            subprocess.run(['git', 'clean', '-fd'], cwd=repo_path, check=False, env=git_env)
            
            # Clear git index lock if it exists
            index_lock = git_dir / 'index.lock'
            if index_lock.exists():
                index_lock.unlink(missing_ok=True)
            
            print("Removing .git directory...")
            if not remove_with_retry(git_dir):
                raise CannotRemoveGitDirectory(
                    "Unable to remove .git directory. This usually happens when files are "
                    "being held open by another program.\n\n"
                    "Please:\n"
                    "1. Close any IDEs or text editors that might have project files open\n"
                    "2. Close any file explorers open to the project directory\n"
                    "3. Try the update again"
                )
        print("Removed .git directory")

        # Move venv if it exists. It can't be deleted because it's being used by the current process.
        # But we need the root directory to be empty for the git clone to work.
        venv_dir = repo_path / 'venv'
        if venv_dir.exists():
            venv_backup = move_venv_directory(repo_path)
            if not venv_backup:
                raise Exception("Failed to backup venv directory")
        
        # Remove all other files and directories
        for item in repo_path.iterdir():
            if item != git_dir:
                remove_with_retry(item)

        # Final verification
        remaining = list(repo_path.iterdir())
        if remaining:
            print(f"Failed to remove: {remaining}")
            # If failed to remove, restore git and venv
            if git_backup:
                restore_git_directory(git_backup, repo_path)
            if venv_backup:
                restore_venv_directory(venv_backup, repo_path)
            raise Exception("Unable to clean directory for update")

        # Clone latest version
        print("Cloning new version...")
        subprocess.run(['git', 'clone', '-b', branch, repo_url, str(repo_path)], check=True)

        # Restore venv if we had a backup
        if venv_backup:
            if not restore_venv_directory(venv_backup, repo_path):
                raise Exception("Failed to restore virtual environment")

        # Run install script
        print("Running install script...")

        if platform.system() == "Windows":
            # Create a batch file for Windows
            batch_file = repo_path / "update_launcher.bat"
            with open(batch_file, 'w') as f:
                f.write('@echo off\n')
                f.write('set "VIRTUAL_ENV="\n')  # Deactivate any active virtual environment
                f.write('set "PATH=%PATH:venv\\Scripts;=%"\n')  # Remove venv from PATH
                f.write('timeout /t 5 /nobreak >nul\n')
                f.write(f'"{system_python}" install_wallet.py --launch\n')
                f.write('pause\n')
                f.write('del "%~f0"\n')

            # Launch the batch file detached from current process
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.Popen(
                str(batch_file),
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                shell=True
            )
        else:
            # Direct execution for Unix-like systems
            subprocess.Popen(
                [system_python, 'install_wallet.py', '--launch'],
                cwd=str(repo_path)
            )

        wx.GetApp().ExitMainLoop()
        print("Exited WalletApp MainLoop")
        sys.exit(0)  # Exit current process

    except CannotRemoveGitDirectory as e:
        if git_backup:
            restore_git_directory(git_backup, repo_path)
        print(f"Update failed: {e}")
        wx.MessageBox(f"Update failed: {str(e)}",
                     "Update Error",
                     wx.OK | wx.ICON_ERROR)
        return False
    except subprocess.CalledProcessError as e:
        print(f"Update failed: {e}")
        print(traceback.format_exc())
        wx.MessageBox(f"Update failed: {str(e)}",
                     "Update Error",
                     wx.OK | wx.ICON_ERROR)
        return False
    except Exception as e:
        print(f"Update failed with unexpected error: {e}")
        print(traceback.format_exc())

        # Attempt to restore .git directory if we have a backup
        if git_backup:
            restore_git_directory(git_backup, repo_path)
        if venv_backup:
            restore_venv_directory(venv_backup, repo_path)

        wx.MessageBox(f"Update failed with unexpected error: {str(e)}",
                     "Update Error",
                     wx.OK | wx.ICON_ERROR)
        return False
    
def check_and_show_update_dialog(parent: WalletDialogParent) -> bool:
    """
    Check if update is available and show dialog if necessary.
    Returns True if update was successful or not needed, False if cancelled or failed.
    """
    logger.info("Checking for updates...")

    # Get branch from configuration
    branch = parent.config.get_global_config('update_branch')
    logger.debug(f"Checking for updates against branch: {branch}")

    if not update_available(branch):
        logger.info("No updates available")
        return True
    
    try:
        commit_details = get_commit_details(branch)
        if not commit_details:
            logger.error("Failed to fetch commit details")
            return False

        dlg = UpdateDialog(parent, commit_details, branch)
        result = dlg.ShowModal()
        dlg.Destroy()

        if result == wx.ID_YES:
            # Create and show progress dialog
            progress_dlg = wx.ProgressDialog(
                "Updating PftPyClient",
                "Please wait while updating PftPyClient...",
                maximum=100,
                parent=parent,
                style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE
            )
            progress_dlg.Pulse()  # Show indeterminate progress
            
            try:
                perform_update(branch)
            except Exception as e:
                raise e
            finally:
                progress_dlg.Destroy()
    
        return True  # User chose to skip update
    
    except Exception as e:
        print(f"Error during update dialog: {e}")
        print(traceback.format_exc())
        return False
    
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
    
    return desktop

class CannotRemoveGitDirectory(Exception):
    """Exception raised when unable to remove .git directory"""
    pass

