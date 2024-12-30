import shutil
import subprocess
import wx
import sys
import os
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

        # Create HTML content
        html_content = f"""
        <html>
        <body>
        <h3>A new version of PftPyClient is available</h3>
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

def get_current_commit_hash():
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], 
            capture_output=True, 
            text=True, 
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
    
def get_remote_commit_hash(branch: str) -> Optional[str]:
    try:
        # Fetch latest changes without merging
        subprocess.run(['git', 'fetch'], check=True)

        # Get the latest commit hash from origin/branch
        result = subprocess.run(
            ['git', 'rev-parse', f'origin/{branch}'],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
    
def update_available(branch: str) -> bool:
    current = get_current_commit_hash()
    logger.debug(f"Current commit hash: {current}")
    remote = get_remote_commit_hash(branch)
    logger.debug(f"Remote commit hash for {branch}: {remote}")
    return current != remote and current is not None and remote is not None

def perform_update(branch: str) -> bool:
    repo_url = REPO_URL
    repo_path = Path(__file__).parent.parent.parent  # Gets the root directory
    logger.debug(f"repo_path: {repo_path}")

    try:

        # First, try to remove the .git directory specifically
        git_dir = repo_path / '.git'
        if git_dir.exists():
            logger.debug("Removing .git directory...")
            shutil.rmtree(git_dir, ignore_errors=True)

        # Using shutil instead of rm command for cross-platform compatibility
        for item in repo_path.iterdir():
            try:
                if item.is_file():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except PermissionError as e:
                logger.warning(f"Permission error while removing {item}: {e}")
                # Continue with other files even if one fails
                continue

        # Clone latest version
        subprocess.run(['git', 'clone', '-b', branch, repo_url, str(repo_path)], check=True)

        # Run install script
        subprocess.run(
            [sys.executable, 'install_wallet.py'],
            cwd=str(repo_path),
            check=True
        )

        wx.MessageBox("Update completed successfully. The application will now restart.",
                      "Update Complete",
                      wx.OK | wx.ICON_INFORMATION
        )

        wx.GetApp().ExitMainLoop()
        os.execv(sys.executable, [sys.executable] + sys.argv)

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Update failed: {e}")
        logger.error(traceback.format_exc())
        wx.MessageBox(f"Update failed: {str(e)}",
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
            return perform_update(branch)
        return True  # User chose to skip update
    
    except Exception as e:
        logger.error(f"Error during update dialog: {e}", exc_info=True)
        logger.error(traceback.format_exc())
        return False
