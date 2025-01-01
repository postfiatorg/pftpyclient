# Standard library imports
import time
import traceback
import random
import urllib.parse
import asyncio
import os
import re
from threading import Thread, Event
from pathlib import Path
from enum import Enum, auto

# Third-party imports
import wx
import wx.adv
import wx.grid as gridlib
import wx.html
import wx.lib.newevent
import xrpl
from xrpl.wallet import Wallet
from xrpl.asyncio.clients import AsyncWebsocketClient
from loguru import logger
from cryptography.fernet import InvalidToken
import pandas as pd
import nest_asyncio

# PftPyclient imports
from pftpyclient.utilities.wallet_state import (
    WalletState, 
    requires_wallet_state,
    FUNDED_STATES,
    TRUSTLINED_STATES,
    ACTIVATED_STATES
)
from pftpyclient.utilities.task_manager import (
    PostFiatTaskManager, 
    NoMatchingTaskException, 
    WrongTaskStateException, 
    compress_string,
    construct_memo
)
from pftpyclient.user_login.credentials import CredentialManager
from pftpyclient.basic_utilities.configure_logger import configure_logger, update_wx_sink
from pftpyclient.performance.monitor import PerformanceMonitor
from pftpyclient.configuration.configuration import ConfigurationManager, get_network_config
import pftpyclient.configuration.constants as constants
from pftpyclient.user_login.migrate_credentials import check_and_show_migration_dialog
from pftpyclient.utilities.updater import check_and_show_update_dialog
from pftpyclient.wallet_ux.dialogs import *
from pftpyclient.wallet_ux.dialogs import CustomDialog
from pftpyclient.version import VERSION

# Configure the logger at module level
wx_sink = configure_logger(
    log_to_file=True,
    output_directory=Path.cwd() / "pftpyclient",
    log_filename="prod_wallet.log",
    level="DEBUG"
)

# Try to use the default browser
if os.name == 'nt':
    try: 
        webbrowser.get('windows-default')
    except webbrowser.Error:
        pass
elif os.name == 'posix':
    try:
        webbrowser.get('macosx')
    except webbrowser.Error:
        pass

# Apply the nest_asyncio patch
nest_asyncio.apply()

UpdateGridEvent, EVT_UPDATE_GRID = wx.lib.newevent.NewEvent()

class WalletUIState(Enum):
    IDLE = auto()
    BUSY = auto()
    SYNCING = auto()
    TRANSACTION_PENDING = auto()
    ERROR = auto()

class PostFiatWalletApp(wx.App):
    def OnInit(self):
        frame = WalletApp()
        self.SetTopWindow(frame)
        frame.Show(True)
        return True
    
    def ReopenApp(self):
        self.GetTopWindow().Raise()

class XRPLMonitorThread(Thread):
    def __init__(self, gui):
        Thread.__init__(self, daemon=True)
        self.gui: WalletApp = gui
        self.config = ConfigurationManager()
        self.ws_urls = self.config.get_ws_endpoints()
        self.ws_url_index = 0
        self.url = self.ws_urls[self.ws_url_index]
        logger.debug(f"Starting XRPL monitor thread with endpoint: {self.url}")
        self.loop = asyncio.new_event_loop()
        self.context = None
        self._stop_event = Event()

        # Error handling parameters
        self.reconnect_delay = 1  # Initial delay in seconds
        self.max_reconnect_delay = 30  # Maximum delay
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5  # Per node

        # Ledger monitoring
        self.last_ledger_time = None
        self.LEDGER_TIMEOUT = 30  # seconds
        self.CHECK_INTERVAL = 4  # match XRPL block time

    def run(self):
        """Thread entry point"""
        asyncio.set_event_loop(self.loop)
        try:
            self.context = self.loop.run_until_complete(self.monitor())
        except Exception as e:
            if not self.stopped():
                logger.error(f"Unexpected error in XRPLMonitorThread: {e}")
        finally:
            self.loop.close()

    def stop(self):
        """Signal the thread to stop"""
        self._stop_event.set()

        # Close websocket connection if it exists
        if hasattr(self, 'client') and self.client:
            # Use the worker's existing loop to close
            future = asyncio.run_coroutine_threadsafe(
                self.client.close(),
                self.loop
            )
            try:
                # Wait for the websocket to close with a timeout
                future.result(timeout=2)
            except Exception:
                pass  # Ignore timeout or other errors during close

        # Cancel any pending tasks
        pending_tasks = asyncio.all_tasks(self.loop)
        for task in pending_tasks:
            task.cancel()

        # Stop the event loop
        try:
            self.loop.call_soon_threadsafe(
                lambda: self.loop.stop()
            )
        except Exception as e:
            pass  # Ignore any errors during loop stop

    def stopped(self):
        """Check if the thread has been signaled to stop"""
        return self._stop_event.is_set()
    
    def set_ui_state(self, state: WalletUIState, message: str = None):
        """Helper method to safely update UI state from thread"""
        wx.CallAfter(self.gui.set_wallet_ui_state, state, message)

    async def handle_connection_error(self, error_msg: str) -> bool:
        """
        Connection error handling with exponential backoff
        Returns True if should retry, False if should switch nodes
        """
        logger.error(error_msg)
        self.set_ui_state(WalletUIState.ERROR, error_msg)
        
        self.reconnect_attempts += 1
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.warning(f"Max reconnection attempts reached for node {self.url}. Switching to next node.")
            self.switch_node()
            self.reconnect_attempts = 0
            self.reconnect_delay = 1
            return False
            
        # Exponential backoff with jitter
        jitter = random.uniform(0, 0.1) * self.reconnect_delay
        delay = min(self.reconnect_delay + jitter, self.max_reconnect_delay)
        logger.info(f"Reconnecting in {delay:.1f} seconds...")
        await asyncio.sleep(delay)
        self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
        return True

    async def monitor(self):
        """Main monitoring coroutine with error handling and reconnection logic"""
        while not self.stopped():
            try:
                await self.watch_xrpl_account(self.gui.wallet.classic_address, self.gui.wallet)
                # Reset reconnection parameters on successful connection
                self.reconnect_delay = 1
                self.reconnect_attempts = 0

            except asyncio.CancelledError:
                logger.debug("Monitor task cancelled")
                break

            except Exception as e:
                if self.stopped():
                    break

                await self.handle_connection_error(f"Error in monitor: {e}")
    
    def switch_node(self):
        self.ws_url_index = (self.ws_url_index + 1) % len(self.ws_urls)
        self.url = self.ws_urls[self.ws_url_index]
        logger.info(f"Switching to next node: {self.url}")

    async def watch_xrpl_account(self, address, wallet=None):
        self.account = address
        self.wallet = wallet
        self.last_ledger_time = time.time()

        async with AsyncWebsocketClient(self.url) as self.client:
            self.set_ui_state(WalletUIState.SYNCING, "Connecting to XRPL websocket...")

            # Subcribe to streams
            response = await self.client.request(xrpl.models.requests.Subscribe(
                streams=["ledger"],
                accounts=[self.account]
            ))

            if not response.is_successful():
                self.set_ui_state(WalletUIState.IDLE, "Failed to connect to XRPL websocket.")
                raise Exception(f"Subscription failed: {response.result}")
            
            self.set_ui_state(WalletUIState.IDLE)
            logger.info(f"Successfully subscribed to account {self.account} updates on node {self.url}")

            # Create task for timeout checking
            async def check_timeouts():
                while True:
                    await asyncio.sleep(self.CHECK_INTERVAL)
                    if self.last_ledger_time is not None:
                        time_since_last_ledger = time.time() - self.last_ledger_time
                        if time_since_last_ledger > self.LEDGER_TIMEOUT:
                            raise Exception(f"No ledger updates received for {time_since_last_ledger:.1f} seconds")
            
            timeout_task = asyncio.create_task(check_timeouts())

            try:
                async for message in self.client:
                    if self.stopped():
                        break
                        
                    try:
                        mtype = message.get("type")
                        
                        if mtype == "ledgerClosed":
                            self.last_ledger_time = time.time()
                            wx.CallAfter(self.gui.update_ledger, message)
                        elif mtype == "transaction":
                            await self.process_transaction(message)
                            
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        self.set_ui_state(WalletUIState.ERROR, f"Error processing update: {str(e)}")
                        continue

            finally:
                timeout_task.cancel()
                try:
                    await timeout_task
                except asyncio.CancelledError:
                    pass

    async def process_transaction(self, tx_message):
        """Process a single transaction update from websocket"""
        try:
            self.set_ui_state(WalletUIState.BUSY, "Processing new transaction...")
            logger.debug(f"Full websocket transaction message: {tx_message}")

            formatted_tx = {
                "tx_json": tx_message.get("tx_json", {}),
                "meta": tx_message.get("meta", {}),
                "hash": tx_message.get("hash"),
                "ledger_index": tx_message.get("ledger_index"),
                "validated": tx_message.get("validated", False)
            }

            # Create DataFrame in same format as sync_transactions expects
            tx_df = pd.DataFrame([formatted_tx])

            # Process through sync_memo_transactions pipeline
            if not tx_df.empty:
                wx.CallAfter(self.gui.task_manager.sync_memo_transactions, tx_df)

                # Update account info
                response = await self.client.request(xrpl.models.requests.AccountInfo(
                    account=self.account,
                    ledger_index="validated"
                ))

                if response.is_successful():
                    def update_all():
                        self.gui.update_account(response.result["account_data"])
                        self.gui.update_tokens()
                        self.gui.refresh_grids()
                    wx.CallAfter(update_all)
                else:
                    logger.error(f"Failed to get account info: {response.result}")
            
            self.set_ui_state(WalletUIState.IDLE)

        except Exception as e:
            logger.error(f"Error processing transaction update: {e}")
            logger.error(traceback.format_exc())
            self.set_ui_state(WalletUIState.IDLE, f"Error: {str(e)}")

