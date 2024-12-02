import wx
from pathlib import Path
from typing import Dict
from loguru import logger
from xrpl.wallet import Wallet
from pftpyclient.user_login.credentials import CredentialManager
from pftpyclient.postfiatsecurity.hash_tools import password_decrypt
import sqlite3

class MigrationDialog(wx.Dialog):
    def __init__(self, parent, old_credentials: Dict[str, Dict[str, str]]):
        super().__init__(parent, title="Credential Migration Required", 
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        
        self.old_credentials = old_credentials
        self.usernames = list(old_credentials.keys())
        self.current_index = 0
        
        self.setup_ui()
        self.Center()

        self.Bind(wx.EVT_CLOSE, self.on_close)

    def setup_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Main message
        msg = wx.StaticText(self, label="Your credentials need to be migrated to a new format.\n"
                                      "Please enter your password for each account to continue.")
        msg.Wrap(300)
        main_sizer.Add(msg, 0, wx.ALL | wx.CENTER, 10)

        # Progress text
        self.progress_text = wx.StaticText(
            self, 
            label=f"Account {self.current_index + 1} of {len(self.usernames)}"
        )
        main_sizer.Add(self.progress_text, 0, wx.ALL | wx.CENTER, 5)

        # Username text
        self.username_text = wx.StaticText(
            self, 
            label=f"Username: {self.usernames[self.current_index]}"
        )
        main_sizer.Add(self.username_text, 0, wx.ALL | wx.CENTER, 5)

        # Password entry
        pwd_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pwd_label = wx.StaticText(self, label="Password:")
        self.password_entry = wx.TextCtrl(self, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        pwd_sizer.Add(pwd_label, 0, wx.ALL | wx.CENTER, 5)
        pwd_sizer.Add(self.password_entry, 1, wx.ALL | wx.EXPAND, 5)
        main_sizer.Add(pwd_sizer, 0, wx.ALL | wx.EXPAND, 5)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        migrate_btn = wx.Button(self, label="Migrate")
        skip_btn = wx.Button(self, label="Skip")
        cancel_btn = wx.Button(self, label="Cancel All")
        
        btn_sizer.Add(migrate_btn, 0, wx.ALL, 5)
        btn_sizer.Add(skip_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        main_sizer.Add(btn_sizer, 0, wx.ALL | wx.CENTER, 5)

        # Status message
        self.status_text = wx.StaticText(self, label="", size=(300, -1))
        self.status_text.SetForegroundColour(wx.RED)
        self.status_text.Wrap(300)
        main_sizer.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 5)

        # Bind events
        migrate_btn.Bind(wx.EVT_BUTTON, self.on_migrate)
        skip_btn.Bind(wx.EVT_BUTTON, self.on_skip)
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Bind ENTER key to migrate
        self.password_entry.Bind(wx.EVT_TEXT_ENTER, self.on_migrate)
        
        self.SetSizer(main_sizer)
        main_sizer.Fit(self)

    def on_migrate(self, event):
        """Handle migration attempt for current user"""
        username = self.usernames[self.current_index]
        password = self.password_entry.GetValue()
        
        if not password:
            self.status_text.SetLabel("Please enter a password")
            return

        try:
            # Get old credentials for current user
            encrypted_address = self.old_credentials[username]['v1xrpaddress']
            encrypted_secret = self.old_credentials[username]['v1xrpsecret']
            
            # Convert string representation of bytes to actual bytes
            address_bytes = eval(encrypted_address)  # safely converts b'...' string to bytes
            secret_bytes = eval(encrypted_secret)    # safely converts b'...' string to bytes
            
            # Decrypt using the old method
            address = password_decrypt(address_bytes, password).decode('utf-8')
            secret = password_decrypt(secret_bytes, password).decode('utf-8')

            # Validate credentials
            wallet = Wallet.from_seed(secret)
            if wallet.classic_address != address:
                self.status_text.SetLabel("Invalid credentials - address mismatch")
                return

            # Create new credential manager and store credentials
            new_creds = CredentialManager(username, password, allow_new_user=True)
            new_creds.enter_and_encrypt_credential({
                f"{username}__v1xrpaddress": address,
                f"{username}__v1xrpsecret": secret
            })

            self.move_to_next()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Migration failed for {username}: {error_msg}", exc_info=True)
            self.status_text.SetLabel(f"Migration failed: Double check your password.")

    def on_skip(self, event):
        """Handle skipping current user"""
        username = self.usernames[self.current_index]
        dlg = wx.MessageDialog(
            self,
            f"Are you sure you want to skip migrating {username}?",
            "Skip Account",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            logger.info(f"Skipped migration for {username}")
            self.move_to_next()
        
        dlg.Destroy()

    def on_cancel(self, event):
        """Handle canceling entire migration"""
        dlg = wx.MessageDialog(
            self,
            "Are you sure you want to cancel the migration?\n\n"
            "You won't be able to access non-migrated accounts but you can migrate later.",
            "Cancel Migration",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.EndModal(wx.ID_CANCEL)
        
        dlg.Destroy()

    def move_to_next(self):
        """Move to next user or finish migration"""
        self.current_index += 1
        self.password_entry.SetValue("")
        self.status_text.SetLabel("")
        
        if self.current_index >= len(self.usernames):
            self.finish_migration()
        else:
            self.progress_text.SetLabel(
                f"Account {self.current_index + 1} of {len(self.usernames)}")
            self.username_text.SetLabel(
                f"Username: {self.usernames[self.current_index]}")

    def finish_migration(self):
        """Complete the migration process"""
        wx.MessageBox("Credential migration completed successfully.")

    def on_close(self, event):
        """Handle window close button (X) - treat it the same as Cancel"""
        logger.debug("Migration dialog close button clicked")
        self.EndModal(wx.ID_CANCEL)

def parse_old_credentials() -> Dict[str, Dict[str, str]]:
    """Parse the old credentials file into a dictionary of usernames and their credentials"""
    cred_file = Path.home().joinpath("postfiatcreds", "manyasone_cred_list.txt")
    logger.debug(f"Looking for old credentials at: {cred_file}")
    
    if not cred_file.exists():
        logger.debug("Old credentials file not found")
        return {}

    credentials = {}
    current_username = None
    current_type = None
    
    try:
        with open(cred_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                logger.debug(f"Line {line_num}: {line[:50]}...")  # Log line number and content
                
                line = line.strip()
                if not line:
                    continue
                    
                if line.startswith('variable___'):
                    key = line.replace('variable___', '')
                    parts = key.split('__')

                    # Skip entries that don't match our expected format
                    if len(parts) < 2 or parts[1] not in ['v1xrpaddress', 'v1xrpsecret']:
                        logger.debug(f"Skipping non-credential entry: {key}")
                        current_username = None
                        current_type = None
                        continue

                    username = parts[0]
                    cred_type = parts[1]
                    
                    if username not in credentials:
                        credentials[username] = {}
                    current_username = username
                    current_type = cred_type

                elif current_username and line.startswith("b'"):
                    if current_type in ['v1xrpaddress', 'v1xrpsecret']:
                        credentials[current_username][current_type] = line
        
        return credentials
        
    except Exception as e:
        logger.error(f"Error parsing old credentials: {e}", exc_info=True)  # This will show the full traceback
        return {}

def check_and_show_migration_dialog(parent=None, force: bool = False) -> bool:
    """
    Check if migration is needed and show dialog if necessary.
    Returns True if migration was successful or not needed, False if cancelled.
    """
    logger.info("Checking if credential migration is needed...")
    
    old_creds = parse_old_credentials()
    logger.debug(f"Found {len(old_creds)} accounts in old credentials file")

    existing_users = []

    # Get list of existing usernames from SQLite
    try:
        db_path = Path.home().joinpath("postfiatcreds", "credentials.sqlite")
        if db_path.exists():
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT username FROM credentials")
                existing_users = [row[0] for row in cursor.fetchall()]
                logger.debug(f"Found {len(existing_users)} existing users in SQLite DB")
                
                # Filter out already migrated credentials
                unmigrated_creds = {
                    username: creds 
                    for username, creds in old_creds.items() 
                    if username not in existing_users
                }
                
                logger.debug(f"Found {len(unmigrated_creds)} unmigrated accounts")
        else:
            logger.debug("No existing credentials database found")
            unmigrated_creds = old_creds
        
    except Exception as e:
        logger.error(f"Error checking existing credentials: {e}", exc_info=True)
        unmigrated_creds = old_creds
    
    if unmigrated_creds and (force or not existing_users):
        logger.info("Migration needed, launching dialog...")
        try:
            dlg = MigrationDialog(parent, unmigrated_creds)
            logger.debug("Showing migration dialog...")
            result = dlg.ShowModal()
            logger.debug(f"Migration dialog result: {result}")
            dlg.Destroy()
            return result == wx.ID_OK
        except Exception as e:
            logger.error(f"Error during migration dialog: {e}", exc_info=True)
            return False
    elif force:
        wx.MessageBox("No credentials to migrate!")
    
    return True