class WalletApp(wx.Frame):

    STATE_AVAILABLE_TABS = {
        WalletState.UNFUNDED: ["Summary", "Log"],
        WalletState.FUNDED: ["Summary", "Payments", "Log"],
        WalletState.TRUSTLINED: ["Summary", "Payments", "Memos", "Log"],
        WalletState.INITIATED: ["Summary", "Payments", "Memos", "Log"],
        WalletState.HANDSHAKE_SENT: ["Summary", "Payments", "Memos", "Log"],
        WalletState.HANDSHAKE_RECEIVED: ["Summary", "Payments", "Memos", "Log"],
        WalletState.ACTIVE: ["Summary", "Proposals", "Verification", "Rewards", "Payments", "Memos", "Log"]
    }

    GRID_CONFIGS = {
        'proposals': {
            'columns': [
                ('task_id', 'Task ID', 200),
                ('request', 'Request', 250),
                ('proposal', 'Proposal', 300),
                ('response', 'Response', 150)
            ]
        },
        'rewards': {
            'columns': [
                ('task_id', 'Task ID', 170),
                ('proposal', 'Proposal', 300),
                ('reward', 'Reward', 250),
                ('payout', 'Payout', 75)
            ]
        },
        'verification': {
            'columns': [
                ('task_id', 'Task ID', 190),
                ('proposal', 'Proposal', 300),
                ('verification', 'Verification', 400)
            ]
        },
        'memos': {
            'columns': [
                ('memo_id', 'Message ID', 190),
                ('memo', 'Memo', 500),
                ('direction', 'To/From', 55),
                ('display_address', 'Address', 250)
            ]
        },
        'summary': {
            'columns': [
                ('Key', 'Key', 125),
                ('Value', 'Value', 550)
            ]
        },
        'payments': {
            'columns': [
                ('datetime', 'Date', 120),
                ('amount', 'Amount', 70),
                ('token', 'Token', 50),
                ('direction', 'To/From', 55),
                ('display_address', 'Address', 250),
                ('tx_hash', 'Tx Hash', 450)
            ]
        }
    }

    def __init__(self):
        wx.Frame.__init__(self, None, title=f"PftPyClient v{VERSION}", size=(1150, 700))
        self.default_size = (1150, 700)
        self.min_size = (800, 600)
        self.max_size = (1600, 1000)
        self.zoom_factor = 1.0
        self.SetMinSize(self.min_size)
        self.SetMaxSize(self.max_size)

        self.ctrl_pressed = False
        self.last_ctrl_press_time = 0
        self.ctrl_toggle_delay = 0.2  # 200 ms debounce

        # Bind the zoom event to the entire frame
        wx.GetApp().Bind(wx.EVT_MOUSEWHEEL, self.on_mouse_wheel_zoom)
        
        # Use EVT_KEY_DOWN and EVT_KEY_UP events to detect Ctrl key presses
        wx.GetApp().Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        wx.GetApp().Bind(wx.EVT_KEY_UP, self.on_key_up)

        # Set the icon
        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "..", "images", "simple_pf_logo.ico")
        icon = wx.Icon(icon_path, wx.BITMAP_TYPE_ICO)
        self.SetIcon(icon)

        self.config = ConfigurationManager()
        self.network_config = get_network_config()
        self.network_url = self.config.get_current_endpoint()
        self.ws_url = self.config.get_current_ws_endpoint()
        self.pft_issuer = self.network_config.issuer_address
        
        self.perf_monitor = None
        if self.config.get_global_config('performance_monitor'):
            self.launch_perf_monitor()

        self.create_menu_bar()

        self.tab_pages = {}  # Store references to tab pages

        self.wallet = None
        self.build_ui()

        # Add the wx handler to the logger after UI is built
        update_wx_sink(self.log_text)

        self.worker = None
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(EVT_UPDATE_GRID, self.update_grid)

        # grid dimensions
        self.grid_row_heights = {}
        self.grid_column_widths = {}
        self.grid_base_row_height = 125
        self.row_height_margin = 25

        self.username = None

        self.wallet_state_in_transition = None
        self.take_action_dialog_shown = False
        self.wallet_state_monitor_timer = None
        self.state_check_interval = 10000  # 10 seconds

        # Check for migration
        check_and_show_migration_dialog(parent=self)

        # Check for update
        check_and_show_update_dialog(parent=self)

    def setup_grid(self, grid, grid_name):
        """Setup grid with columns based on grid configuration"""
        columns = self.GRID_CONFIGS[grid_name]['columns']
        grid.CreateGrid(0, len(columns))
        for idx, (col_id, col_label, width) in enumerate(columns):
            grid.SetColLabelValue(idx, col_label)
            grid.SetColSize(idx, width)
        return grid
    
    def create_menu_bar(self):
        """Create the menu bar with File and Extras menus"""
        self.menubar = wx.MenuBar()

        # File menu
        file_menu = wx.Menu()
        updates_item = file_menu.Append(wx.ID_ANY, "Check for Updates", "Check for updates")
        preferences_item = file_menu.Append(wx.ID_ANY, "Preferences", "Configure client settings")
        logout_item = file_menu.Append(wx.ID_ANY, "Logout", "Return to login screen")
        quit_item = file_menu.Append(wx.ID_EXIT, "Quit", "Quit the application")
        self.Bind(wx.EVT_MENU, self.on_check_for_updates, updates_item)
        self.Bind(wx.EVT_MENU, self.on_preferences, preferences_item)
        self.Bind(wx.EVT_MENU, self.on_logout, logout_item)
        self.Bind(wx.EVT_MENU, self.on_close, quit_item)
        self.menubar.Append(file_menu, "File")

        # Create Account menu
        self.account_menu = wx.Menu()
        self.contacts_item = self.account_menu.Append(wx.ID_ANY, "Manage Contacts")
        self.update_gdoc_item = self.account_menu.Append(wx.ID_ANY, "Update Google Doc")  # New item
        self.change_password_item = self.account_menu.Append(wx.ID_ANY, "Change Password")
        self.show_secret_item = self.account_menu.Append(wx.ID_ANY, "Show Secret")
        self.update_trustline_item = self.account_menu.Append(wx.ID_ANY, "Update Trustline")
        self.delete_account_item = self.account_menu.Append(wx.ID_ANY, "Delete Account")
        self.menubar.Append(self.account_menu, "Account")

        # Bind menu events
        self.Bind(wx.EVT_MENU, self.on_manage_contacts, self.contacts_item)
        self.Bind(wx.EVT_MENU, self.on_update_google_doc, self.update_gdoc_item)
        self.Bind(wx.EVT_MENU, self.on_change_password, self.change_password_item)
        self.Bind(wx.EVT_MENU, self.on_show_secret, self.show_secret_item)
        self.Bind(wx.EVT_MENU, self.on_update_trustline, self.update_trustline_item)
        self.Bind(wx.EVT_MENU, self.on_delete_credentials, self.delete_account_item)

        # Extras menu
        extras_menu = wx.Menu()
        self.migrate_item = extras_menu.Append(wx.ID_ANY, "Migrate Old Credentials", "Migrate credentials from old format")
        self.perf_monitor_item = extras_menu.Append(wx.ID_ANY, "Performance Monitor", "Monitor client's performance")
        self.Bind(wx.EVT_MENU, self.on_migrate_credentials, self.migrate_item)
        self.Bind(wx.EVT_MENU, self.launch_perf_monitor, self.perf_monitor_item)
        self.menubar.Append(extras_menu, "Extras")

        self.SetMenuBar(self.menubar)

        # Initially disable Account menu
        self.menubar.EnableTop(self.menubar.FindMenu("Account"), False)

    def enable_menus(self):
        """Enable certain menus after successful login"""
        self.menubar.EnableTop(self.menubar.FindMenu("Account"), True)

    def build_ui(self):
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        # Login panel
        self.login_panel = self.create_login_panel()
        self.sizer.Add(self.login_panel, 1, wx.EXPAND)

        # create user details panel
        self.user_details_panel = self.create_user_details_panel()
        self.user_details_panel.Hide()
        self.sizer.Add(self.user_details_panel, 1, wx.EXPAND)

        # Tabs (hidden initially)
        self.tabs = wx.Notebook(self.panel)
        self.tabs.Hide()
        self.tabs.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)
        self.sizer.Add(self.tabs, 1, wx.EXPAND | wx.TOP, 20)

        #################################
        # SUMMARY
        #################################

        self.summary_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.summary_tab, "Summary")
        self.summary_sizer = wx.BoxSizer(wx.VERTICAL)
        self.summary_tab.SetSizer(self.summary_sizer)

        # Create Summary tab elements
        username_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.summary_lbl_username = wx.StaticText(self.summary_tab, label="Username: ")
        self.summary_lbl_endpoint = wx.StaticText(self.summary_tab, label=f"HTTPS: {self.network_url}")
        self.summary_lbl_ws_endpoint = wx.StaticText(self.summary_tab, label=f"WebSocket: {self.ws_url}")
        network_text = "Testnet" if self.config.get_global_config('use_testnet') else "Mainnet"
        self.summary_lbl_network = wx.StaticText(self.summary_tab, label=f"Network: {network_text}")
        self.summary_lbl_wallet_state = wx.StaticText(self.summary_tab, label="Wallet State: ")

        username_row_sizer.Add(self.summary_lbl_username, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        username_row_sizer.AddStretchSpacer()
        username_row_sizer.Add(self.summary_lbl_endpoint, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=10)
        username_row_sizer.Add(self.summary_lbl_ws_endpoint, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=10)
        username_row_sizer.Add(self.summary_lbl_network, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=10)
        username_row_sizer.Add(self.summary_lbl_wallet_state, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=10)
        self.summary_sizer.Add(username_row_sizer, 0, wx.EXPAND)

        # Create XRP balance row
        xrp_balance_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.summary_lbl_xrp_balance = wx.StaticText(self.summary_tab, label="XRP Balance: ")
        self.summary_lbl_next_action = wx.StaticText(self.summary_tab, label="Next Action: ")
        xrp_balance_row_sizer.Add(self.summary_lbl_xrp_balance, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        xrp_balance_row_sizer.AddStretchSpacer()
        xrp_balance_row_sizer.Add(self.summary_lbl_next_action, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(xrp_balance_row_sizer, 0, wx.EXPAND)

        # Create PFT balance row
        pft_balance_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.summary_lbl_pft_balance = wx.StaticText(self.summary_tab, label="PFT Balance: ")
        self.btn_wallet_action = wx.Button(self.summary_tab, label="Take Action")
        pft_balance_row_sizer.Add(self.summary_lbl_pft_balance, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        pft_balance_row_sizer.AddStretchSpacer()
        pft_balance_row_sizer.Add(self.btn_wallet_action, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(pft_balance_row_sizer, 0, wx.EXPAND)

        # Create address section
        self.summary_lbl_address = wx.StaticText(self.summary_tab, label="XRP Address: ")
        self.summary_sizer.Add(self.summary_lbl_address, 0, flag=wx.ALL, border=5)

        # Bind wallet action button
        self.btn_wallet_action.Bind(wx.EVT_BUTTON, self.on_take_action)

        # Set font weights
        font = self.summary_lbl_next_action.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.summary_lbl_next_action.SetFont(font)
        self.btn_wallet_action.SetFont(font)

        # Create Key Account Details section
        self.summary_lbl_key_details = wx.StaticText(self.summary_tab, label="Key Account Details:")
        self.summary_sizer.Add(self.summary_lbl_key_details, 0, flag=wx.ALL, border=5)
        self.summary_grid = self.setup_grid(gridlib.Grid(self.summary_tab), 'summary')
        self.summary_sizer.Add(self.summary_grid, 1, wx.EXPAND | wx.ALL, 5)

        self.summary_tab.SetSizer(self.summary_sizer)

        # Store reference to summary tab page
        self.tab_pages["Summary"] = self.summary_tab

        #################################
        # PROPOSALS
        #################################

        self.proposals_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.proposals_tab, "Proposals")
        self.proposals_sizer = wx.BoxSizer(wx.VERTICAL)
        self.proposals_sizer.AddSpacer(10)
        self.proposals_tab.SetSizer(self.proposals_sizer)

        # Add the task management buttons in the Accepted tab
        self.proposals_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_request_task = wx.Button(self.proposals_tab, label="Request Task")
        self.proposals_button_sizer.Add(self.btn_request_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_request_task.Bind(wx.EVT_BUTTON, self.on_request_task)

        self.btn_accept_task = wx.Button(self.proposals_tab, label="Accept Task")
        self.proposals_button_sizer.Add(self.btn_accept_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_accept_task.Bind(wx.EVT_BUTTON, self.on_accept_task)

        self.proposals_sizer.Add(self.proposals_button_sizer, 0, wx.EXPAND)

        self.proposals_button_sizer_2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refuse_task = wx.Button(self.proposals_tab, label="Refuse Task")
        self.proposals_button_sizer_2.Add(self.btn_refuse_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_refuse_task.Bind(wx.EVT_BUTTON, self.on_refuse_task)

        self.btn_submit_for_verification = wx.Button(self.proposals_tab, label="Submit for Verification")
        self.proposals_button_sizer_2.Add(self.btn_submit_for_verification, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_for_verification.Bind(wx.EVT_BUTTON, self.on_submit_for_verification)

        self.proposals_sizer.Add(self.proposals_button_sizer_2, 0, wx.EXPAND)

        # Add checkbox for showing refused tasks
        bottom_controls_sizer = wx.BoxSizer(wx.HORIZONTAL)
        bottom_controls_sizer.AddStretchSpacer()
        self.chk_show_refused = wx.CheckBox(self.proposals_tab, label="Show Refused Tasks")
        self.chk_show_refused.Bind(wx.EVT_CHECKBOX, self.on_toggle_refused_tasks)
        bottom_controls_sizer.Add(self.chk_show_refused, 0, wx.ALL, 2)
        self.proposals_sizer.Add(bottom_controls_sizer, 0, wx.EXPAND | wx.ALL, 2)

        # Add grid to Proposals tab
        self.proposals_grid = self.setup_grid(gridlib.Grid(self.proposals_tab), 'proposals')
        self.proposals_grid.EnableEditing(False)
        self.proposals_grid.SetSelectionMode(gridlib.Grid.SelectRows)
        self.proposals_grid.Bind(gridlib.EVT_GRID_SELECT_CELL, self.on_proposal_selection)  # Bind selection event
        self.proposals_sizer.Add(self.proposals_grid, 1, wx.EXPAND | wx.ALL, 20)

        # Store reference to proposals tab page
        self.tab_pages["Proposals"] = self.proposals_tab

        #################################
        # VERIFICATION
        #################################

        self.verification_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.verification_tab, "Verification")
        self.verification_sizer = wx.BoxSizer(wx.VERTICAL)
        self.verification_tab.SetSizer(self.verification_sizer)

        # Task ID input box
        task_id_sizer = wx.BoxSizer(wx.HORIZONTAL)
        task_id_label = wx.StaticText(self.verification_tab, label="Task ID:")
        self.verification_txt_task_id = wx.StaticText(self.verification_tab, label="")
        task_id_sizer.Add(task_id_label, flag=wx.ALL | wx.CENTER, border=5)
        task_id_sizer.Add(self.verification_txt_task_id, flag=wx.ALL | wx.CENTER, border=5)
        self.verification_sizer.Add(task_id_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        # Verification Details input box
        self.verification_lbl_details = wx.StaticText(self.verification_tab, label="Verification Details:")
        self.verification_sizer.Add(self.verification_lbl_details, flag=wx.ALL, border=5)
        self.verification_txt_details = wx.TextCtrl(self.verification_tab, style=wx.TE_MULTILINE, size=(-1, 100))
        self.verification_sizer.Add(self.verification_txt_details, flag=wx.EXPAND | wx.ALL, border=5)

        # Submit Verification Details and Log Pomodoro buttons
        self.verification_button_sizer_1 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_submit_verification_details = wx.Button(self.verification_tab, label="Submit Verification Details")
        self.btn_log_pomodoro = wx.Button(self.verification_tab, label="Log Pomodoro")
        self.verification_button_sizer_1.Add(self.btn_submit_verification_details, 1, wx.EXPAND | wx.ALL, 5)
        self.verification_button_sizer_1.Add(self.btn_log_pomodoro, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_verification_details.Bind(wx.EVT_BUTTON, self.on_submit_verification_details)
        self.btn_log_pomodoro.Bind(wx.EVT_BUTTON, self.on_log_pomodoro)
        self.verification_sizer.Add(self.verification_button_sizer_1, 0, wx.EXPAND)

        # Refuse button and force update button
        self.verification_button_sizer_2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refuse_verification = wx.Button(self.verification_tab, label="Refuse")
        self.btn_force_update = wx.Button(self.verification_tab, label="Force Update")
        self.verification_button_sizer_2.Add(self.btn_refuse_verification, 1, wx.EXPAND | wx.ALL, 5)
        self.verification_button_sizer_2.Add(self.btn_force_update, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_refuse_verification.Bind(wx.EVT_BUTTON, self.on_refuse_verification)
        self.btn_force_update.Bind(wx.EVT_BUTTON, self.on_force_update)
        self.verification_sizer.Add(self.verification_button_sizer_2, 0, wx.EXPAND)

        # Add grid to Verification tab
        self.verification_grid = self.setup_grid(gridlib.Grid(self.verification_tab), 'verification')
        self.verification_grid.EnableEditing(False)
        self.verification_grid.SetSelectionMode(gridlib.Grid.SelectRows)
        self.verification_grid.Bind(gridlib.EVT_GRID_SELECT_CELL, self.on_verification_selection)
        self.verification_sizer.Add(self.verification_grid, 1, wx.EXPAND | wx.ALL, 20)

        # Store reference to verification tab page
        self.tab_pages["Verification"] = self.verification_tab

        #################################
        # REWARDS
        #################################

        self.rewards_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.rewards_tab, "Rewards")
        self.rewards_sizer = wx.BoxSizer(wx.VERTICAL)
        self.rewards_tab.SetSizer(self.rewards_sizer)

        # Add grid to Rewards tab
        self.rewards_grid = self.setup_grid(gridlib.Grid(self.rewards_tab), 'rewards')
        self.rewards_sizer.Add(self.rewards_grid, 1, wx.EXPAND | wx.ALL, 20)    

        # Store reference to rewards tab page
        self.tab_pages["Rewards"] = self.rewards_tab

        #################################
        # PAYMENTS
        #################################

        self.build_payments_tab()

        # Store reference to payments tab page
        self.tab_pages["Payments"] = self.payments_tab

        #################################
        # MEMOS
        #################################

        self.memos_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.memos_tab, "Memos")
        self.memos_sizer = wx.BoxSizer(wx.VERTICAL)
        self.memos_tab.SetSizer(self.memos_sizer)

        self.memos_sizer.AddSpacer(10)

        # Add recipient selection
        recipient_sizer = wx.BoxSizer(wx.HORIZONTAL)
        recipient_lbl = wx.StaticText(self.memos_tab, label="Recipient:")
        recipient_sizer.Add(recipient_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.memo_recipient = wx.ComboBox(
            self.memos_tab,
            style=wx.CB_DROPDOWN,
            size=(200, -1)
        )
        self.memo_recipient.Bind(wx.EVT_COMBOBOX, self.on_destination_selected)
        self.memo_recipient.Bind(wx.EVT_TEXT, self.on_destination_text)
        recipient_sizer.Add(self.memo_recipient, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        # Add checkboxes
        self.memo_chk_encrypt = wx.CheckBox(self.memos_tab, label="Encrypt")
        recipient_sizer.Add(self.memo_chk_encrypt, flag=wx.ALIGN_CENTER_VERTICAL, border=5)
        self.memos_sizer.Add(recipient_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Create splitter window
        self.memos_splitter = wx.SplitterWindow(self.memos_tab, style=wx.SP_3D | wx.SP_LIVE_UPDATE)
        top_panel = wx.Panel(self.memos_splitter)
        top_sizer = wx.BoxSizer(wx.VERTICAL)

        # Add memo input box section with encryption requests button
        memo_header_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_memo = wx.StaticText(top_panel, label="Enter your memo:")
        memo_header_sizer.Add(self.lbl_memo, 1, wx.ALIGN_CENTER_VERTICAL)

        self.btn_encryption_requests = wx.Button(top_panel, label="Encryption Requests")
        self.btn_encryption_requests.Bind(wx.EVT_BUTTON, self.on_encryption_requests)
        memo_header_sizer.Add(self.btn_encryption_requests, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 5)

        top_sizer.Add(memo_header_sizer, 0, wx.EXPAND | wx.ALL, border=5)
        self.txt_memo_input = wx.TextCtrl(top_panel, style=wx.TE_MULTILINE, size=(-1, 200))
        top_sizer.Add(self.txt_memo_input, 1, wx.EXPAND | wx.ALL, border=5)

        # Add submit button
        self.btn_submit_memo = wx.Button(top_panel, label="Submit Memo")
        top_sizer.Add(self.btn_submit_memo, flag=wx.ALL | wx.EXPAND, border=5)
        self.btn_submit_memo.Bind(wx.EVT_BUTTON, self.on_submit_memo)

        top_panel.SetSizer(top_sizer)

        # Add grid to Memos tab
        bottom_panel = wx.Panel(self.memos_splitter)
        bottom_sizer = wx.BoxSizer(wx.VERTICAL)
        self.memos_grid = self.setup_grid(gridlib.Grid(bottom_panel), 'memos')
        bottom_sizer.Add(self.memos_grid, 1, wx.EXPAND | wx.ALL, 20)
        bottom_panel.SetSizer(bottom_sizer)

        # Initialize splitter
        self.memos_splitter.SplitHorizontally(top_panel, bottom_panel)
        self.memos_splitter.SetMinimumPaneSize(100)
        self.memos_splitter.SetSashGravity(0.4)

        self.memos_sizer.Add(self.memos_splitter, 1, wx.EXPAND)

        # Store reference to memos tab page
        self.tab_pages["Memos"] = self.memos_tab

        #################################
        # LOGS
        #################################

        self.log_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.log_tab, "Log")
        self.log_sizer = wx.BoxSizer(wx.VERTICAL)
        self.log_tab.SetSizer(self.log_sizer)

        # Create a text control for logs
        self.log_text = wx.TextCtrl(self.log_tab, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.log_sizer.Add(self.log_text, 1, wx.EXPAND | wx.ALL, 5)

        # Store reference to log tab page
        self.tab_pages["Log"] = self.log_tab

        #################################

        self.panel.SetSizer(self.sizer)

        self.status_bar = self.CreateStatusBar()
        self.status_bar.SetFieldsCount(2)
        self.status_bar.SetStatusWidths([-3, -1])  # 75% for message, 25% for state
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def create_login_panel(self):
        panel = wx.Panel(self.panel)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Load and resize the logo
        current_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(current_dir, '..', 'images', 'simple_pf_logo.png')
        logo = wx.Image(logo_path, wx.BITMAP_TYPE_ANY)
        logo = logo.Scale(230, 230, wx.IMAGE_QUALITY_HIGH)
        bitmap = wx.Bitmap(logo)
        logo_ctrl = wx.StaticBitmap(panel, -1, bitmap=bitmap)

        # Create a box to center the content
        box = wx.Panel(panel)
        if os.name == 'posix':  # macOS
            sys_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
            darkened_color = self.darken_color(sys_color, 0.95)  # 5% darker
        else:  # Windows
            darkened_color = wx.Colour(220, 220, 220)
        box.SetBackgroundColour(darkened_color)
        box_sizer = wx.BoxSizer(wx.VERTICAL)

        # Username
        self.login_lbl_username = wx.StaticText(box, label="Username:")
        box_sizer.Add(self.login_lbl_username, flag=wx.ALL, border=5)

        # Create combobox for username dropdown
        self.login_txt_username = wx.ComboBox(box, style=wx.CB_DROPDOWN)
        box_sizer.Add(self.login_txt_username, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        # Password
        self.login_lbl_password = wx.StaticText(box, label="Password:")
        box_sizer.Add(self.login_lbl_password, flag=wx.ALL, border=5)
        self.login_txt_password = wx.TextCtrl(box, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        box_sizer.Add(self.login_txt_password, flag=wx.EXPAND | wx.ALL, border=5)

        # Error label
        self.login_error_label = wx.StaticText(box, label="")
        self.login_error_label.SetForegroundColour(wx.RED)
        box_sizer.Add(self.login_error_label, flag=wx.EXPAND |wx.ALL, border=5)

        # Login button
        self.btn_login = wx.Button(box, label="Login")
        box_sizer.Add(self.btn_login, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_login.Bind(wx.EVT_BUTTON, self.on_login)

        # Create New User button
        self.btn_new_user = wx.Button(box, label="Create New User")
        box_sizer.Add(self.btn_new_user, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_new_user.Bind(wx.EVT_BUTTON, self.on_create_new_user)

        box.SetSizer(box_sizer)

        # Create a vertical sizer for logo and login box
        content_sizer = wx.BoxSizer(wx.VERTICAL)
        content_sizer.Add(logo_ctrl, 0, wx.ALIGN_CENTER | wx.BOTTOM, 20)
        content_sizer.Add(box, 0, wx.EXPAND, 20)

        # Center the box on the panel
        main_sizer.AddStretchSpacer(1)
        main_sizer.Add(content_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 20)
        main_sizer.AddStretchSpacer(1)

        panel.SetSizer(main_sizer)

        # Bind events
        self.login_txt_username.Bind(wx.EVT_COMBOBOX_DROPDOWN, self.on_dropdown_opened)
        self.login_txt_username.Bind(wx.EVT_COMBOBOX, self.on_username_selected)
        self.login_txt_username.Bind(wx.EVT_TEXT, self.on_clear_error)
        self.login_txt_password.Bind(wx.EVT_TEXT, self.on_clear_error)

        # Add Enter key bindings
        self.login_txt_username.Bind(wx.EVT_TEXT_ENTER, self.on_login)
        self.login_txt_password.Bind(wx.EVT_TEXT_ENTER, self.on_login)

        self.populate_username_dropdown()

        return panel
    
    def build_payments_tab(self):
        """Build the unified payments interface"""
        self.payments_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.payments_tab, "Payments")
        self.payments_sizer = wx.BoxSizer(wx.VERTICAL)
        self.payments_tab.SetSizer(self.payments_sizer)

        # Add spacing at the top
        self.payments_sizer.AddSpacer(10)

        # Create a horizontal sizer for inputs and send button
        main_input_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # left side - inputs and memo
        left_sizer = wx.BoxSizer(wx.VERTICAL)

        # Amount, token and destination input section
        top_row_input_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Amount input with label
        amount_label = wx.StaticText(self.payments_tab, label="Send Amount:")
        top_row_input_sizer.Add(amount_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.payment_txt_amount = wx.TextCtrl(self.payments_tab, size=(100, -1))
        top_row_input_sizer.Add(self.payment_txt_amount, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        # Token selector
        self.token_selector = wx.ComboBox(
            self.payments_tab,
            choices=["XRP", "PFT"],
            style=wx.CB_READONLY | wx.CB_DROPDOWN,
            size=(70, -1)
        )
        self.token_selector.SetSelection(0)  # Default to XRP
        top_row_input_sizer.Add(self.token_selector, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        # Destination input with label
        dest_label = wx.StaticText(self.payments_tab, label="To:")
        top_row_input_sizer.Add(dest_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.txt_payment_destination = wx.ComboBox(
            self.payments_tab,
            style=wx.CB_DROPDOWN,
            size=(200, -1)  # Wider to accommodate XRP addresses
        )
        self.txt_payment_destination.Bind(wx.EVT_COMBOBOX, self.on_destination_selected)
        self.txt_payment_destination.Bind(wx.EVT_TEXT, self.on_destination_text)

        top_row_input_sizer.Add(self.txt_payment_destination, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        # Destination tags / Memo ID inputs
        dest_tag_label = wx.StaticText(self.payments_tab, label="Memo ID (optional):")
        top_row_input_sizer.Add(dest_tag_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.txt_destination_tag = wx.TextCtrl(self.payments_tab, size=(100, -1))
        tooltip_text = ("Some exchanges and services require a Memo ID (also called Destination Tag) " 
                       "for XRP deposits.\nCheck your recipient's requirements - sending XRP without " 
                       "a required Memo ID may result in lost funds.")
        self.txt_destination_tag.SetToolTip(tooltip_text)
        dest_tag_label.SetToolTip(tooltip_text)
        top_row_input_sizer.Add(self.txt_destination_tag, 0, wx.ALIGN_CENTER_VERTICAL)

        left_sizer.Add(top_row_input_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Optional memo field
        memo_sizer = wx.BoxSizer(wx.HORIZONTAL)
        memo_label = wx.StaticText(self.payments_tab, label="Memo (Optional):")
        self.txt_payment_memo = wx.TextCtrl(self.payments_tab)
        memo_sizer.Add(memo_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        memo_sizer.Add(self.txt_payment_memo, 1)
        
        left_sizer.Add(memo_sizer, 0, wx.EXPAND | wx.ALL, 5)

        main_input_sizer.Add(left_sizer, 1, wx.EXPAND | wx.RIGHT, 5)

        # Send button - height matches both input rows
        button_height = self.payment_txt_amount.GetSize().height * 2 + 5
        self.btn_send = wx.Button(self.payments_tab, label="Send", size=(-1, button_height))
        main_input_sizer.Add(self.btn_send, 0, wx.ALIGN_CENTER_VERTICAL)

        self.payments_sizer.Add(main_input_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Payments history grid
        self.payments_grid = self.setup_grid(gridlib.Grid(self.payments_tab), 'payments')
        self.payments_sizer.Add(self.payments_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Bind events
        self.btn_send.Bind(wx.EVT_BUTTON, self.on_send_payment)

    def on_destination_selected(self, event):
        """Handle selection from dropdown - extract the address"""
        event.Skip()

    def on_destination_text(self, event):
        """Handle manual text entry - allow any text"""
        event.Skip()

    def on_toggle_refused_tasks(self, event):
        """Handle toggling of the refused tasks checkbox"""
        try:
            include_refused = self.chk_show_refused.IsChecked()
            # Get proposals data with the new include_refused setting
            proposals_df = self.task_manager.get_proposals_df(include_refused=include_refused)
            # Update only the proposals grid
            wx.PostEvent(self, UpdateGridEvent(data=proposals_df, target="proposals", caller=f"{self.__class__.__name__}.on_toggle_refused_tasks"))
        except Exception as e:
            logger.error(f"Error updating proposals grid: {e}")
            wx.MessageBox(f"Error updating proposals grid: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def update_all_destination_comboboxes(self):
        """Update all destination comboboxes"""
        self._populate_destination_combobox(combobox=self.txt_payment_destination)
        self._populate_destination_combobox(
            combobox=self.memo_recipient, 
            default_destination=self.network_config.remembrancer_address
        )

    def _populate_destination_combobox(self, combobox, default_destination=None):
        """
        Populate destination combobox with contacts
        Args:
            combobox: wx.ComboBox to populate
            default_destination: Optional default address to select
        """
        current_value = combobox.GetValue()

        combobox.Clear()
        contacts = self.task_manager.get_contacts()

        # Add contacts in format "name (address)"
        for address, name in contacts.items():
            display_text = f"{name} ({address})"
            combobox.Append(display_text, address)

        # If there was a custom value, add it back
        if current_value and current_value not in [combobox.GetString(i) for i in range(combobox.GetCount())]:
            combobox.Append(current_value)
            combobox.SetValue(current_value)

        # Set default selection
        elif default_destination:
            # First try to find it in existing contacts
            found = False
            for i in range(combobox.GetCount()):
                if combobox.GetClientData(i) == default_destination:
                    combobox.SetSelection(i)  # Use SetSelection instead of SetValue
                    found = True
                    break

            if not found:
                # For system addresses like remembrancer, try to get the name from network config
                network_config = get_network_config()
                if default_destination == network_config.remembrancer_address:
                    display_text = f"{network_config.remembrancer_name} ({default_destination})"
                else:
                    display_text = default_destination

                combobox.Append(display_text, default_destination)
                combobox.SetSelection(combobox.GetCount() - 1)

    def on_send_payment(self, event):
        """Handle unified payment submission"""
        # Check if password is required
        if self.config.get_global_config('require_password_for_payment'):
            dialog = wx.PasswordEntryDialog(
                self, 
                "Enter Password", 
                "Please enter your password to confirm payment."
            )
        
            if dialog.ShowModal() != wx.ID_OK:
                dialog.Destroy()
                return
                
            password = dialog.GetValue()
            dialog.Destroy()
            # wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)
        
            if not self.task_manager.verify_password(password):
                wx.MessageBox("Incorrect password", "Error", wx.OK | wx.ICON_ERROR)
                return
    
        token_type = self.token_selector.GetValue()
        amount = self.payment_txt_amount.GetValue()

        # Get destination - check if it's a saved contact first
        destination_idx = self.txt_payment_destination.GetSelection()
        if destination_idx != wx.NOT_FOUND:
            # Get the stored address from client data
            destination = self.txt_payment_destination.GetClientData(destination_idx)
        else:
            # Manual entry - use raw text value
            destination = self.txt_payment_destination.GetValue()

        try:
            destination = self.validate_address(destination)
        except ValueError as e:
            logger.error(f"Error validating address: {e}")
            wx.MessageBox(f"Recipient address is invalid: {e}", "Error", wx.OK | wx.ICON_ERROR)
            self.btn_send.SetLabel("Send")
            self.set_wallet_ui_state(WalletUIState.IDLE)
            return

        memo = self.txt_payment_memo.GetValue()
        destination_tag = self.txt_destination_tag.GetValue()

        self.btn_send.SetLabel("Sending...")

        # TODO: Consider adding more validation here
        if not amount or not destination:
            wx.MessageBox("Please enter a valid amount and destination!", "Error", wx.OK | wx.ICON_ERROR)
            self.btn_send.SetLabel("Send")
            return
        
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Confirming payment...")
        
        # Show confirmation dialog with contact saving option
        if not self.show_payment_confirmation(amount, destination, token_type):
            self.btn_send.SetLabel("Send")
            self.set_wallet_ui_state(WalletUIState.IDLE)
            return
        
        self.set_wallet_ui_state(message="Submitting payment...")
        self.btn_send.Disable()

        try:
            if token_type == "XRP":
                dest_tag = int(destination_tag) if destination_tag.strip() else None
                response = self.task_manager.send_xrp(amount, destination, memo, destination_tag=dest_tag)
            else: # PFT
                response = self.task_manager.send_pft(amount, destination, memo)

            formatted_response = self.format_response(response)
            dialog = SelectableMessageDialog(self, f"{token_type} Payment Submitted", formatted_response)
            dialog.ShowModal()
            dialog.Destroy()

        except ValueError as e:
            logger.error(f"Invalid input: {e}")
            wx.MessageBox(f"Invalid input: {e}", "Error", wx.OK | wx.ICON_ERROR)
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            logger.error(f"Error submitting payment: {e}")
            wx.MessageBox(f"Error submitting payment: {e}", "Error", wx.OK | wx.ICON_ERROR)
        except Exception as e:
            logger.error(f"Error submitting payment: {e}")
            wx.MessageBox(f"Error submitting payment: {e}", "Error", wx.OK | wx.ICON_ERROR)
        
        self.btn_send.Enable()
        self.btn_send.SetLabel("Send")
        # self._sync_and_refresh()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def populate_username_dropdown(self):
        """Populates the username dropdown with cached usernames"""
        try:
            cached_usernames = CredentialManager.get_cached_usernames()
            self.login_txt_username.Clear()
            self.login_txt_username.AppendItems(cached_usernames)

            # Get the last logged-in user
            last_user = self.config.get_global_config('last_logged_in_user')

            if last_user and last_user in cached_usernames:
                self.login_txt_username.SetValue(last_user)
                self.login_txt_password.SetFocus()
            elif cached_usernames:
                self.login_txt_username.SetValue(cached_usernames[0])
                self.login_txt_password.SetFocus()
        except Exception as e:
            logger.error(f"Error populating username dropdown: {e}")
            self.show_error("Error loading cached usernames")

    def on_dropdown_opened(self, event):
        """Handle dropdown being opened"""
        self.populate_username_dropdown()
        event.Skip()

    def on_username_selected(self, event):
        """Handle username selection from dropdown"""
        self.login_txt_password.SetFocus()
        self.on_clear_error(event)
    
    def create_user_details_panel(self):
        panel = wx.Panel(self.panel)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Return to Login button
        return_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_return_to_login = wx.Button(panel, label="Return to Login")
        return_btn_sizer.Add(self.btn_return_to_login, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        self.btn_return_to_login.Bind(wx.EVT_BUTTON, self.on_return_to_login)
        main_sizer.Add(return_btn_sizer, 0, wx.ALIGN_CENTER | wx.TOP, 10)
        main_sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.TOP, 5)
        
        # Create a centered box for the content
        content_panel = wx.Panel(panel)
        if os.name == "posix":  # macOS
            sys_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
            darkened_color = self.darken_color(sys_color, 0.95)  # 5% darker
        else:
            darkened_color = wx.Colour(220, 220, 220)
        content_panel.SetBackgroundColour(darkened_color)

        content_sizer = wx.BoxSizer(wx.VERTICAL)

        # Fixed width for all text controls
        text_ctrl_width = 400

        # Fixed width for all text controls
        text_ctrl_width = 400
        
        # XRP Address
        address_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_lbl_xrp_address = wx.StaticText(content_panel, label="XRP Address:")
        address_sizer.Add(self.create_lbl_xrp_address, 0, wx.BOTTOM, 5)
        self.create_txt_xrp_address = wx.TextCtrl(content_panel, size=(text_ctrl_width, -1))
        address_sizer.Add(self.create_txt_xrp_address, 1, wx.EXPAND)
        content_sizer.Add(address_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # XRP Secret
        secret_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_lbl_xrp_secret = wx.StaticText(content_panel, label="XRP Secret:")
        secret_sizer.Add(self.create_lbl_xrp_secret, 0, wx.BOTTOM, 5)
        
        secret_input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.create_txt_xrp_secret = wx.TextCtrl(content_panel, style=wx.TE_PASSWORD, size=(text_ctrl_width - 100, -1))
        secret_input_sizer.Add(self.create_txt_xrp_secret, 1, wx.EXPAND | wx.RIGHT, 10)
        self.chk_show_secret = wx.CheckBox(content_panel, label="Show Secret")
        secret_input_sizer.Add(self.chk_show_secret, 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_show_secret.Bind(wx.EVT_CHECKBOX, self.on_toggle_secret_visibility_user_details)
        
        secret_sizer.Add(secret_input_sizer, 1, wx.EXPAND)
        content_sizer.Add(secret_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # Username
        username_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_lbl_username = wx.StaticText(content_panel, label="Username:")
        username_sizer.Add(self.create_lbl_username, 0, wx.BOTTOM, 5)
        self.create_txt_username = wx.TextCtrl(content_panel, style=wx.TE_PROCESS_ENTER, size=(text_ctrl_width, -1))
        username_sizer.Add(self.create_txt_username, 1, wx.EXPAND)
        content_sizer.Add(username_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # Password
        password_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_lbl_password = wx.StaticText(content_panel, label="Password (minimum 8 characters):")
        password_sizer.Add(self.create_lbl_password, 0, wx.BOTTOM, 5)
        self.create_txt_password = wx.TextCtrl(content_panel, style=wx.TE_PASSWORD, size=(text_ctrl_width, -1))
        password_sizer.Add(self.create_txt_password, 1, wx.EXPAND)
        content_sizer.Add(password_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # Confirm Password
        confirm_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_lbl_confirm_password = wx.StaticText(content_panel, label="Confirm Password:")
        confirm_sizer.Add(self.create_lbl_confirm_password, 0, wx.BOTTOM, 5)
        self.create_txt_confirm_password = wx.TextCtrl(content_panel, style=wx.TE_PASSWORD, size=(text_ctrl_width, -1))
        confirm_sizer.Add(self.create_txt_confirm_password, 1, wx.EXPAND)
        content_sizer.Add(confirm_sizer, 0, wx.ALL | wx.EXPAND, 10)
        # Wallet buttons
        wallet_buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_generate_wallet = wx.Button(content_panel, label="Generate New XRP Wallet")
        self.btn_restore_wallet = wx.Button(content_panel, label="Restore from Seed")
        wallet_buttons_sizer.Add(self.btn_generate_wallet, 1, wx.RIGHT, 5)
        wallet_buttons_sizer.Add(self.btn_restore_wallet, 1, wx.LEFT, 5)
        content_sizer.Add(wallet_buttons_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)
        self.btn_restore_wallet.Bind(wx.EVT_BUTTON, self.on_restore_wallet)

        # Cache button
        self.btn_cache_user = wx.Button(content_panel, label="Cache Credentials")
        content_sizer.Add(self.btn_cache_user, 0, wx.ALL | wx.EXPAND, 10)
        self.btn_cache_user.Bind(wx.EVT_BUTTON, self.on_cache_user)

        content_panel.SetSizer(content_sizer)

        # Add content panel to main sizer with centering
        main_sizer.AddStretchSpacer(1)
        main_sizer.Add(content_panel, 0, wx.ALIGN_CENTER | wx.ALL, 20)
        main_sizer.AddStretchSpacer(1)

        # Set tooltips
        self.tooltip_xrp_address = wx.ToolTip("This is your XRP address. It is used to receive XRP or PFT.")
        self.tooltip_xrp_secret = wx.ToolTip("This is your XRP secret. NEVER SHARE THIS SECRET WITH ANYONE! NEVER LOSE THIS SECRET!")
        self.tooltip_username = wx.ToolTip("Set a username that you will use to log in with. You can use lowercase letters, numbers, and underscores.")
        self.tooltip_password = wx.ToolTip("Set a password that you will use to log in with. This password is used to encrypt your XRP address and secret.")
        self.tooltip_confirm_password = wx.ToolTip("Confirm your password.")
        
        self.create_txt_xrp_address.SetToolTip(self.tooltip_xrp_address)
        self.create_txt_xrp_secret.SetToolTip(self.tooltip_xrp_secret)
        self.create_txt_username.SetToolTip(self.tooltip_username)
        self.create_txt_password.SetToolTip(self.tooltip_password)
        self.create_txt_confirm_password.SetToolTip(self.tooltip_confirm_password)

        panel.SetSizer(main_sizer)

        return panel
    
    def on_force_lowercase(self, event):
        value = self.create_txt_username.GetValue()
        lowercase_value = value.lower()
        if value != lowercase_value:
            self.create_txt_username.SetValue(lowercase_value)
            self.create_txt_username.SetInsertionPointEnd()
    
    def on_toggle_secret_visibility_user_details(self, event):
        if self.chk_show_secret.IsChecked():
            self.create_txt_xrp_secret.SetWindowStyle(wx.TE_PROCESS_ENTER)  # Default style
        else:
            self.create_txt_xrp_secret.SetWindowStyle(wx.TE_PASSWORD)

        # Store the current value and cursor position
        current_value = self.create_txt_xrp_secret.GetValue()

        # Recreate the text control with the new style
        new_txt_xrp_secret = wx.TextCtrl(self.create_txt_xrp_secret.GetParent(), 
                                        value=current_value,
                                        style=self.create_txt_xrp_secret.GetWindowStyle())
        
        # Replace the old control with the new one in the sizer
        self.create_txt_xrp_secret.GetContainingSizer().Replace(self.create_txt_xrp_secret, new_txt_xrp_secret)
        self.create_txt_xrp_secret.Destroy()
        self.create_txt_xrp_secret = new_txt_xrp_secret

        # Refresh the layout
        self.create_txt_xrp_secret.GetParent().Layout()

    def on_generate_wallet(self, event):
        # Generate a new XRP wallet
        self.wallet = Wallet.create()
        self.create_txt_xrp_address.SetValue(self.wallet.classic_address)
        self.create_txt_xrp_secret.SetValue(self.wallet.seed)

    def on_restore_wallet(self, event):
        """Restore wallet from existing seed"""
        dialog = CustomDialog(self, "Restore Wallet", ["XRP Secret"])
        if dialog.ShowModal() == wx.ID_OK:
            seed = dialog.GetValues()["XRP Secret"]
            try:
                # Attempt to create wallet from seed
                wallet = Wallet.from_seed(seed)

                # Update the UI with the restored wallet details
                self.create_txt_xrp_address.SetValue(wallet.classic_address)
                self.create_txt_xrp_secret.SetValue(wallet.seed)

                wx.MessageBox("Wallet restored successfully!", "Success", wx.OK | wx.ICON_INFORMATION)

            except Exception as e:
                logger.error(f"Error restoring wallet: {e}")
                wx.MessageBox("Invalid seed format. Please check your seed and try again.", "Error", wx.OK | wx.ICON_ERROR)

        dialog.Destroy()

    def on_cache_user(self, event):
        """Caches the user's credentials"""
        input_map = {
            'Username_Input': self.create_txt_username.GetValue(),
            'Password_Input': self.create_txt_password.GetValue(),
            'XRP Address_Input': self.create_txt_xrp_address.GetValue(),
            'XRP Secret_Input': self.create_txt_xrp_secret.GetValue(),
            'Confirm Password_Input': self.create_txt_confirm_password.GetValue(),
        }

        if self.create_txt_password.GetValue() != self.create_txt_confirm_password.GetValue():
            logger.error("Passwords Do Not Match! Please Retry.")
            wx.MessageBox('Passwords Do Not Match! Please Retry.', 'Error', wx.OK | wx.ICON_ERROR)
        elif any(not value for value in input_map.values()):
            logger.error("All fields are required for caching!")
            wx.MessageBox('All fields are required for caching!', 'Error', wx.OK | wx.ICON_ERROR)
        else:
            try:
                response = CredentialManager.cache_credentials(input_map)
                wx.MessageBox(response, 'Info', wx.OK | wx.ICON_INFORMATION)
            except Exception as e:
                logger.error(f"{e}")
                wx.MessageBox(f"{e}", 'Error', wx.OK | wx.ICON_ERROR)
            else:
                # Clear all fields if caching was successful
                self.create_txt_username.SetValue('')
                self.create_txt_password.SetValue('')
                self.create_txt_xrp_address.SetValue('')
                self.create_txt_xrp_secret.SetValue('')
                self.create_txt_confirm_password.SetValue('')

    def on_login(self, event):
        self.set_wallet_ui_state(WalletUIState.BUSY, "Logging in...")
        self.btn_login.SetLabel("Logging in...")
        self.btn_login.Update()

        self.username = self.login_txt_username.GetValue()
        password = self.login_txt_password.GetValue()

        try:
            self.task_manager = PostFiatTaskManager(
                username=self.username, 
                password=password,
                network_url=self.network_url,
                config=self.config
            )

        except (ValueError, InvalidToken, KeyError) as e:
            logger.error(f"Login failed: {e}")
            logger.error(traceback.format_exc())
            self.show_error("Invalid username or password")
            self.btn_login.SetLabel("Login")
            self.btn_login.Update()
            return
        except Exception as e:
            logger.error(f"Login failed: {e}")
            logger.error(traceback.format_exc())
            self.show_error(f"Login failed: {e}")
            self.btn_login.SetLabel("Login")
            self.btn_login.Update()
            return
        
        self.enable_menus()
        
        self.wallet = self.task_manager.user_wallet

        self.start_wallet_state_monitoring()

        logger.info(f"Logged in as {self.username}")

        # Save the last logged-in user
        self.config.set_global_config('last_logged_in_user', self.username)

        # Hide login panel and show tabs
        self.login_panel.Hide()
        self.tabs.Show()

        self.update_account_display()
        self.update_tokens()

        # Update layout and ensure correct sizing
        self.panel.Layout()
        self.Layout()
        self.Fit()

        self.worker = XRPLMonitorThread(self)
        self.worker.start()

        # Populate grids with data.
        # No need to call sync_and_refresh here, since sync_transactions was called by the task manager's instantiation
        self.refresh_grids()
        self.auto_size_window()

        self.set_wallet_ui_state(WalletUIState.IDLE)

        self.update_all_destination_comboboxes()

    def update_network_display(self):
        """Update UI elements that display network information"""
        self.summary_lbl_endpoint.SetLabel(f"HTTPS: {self.network_url}")
        self.summary_lbl_ws_endpoint.SetLabel(f"Websocket: {self.ws_url}")
        self.summary_sizer.Layout()
        self.summary_tab.Layout()

    def check_wallet_state(self):
        """Check the wallet state and update the UI accordingly"""
        if hasattr(self, 'task_manager'):
            self.task_manager.determine_wallet_state()
            self.update_account_display()
            self.update_ui_based_on_wallet_state()

    def start_wallet_state_monitoring(self):
        """Start monitoring wallet state transitions"""
        self.wallet_state_in_transition = True
        if self.wallet_state_monitor_timer is None:
            self.wallet_state_monitor_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.on_state_monitor_tick, self.wallet_state_monitor_timer)
        
        if not self.wallet_state_monitor_timer.IsRunning():
            self.wallet_state_monitor_timer.Start(self.state_check_interval)
            logger.debug("Started wallet state monitoring")

    def stop_wallet_state_monitoring(self):
        """Stop monitoring wallet state transitions"""
        self.wallet_state_in_transition = False
        if self.wallet_state_monitor_timer and self.wallet_state_monitor_timer.IsRunning():
            self.wallet_state_monitor_timer.Stop()
            logger.debug("Stopped wallet state monitoring")

    def on_state_monitor_tick(self, event):
        """Handle timer tick for wallet state monitoring"""
        if self.wallet_state_in_transition:
            logger.debug("Checking wallet state during transition...")
            self.check_wallet_state()
        else:
            self.stop_wallet_state_monitoring()

    @PerformanceMonitor.measure('update_ui_based_on_wallet_state')
    def update_ui_based_on_wallet_state(self):
        """Update UI elements based on wallet state. Only hides PFT tabs if wallet is not active."""
        current_state = self.task_manager.wallet_state
        available_tabs = self.STATE_AVAILABLE_TABS[current_state]

        self.tabs.Freeze()

        # Update all tabs' visibility and enabled state
        for tab_name, tab_page in self.tab_pages.items():
            tab_index = self.tabs.FindPage(tab_page)
            if tab_index != wx.NOT_FOUND:
                if tab_name in available_tabs:
                    self.tabs.GetPage(tab_index).Enable()
                else:
                    self.tabs.GetPage(tab_index).Disable()

        self.tabs.Layout()
        self.panel.Layout()
        self.Layout()
        self.tabs.Thaw()

        # Update summary tab wallet state labels
        if hasattr(self, 'summary_lbl_wallet_state'):
            self.summary_lbl_wallet_state.SetLabel(f"Wallet State: {current_state.value}")

        if current_state == WalletState.ACTIVE:
            if hasattr(self, 'summary_lbl_next_action'):
                self.summary_lbl_next_action.Hide()
            if hasattr(self, 'btn_wallet_action'):
                self.btn_wallet_action.Hide()
        else:
            if hasattr(self, 'summary_lbl_next_action'):
                self.summary_lbl_next_action.Show()
                self.summary_lbl_next_action.SetLabel(f"Next Action: {self.task_manager.get_required_action()}")
            if hasattr(self, 'btn_wallet_action'):
                self.btn_wallet_action.Show()

        self.summary_sizer.Layout()
        self.summary_tab.Layout()
        self.panel.Layout()

        # Only show message box once
        if not self.take_action_dialog_shown:
            if current_state != WalletState.ACTIVE:
                required_action = self.task_manager.get_required_action()
                message = (
                    f"Some features are currently locked because your wallet is not fully set up.\n\n"
                    f"Next required action: {required_action}\n\n"
                    f"All features will be unlocked once wallet reaches '{WalletState.ACTIVE.value}' state."
                )
                wx.MessageBox(message, "Wallet Features Limited", wx.OK | wx.ICON_INFORMATION)
                self.take_action_dialog_shown = True

    def on_create_new_user(self, event):
        self.login_panel.Hide()
        self.user_details_panel.Show()
        self.panel.Layout()
        self.Refresh()

    def on_return_to_login(self, event):
        self.user_details_panel.Hide()
        self.login_panel.Show()
        self.panel.Layout()
        self.Refresh()

    def show_error(self, message):
        self.login_error_label.SetLabel(message)

        # Simple shake animation
        original_pos = self.login_error_label.GetPosition()
        for i in range(5):
            self.login_error_label.Move(original_pos.x + 2, original_pos.y)
            wx.MilliSleep(40)
            self.login_error_label.Move(original_pos.x - 2, original_pos.y)
            wx.MilliSleep(40)
        self.login_error_label.Move(original_pos)

        self.login_panel.Layout()

    def on_clear_error(self, event):
        self.login_error_label.SetLabel("")
        event.Skip()

    @PerformanceMonitor.measure('update_account_display')
    def update_account_display(self):
        """Update all account-related display elements"""
        logger.debug(f"Updating account display for {self.username}")

        # Update basic account info
        self.summary_lbl_username.SetLabel(f"Username: {self.username}")
        self.summary_lbl_address.SetLabel(f"XRP Address: {self.wallet.address}")

        xrp_balance = self.task_manager.get_xrp_balance()
        xrp_balance = xrpl.utils.drops_to_xrp(str(xrp_balance))
        self.summary_lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

        # PFT balance pending update
        self.summary_lbl_pft_balance.SetLabel(f"PFT Balance: Updating...")

        self.summary_lbl_wallet_state.SetLabel(f"Wallet State: {self.task_manager.wallet_state.value}")
        self.summary_lbl_next_action.SetLabel(f"Next Action: {self.task_manager.get_required_action()}")

    def on_take_action(self, event):
        """Handle wallet action button click based on current state"""
        current_state = self.task_manager.wallet_state

        match current_state:
            case WalletState.UNFUNDED:
                message = (
                    "To activate your wallet, you need \nto send at least 1 XRP to your address. \n\n"
                    f"Your XRP address:\n\n{self.wallet.classic_address}\n\n"
                )
                dialog = SelectableMessageDialog(self, "Fund Your Wallet", message)
                dialog.ShowModal()
                dialog.Destroy()

            case WalletState.FUNDED:
                message = (
                    "Your wallet needs a trust line to handle PFT tokens.\n\n"
                    "This transaction will:\n"
                    "- Set up the required trust line\n"
                    "- Cost a small amount of XRP (~0.000001 XRP)\n"
                    "- Enable PFT token transactions\n\n"
                    "Proceed?"
                )
                if wx.YES == wx.MessageBox(message, "Set Trust Line", wx.YES_NO | wx.ICON_QUESTION):
                    try:
                        self.task_manager.handle_trust_line()
                        self.start_wallet_state_monitoring()
                    except Exception as e:
                        logger.error(f"Error setting trust line: {e}")
                        wx.MessageBox(f"Error setting trust line: {e}", "Error", wx.OK | wx.ICON_ERROR)

            case WalletState.TRUSTLINED:
                message = (
                    "To start accepting tasks, you need to perform the Initiation Rite.\n\n"
                    "This transaction will cost 1 XRP.\n\n"
                    "Please write 1 sentence committing to a long term objective of your choice:"
                )
                dialog = CustomDialog(self, "Initiation Rite", ["Commitment"], message=message)
                if dialog.ShowModal() == wx.ID_OK:
                    commitment = dialog.GetValues()["Commitment"]
                    try:
                        response = self.task_manager.send_initiation_rite(commitment)
                        formatted_response = self.format_response(response)
                        dialog = SelectableMessageDialog(self, "Initiation Rite Result", formatted_response)
                        dialog.ShowModal()
                        dialog.Destroy()
                        wx.CallAfter(lambda: self.check_wallet_state())
                        self.start_wallet_state_monitoring()
                    except Exception as e:
                        logger.error(f"Error sending initiation rite: {e}")
                        wx.MessageBox(f"Error sending initiation rite: {e}", "Error", wx.OK | wx.ICON_ERROR)
                dialog.Destroy()

            case WalletState.INITIATED:
                message = (
                    "To continue with wallet initialization,\n"
                    "you need to establish secure communication with the network.\n\n"
                    "This will:\n"
                    "- Set up encrypted messaging capabilities\n"
                    "- Cost a small amount of XRP (~0.000001 XRP)\n"
                    "- Enable secure communication with the network\n\n"
                    "Would you like to proceed?"
                )
                if wx.YES == wx.MessageBox(message, "Send Handshake", wx.YES_NO | wx.ICON_QUESTION):
                    try:
                        response = self.task_manager.send_handshake(self.network_config.node_address)
                        formatted_response = self.format_response(response)
                        dialog = SelectableMessageDialog(self, "Handshake Result", formatted_response)
                        dialog.ShowModal()
                        dialog.Destroy()
                        wx.CallAfter(lambda: self.check_wallet_state())
                        self.start_wallet_state_monitoring()
                    except Exception as e:
                        logger.error(f"Error sending handshake: {e}")
                        wx.MessageBox(f"Error sending handshake: {e}", "Error", wx.OK | wx.ICON_ERROR)

            case WalletState.HANDSHAKE_SENT:
                message = (
                    "Waiting for handshake response from node to establish encrypted channel.\n\n"
                    "This will only take a moment."
                )
                wx.MessageBox(message, "Waiting for Handshake", wx.OK | wx.ICON_INFORMATION)

            case WalletState.HANDSHAKE_RECEIVED:
                if self.show_google_doc_template(is_initial_setup=True):
                    self.handle_google_doc_submission(event, is_initial_setup=True)
                    self.start_wallet_state_monitoring()

            case _:
                logger.error(f"Unknown wallet state: {current_state}")

    @PerformanceMonitor.measure('run_bg_job')
    def run_bg_job(self, job):
        if self.worker.context:
            asyncio.run_coroutine_threadsafe(job, self.worker.loop)

    def update_ledger(self, message):
        pass  # Simplified for this version

    @PerformanceMonitor.measure('update_account')
    def update_account(self, acct):
        logger.debug(f"Updating account: {acct}")
        xrp_balance = str(xrpl.utils.drops_to_xrp(acct["Balance"]))
        self.summary_lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

        # Check if account state should change
        if self.task_manager:
            current_state = self.task_manager.wallet_state

            # If balance is > 0 XRP and current state is UNFUNDED, update state
            if (current_state == WalletState.UNFUNDED and float(xrp_balance) > 0):
                logger.info("Account now funded. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.FUNDED
                message = (
                    "Your wallet is now funded!\n\n"
                    "You can now proceed with setting up a trust line for PFT tokens.\n"
                    "Click the 'Take Action' button to continue."
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "XRP Received!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.check_wallet_state())

            # If trust line is detected and current state is FUNDED
            elif (current_state == WalletState.FUNDED and self.task_manager.has_trust_line()):
                logger.info("Trust line detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.TRUSTLINED
                message = (
                    "Trust line successfully established!\n\n"
                    "You can now proceed with the initiation rite.\n"
                    "Click the 'Take Action' button to continue."                    
                )
                self.stop_wallet_state_monitoring()
                wx.CallAfter(lambda: wx.MessageBox(message, "Trust Line Set!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.check_wallet_state())

            # If initiation rite is detected and current state is TRUSTLINED
            elif (current_state == WalletState.TRUSTLINED and self.task_manager.initiation_rite_sent()):
                logger.info("Initiation rite detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.INITIATED
                message = (
                    "Initiation rite successfully sent!\n\n"
                    "You can now proceed with setting up an encryption channel with the node.\n"
                    "Click the 'Take Action' button to continue."
                )
                self.stop_wallet_state_monitoring()
                wx.CallAfter(lambda: wx.MessageBox(message, "Initiation Complete!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.check_wallet_state())

            # If sent handshake is detected and current state is INITIATED
            elif (current_state == WalletState.INITIATED and self.task_manager.handshake_sent()):
                logger.info("Sent handshake. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.HANDSHAKE_SENT

            # If received handshake is detected and current state is HANDSHAKE_SENT
            elif (current_state == WalletState.HANDSHAKE_SENT and self.task_manager.handshake_received()):
                logger.info("Received handshake. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.HANDSHAKE_RECEIVED
                message = (
                    "Handshake protocol complete!\n\n"
                    "You can now proceed with setting up your Google Doc.\n"
                    "Click the 'Take Action' button to continue."
                )
                self.stop_wallet_state_monitoring()
                wx.CallAfter(lambda: wx.MessageBox(message, "Handshake Sent!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.check_wallet_state())

            # If Google Doc is detected and current state is HANDSHAKE_RECEIVED
            elif (current_state == WalletState.HANDSHAKE_RECEIVED and self.task_manager.google_doc_sent()):
                logger.info("Google Doc detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.ACTIVE
                message = (
                    "Google Doc successfully set up!\n\n"
                    "Your wallet is now fully initialized and ready to use.\n"
                    "You can now start accepting tasks."
                )
                self.stop_wallet_state_monitoring()
                wx.CallAfter(lambda: wx.MessageBox(message, "Google Doc Ready!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.check_wallet_state())

    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('update_tokens')
    def update_tokens(self):
        logger.debug(f"Fetching token balances for account: {self.wallet.address}")
        try:
            client = xrpl.clients.JsonRpcClient(self.network_url)
            account_lines = xrpl.models.requests.AccountLines(
                account=self.wallet.address,
                ledger_index="validated"
            )
            response = client.request(account_lines)
            logger.debug(f"AccountLines response: {response.result}")

            if not response.is_successful():
                logger.error(f"Error fetching AccountLines: {response}")
                return

            lines = response.result.get('lines', [])
            logger.debug(f"Account lines: {lines}")

            pft_balance = 0.0
            for line in lines:
                logger.debug(f"Processing line: {line}")
                if line['currency'] == 'PFT' and line['account'] == self.pft_issuer:
                    pft_balance = float(line['balance'])
                    logger.debug(f"Found PFT balance: {pft_balance}")

            self.summary_lbl_pft_balance.SetLabel(f"PFT Balance: {pft_balance}")

        except Exception as e:
            logger.exception(f"Exception in update_tokens: {e}")

    def on_close(self, event):
        self.logout()
        if self.perf_monitor:
            self.perf_monitor.stop()
            self.perf_monitor = None
        self.Destroy()

    def _sync_and_refresh(self):
        """Internal method to sync transactions and refresh grids"""
        try:
            self.set_wallet_ui_state(WalletUIState.SYNCING, "Syncing transactions...")
            if self.task_manager.sync_transactions():
                logger.debug("New transactions found, updating grids")
                self.refresh_grids()
            else:
                logger.debug("No new transactions found, skipping grid updates.")
            self.set_wallet_ui_state(WalletUIState.IDLE)
        except Exception as e:
            logger.error(f"Error during sync and refresh cycle: {e}")
            self.set_wallet_ui_state(WalletUIState.IDLE, f"Sync error: {e}")
            raise
    
    def on_force_update(self, _):
        """Handle manual force update requests"""
        logger.info("Manual force update triggered")
        self.btn_force_update.SetLabel("Updating...")
        self.btn_force_update.Update()

        try:
            self._sync_and_refresh()
        except Exception as e:
            logger.error(f"Force update failed: {e}")
            wx.MessageBox(
                "Failed to update wallet data. Please check the console log for more details.",
                "Force Update Error",
                wx.OK | wx.ICON_ERROR
            )
        finally:
            self.btn_force_update.SetLabel("Force Update")
            self.btn_force_update.Update()

    @PerformanceMonitor.measure('refresh_grids')
    def refresh_grids(self, event=None):
        """Update all grids based on wallet state with proper error handling"""
        logger.debug("Starting grid refresh")
        current_state = self.task_manager.wallet_state

        # Summary grid (available in FUNDED_STATES)
        if current_state in FUNDED_STATES:
            try: 
                key_account_details = self.task_manager.process_account_info()
                wx.PostEvent(self, UpdateGridEvent(data=key_account_details, target="summary", caller=f"{self.__class__.__name__}.refresh_grids"))
            except Exception as e:
                logger.error(f"Failed updating summary grid: {e}")

        # Memos, payments grid (available in TRUSTLINED_STATES)
        if current_state in TRUSTLINED_STATES:
            for grid_type, getter_method in [
                ("memos", self.task_manager.get_memos_df),
                ("payments", self.task_manager.get_payments_df)
            ]:
                try:
                    data = getter_method()
                    wx.PostEvent(self, UpdateGridEvent(data=data, target=grid_type, caller=f"{self.__class__.__name__}.refresh_grids"))
                except Exception as e:
                    logger.error(f"Failed updating {grid_type} grid: {e}")
                    logger.error(traceback.format_exc())

        # Proposals, Rewards, and Verification grids (available in PFT_STATES)
        if current_state in ACTIVATED_STATES:
            for grid_type, getter_method in [
                ("proposals", self.task_manager.get_proposals_df),
                ("rewards", self.task_manager.get_rewards_df),
                ("verification", self.task_manager.get_verification_df)
            ]:
                try:
                    data = getter_method()
                    wx.PostEvent(self, UpdateGridEvent(data=data, target=grid_type, caller=f"{self.__class__.__name__}.refresh_grids"))
                except Exception as e:
                    logger.error(f"Failed updating {grid_type} grid: {e}")
                    logger.error(traceback.format_exc())

    @PerformanceMonitor.measure('update_grid')
    def update_grid(self, event):
        """Update a specific grid based on the event target and wallet state"""
        if not hasattr(event, 'target'):
            logger.error(f"No target found in event: {event}")
            return
        
        current_state = self.task_manager.wallet_state

        # Define wallet state requirements for each grid
        grid_state_requirements = {
            'rewards': ACTIVATED_STATES,
            'verification': ACTIVATED_STATES,
            'proposals': ACTIVATED_STATES,
            'memos': TRUSTLINED_STATES,
            'payments': TRUSTLINED_STATES,  # XRP requires FUNDED_STATES, but PFT requires TRUSTLINED_STATES
            'summary': []
        }

        target = event.target
        required_states = grid_state_requirements.get(target, [])

        # Skip grid update if wallet state is not met
        if required_states and current_state not in required_states:
            logger.debug(f"Skipping {target} grid update because wallet state is {current_state}")
            return

        # Handle each grid based on target
        match event.target:
            case "rewards":
                self.populate_grid_generic(self.rewards_grid, event.data, 'rewards')
            case "verification":
                self.populate_grid_generic(self.verification_grid, event.data, 'verification')
            case "proposals":
                self.populate_grid_generic(self.proposals_grid, event.data, 'proposals')
            case "payments":
                self.populate_grid_generic(self.payments_grid, event.data, 'payments')
            case "memos":
                self.populate_grid_generic(self.memos_grid, event.data, 'memos')
            case "summary":
                self.populate_summary_grid(event.data)
            case _:
                logger.error(f"Unknown grid target: {event.target}")

        # self.auto_size_window()

    @PerformanceMonitor.measure('populate_grid_generic')
    def populate_grid_generic(self, grid: wx.grid.Grid, data: pd.DataFrame, grid_name: str):
        """Generic grid population method that respects zoom settings"""

        if data.empty:
            logger.debug(f"No data to populate {grid_name} grid")
            grid.ClearGrid()
            return
        
        # Store all values from the selected row if there is one
        had_selection = grid.GetSelectedRows()
        selected_row_values = None
        if had_selection:
            selected_row_values = [
                grid.GetCellValue(had_selection[0], col) 
                for col in range(grid.GetNumberCols())
            ]

        if grid.GetNumberRows() > 0:
            grid.DeleteRows(0, grid.GetNumberRows())

        # Store original column sizes if not already stored
        if grid_name not in self.grid_column_widths:
            self.grid_column_widths[grid_name] = [grid.GetColSize(col) for col in range(grid.GetNumberCols())]

        # Add new rows
        grid.AppendRows(len(data))

        # Get the column configuration for this grid
        columns = self.GRID_CONFIGS.get(grid_name, {}).get('columns', [])
        if not columns:
            logger.error(f"No column configuration found for {grid_name}")
            return
        
        # Get the column configuration for this grid
        columns = self.GRID_CONFIGS.get(grid_name, {}).get('columns', [])
        if not columns:
            logger.error(f"No column configuration found for {grid_name}")
            return

        # Populate data using the column mapping
        for idx in range(len(data)):
            for col, (col_id, _, _) in enumerate(columns):
                if col_id in data.columns:
                    value = data.iloc[idx][col_id]
                    grid.SetCellValue(idx, col, str(value))
                    grid.SetCellRenderer(idx, col, gridlib.GridCellAutoWrapStringRenderer())
                else:
                    logger.error(f"Column {col_id} not found in data for {grid_name}")

        # Let wxPython handle initial row sizing
        grid.AutoSizeRows()

        # Store the auto-sized row heights with an additional margin
        self.grid_row_heights[grid_name] = [
            grid.GetRowSize(row) + self.row_height_margin 
            for row in range(grid.GetNumberRows())
            ]
        
        # Apply the stored row heights and column widths with the zoom factor
        for row in range(grid.GetNumberRows()):
            grid.SetRowSize(row, int(self.grid_row_heights[grid_name][row] * self.zoom_factor))

        column_zoom_factor = 1.0 + ((self.zoom_factor - 1.0) * 0.3)  # 30% of the regular zoom effect
        for col, original_width in enumerate(self.grid_column_widths[grid_name]):
            grid.SetColSize(col, int(original_width * column_zoom_factor))

        # self.auto_size_window()

        # Restore selection if there was one
        if selected_row_values:
            # Find the row with matching values
            for row in range(grid.GetNumberRows()):
                current_row_values = [
                    grid.GetCellValue(row, col) 
                    for col in range(grid.GetNumberCols())
                ]
                if current_row_values == selected_row_values:
                    grid.SelectRow(row)
                    break
        else:
            grid.ClearSelection()  # Only clear if there wasn't a previous selection

        grid.Refresh()

        # Restore selection if there was one
        if selected_row_values:
            # Find the row with matching values
            for row in range(grid.GetNumberRows()):
                current_row_values = [
                    grid.GetCellValue(row, col) 
                    for col in range(grid.GetNumberCols())
                ]
                if current_row_values == selected_row_values:
                    grid.SelectRow(row)
                    break
        else:
            grid.ClearSelection()  # Only clear if there wasn't a previous selection

        grid.Refresh()

    @PerformanceMonitor.measure('populate_summary_grid')
    def populate_summary_grid(self, key_account_details):
        """Convert dictionary to dataframe and use generic grid population method"""
        summary_df = pd.DataFrame(list(key_account_details.items()), columns=['Key', 'Value'])
        self.populate_grid_generic(self.summary_grid, summary_df, 'summary')

    def auto_size_window(self):
        """Adjust window size while maintaining reasonable dimensions"""
        self.rewards_tab.Layout()
        self.tabs.Layout()
        self.panel.Layout()

        # Simply use default size with some flexibility for width
        current_size = self.GetSize()
        new_width = min(max(current_size.width, self.default_size[0]), self.max_size[0])
        new_size = (new_width, self.default_size[1])
        
        self.SetSize(new_size)

    def on_key_down(self, event):
        if event.GetKeyCode() == wx.WXK_CONTROL:
            current_time = time.time()
            if not self.ctrl_pressed and (current_time - self.last_ctrl_press_time > self.ctrl_toggle_delay):
                self.ctrl_pressed = True
                self.last_ctrl_press_time = current_time
        event.Skip()

    def on_key_up(self, event):
        if event.GetKeyCode() == wx.WXK_CONTROL:
            self.ctrl_pressed = False
        event.Skip()

    def on_mouse_wheel_zoom(self, event):
        if self.ctrl_pressed:
            if event.GetWheelRotation() > 0:
                self.zoom_factor *= 1.01
            else:
                self.zoom_factor /= 1.01
            self.zoom_factor = max(0.75, min(self.zoom_factor, 2.0))
            self.apply_zoom()
        else:
            event.Skip()

    def store_grid_dimensions(self, grid, grid_name):
        if grid_name not in self.grid_column_widths:
            self.grid_column_widths[grid_name] = [grid.GetColSize(col) for col in range(grid.GetNumberCols())]
        if grid_name not in self.grid_row_heights:
            self.grid_row_heights[grid_name] = [grid.GetRowSize(row) for row in range(grid.GetNumberRows())]

    def apply_zoom(self):
        base_font_size = 10
        new_font_size = int(base_font_size * self.zoom_factor)

        font = wx.Font(new_font_size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

        def set_font_recursive(window):
            window.SetFont(font)
            if isinstance(window, wx.grid.Grid):
                window.SetDefaultCellFont(font)

                # Let wxPython handle initial row sizes based on new font
                window.AutoSizeRows()

                # Apply margin and zoom to the auto-sized rows
                for row in range(window.GetNumberRows()):
                    current_height = window.GetRowSize(row)
                    window.SetRowSize(row, int((current_height + self.row_height_margin) * self.zoom_factor))

                grid_name = None
                match window:
                    case self.proposals_grid:
                        grid_name = "proposals"
                    case self.rewards_grid:
                        grid_name = "rewards"
                    case self.verification_grid:
                        grid_name = "verification"
                    case self.summary_grid:
                        grid_name = "summary"
                    case self.payments_grid:
                        grid_name = "payments"
                    case self.memos_grid:
                        grid_name = "memos"
                    case _:
                        grid_name = None
                        logger.error(f"No grid name found for {window}")

                if grid_name and grid_name in self.grid_column_widths:
                    self.store_grid_dimensions(window, grid_name)
                    column_zoom_factor = 1.0 + ((self.zoom_factor - 1.0) * 0.3)  # 30% of the regular zoom effect
                    for col, original_size in enumerate(self.grid_column_widths[grid_name]):
                        window.SetColSize(col, int(original_size * column_zoom_factor))

            for child in window.GetChildren():
                set_font_recursive(child)

        set_font_recursive(self)

        # Refresh layout
        self.panel.Layout()
        self.tabs.Layout()
        for i in range(self.tabs.GetPageCount()):
            self.tabs.GetPage(i).Layout()

        # self.auto_size_window()

    def on_tab_changed(self, event):
        # self.auto_size_window()  # NOTE: Users complained about this, so it's disabled for now. Consider deprecating.
        event.Skip()
        
    def on_proposal_selection(self, event):
        """Handle proposal grid selection"""
        row = event.GetRow()

        # Get task ID from selected row
        task_id = self.proposals_grid.GetCellValue(row, 0)  # First column is task ID

        if not task_id:
            logger.debug("No task ID found in selected row")
            event.Skip()
            return

        logger.debug(f"Selected task ID: {task_id}")

        # Update button states based on task state
        self.update_proposal_buttons(task_id)

        self.proposals_grid.Refresh()
        event.Skip()

    def update_proposal_buttons(self, task_id):
        """Enable/disable buttons based on task state"""
        try:
            task_df = self.task_manager.get_task(task_id)
            latest_state = self.task_manager.get_task_state(task_df)

            # Enable/disable buttons based on task state
            self.btn_accept_task.Enable(latest_state == constants.TaskType.PROPOSAL.name)
            self.btn_refuse_task.Enable(latest_state != constants.TaskType.REFUSAL.name)
            self.btn_submit_for_verification.Enable(latest_state == constants.TaskType.ACCEPTANCE.name)

        except Exception as e:
            logger.error(f"Error updating proposal buttons: {e}")
            self.btn_accept_task.Enable(False)
            self.btn_refuse_task.Enable(False)
            self.btn_submit_for_verification.Enable(False)

    def get_selected_task_id(self):
        """Get task ID from selected row in proposals grid"""
        selected_rows = self.proposals_grid.GetSelectedRows()
        if not selected_rows:
            wx.MessageBox("Please select a task first", "No Task Selected", wx.OK | wx.ICON_WARNING)
            return None
        
        return self.proposals_grid.GetCellValue(selected_rows[0], 0)  # First column is task ID

    def on_request_task(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Requesting Task...")
        self.btn_request_task.SetLabel("Requesting Task...")
        self.btn_request_task.Update()

        dialog = CustomDialog(self, "Request Task", ["Task Request"])
        if dialog.ShowModal() == wx.ID_OK:
            request_message = dialog.GetValues()["Task Request"]
            try:
                response = self.task_manager.request_post_fiat(request_message=request_message)
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Task Request Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except Exception as e:
                logger.error(f"Error requesting task: {e}")
                wx.MessageBox(f"Error requesting task: {e}", 'Task Request Error', wx.OK | wx.ICON_ERROR)
        dialog.Destroy()

        self.btn_request_task.SetLabel("Request Task")
        self.btn_request_task.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_accept_task(self, event):
        task_id = self.get_selected_task_id()
        if not task_id:
            return

        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Accepting Task...")
        self.btn_accept_task.SetLabel("Accepting Task...")
        self.btn_accept_task.Update()

        dialog = CustomDialog(
            self, 
            "Accept Task", 
            ["Task ID", "Acceptance String"],
            placeholders={"Acceptance String": "I accept!"},
            readonly_values={"Task ID": task_id}
        )
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            acceptance_string = values["Acceptance String"]
            try:
                response = self.task_manager.send_acceptance_for_task_id(
                    task_id=task_id,
                    acceptance_string=acceptance_string
                )
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Task Acceptance Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except NoMatchingTaskException as e:
                logger.error(f"Error accepting task: {e}")
                wx.MessageBox(f"Couldn't find task with task ID {task_id}. Did you enter it correctly?", 'Task Acceptance Error', wx.OK | wx.ICON_ERROR)
            except WrongTaskStateException as e:
                logger.error(f"Error accepting task: {e}")
                wx.MessageBox(f"Task ID {task_id} is not in the correct state to be accepted. Current status: {e}", 'Task Acceptance Error', wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error accepting task: {e}")
                wx.MessageBox(f"Error accepting task: {e}", 'Task Acceptance Error', wx.OK | wx.ICON_ERROR)
            # else:
            #     wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)
        dialog.Destroy()

        self.btn_accept_task.SetLabel("Accept Task")
        self.btn_accept_task.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_refuse_task(self, event):
        task_id = self.get_selected_task_id()
        if not task_id:
            return
        
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Refusing Task...")
        self.btn_refuse_task.SetLabel("Refusing Task...")
        self.btn_refuse_task.Update()

        dialog = CustomDialog(
            self, 
            "Refuse Task", 
            ["Task ID", "Refusal Reason"],
            placeholders={"Refusal Reason": "I refuse because of ..."},
            readonly_values={"Task ID": task_id}
        )
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            refusal_reason = values["Refusal Reason"]
            try:
                response = self.task_manager.send_refusal_for_task(
                    task_id=task_id,
                    refusal_reason=refusal_reason
                )
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Task Refusal Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except NoMatchingTaskException as e:
                logger.error(f"Error refusing task: {e}")
                wx.MessageBox(f"Couldn't find task with task ID {task_id}. Did you enter it correctly?", 'Task Refusal Error', wx.OK | wx.ICON_ERROR)
            except WrongTaskStateException as e:
                logger.error(f"Error refusing task: {e}")
                wx.MessageBox(f"Task ID {task_id} is not in the correct state to be refused. Current status: {e}", 'Task Refusal Error', wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error refusing task: {e}")
                wx.MessageBox(f"Error refusing task: {e}", 'Task Refusal Error', wx.OK | wx.ICON_ERROR)
            # else:
            #     wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)

        dialog.Destroy()
        self.btn_refuse_task.SetLabel("Refuse Task")
        self.btn_refuse_task.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_submit_for_verification(self, event):
        task_id = self.get_selected_task_id()
        if not task_id:
            return

        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting for Verification...")
        self.btn_submit_for_verification.SetLabel("Submitting for Verification...")
        self.btn_submit_for_verification.Update()

        dialog = CustomDialog(
            self, 
            "Submit for Verification", 
            ["Task ID", "Completion String"],
            placeholders={"Completion String": "Place something like 'I completed the task!' here"},
            readonly_values={"Task ID": task_id}
        )
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            completion_string = values["Completion String"]
            try:
                response = self.task_manager.submit_initial_completion(
                    completion_string=completion_string,
                    task_id=task_id
                )
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Task Submission Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except NoMatchingTaskException as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Couldn't find task with task ID {task_id}. Did you enter it correctly?", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            except WrongTaskStateException as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Task ID {task_id} has not yet been accepted. Current status: {e}", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Error submitting initial completion: {e}", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            # else:
            #     wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)
        dialog.Destroy()

        self.btn_submit_for_verification.SetLabel("Submit for Verification")
        self.btn_submit_for_verification.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_verification_selection(self, event):
        """Handle verification grid selection"""
        row = event.GetRow()

        # Get task ID from selected row
        task_id = self.verification_grid.GetCellValue(row, 0)

        if not task_id:
            logger.debug("No task ID found in selected row")
            event.Skip()
            return

        logger.debug(f"Selected task ID: {task_id}")

        self.verification_txt_task_id.SetLabel(task_id)

        self.verification_grid.Refresh()
        event.Skip()

    def on_refuse_verification(self, event):
        """Handle refusal of verification"""
        task_id = self.verification_txt_task_id.GetLabel()
        if not task_id:
            wx.MessageBox("Please select a task first", "No Task Selected", wx.OK | wx.ICON_WARNING)
            return
        
        dialog = CustomDialog(
            self,
            "Refuse Task",
            ["Task ID", "Refusal Reason"],
            placeholders={"Refusal Reason": "I refuse because of ..."},
            readonly_values={"Task ID": task_id}
        )

        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            try:
                response = self.task_manager.send_refusal_for_task(
                    task_id=values["Task ID"],
                    refusal_reason=values["Refusal Reason"]
                )
                formatted_response = self.format_response(response)
                result_dialog = SelectableMessageDialog(self, "Task Refusal Result", formatted_response)
                result_dialog.ShowModal()
                result_dialog.Destroy()
                # wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)
            except Exception as e:
                wx.MessageBox(f"Error refusing task: {e}", "Error", wx.OK | wx.ICON_ERROR)

        dialog.Destroy()  

    def on_submit_verification_details(self, event):
        """Handle submission of verification details"""
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting Verification Details...")
        self.btn_submit_verification_details.SetLabel("Submitting Verification Details...")
        self.btn_submit_verification_details.Update()

        task_id = self.verification_txt_task_id.GetLabel()
        response_string = self.verification_txt_details.GetValue()

        if not task_id or not response_string:
            wx.MessageBox("Please enter verification details", "Error", wx.OK | wx.ICON_ERROR)
        else:
            try:
                response = self.task_manager.send_verification_response(
                    response_string=response_string,
                    task_id=task_id
                )
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Verification Submission Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except NoMatchingTaskException as e:
                logger.error(f"Error sending verification response: {e}")
                wx.MessageBox(f"Couldn't find task with task ID {task_id}. Did you enter it correctly?", 'Verification Submission Error', wx.OK | wx.ICON_ERROR)
            except WrongTaskStateException as e:
                logger.error(f"Error sending verification response: {e}")
                wx.MessageBox(f"Task ID {task_id} is not in the correct state for verification. Current status: {e}", 'Verification Submission Error', wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error sending verification response: {e}")
                wx.MessageBox(f"Error sending verification response: {e}", 'Verification Submission Error', wx.OK | wx.ICON_ERROR)
            else:
                self.verification_txt_details.SetValue("")
                self.verification_txt_task_id.SetLabel("")
                self.btn_submit_verification_details.SetLabel("Submit Verification Details")
                self.btn_submit_verification_details.Update()
                # wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)

        self.btn_submit_verification_details.SetLabel("Submit Verification Details")
        self.btn_submit_verification_details.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_log_pomodoro(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Logging Pomodoro...")
        self.btn_log_pomodoro.SetLabel("Logging Pomodoro...")
        self.btn_log_pomodoro.Update()

        task_id = self.verification_txt_task_id.GetValue()
        pomodoro_text = self.verification_txt_details.GetValue()

        if not task_id or not pomodoro_text:
            wx.MessageBox("Please enter a task ID and pomodoro text", "Error", wx.OK | wx.ICON_ERROR)
        else:
            try:
                response = self.task_manager.send_pomodoro_for_task_id(task_id=task_id, pomodoro_text=pomodoro_text)
                formatted_response = self.format_response(response)
                dialog = SelectableMessageDialog(self, "Pomodoro Log Result", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()
            except Exception as e:
                logger.error(f"Error logging pomodoro: {e}")
                wx.MessageBox(f"Error logging pomodoro: {e}", 'Pomodoro Log Error', wx.OK | wx.ICON_ERROR)
            else:
                self.verification_txt_details.SetValue("")

        self.btn_log_pomodoro.SetLabel("Log Pomodoro")
        self.btn_log_pomodoro.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def validate_address(self, address: str) -> Optional[str]:
        """Validate and clean up the XRP address"""
        # Match an XRP address: r followed by 24-34 alphanumeric characters
        xrp_match = re.search(r'r[a-zA-Z0-9]{24,34}', address)
        if xrp_match:
            return xrp_match.group()
        else:
            raise ValueError(f"Invalid XRP address: {address}")

    def on_submit_memo(self, event):
        """Submits a memo."""
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting Memo...")
        self.btn_submit_memo.SetLabel("Submitting...")
        self.btn_submit_memo.Update()
        logger.info("Submitting Memo")

        memo_text = self.txt_memo_input.GetValue()

        # Get selected recipient data
        recipient_idx = self.memo_recipient.GetSelection()
        logger.debug(f"Recipient index: {recipient_idx}")
        if recipient_idx != wx.NOT_FOUND:
            recipient = self.memo_recipient.GetClientData(recipient_idx)
            logger.debug(f"Getting client data for recipient index {recipient_idx}: recipient {recipient}")
        else:
            recipient = self.memo_recipient.GetValue()
            logger.debug(f"Recipient index not found. Using value: {recipient}")

        try:
            recipient = self.validate_address(recipient)
        except ValueError as e:
            logger.error(f"Error validating recipient: {e}")
            wx.MessageBox(f"Recipient address is invalid: {e}", "Error", wx.OK | wx.ICON_ERROR)
            self.btn_submit_memo.SetLabel("Submit Memo")
            self.set_wallet_ui_state(WalletUIState.IDLE)
            return

        encrypt = self.memo_chk_encrypt.IsChecked()

        if not memo_text or not recipient:
            wx.MessageBox("Please enter a memo and recipient", "Error", wx.OK | wx.ICON_ERROR)
            self.btn_submit_memo.SetLabel("Submit Memo")
            self.set_wallet_ui_state(WalletUIState.IDLE)
            return
        
        logger.debug(f"Preparing memo (encrypt={encrypt})")

        try:
            # First check if encryption is possible if requested
            if encrypt:
                logger.debug(f"Checking handshake for {recipient}")
                handshake_sent, received_key = self.task_manager.get_handshake_for_address(recipient)
                logger.debug(f"Handshake sent: {handshake_sent}, received key: {received_key}")
                if not received_key:
                    logger.debug(f"No received key for {recipient}")
                    if not handshake_sent:
                        logger.debug(f"Handshake not sent for {recipient}")
                        if wx.YES == wx.MessageBox(
                            "Encryption requires a handshake exchange. Would you like to send a handshake now?",
                            "Handshake Required",
                            wx.YES_NO | wx.ICON_QUESTION
                        ):
                            logger.debug(f"Sending handshake to {recipient}")
                            response = self.task_manager.send_handshake(recipient)
                            formatted_response = self.format_response(response)
                            dialog = SelectableMessageDialog(self, "Handshake Submission Result", formatted_response)
                            dialog.ShowModal()
                            dialog.Destroy()
                            wx.MessageBox(
                                "Handshake sent. You'll need to wait for the recipient to send their handshake "
                                "before you can send encrypted messages.\n\nWould you like to send this message "
                                "unencrypted instead?",
                                "Handshake Sent",
                                wx.YES_NO | wx.ICON_INFORMATION
                            )
                            # self._sync_and_refresh()
                        self.btn_submit_memo.SetLabel("Submit Memo")
                        self.set_wallet_ui_state(WalletUIState.IDLE)
                        return
                    else:
                        if wx.NO == wx.MessageBox(
                            "Still waiting for recipient's handshake. Would you like to send "
                            "this message unencrypted instead?",
                            "Handshake Pending",
                            wx.YES_NO | wx.ICON_QUESTION
                        ):
                            self.btn_submit_memo.SetLabel("Submit Memo")
                            self.set_wallet_ui_state(WalletUIState.IDLE)
                            return
                        encrypt = False

            # Estimate chunks needed
            test_memo = memo_text 
            if encrypt:
                # Add encryption overhead to size estimate
                test_memo = self.task_manager.encrypt_memo(test_memo, received_key)

            # Create test Memo object
            compressed_memo = construct_memo(
                memo_format=self.task_manager.credential_manager.postfiat_username,
                memo_type=self.task_manager.generate_custom_id(),
                memo_data=compress_string(test_memo)
            )

            # Calculate chunks needed
            num_chunks = self.task_manager.calculate_required_chunks(compressed_memo)

            message = (
                f"Memo will be {'encrypted, ' if encrypt else ''}compressed and sent over {num_chunks} transaction(s) and "
                f"cost 1 PFT per chunk ({num_chunks} PFT + {num_chunks * constants.MIN_XRP_PER_TRANSACTION} XRP total).\n\n"
                f"Destination XRP Address: {recipient}\n"
                f"Memo: {memo_text[:20]}{'...' if len(memo_text) > 20 else ''}\n\n"
                f"Continue?"
            )
            if wx.NO == wx.MessageBox(message, "Confirmation", wx.YES_NO | wx.ICON_QUESTION):
                self.btn_submit_memo.SetLabel("Submit Memo")
                return
            
            # Send the memo
            responses = self.task_manager.send_memo(recipient, memo_text, chunk=True, encrypt=encrypt)
            
            formatted_responses = [self.format_response(response) for response in responses]
            logger.info(f"Memo Submission Result: {formatted_responses}")

            for idx, formatted_response in enumerate(formatted_responses):
                if idx == 0:
                    dialog = SelectableMessageDialog(self, f"Memo Submission Result", formatted_response)
                else:
                    dialog = SelectableMessageDialog(self, f"Memo Submission Result {idx + 1}", formatted_response)
                dialog.ShowModal()
                dialog.Destroy()

        except Exception as e:
            logger.error(f"Error submitting memo: {e}")
            wx.MessageBox(f"Error submitting memo: {e}", "Error", wx.OK | wx.ICON_ERROR)
        else:
            self.txt_memo_input.SetValue("")
            # wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)

        self.btn_submit_memo.SetLabel("Submit Memo")
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_update_google_doc(self, event):
        """Handle updating Google Doc link"""
        if self.show_google_doc_template(is_initial_setup=False):
            self.handle_google_doc_submission(event)

    def show_google_doc_template(self, is_initial_setup: bool = False):
        """Show the template and instructions for Google Doc Setup"""
        header = "Setup Google Doc" if is_initial_setup else "Update Google Doc"
        template_text = (
            "___x TASK VERIFICATION SECTION START x___\n \n"
            "___x TASK VERIFICATION SECTION END x___\n"
        )
        
        message = (
            f"{'To continue with wallet initialization,' if is_initial_setup else 'To update your Google Doc,'}\n"
            "you need to provide a Google Doc link.\n\n"
            "This document should:\n"
            "- Be viewable by anyone who has the link\n"
            "- Include a task verification section\n\n"
            "Copy and paste the text below into your Google Doc:\n\n"
            f"\n{template_text}\n\n"
            "When you're ready, click OK to proceed."
        )

        template_dialog = SelectableMessageDialog(self, header, message)
        result = template_dialog.ShowModal()
        template_dialog.Destroy()
        return result == wx.ID_OK

    def handle_google_doc_submission(self, event, is_initial_setup: bool = False):
        """Common handler for Google Doc submission/updates"""
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Updating Google Doc Link...")
        dialog = GoogleDocSetupDialog(self, is_initial_setup=is_initial_setup)
        while True:
            if dialog.ShowModal() == wx.ID_OK:
                google_doc_link = dialog.get_link()
                try:
                    # Validate and send the new Google Doc link
                    response = self.task_manager.handle_google_doc_setup(google_doc_link)
                    if response.is_successful():
                        formatted_response = self.format_response(response)
                        success_dialog = SelectableMessageDialog(self, "Success", formatted_response)
                        success_dialog.ShowModal()
                        success_dialog.Destroy()
                        break
                except Exception as e:
                    logger.error(f"Error updating Google Doc link: {e}")
                    logger.error(traceback.format_exc())
                    dialog.show_error(str(e))
                    continue
                # else:
                #     wx.CallLater(constants.REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self._sync_and_refresh)
            else:
                break
        dialog.Destroy()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_show_secret(self, event):
        dialog = wx.PasswordEntryDialog(self, "Enter Password", "Please enter your password to view your secret.")

        if dialog.ShowModal() == wx.ID_OK:
            password = dialog.GetValue()
            dialog.Destroy()

            try: 
                if self.task_manager.verify_password(password):
                    seed = self.wallet.seed
                    message = (
                        "WARNING: NEVER share this with anyone!\n\n"
                        f"Secret: {seed}"
                    )
                    seed_dialog = SelectableMessageDialog(self, "Wallet Secret", message)
                    seed_dialog.ShowModal()
                    seed_dialog.Destroy()
                else:
                    wx.MessageBox("Incorrect password", "Error", wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error showing secret: {e}")
                wx.MessageBox(f"Error showing secret: {e}", "Error", wx.OK | wx.ICON_ERROR)
        
        else:
            dialog.Destroy()

    def on_change_password(self, event):
        """Handle password change request"""
        dialog = ChangePasswordDialog(self)
        while True:  # Keep dialog open until success or cancel
            if dialog.ShowModal() == wx.ID_OK:
                try:
                    current_password = dialog.current_password.GetValue()
                    new_password = dialog.new_password.GetValue()
                    confirm_password = dialog.confirm_password.GetValue()

                    # Verify current password
                    if not self.task_manager.verify_password(current_password):
                        raise ValueError("Incorrect password")

                    # Validate new password
                    if not CredentialManager.is_valid_password(new_password):
                        raise ValueError("Invalid password. Must be at least 8 characters long and contain only letters, numbers, or basic symbols")
                        
                    # Check if passwords match
                    if new_password != confirm_password:
                        raise ValueError("New passwords do not match")
                        
                    # Attempt password change
                    success = self.task_manager.change_password(new_password)
                    
                    if success:
                        wx.MessageBox(
                            "Password changed successfully!", 
                            "Success",
                            wx.OK | wx.ICON_INFORMATION
                        )
                        break
                        
                except ValueError as e:
                    wx.MessageBox(
                        str(e),
                        "Error",
                        wx.OK | wx.ICON_ERROR
                    )
                except Exception as e:
                    wx.MessageBox(
                        f"An error occurred: {e}",
                        "Error",
                        wx.OK | wx.ICON_ERROR
                    )
            else: 
                break 
        dialog.Destroy()

    def on_update_trustline(self, event):
        """Handle update trustline request"""
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Updating Trust Line Limit...")
        dialog = UpdateTrustlineDialog(self)

        while True:
            if dialog.ShowModal() == wx.ID_OK:
                new_limit = dialog.get_new_limit()
                try:
                    # Validate and update the trust line limit
                    response = self.task_manager.update_trust_line_limit(new_limit)
                    if response.is_successful():
                        formatted_response = self.format_response(response)
                        success_dialog = SelectableMessageDialog(self, "Success", formatted_response)
                        success_dialog.ShowModal()
                        success_dialog.Destroy()
                        break
                except Exception as e:
                    logger.error(f"Error updating trust line limit: {e}")
                    logger.error(traceback.format_exc())
                    dialog.show_error(str(e))
                    continue
            else:
                break
                
        dialog.Destroy()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_delete_credentials(self, event):
        """Handle delete credentials request"""
        logger.info("Credentials deletion requested")
        
        dialog = wx.PasswordEntryDialog(self, "Enter Password", "Please enter your password to delete your account.")

        if dialog.ShowModal() == wx.ID_OK:
            password = dialog.GetValue()
            dialog.Destroy()

            if self.task_manager.verify_password(password):
                delete_dialog = DeleteCredentialsDialog(self)
                if delete_dialog.ShowModal() == wx.ID_OK:
                    try:
                        # Attempt to delete account
                        self.task_manager.credential_manager.delete_credentials()

                        logger.info("Account deleted successfully")
                        wx.MessageBox("Your credentials have been deleted.\nYou will now be logged out.", 
                                      "Account Deleted", 
                                      wx.OK | wx.ICON_INFORMATION)
                        
                        delete_dialog.Destroy()
                        self.logout()

                    except Exception as e:
                        logger.error(f"Error deleting credentials: {e}")
                        wx.MessageBox(f"Error deleting credentials: {e}", "Error", wx.OK | wx.ICON_ERROR)
                else:
                    delete_dialog.Destroy()
            else:
                wx.MessageBox("Incorrect password", "Error", wx.OK | wx.ICON_ERROR)

        else:
            dialog.Destroy()

    def format_response(self, response):
        if isinstance(response, list):
            response = response[0]  # Take the first transaction if its a list

        if hasattr(response, 'status') and response.status == "success":
            tx_json = response.result.get('tx_json', {})
            meta = response.result.get('meta', {})
            hash = response.result.get('hash', 'N/A')
            livenet_link = self.task_manager.get_explorer_transaction_url(hash)

            # Determine the currency and amount
            deliver_max = tx_json.get('DeliverMax', '0')
            if isinstance(deliver_max, dict):
                currency = deliver_max.get('currency', 'N/A')
                amount = deliver_max.get('value', '0')
            else:
                currency = 'XRP'
                amount = xrpl.utils.drops_to_xrp(deliver_max or '0')
            
            formatted_response = (
                f"Transaction Status: Success\n"
                f"Transaction Type: {tx_json.get('TransactionType', 'N/A')}\n"
                f"From: {tx_json.get('Account', 'N/A')}\n"
                f"To: {tx_json.get('Destination', 'N/A')}\n"
                f"Amount: {amount} {currency}\n"
                f"Fee: {xrpl.utils.drops_to_xrp(tx_json.get('Fee', '0'))} XRP\n"
                f"Ledger Index: {response.result.get('ledger_index', 'N/A')}\n"
                f"Transaction Hash: {response.result.get('hash', 'N/A')}\n"
                f"Date: {response.result.get('date', 'N/A')}\n"
                f"See transaction details at: <a href='{livenet_link}'>{livenet_link}</a>\n\n"
            )

            logger.debug(f"Formatted Response: {formatted_response}")

            # Add memo if present
            if tx_json.get('Memos'):
                memo_data = tx_json['Memos'][0]['Memo'].get('MemoData', '')
                decoded_memo = bytes.fromhex(memo_data).decode('utf-8', errors='ignore')
                formatted_response += f"Memo: {decoded_memo}\n"

            # Add transaction result
            if meta:
                formatted_response += f"Transaction Result: {meta.get('TransactionResult', 'N/A')}\n"

            return formatted_response
        
        elif hasattr(self, 'wallet.classic_address'):
            livenet_link = self.task_manager.get_explorer_account_url(self.wallet.address)

            formatted_response = (
                f"Transaction Failed\n"
                f"Error: {response}\n"
                f"Check details at: <a href='{livenet_link}'>{livenet_link}</a>\n\n"
            )
            
            return formatted_response
        
        else:
            formatted_response = f"Transaction Failed\nError: {response}"
            return formatted_response
        
    def darken_color(self, color, factor=0.95):
        """Darkens a wx.Colour object by a given factor (0.0 to 1.0)"""
        return wx.Colour(
            int(color.Red() * factor), 
            int(color.Green() * factor), 
            int(color.Blue() * factor),
            color.Alpha()
        )
    
    def on_migrate_credentials(self, event):
        """Handle migration of old credentials"""
        check_and_show_migration_dialog(parent=self, force=True)
    
    def launch_perf_monitor(self, event=None):
        """Toggle the performance monitor on and off"""
        if self.perf_monitor is None:
            self.perf_monitor = PerformanceMonitor(
                output_dir=Path.cwd() / "pftpyclient" / "logs"
            )
            PerformanceMonitor._instance = self.perf_monitor

            def monitor_thread():
                self.perf_monitor.start()
                while not self.perf_monitor.shutdown_event.is_set():
                    time.sleep(1)
                self.perf_monitor.stop()
                self.perf_monitor = None
                PerformanceMonitor._instance = None
        
            Thread(target=monitor_thread, daemon=True).start()

    def on_check_for_updates(self, event):
        """Handle check for updates request"""
        check_and_show_update_dialog(parent=self)

    def on_preferences(self, event):
        """Handle preferences dialog"""
        dialog = PreferencesDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            # Check if performance monitor setting changed
            if self.config.get_global_config('performance_monitor'):
                self.launch_perf_monitor(None)
            else:
                if self.perf_monitor:
                    self.perf_monitor.shutdown_event.set()
        dialog.Destroy()

    def set_wallet_ui_state(self, state: WalletUIState=None, message: str = ""):
        """Update the status bar with current wallet state"""
        if state:
            self.current_ui_state = state
        status_text = message or f"Wallet state: {self.current_ui_state.name.lower()}"
        self.status_bar.SetStatusText(status_text, 0)
        self.status_bar.SetStatusText(self.current_ui_state.name, 1)

    def is_wallet_busy(self):
        """Check if wallet is in a busy state"""
        return hasattr(self, 'current_ui_state') and self.current_ui_state != WalletUIState.IDLE
    
    def on_logout(self, event):
        """Handle logout request"""
        if self.is_wallet_busy():
            wx.MessageBox("Please wait for the current operation to complete before logging out.", "Wallet Busy", wx.OK | wx.ICON_WARNING)
            return

        if wx.YES == wx.MessageBox("Are you sure you want to logout?", "Confirm Logout", wx.YES_NO | wx.ICON_QUESTION):
            self.logout()

    def logout(self):
        """Perform logout operations and reset UI"""
        try:

            # Stop background processes
            if hasattr(self, 'worker') and self.worker:
                self.worker.stop()
                # Wait for thread to complete (with timeout)
                self.worker.join(timeout=2)
                if self.worker.is_alive():
                    logger.error("Worker thread did not stop gracefully")
                self.worker = None

            self.stop_wallet_state_monitoring()

            # Clear sensitive data
            if hasattr(self, 'task_manager'):
                logger.debug("Clearing credentials")
                self.task_manager.credential_manager.clear_credentials()
                self.task_manager = None

            if hasattr(self, 'wallet'):
                logger.debug("Clearing wallet")
                self.wallet = None

            # Clear grids
            logger.debug("Clearing grids")
            for grid_name in self.GRID_CONFIGS:
                grid = getattr(self, f"{grid_name}_grid", None)
                if grid and grid.GetNumberRows() > 0:
                    grid.DeleteRows(0, grid.GetNumberRows())

            # Clear miscellaneous text fields
            self.txt_memo_input.SetValue("")
            self.verification_txt_details.SetValue("")

            self.tabs.Hide()

            # Reset menu state
            self.menubar.EnableTop(self.menubar.FindMenu("Account"), False)

            logger.debug("Logging out...")

            # Show login panel
            self.btn_login.SetLabel("Login")
            self.btn_login.Update()
            self.login_panel.Show()
            self.login_txt_password.SetValue("")            
            self.login_txt_password.Update()

            # Reset status bar
            self.set_wallet_ui_state(WalletUIState.IDLE, "Logged out")

        except Exception as e:
            logger.error(f"Error during logout: {e}")
            wx.MessageBox(f"Error during logout: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def on_manage_contacts(self, event):
        """Handle manage contacts request"""
        dialog = ContactsDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            self.refresh_grids()
            self.update_all_destination_comboboxes()
        dialog.Destroy()

    def show_payment_confirmation(self, amount, destination, token_type):
        """Show payment confirmation dialog"""
        dialog = ConfirmPaymentDialog(self, amount, destination, token_type)
        result = dialog.ShowModal()

        if result == wx.ID_OK:
            # Save contact if requested
            contact_name = dialog.get_contact_info()
            if contact_name:
                self.task_manager.save_contact(destination, contact_name)
                self.update_all_destination_comboboxes()
        dialog.Destroy()
        return result == wx.ID_OK
    
    def on_encryption_requests(self, event):
        """Show the encryption requests dialog"""
        dialog = EncryptionRequestsDialog(self)
        dialog.ShowModal()
        dialog.Destroy()

    def try_connect_endpoint(self, endpoint: str) -> bool:
        """
        Attempt to connect to a new RPC endpoint with timeout.
        
        Args:
            endpoint: The RPC endpoint URL to test
            timeout: Maximum time to wait for connection in seconds
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Create a JsonRpcClient for the endpoint
            logger.debug(f"Attempting to connect to {endpoint}")
            client = xrpl.clients.JsonRpcClient(endpoint)
            
            # Try to get server info as a connection test
            response = client.request(xrpl.models.requests.ServerInfo())
            
            success = response.is_successful()
            message = f"Successful connection to {endpoint}" if success else f"Failed connection to {endpoint}: {response}"
            logger.debug(message)

            return success

        except Exception as e:
            logger.error(f"Connection test failed for {endpoint}: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return False
    
    async def _test_ws_connection(self, endpoint: str) -> bool:
        """Test WebSocket connection asynchronously"""
        try:
            async with AsyncWebsocketClient(endpoint) as client:
                response = await client.request(xrpl.models.requests.ServerInfo())
                return response.is_successful()
        except Exception as e:
            logger.error(f"WebSocket connection test failed: {e}")
            return False
        
    def try_connect_ws_endpoint(self, endpoint: str) -> bool:
        """
        Attempt to connect to a new WebSocket endpoint.
        
        Args:
            endpoint: The WebSocket endpoint URL to test
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Ensure endpoint uses WebSocket protocol
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.scheme not in ['ws', 'wss']:
                if parsed.scheme in ['http', 'https']:
                    # Convert HTTP to WS protocol
                    scheme = 'wss' if parsed.scheme == 'https' else 'ws'
                    endpoint = endpoint.replace(parsed.scheme, scheme, 1)
                else:
                    endpoint = f"ws://{endpoint}"

            logger.debug(f"Attempting to connect to WebSocket endpoint: {endpoint}")

            # Create event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Run connection test
            success = loop.run_until_complete(self._test_ws_connection(endpoint))

            message = f"{'Successful' if success else 'Failed'} connection to {endpoint}"
            logger.debug(message)

            return success

        except Exception as e:
            logger.error(f"WebSocket connection test failed for {endpoint}: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return False
        
    def restart_xrpl_monitor(self):
        """Restart the XRPL monitor thread with the new WebSocket endpoint"""
        if hasattr(self, 'worker') and self.worker:
            logger.debug("Stopping existing XRPL monitor thread")
            self.worker.stop()
            self.worker.join(timeout=2)
            
            if self.worker.is_alive():
                logger.warning("XRPL monitor thread did not stop gracefully")
            
            self.worker = XRPLMonitorThread(self)
            self.worker.start()

def main():
    logger.info("Starting Post Fiat Wallet")
    app = PostFiatWalletApp()
    app.MainLoop()

if __name__ == "__main__":
    main()
