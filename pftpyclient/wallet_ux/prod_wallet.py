import time
import wx
import wx.adv
import wx.grid as gridlib
import wx.html
import xrpl
from xrpl.wallet import Wallet
import asyncio
from threading import Thread, Event
import wx.lib.newevent
import nest_asyncio
from pftpyclient.task_manager.wallet_state import (
    WalletState, 
    requires_wallet_state,
    FUNDED_STATES,
    TRUSTLINED_STATES,
    INITIATED_STATES,
    GOOGLE_DOC_SENT_STATES,
    PFT_STATES
)
from pftpyclient.task_manager.basic_tasks import (
    PostFiatTaskManager, 
    NoMatchingTaskException, 
    WrongTaskStateException, 
    MAX_CHUNK_SIZE, 
    compress_string
)
from pftpyclient.user_login.credentials import CredentialManager
import webbrowser
import os
from pftpyclient.basic_utilities.configure_logger import configure_logger, update_wx_sink
from pftpyclient.performance.monitor import PerformanceMonitor
from pftpyclient.configuration.configuration import ConfigurationManager
from loguru import logger
from pathlib import Path
from cryptography.fernet import InvalidToken
import pandas as pd
from enum import Enum, auto
# Configure the logger at module level
wx_sink = configure_logger(
    log_to_file=True,
    output_directory=Path.cwd() / "pftpyclient",
    log_filename="prod_wallet.log",
    level="DEBUG"
)
from pftpyclient.wallet_ux.constants import *

UPDATE_TIMER_INTERVAL_SEC = 60  # 60 Seconds
REFRESH_GRIDS_AFTER_TASK_DELAY_SEC = 10  # 10 seconds

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
        self.gui = gui
        use_testnet = ConfigurationManager().get_global_config('use_testnet')
        self.nodes = MAINNET_WEBSOCKETS if not use_testnet else TESTNET_WEBSOCKETS
        self.current_node_index = 0
        self.url = self.nodes[self.current_node_index]
        self.loop = asyncio.new_event_loop()
        self.context = None
        self.expecting_state_change = False
        self._stop_event = Event()

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

    def stopped(self):
        """Check if the thread has been signaled to stop"""
        return self._stop_event.is_set()

    async def monitor(self):
        """Main monitoring coroutine"""
        try:
            while not self.stopped():
                await self.watch_xrpl_account(self.gui.wallet.classic_address, self.gui.wallet)
        except asyncio.CancelledError:
            logger.debug("Monitor task cancelled")
        except Exception as e:
            if not self.stopped():
                logger.error(f"Unexpected error in monitor: {e}")
    
    def switch_node(self):
        self.current_node_index = (self.current_node_index + 1) % len(self.nodes)
        self.url = self.nodes[self.current_node_index]
        logger.info(f"Switching to next node: {self.url}")

    async def watch_xrpl_account(self, address, wallet=None):
        self.account = address
        self.wallet = wallet
        check_interval = 10

        while not self.stopped():
            try:
                async with xrpl.asyncio.clients.AsyncWebsocketClient(self.url) as self.client:
                    while True:
                        try: 
                            response = await asyncio.wait_for(self.on_connected(), timeout=check_interval)

                            async for message in self.client:
                                mtype = message.get("type")
                                # Message type "ledgerClosed" is received when a new ledger is closed (block added to the ledger)
                                if mtype == "ledgerClosed":
                                    wx.CallAfter(self.gui.update_ledger, message)
                                    # Only check account info if we are expecting a state change.
                                    # This is necessary because the transaction stream can be delayed significantly.
                                    # TODO: This is a hack to get around the delay.
                                    # TODO: The issue might be caused by a logic flaw in the on_connected() method.
                                    if self.expecting_state_change:
                                        logger.debug(f"Checking account info because we are expecting a state change.")
                                        try:
                                            response = await asyncio.wait_for(
                                                self.client.request(xrpl.models.requests.AccountInfo(
                                                    account=self.account,
                                                    ledger_index="validated"
                                                )),
                                                timeout=check_interval
                                            )
                                            wx.CallAfter(self.gui.update_account, response.result["account_data"])
                                            wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))                                       
                                        except asyncio.TimeoutError:
                                            logger.warning(f"Request to {self.url} timed out. Switching to next node.")
                                            self.switch_node()
                                            return
                                        except Exception as e:
                                            logger.error(f"Error processing request: {e}")
                                # Message type "transaction" is received when a transaction is detected
                                elif mtype == "transaction":
                                    try:
                                        response = await asyncio.wait_for(
                                            self.client.request(xrpl.models.requests.AccountInfo(
                                                account=self.account,
                                                ledger_index="validated"
                                            )),
                                            timeout=check_interval
                                        )
                                        wx.CallAfter(self.gui.update_account, response.result["account_data"])
                                        wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))
                                    except asyncio.TimeoutError:
                                        logger.warning(f"Request to {self.url} timed out. Switching to next node.")
                                        self.switch_node()
                                        return
                                    except Exception as e:
                                        logger.error(f"Error processing request: {e}")

                        except asyncio.TimeoutError:
                            logger.warning(f"Node {self.url} timed out. Switching to next node.")
                            self.switch_node()
                            return
                        except Exception as e:
                            if "actNotFound" in str(e):
                                logger.debug(f"Account {self.account} not found yet, waiting...")
                                await asyncio.sleep(check_interval)
                                continue
                            else:
                                logger.error(f"Unexpected error in monitoring loop: {e}")
                                await asyncio.sleep(check_interval)

            except Exception as e:
                if self.stopped():
                    break
                logger.error(f"Error in watch_xrpl_account: {e}")

    async def on_connected(self):
        logger.debug(f"on_connected: {self.account}")
        response = await self.client.request(xrpl.models.requests.Subscribe(
            streams=["ledger"],
            accounts=[self.account]
        ))
        wx.CallAfter(self.gui.update_ledger, response.result)
        response = await self.client.request(xrpl.models.requests.AccountInfo(
            account=self.account,
            ledger_index="validated"
        ))
        if response.is_successful():
            logger.debug(f"on_connected success result: {response.result}")
            wx.CallAfter(self.gui.update_account, response.result["account_data"])
            wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))
            return response
        else:
            if response.result.get("error") == "actNotFound":
                raise Exception("actNotFound")
            logger.error(f"Error in on_connected: {response.result}")
            raise Exception(str(response.result))

class CustomDialog(wx.Dialog):
    def __init__(self, title, fields, message=None):
        super(CustomDialog, self).__init__(None, title=title, size=(500, 200))
        self.fields = fields
        self.message = message
        self.InitUI()

        # For layout update before getting best size
        self.GetSizer().Fit(self)
        self.Layout()

        best_size = self.GetBestSize()
        min_height = best_size.height
        self.SetSize((500, min_height))

    def InitUI(self):
        pnl = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        if self.message:
            message_label = wx.StaticText(pnl, label=self.message, style=wx.ST_NO_AUTORESIZE)
            message_label.Wrap(480)  # wrap text at slightly less than width of dialog
            vbox.Add(message_label, flag=wx.EXPAND | wx.ALL, border=10)

        self.text_controls = {}
        for field in self.fields:
            hbox = wx.BoxSizer(wx.HORIZONTAL)
            label = wx.StaticText(pnl, label=field)
            hbox.Add(label, flag=wx.RIGHT, border=8)
            text_ctrl = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 100))
            hbox.Add(text_ctrl, proportion=1)
            self.text_controls[field] = text_ctrl
            vbox.Add(hbox, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        vbox.Add((-1, 25))

        hbox_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.submit_button = wx.Button(pnl, label="Submit")
        self.close_button = wx.Button(pnl, label="Close")
        hbox_buttons.Add(self.submit_button)
        hbox_buttons.Add(self.close_button, flag=wx.LEFT | wx.BOTTOM, border=5)
        vbox.Add(hbox_buttons, flag=wx.ALIGN_RIGHT | wx.RIGHT, border=10)

        pnl.SetSizer(vbox)

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(pnl, 1, wx.EXPAND)
        self.SetSizer(dialog_sizer)

        self.submit_button.Bind(wx.EVT_BUTTON, self.OnSubmit)
        self.close_button.Bind(wx.EVT_BUTTON, self.OnClose)

    def OnSubmit(self, e):
        self.EndModal(wx.ID_OK)

    def OnClose(self, e):
        self.EndModal(wx.ID_CANCEL)

    def GetValues(self):
        return {field: text_ctrl.GetValue() for field, text_ctrl in self.text_controls.items()}

class WalletApp(wx.Frame):

    STATE_AVAILABLE_TABS = {
        WalletState.UNFUNDED: ["Summary", "Log"],
        WalletState.FUNDED: ["Summary", "Payments", "Log"],
        WalletState.TRUSTLINED: ["Summary", "Payments", "Memos", "Log"],
        WalletState.INITIATED: ["Summary", "Payments", "Memos", "Log"],
        WalletState.GOOGLE_DOC_SENT: ["Summary", "Payments", "Memos", "Log"],
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
        wx.Frame.__init__(self, None, title="Post Fiat Client Wallet Beta v.0.1", size=(1150, 700))
        self.default_size = (1150, 700)
        self.min_size = (800, 600)
        self.max_size = (1400, 900)
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
        use_testnet = self.config.get_global_config('use_testnet')
        self.network_url = MAINNET_URL if not use_testnet else TESTNET_URL
        self.pft_issuer = ISSUER_ADDRESS if not use_testnet else TESTNET_ISSUER_ADDRESS
        
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
        preferences_item = file_menu.Append(wx.ID_ANY, "Preferences", "Configure client settings")
        logout_item = file_menu.Append(wx.ID_ANY, "Logout", "Return to login screen")
        quit_item = file_menu.Append(wx.ID_EXIT, "Quit", "Quit the application")
        self.Bind(wx.EVT_MENU, self.on_preferences, preferences_item)
        self.Bind(wx.EVT_MENU, self.on_logout, logout_item)
        self.Bind(wx.EVT_MENU, self.on_close, quit_item)
        self.menubar.Append(file_menu, "File")

        # Create Account menu
        self.account_menu = wx.Menu()
        self.contacts_item = self.account_menu.Append(wx.ID_ANY, "Manage Contacts")
        self.change_password_item = self.account_menu.Append(wx.ID_ANY, "Change Password")
        self.show_secret_item = self.account_menu.Append(wx.ID_ANY, "Show Secret")
        self.delete_account_item = self.account_menu.Append(wx.ID_ANY, "Delete Account")
        self.menubar.Append(self.account_menu, "Account")

        # Bind menu events
        self.Bind(wx.EVT_MENU, self.on_manage_contacts, self.contacts_item)
        self.Bind(wx.EVT_MENU, self.on_change_password, self.change_password_item)
        self.Bind(wx.EVT_MENU, self.on_show_secret, self.show_secret_item)
        self.Bind(wx.EVT_MENU, self.on_delete_credentials, self.delete_account_item)

        # Extras menu
        extras_menu = wx.Menu()
        self.perf_monitor_item = extras_menu.Append(wx.ID_ANY, "Performance Monitor", "Monitor client's performance")
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
        network_text = "Testnet" if self.config.get_global_config('use_testnet') else "Mainnet"
        self.summary_lbl_network = wx.StaticText(self.summary_tab, label=f"Network: {network_text}")
        self.summary_lbl_wallet_state = wx.StaticText(self.summary_tab, label="Wallet State: ")

        username_row_sizer.Add(self.summary_lbl_username, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        username_row_sizer.AddStretchSpacer()
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

        # Add grid to Proposals tab
        self.proposals_grid = self.setup_grid(gridlib.Grid(self.proposals_tab), 'proposals')
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
        self.verification_lbl_task_id = wx.StaticText(self.verification_tab, label="Task ID:")
        self.verification_sizer.Add(self.verification_lbl_task_id, flag=wx.ALL, border=5)
        self.verification_txt_task_id = wx.TextCtrl(self.verification_tab)
        self.verification_sizer.Add(self.verification_txt_task_id, flag=wx.EXPAND | wx.ALL, border=5)

        # Verification Details input box
        self.verification_lbl_details = wx.StaticText(self.verification_tab, label="Verification Details:")
        self.verification_sizer.Add(self.verification_lbl_details, flag=wx.ALL, border=5)
        self.verification_txt_details = wx.TextCtrl(self.verification_tab, style=wx.TE_MULTILINE, size=(-1, 100))
        self.verification_sizer.Add(self.verification_txt_details, flag=wx.EXPAND | wx.ALL, border=5)

        # Submit Verification Details and Log Pomodoro buttons
        self.verification_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_submit_verification_details = wx.Button(self.verification_tab, label="Submit Verification Details")
        self.verification_button_sizer.Add(self.btn_submit_verification_details, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_verification_details.Bind(wx.EVT_BUTTON, self.on_submit_verification_details)

        self.btn_log_pomodoro = wx.Button(self.verification_tab, label="Log Pomodoro")
        self.verification_button_sizer.Add(self.btn_log_pomodoro, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_log_pomodoro.Bind(wx.EVT_BUTTON, self.on_log_pomodoro)

        self.verification_sizer.Add(self.verification_button_sizer, 0, wx.EXPAND)

        # Add a Force Update button to the Verification tab
        self.btn_force_update = wx.Button(self.verification_tab, label="Force Update")
        self.verification_sizer.Add(self.btn_force_update, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_force_update.Bind(wx.EVT_BUTTON, self.on_force_update)

        # Add grid to Verification tab
        self.verification_grid = self.setup_grid(gridlib.Grid(self.verification_tab), 'verification')
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

        # Add memo input box section with encryption requests button
        memo_header_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_memo = wx.StaticText(self.memos_tab, label="Enter your memo:")
        memo_header_sizer.Add(self.lbl_memo, 1, wx.ALIGN_CENTER_VERTICAL)

        self.btn_encryption_requests = wx.Button(self.memos_tab, label="Encryption Requests")
        self.btn_encryption_requests.Bind(wx.EVT_BUTTON, self.on_encryption_requests)
        memo_header_sizer.Add(self.btn_encryption_requests, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 5)

        self.memos_sizer.Add(memo_header_sizer, 0, wx.EXPAND | wx.ALL, border=5)
        self.txt_memo_input = wx.TextCtrl(self.memos_tab, style=wx.TE_MULTILINE, size=(-1, 200))
        self.memos_sizer.Add(self.txt_memo_input, 1, wx.EXPAND | wx.ALL, border=5)

        # Add submit button
        self.btn_submit_memo = wx.Button(self.memos_tab, label="Submit Memo")
        self.memos_sizer.Add(self.btn_submit_memo, flag=wx.ALL | wx.EXPAND, border=5)
        self.btn_submit_memo.Bind(wx.EVT_BUTTON, self.on_submit_memo)        

        # Add grid to Memos tab
        self.memos_grid = self.setup_grid(gridlib.Grid(self.memos_tab), 'memos')
        self.memos_sizer.Add(self.memos_grid, 1, wx.EXPAND | wx.ALL, 20)

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
        selection = event.GetSelection()
        if selection != wx.NOT_FOUND:
            # Get the stored address from client data
            address = self.txt_payment_destination.GetClientData(selection)
            wx.CallAfter(self.txt_payment_destination.ChangeValue, address)
        event.Skip()

    def on_destination_text(self, event):
        """Handle manual text entry - allow any text"""
        event.Skip()

    def update_all_destination_comboboxes(self):
        """Update all destination comboboxes"""
        self._populate_destination_combobox(self.txt_payment_destination)
        self._populate_destination_combobox(self.memo_recipient, REMEMBRANCER_ADDRESS)

    def _populate_destination_combobox(self, combobox, default_destination=None):
        """
        Populate destination combobox with contacts
        Args:
            combobox: wx.ComboBox to populate
            default_destination: Optional default address to select
        """
        combobox.Clear()
        contacts = self.task_manager.get_contacts()

        # Add contacts in format "name (address)"
        for address, name in contacts.items():
            display_text = f"{name} ({address})"
            combobox.Append(display_text, address)

        # Set default selection
        if default_destination:
            if default_destination in contacts:
                display_text = f"{contacts[default_destination]} ({default_destination})"
                combobox.SetValue(display_text)
            else:
                combobox.SetValue(default_destination)

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
            wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)
        
            if not self.task_manager.verify_password(password):
                wx.MessageBox("Incorrect password", "Error", wx.OK | wx.ICON_ERROR)
                return
    
        token_type = self.token_selector.GetValue()
        amount = self.payment_txt_amount.GetValue()
        destination = self.txt_payment_destination.GetValue()
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
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Return to Login button
        return_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_return_to_login = wx.Button(panel, label="Return to Login")
        return_btn_sizer.Add(self.btn_return_to_login, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        self.btn_return_to_login.Bind(wx.EVT_BUTTON, self.on_return_to_login)
        sizer.Add(return_btn_sizer, 0, wx.ALIGN_CENTER | wx.TOP, 10)
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.TOP, 5)
        
        user_details_sizer = wx.BoxSizer(wx.VERTICAL)

        # XRP Address
        self.create_lbl_xrp_address = wx.StaticText(panel, label="XRP Address:")
        user_details_sizer.Add(self.create_lbl_xrp_address, flag=wx.ALL, border=5)
        self.create_txt_xrp_address = wx.TextCtrl(panel)
        user_details_sizer.Add(self.create_txt_xrp_address, flag=wx.EXPAND | wx.ALL, border=5)

        # XRP Secret
        secret_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.create_lbl_xrp_secret = wx.StaticText(panel, label="XRP Secret:")
        user_details_sizer.Add(self.create_lbl_xrp_secret, flag=wx.ALL, border=5)
        self.create_txt_xrp_secret = wx.TextCtrl(panel, style=wx.TE_PASSWORD)  # TODO: make a checkbox to show/hide the secret
        secret_sizer.Add(self.create_txt_xrp_secret, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
        self.chk_show_secret = wx.CheckBox(panel, label="Show Secret")
        secret_sizer.Add(self.chk_show_secret, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        user_details_sizer.Add(secret_sizer, flag=wx.EXPAND)

        self.chk_show_secret.Bind(wx.EVT_CHECKBOX, self.on_toggle_secret_visibility_user_details)

        # Username
        self.create_lbl_username = wx.StaticText(panel, label="Username:")
        user_details_sizer.Add(self.create_lbl_username, flag=wx.ALL, border=5)
        self.create_txt_username = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        user_details_sizer.Add(self.create_txt_username, flag=wx.EXPAND | wx.ALL, border=5)

        # Bind event to force lowercase
        self.create_txt_username.Bind(wx.EVT_TEXT, self.on_force_lowercase)

        # Password
        self.create_lbl_password = wx.StaticText(panel, label="Password (minimum 8 characters):")
        user_details_sizer.Add(self.create_lbl_password, flag=wx.ALL, border=5)
        self.create_txt_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        user_details_sizer.Add(self.create_txt_password, flag=wx.EXPAND | wx.ALL, border=5)

        # Confirm Password
        self.create_lbl_confirm_password = wx.StaticText(panel, label="Confirm Password:")
        user_details_sizer.Add(self.create_lbl_confirm_password, flag=wx.ALL, border=5)
        self.create_txt_confirm_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        user_details_sizer.Add(self.create_txt_confirm_password, flag=wx.EXPAND | wx.ALL, border=5)

        # Tooltips
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

        # Buttons
        wallet_buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_generate_wallet = wx.Button(panel, label="Generate New XRP Wallet")
        wallet_buttons_sizer.Add(self.btn_generate_wallet, 1, flag=wx.EXPAND | wx.RIGHT, border=5)
        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)

        self.btn_restore_wallet = wx.Button(panel, label="Restore from Seed")
        wallet_buttons_sizer.Add(self.btn_restore_wallet, 1, flag=wx.EXPAND | wx.LEFT, border=5)
        self.btn_restore_wallet.Bind(wx.EVT_BUTTON, self.on_restore_wallet)

        user_details_sizer.Add(wallet_buttons_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        self.btn_cache_user = wx.Button(panel, label="Cache Credentials")
        user_details_sizer.Add(self.btn_cache_user, flag=wx.EXPAND, border=15)
        self.btn_cache_user.Bind(wx.EVT_BUTTON, self.on_cache_user)

        sizer.Add(user_details_sizer, 1, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)

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
        dialog = CustomDialog("Restore Wallet", ["XRP Secret"])
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

        username = self.login_txt_username.GetValue()
        password = self.login_txt_password.GetValue()

        try:
            self.task_manager = PostFiatTaskManager(
                username=username, 
                password=password,
                network_url=self.network_url,
                config=self.config
            )

        except (ValueError, InvalidToken, KeyError) as e:
            logger.error(f"Login failed: {e}")
            self.show_error("Invalid username or password")
            self.btn_login.SetLabel("Login")
            self.btn_login.Update()
            return
        except Exception as e:
            logger.error(f"Login failed: {e}")
            self.show_error(f"Login failed: {e}")
            self.btn_login.SetLabel("Login")
            self.btn_login.Update()
            return
        
        self.enable_menus()
        
        self.wallet = self.task_manager.user_wallet
        classic_address = self.wallet.classic_address

        self.update_ui_based_on_wallet_state()

        logger.info(f"Logged in as {username}")

        # Save the last logged-in user
        self.config.set_global_config('last_logged_in_user', username)

        # Hide login panel and show tabs
        self.login_panel.Hide()
        self.tabs.Show()

        self.update_account_display(username, classic_address)

        # Update layout and ensure correct sizing
        self.panel.Layout()
        self.Layout()
        self.Fit()

        self.worker = XRPLMonitorThread(self)
        self.worker.start()

        # Populate grids with data.
        # No need to call sync_and_refresh here, since sync_transactions was called by the task manager's instantiation
        self.refresh_grids()

        # Start timers
        self.start_pft_update_timer()
        self.start_transaction_update_timer()

        self.set_wallet_ui_state(WalletUIState.IDLE)

        self.update_all_destination_comboboxes()

    @PerformanceMonitor.measure('update_ui_based_on_wallet_state')
    def update_ui_based_on_wallet_state(self, is_state_transition=False):
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

        # Only show message box if not a state transition
        if not is_state_transition:
            if current_state != WalletState.ACTIVE:
                required_action = self.task_manager.get_required_action()
                message = (
                    f"Some features are currently locked because your wallet is not fully set up.\n\n"
                    f"Next required action: {required_action}\n\n"
                    f"All features will be unlocked once wallet reaches '{WalletState.ACTIVE.value}' state."
                )
                wx.MessageBox(message, "Wallet Features Limited", wx.OK | wx.ICON_INFORMATION)

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
    def update_account_display(self, username, classic_address):
        """Update all account-related display elements"""
        logger.debug(f"Updating account display for {username}")

        # Update basic account info
        self.summary_lbl_username.SetLabel(f"Username: {username}")
        self.summary_lbl_address.SetLabel(f"XRP Address: {classic_address}")

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
                    "To activate your wallet, you need \nto send at least 20 XRP to your address. \n\n"
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
                    "- Cost a small amount of XRP (~0.00001 XRP)\n"
                    "- Enable PFT token transactions\n\n"
                    "Proceed?"
                )
                if wx.YES == wx.MessageBox(message, "Set Trust Line", wx.YES_NO | wx.ICON_QUESTION):
                    try:
                        self.worker.expecting_state_change = True
                        self.task_manager.handle_trust_line()
                        wx.CallAfter(self.update_account_info)  # TODO: Not sure if this is needed
                        wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))
                    except Exception as e:
                        logger.error(f"Error setting trust line: {e}")
                        wx.MessageBox(f"Error setting trust line: {e}", "Error", wx.OK | wx.ICON_ERROR)

            case WalletState.TRUSTLINED:
                message = (
                    "To start accepting tasks, you need to perform the Initiation Rite.\n\n"
                    "This transaction will cost 1 XRP.\n\n"
                    "Please write 1 sentence committing to a long term objective of your choice:"
                )
                dialog = CustomDialog("Initiation Rite", ["Commitment"], message=message)
                if dialog.ShowModal() == wx.ID_OK:
                    commitment = dialog.GetValues()["Commitment"]
                    try:
                        self.worker.expecting_state_change = True
                        response = self.task_manager.send_initiation_rite(commitment)
                        formatted_response = self.format_response(response)
                        dialog = SelectableMessageDialog(self, "Initiation Rite Result", formatted_response)
                        dialog.ShowModal()
                        dialog.Destroy()
                        wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))
                    except Exception as e:
                        logger.error(f"Error sending initiation rite: {e}")
                        wx.MessageBox(f"Error sending initiation rite: {e}", "Error", wx.OK | wx.ICON_ERROR)
                dialog.Destroy()

            case WalletState.INITIATED:
                template_text = (
                    f"{self.wallet.classic_address}\n"
                    "___x TASK VERIFICATION SECTION START x___\n \n"
                    "___x TASK VERIFICATION SECTION END x___\n"
                )
                
                message = (
                    "To continue with wallet initialization,\nyou need to provide a Google Doc link.\n\n"
                    "This document should:\n"
                    "- Be viewable by anyone who has the link\n"
                    "- Have your XRP address on the first line\n"
                    "- Include a task verification section\n\n"
                    "Copy and paste the text below into your Google Doc:\n\n"
                    f"\n{template_text}\n\n"
                    "When you're ready, click OK to proceed."
                )

                template_dialog = SelectableMessageDialog(self, "Google Doc Setup", message)
                template_dialog.ShowModal()
                template_dialog.Destroy()

                message = "Now enter the link for your Google Doc:"

                dialog = CustomDialog("Google Doc Setup", ["Google Doc Share Link"], message=message)
                if dialog.ShowModal() == wx.ID_OK:
                    google_doc_link = dialog.GetValues()["Google Doc Share Link"]
                    try:
                        self.worker.expecting_state_change = True
                        self.task_manager.handle_google_doc_setup(google_doc_link)
                        wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))
                    except Exception as e:
                        logger.error(f"Error setting up Google Doc: {e}")
                        wx.MessageBox(f"Error setting up Google Doc: {e}", "Error", wx.OK | wx.ICON_ERROR)
                dialog.Destroy()
            
            case WalletState.GOOGLE_DOC_SENT:
                message = (
                    "To continue with wallet initialization, you need to send a User Genesis transaction.\n\n"
                    "This transaction will:\n"
                    "- Cost 7 PFT\n"
                    "- Register you as a user in the Post Fiat network\n"
                    "- Enable you to start accepting tasks\n\n"
                    "Proceed?"
                )
                if wx.YES == wx.MessageBox(message, "Send Genesis Transaction", wx.YES_NO | wx.ICON_QUESTION):
                    try:
                        self.worker.expecting_state_change = True
                        self.task_manager.handle_genesis()
                        wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))
                    except Exception as e:
                        logger.error(f"Error sending genesis transaction: {e}")
                        wx.MessageBox(f"Error sending genesis transaction: {e}", "Error", wx.OK | wx.ICON_ERROR)

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
                if hasattr(self, 'worker'):
                    self.worker.expecting_state_change = False
                message = (
                    "Your wallet is now funded!\n\n"
                    "You can now proceed with setting up a trust line for PFT tokens.\n"
                    "Click the 'Take Action' button to continue."
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "XRP Received!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))

            # If trust line is detected and current state is FUNDED
            elif (current_state == WalletState.FUNDED and self.task_manager.has_trust_line()):
                logger.info("Trust line detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.TRUSTLINED
                if hasattr(self, 'worker'):
                    self.worker.expecting_state_change = False
                message = (
                    "Trust line successfully established!\n\n"
                    "You can now proceed with the initiation rite.\n"
                    "Click the 'Take Action' button to continue."                    
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "Trust Line Set!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))

            # If initiation rite is detected and current state is TRUSTLINED
            elif (current_state == WalletState.TRUSTLINED and self.task_manager.initiation_rite_sent()):
                logger.info("Initiation rite detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.INITIATED
                if hasattr(self, 'worker'):
                    self.worker.expecting_state_change = False
                message = (
                    "Initiation rite successfully sent!\n\n"
                    "You can now proceed with setting up your Google Doc.\n"
                    "Click the 'Take Action' button to continue."
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "Initiation Complete!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))

            # If Google Doc is detected and current state is INITIATED
            elif (current_state == WalletState.INITIATED and self.task_manager.google_doc_sent()):
                logger.info("Google Doc detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.GOOGLE_DOC_SENT
                if hasattr(self, 'worker'):
                    self.worker.expecting_state_change = False
                message = (
                    "Google Doc successfully set up!\n\n"
                    "You can now proceed with the final step: sending your genesis transaction.\n"
                    "Click the 'Take Action' button to continue."
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "Google Doc Ready!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))

            # If genesis is detected and current state is GOOGLE_DOC_SENT
            elif (current_state == WalletState.GOOGLE_DOC_SENT and self.task_manager.genesis_sent()):
                logger.info("Genesis transaction detected. Updating wallet state.")
                self.task_manager.wallet_state = WalletState.ACTIVE
                if hasattr(self, 'worker'):
                    self.worker.expecting_state_change = False
                message = (
                    "Genesis transaction successful!\n\n"
                    "Your wallet is now fully initialized and ready to use.\n"
                    "You can now start accepting tasks."
                )
                wx.CallAfter(lambda: wx.MessageBox(message, "Wallet Activated!", wx.OK | wx.ICON_INFORMATION))
                wx.CallAfter(lambda: self.update_ui_based_on_wallet_state(is_state_transition=True))

    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('update_tokens')
    def update_tokens(self, account_address):
        #TODO: refactor this to use the task manager
        logger.debug(f"Fetching token balances for account: {account_address}")
        try:
            client = xrpl.clients.JsonRpcClient(self.network_url)
            account_lines = xrpl.models.requests.AccountLines(
                account=account_address,
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
        if self.worker:
            self.worker.loop.stop()

        if self.perf_monitor:
            self.perf_monitor.stop()
            self.perf_monitor = None

        self.Destroy()

    def start_pft_update_timer(self):
        self.pft_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_pft_update_timer, self.pft_update_timer)
        self.pft_update_timer.Start(UPDATE_TIMER_INTERVAL_SEC * 1000)

    def start_transaction_update_timer(self):
        self.tx_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_transaction_update_timer, self.tx_update_timer)
        self.tx_update_timer.Start(UPDATE_TIMER_INTERVAL_SEC * 1000)

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

    def on_transaction_update_timer(self, _):
        """Timer-triggered update"""
        logger.debug("Transaction update timer triggered")
        try:
            self._sync_and_refresh()
        except Exception as e:
            logger.error(f"Timer update failed: {e}")
    
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

        # Proposals, Rewards, and Verification grids (available in PFT_STATES)
        if current_state in PFT_STATES:
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

    @PerformanceMonitor.measure('update_grid')
    def update_grid(self, event):
        """Update a specific grid based on the event target and wallet state"""
        if not hasattr(event, 'target'):
            logger.error(f"No target found in event: {event}")
            return
        
        current_state = self.task_manager.wallet_state

        # Define wallet state requirements for each grid
        grid_state_requirements = {
            'rewards': PFT_STATES,
            'verification': PFT_STATES,
            'proposals': PFT_STATES,
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

        self.auto_size_window()

    @PerformanceMonitor.measure('on_pft_update_timer')
    def on_pft_update_timer(self, event):
        self.set_wallet_ui_state(WalletUIState.SYNCING, "Updating token balance...")
        if self.wallet:
            self.update_tokens(self.wallet.classic_address)
        self.set_wallet_ui_state(WalletUIState.IDLE)

    @PerformanceMonitor.measure('populate_grid_generic')
    def populate_grid_generic(self, grid: wx.grid.Grid, data: pd.DataFrame, grid_name: str):
        """Generic grid population method that respects zoom settings"""

        if data.empty:
            logger.debug(f"No data to populate {grid_name} grid")
            grid.ClearGrid()
            return

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

        self.auto_size_window()

    @PerformanceMonitor.measure('populate_summary_grid')
    def populate_summary_grid(self, key_account_details):
        """Convert dictionary to dataframe and use generic grid population method"""
        summary_df = pd.DataFrame(list(key_account_details.items()), columns=['Key', 'Value'])
        self.populate_grid_generic(self.summary_grid, summary_df, 'summary')

    def auto_size_window(self):
        self.rewards_tab.Layout()
        self.tabs.Layout()
        self.panel.Layout()

        size = self.panel.GetBestSize()
        new_size = (
            min(max(size.width, self.default_size[0]), self.max_size[0]),
            min(max(size.height, self.default_size[1]), self.max_size[1])
        )
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

        self.auto_size_window()

    def on_tab_changed(self, event):
        self.auto_size_window()
        event.Skip()

    def on_request_task(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Requesting Task...")
        self.btn_request_task.SetLabel("Requesting Task...")
        self.btn_request_task.Update()

        dialog = CustomDialog("Request Task", ["Task Request"])
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
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Accepting Task...")
        self.btn_accept_task.SetLabel("Accepting Task...")
        self.btn_accept_task.Update()

        dialog = CustomDialog("Accept Task", ["Task ID", "Acceptance String"])
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
            else:
                wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)
        dialog.Destroy()

        self.btn_accept_task.SetLabel("Accept Task")
        self.btn_accept_task.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_refuse_task(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Refusing Task...")
        self.btn_refuse_task.SetLabel("Refusing Task...")
        self.btn_refuse_task.Update()

        dialog = CustomDialog("Refuse Task", ["Task ID", "Refusal Reason"])
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
            else:
                wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)


        dialog.Destroy()
        self.btn_refuse_task.SetLabel("Refuse Task")
        self.btn_refuse_task.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_submit_for_verification(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting for Verification...")
        self.btn_submit_for_verification.SetLabel("Submitting for Verification...")
        self.btn_submit_for_verification.Update()

        dialog = CustomDialog("Submit for Verification", ["Task ID", "Completion String"])
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
                wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)
            except NoMatchingTaskException as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Couldn't find task with task ID {task_id}. Did you enter it correctly?", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            except WrongTaskStateException as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Task ID {task_id} has not yet been accepted. Current status: {e}", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            except Exception as e:
                logger.error(f"Error submitting initial completion: {e}")
                wx.MessageBox(f"Error submitting initial completion: {e}", 'Task Submission Error', wx.OK | wx.ICON_ERROR)
            else:
                wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)
        dialog.Destroy()

        self.btn_submit_for_verification.SetLabel("Submit for Verification")
        self.btn_submit_for_verification.Update()
        self.set_wallet_ui_state(WalletUIState.IDLE)

    def on_submit_verification_details(self, event):
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting Verification Details...")
        self.btn_submit_verification_details.SetLabel("Submitting Verification Details...")
        self.btn_submit_verification_details.Update()

        task_id = self.verification_txt_task_id.GetValue()
        response_string = self.verification_txt_details.GetValue()

        if not task_id or not response_string:
            wx.MessageBox("Please enter a task ID and verification details", "Error", wx.OK | wx.ICON_ERROR)
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
                self.verification_txt_task_id.SetValue("")
                wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)

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

    def on_submit_memo(self, event):
        """Submits a memo to the remembrancer."""
        self.set_wallet_ui_state(WalletUIState.TRANSACTION_PENDING, "Submitting Memo...")
        self.btn_submit_memo.SetLabel("Submitting...")
        self.btn_submit_memo.Update()
        logger.info("Submitting Memo")

        memo_text = self.txt_memo_input.GetValue()
        recipient = self.memo_recipient.GetSelection()
        if recipient != wx.NOT_FOUND:
            recipient = self.memo_recipient.GetClientData(recipient)
        else:
            recipient = self.memo_recipient.GetValue()

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
                            self._sync_and_refresh()
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

            # Estimate compressed size
            compressed_text = compress_string(test_memo)
            compressed_bytes = compressed_text.encode('utf-8')
            num_chunks = len(compressed_bytes) // MAX_CHUNK_SIZE
            if len(compressed_bytes) % MAX_CHUNK_SIZE != 0:
                num_chunks += 1

            # Calculate uncompressed chunks for comparison
            uncompressed_bytes = test_memo.encode('utf-8')
            uncompressed_chunks = len(uncompressed_bytes) // MAX_CHUNK_SIZE
            if len(uncompressed_bytes) % MAX_CHUNK_SIZE != 0:
                uncompressed_chunks += 1        

            if num_chunks > 1:
                message = (
                    f"Memo will be encrypted, compressed and sent over {num_chunks} transactions "
                    f"(compared to {uncompressed_chunks} without compression) and "
                    f"cost 1 PFT per chunk ({num_chunks} PFT total). Continue?"
                )
                if wx.NO == wx.MessageBox(message, "Confirmation", wx.YES_NO | wx.ICON_QUESTION):
                    self.btn_submit_memo.SetLabel("Submit Memo")
                    return
                
            # Send the memo
            responses = self.task_manager.send_memo(recipient, memo_text, encrypt=encrypt)
            
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
            wx.CallLater(REFRESH_GRIDS_AFTER_TASK_DELAY_SEC * 1000, self.refresh_grids, None)

        self.btn_submit_memo.SetLabel("Submit Memo")
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
            livenet_link = f"https://livenet.xrpl.org/transactions/{response.result.get('hash', 'N/A')}"

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
            livenet_link = f"https://livenet.xrpl.org/accounts/{self.wallet.classic_address}"

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
                # Signal the worker to stop
                self.worker.stop()

                # Cancel any pending tasks
                pending_tasks = asyncio.all_tasks(self.worker.loop)
                for task in pending_tasks:
                    task.cancel()

                # Run the loop one last time to process cancellations
                if pending_tasks:
                    try:
                        self.worker.loop.call_soon_threadsafe(
                            lambda: self.worker.loop.stop()
                        )
                    except Exception as e:
                        pass  # Ignore any errors during loop stop
            
                # Wait for thread to complete (with timeout)
                self.worker.join(timeout=2)
                if self.worker.is_alive():
                    logger.error("Worker thread did not stop gracefully")
                self.worker = None

            # Stop timers
            if hasattr(self, 'pft_update_timer'):
                logger.debug("Stopping PFT update timer")
                self.pft_update_timer.Stop()

            if hasattr(self, 'tx_update_timer'):
                logger.debug("Stopping TX update timer")
                self.tx_update_timer.Stop()

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

class LinkOpeningHtmlWindow(wx.html.HtmlWindow):
    def OnLinkClicked(self, link):
        url = link.GetHref()
        logger.debug(f"Link clicked: {url}")
        try:
            webbrowser.open(url, new=2)
            logger.debug(f"Attempted to open URL: {url}")
        except Exception as e:
            logger.error(f"Failed to open URL {url}. Error: {str(e)}")

class SelectableMessageDialog(wx.Dialog):
    def __init__(self, parent, title, message):
        super(SelectableMessageDialog, self).__init__(parent, title=title, size=(500, 400))

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.html_window = LinkOpeningHtmlWindow(panel, style=wx.html.HW_SCROLLBAR_AUTO)
        sizer.Add(self.html_window, 1, wx.EXPAND | wx.ALL, 10)

        ok_button = wx.Button(panel, wx.ID_OK, label="OK")
        sizer.Add(ok_button, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(sizer)

        self.SetContent(message)
        self.Center()

    def SetContent(self, message):
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ word-wrap: break-word; }}
                pre {{ white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <pre>{message}</pre>
        </body>
        </html>
        """
        self.html_window.SetPage(html_content)

class ChangePasswordDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Change Password")

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Current password
        current_label = wx.StaticText(panel, label="Current Password:")
        self.current_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        sizer.Add(current_label, 0, wx.ALL, 5)
        sizer.Add(self.current_password, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

        # New password
        new_label = wx.StaticText(panel, label="New Password:")
        self.new_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        sizer.Add(new_label, 0, wx.ALL, 5)
        sizer.Add(self.new_password, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        
        # Confirm password
        confirm_label = wx.StaticText(panel, label="Confirm New Password:")
        self.confirm_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        sizer.Add(confirm_label, 0, wx.ALL, 5)
        sizer.Add(self.confirm_password, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        
        # Buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, "Change Password")
        cancel_button = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        button_sizer.Add(ok_button, 0, wx.ALL, 5)
        button_sizer.Add(cancel_button, 0, wx.ALL, 5)
        sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        self.Center()

class DeleteCredentialsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Delete Credentials")
        self.InitUI()

    def InitUI(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Warning icon and text
        warning_sizer = wx.BoxSizer(wx.HORIZONTAL)
        warning_bitmap = wx.ArtProvider.GetBitmap(wx.ART_WARNING, size=(32, 32))
        warning_icon = wx.StaticBitmap(self, bitmap=warning_bitmap)
        warning_sizer.Add(warning_icon, 0, wx.ALL, 5)

        warning_text = (
            "WARNING: This action cannot be undone!\n\n"
            "• All local credentials and saved contacts will be deleted for this account.\n"
            "• Your XRP wallet will remain on the XRPL but you will lose access.\n"
            "• Any PFT tokens in your wallet will become inaccessible.\n\n"
            "MAKE SURE YOU HAVE BACKED UP YOUR XRP SECRET KEY BEFORE PROCEEDING!\n\n"
        )

        warning_label = wx.StaticText(self, label=warning_text)
        warning_label.Wrap(400)
        warning_sizer.Add(warning_label, 1, wx.ALL, 5)
        main_sizer.Add(warning_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # Confirmation text input
        confirm_sizer = wx.BoxSizer(wx.HORIZONTAL)
        confirm_label = wx.StaticText(self, label="Type DELETE to confirm:")
        self.confirm_input = wx.TextCtrl(self)
        
        confirm_sizer.Add(confirm_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        confirm_sizer.Add(self.confirm_input, 1, wx.EXPAND, 10)
        main_sizer.Add(confirm_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        warning_bitmap = wx.ArtProvider.GetBitmap(wx.ART_WARNING, size=(16, 16))
        warning_icon = wx.StaticBitmap(self, bitmap=warning_bitmap)
        self.delete_button = wx.Button(self, label="Delete Account")
        cancel_button = wx.Button(self, label="Cancel")

        button_sizer.Add(warning_icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        button_sizer.Add(self.delete_button, 1, wx.ALL, 5)
        button_sizer.Add(cancel_button, 1, wx.ALL, 5)
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(main_sizer)

        # Bind events
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.confirm_input.Bind(wx.EVT_TEXT, self.on_text_change)

        # Initially disable delete button
        self.delete_button.Enable(False)

        # Set initial size
        self.SetSize(self.GetBestSize())

    def on_text_change(self, event):
        """Enable delete button only when confirmation text matches exactly"""
        self.delete_button.Enable(
            self.confirm_input.GetValue() == "DELETE"
        )

    def on_delete(self, event):
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event):
        self.EndModal(wx.ID_CANCEL)

class EncryptionRequestsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Encryption Requests", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent: WalletApp = parent
        self.task_manager: PostFiatTaskManager = parent.task_manager

        sizer = wx.BoxSizer(wx.VERTICAL)

        help_text = (
            "This dialog shows the status of encryption setup with other users.\n\n"
            "• When you receive a handshake request, it appears in the 'Received' column\n"
            "• After you send a handshake, the time appears in the 'Sent' column\n"
            "• Encryption is ready when both handshakes are exchanged\n\n"
            "Select a received request and click 'Accept' to enable encrypted messaging with that user."
        )
        text = wx.StaticText(self, label=help_text)
        text.Wrap(450)
        sizer.Add(text, 0, wx.ALL | wx.EXPAND, 5)

        # Create list control
        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "From", width=300)
        self.list_ctrl.InsertColumn(1, "Received", width=150)
        self.list_ctrl.InsertColumn(2, "Sent", width=150)
        self.list_ctrl.InsertColumn(3, "Encryption Ready", width=110)
        sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 5)

        # Add buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.accept_btn = wx.Button(self, label="Accept")
        self.accept_btn.Bind(wx.EVT_BUTTON, self.on_accept)
        btn_sizer.Add(self.accept_btn, 0, wx.RIGHT, 5)
        
        self.close_btn = wx.Button(self, label="Close")
        self.close_btn.Bind(wx.EVT_BUTTON, self.on_close)
        btn_sizer.Add(self.close_btn)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        self.SetSizer(sizer)
        self.load_requests()

        # Enable/disable accept button based on selection
        self.accept_btn.Enable(False)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_selection_changed)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_selection_changed)

        start_size = (800, 400)
        self.SetSize(start_size)
        self.SetMinSize(start_size)

    def on_selection_changed(self, event):
        """Enable accept button if an item is selected and not already accepted"""
        idx = self.list_ctrl.GetFirstSelected()
        if idx != -1:
            handshakes = self.task_manager.get_handshakes()
            selected_handshake = handshakes.iloc[self.list_ctrl.GetItemData(idx)]
            # Only enable Accept if we received a handshake but haven't sent one
            can_accept = (pd.notna(selected_handshake['received_at']) and pd.isna(selected_handshake['sent_at']))
            self.accept_btn.Enable(can_accept)
        else:
            self.accept_btn.Enable(False)

    def load_requests(self):
        """Load pending encryption requests into the list control"""
        self.list_ctrl.DeleteAllItems()
        handshakes = self.task_manager.get_handshakes()

        for idx, handshake in handshakes.iterrows():
            index = self.list_ctrl.GetItemCount()
            display_name = handshake['contact_name'] if pd.notna(handshake['contact_name']) else handshake['address']
            self.list_ctrl.InsertItem(index, display_name)

            # Show received time or "Not received" if we haven't received a handshake
            received_at = handshake['received_at']
            if pd.notna(received_at):  # check if timestamp is not NaT/None
                self.list_ctrl.SetItem(index, 1, received_at.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                self.list_ctrl.SetItem(index, 1, "")

            # Show accepted time or "Not sent" if we haven't sent a handshake
            sent_at = handshake['sent_at']
            if pd.notna(sent_at):  # check if timestamp is not NaT/None
                self.list_ctrl.SetItem(index, 2, sent_at.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                self.list_ctrl.SetItem(index, 2, "")

            # Show encryption ready status
            encryption_ready = handshake['encryption_ready']
            self.list_ctrl.SetItem(index, 3, "Yes" if encryption_ready else "No")

            self.list_ctrl.SetItemData(index, idx)

    def on_accept(self, event):
        idx = self.list_ctrl.GetFirstSelected()
        if idx == -1:
            return
        
        address = self.task_manager.get_handshakes().iloc[self.list_ctrl.GetItemData(idx)]['address']
        
        try:
            response = self.task_manager.send_handshake(address)
            formatted_response = self.parent.format_response(response)
            handshake_dialog = SelectableMessageDialog(self, "Handshake Sent", formatted_response)
            handshake_dialog.ShowModal()
            handshake_dialog.Destroy()
            self.parent._sync_and_refresh()
            self.load_requests()
        except Exception as e:
            wx.MessageBox(f"Failed to send handshake: {e}", "Error", wx.OK | wx.ICON_ERROR)

    def on_close(self, event):
        self.Close()

class PreferencesDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Preferences")
        self.config = parent.config

        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Create a static box for grouping preferences
        sb = wx.StaticBox(panel, label="Application Settings")
        sbs = wx.StaticBoxSizer(sb, wx.VERTICAL)

        # Require password for payment checkbox
        self.require_password_for_payment = wx.CheckBox(panel, label="Require password for payment")
        self.require_password_for_payment.SetValue(self.config.get_global_config('require_password_for_payment'))
        sbs.Add(self.require_password_for_payment, 0, wx.ALL | wx.EXPAND, 5)

        # Performance Monitor checkbox
        self.perf_monitor = wx.CheckBox(panel, label="Enable Performance Monitor")
        self.perf_monitor.SetValue(self.config.get_global_config('performance_monitor'))
        sbs.Add(self.perf_monitor, 0, wx.ALL | wx.EXPAND, 5)

        # Cache Format radio buttons
        cache_box = wx.StaticBox(panel, label="Transaction Cache Format")
        cache_sbs = wx.StaticBoxSizer(cache_box, wx.VERTICAL)

        self.cache_csv = wx.RadioButton(panel, label="CSV", style=wx.RB_GROUP)
        self.cache_pickle = wx.RadioButton(panel, label="Pickle")

        current_format = self.config.get_global_config("transaction_cache_format")
        if current_format == "csv":
            self.cache_csv.SetValue(True)
        else:
            self.cache_pickle.SetValue(True)

        cache_sbs.Add(self.cache_csv, 0, wx.ALL, 5)
        cache_sbs.Add(self.cache_pickle, 0, wx.ALL, 5)
        sbs.Add(cache_sbs, 0, wx.ALL | wx.EXPAND, 5)

        # Network selection radio buttons
        network_box = wx.StaticBox(panel, label="XRPL Network")
        network_sbs = wx.StaticBoxSizer(network_box, wx.VERTICAL)

        self.mainnet_radio = wx.RadioButton(panel, label="Mainnet", style=wx.RB_GROUP)
        self.testnet_radio = wx.RadioButton(panel, label="Testnet")

        use_testnet = self.config.get_global_config('use_testnet')
        self.testnet_radio.SetValue(use_testnet)
        self.mainnet_radio.SetValue(not use_testnet)

        network_sbs.Add(self.mainnet_radio, 0, wx.ALL, 5)
        network_sbs.Add(self.testnet_radio, 0, wx.ALL, 5)
        sbs.Add(network_sbs, 0, wx.ALL | wx.EXPAND, 5)

        # Add the static box to the main vertical box
        vbox.Add(sbs, 0, wx.ALL | wx.EXPAND, 10)

        # Add OK and Cancel buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, "OK")
        cancel_button = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        button_sizer.Add(ok_button, 0, wx.ALL, 5)
        button_sizer.Add(cancel_button, 0, wx.ALL, 5)
        vbox.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        # Bind the OK button event
        ok_button.Bind(wx.EVT_BUTTON, self.on_ok)

        panel.SetSizer(vbox)
        vbox.Fit(panel)

        self.SetSize(self.GetBestSize())
        self.Center()

    def on_ok(self, event):
        """Save config when OK is clicked"""
        # Check if network setting changed
        old_network = self.config.get_global_config('use_testnet')
        new_network = self.testnet_radio.GetValue()

        if old_network != new_network:
            wx.MessageBox("Network change requires a restart to take effect", "Restart Required", wx.OK | wx.ICON_WARNING)
            
        self.config.set_global_config('use_testnet', new_network)
        self.config.set_global_config('require_password_for_payment', self.require_password_for_payment.GetValue())
        self.config.set_global_config('performance_monitor', self.perf_monitor.GetValue())
        self.config.set_global_config('transaction_cache_format', 'csv' if self.cache_csv.GetValue() else 'pickle')
        self.EndModal(wx.ID_OK)

class ContactsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Manage Contacts", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.task_manager: PostFiatTaskManager = parent.task_manager
        self.changes_made = False

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Contacts list 
        self.contacts_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.contacts_list.InsertColumn(0, "Name", width=150)
        self.contacts_list.InsertColumn(1, "Address", width=300)
        sizer.Add(self.contacts_list, 1, wx.EXPAND | wx.ALL, 5)

        # Add contact section
        add_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.name_ctrl = wx.TextCtrl(panel)
        self.address_ctrl = wx.TextCtrl(panel)

        add_sizer.Add(wx.StaticText(panel, label="Name:"), 0, wx.CENTER | wx.ALL, 5)
        add_sizer.Add(self.name_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        add_sizer.Add(wx.StaticText(panel, label="Address:"), 0, wx.CENTER | wx.ALL, 5)
        add_sizer.Add(self.address_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        
        sizer.Add(add_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(panel, label="Add Contact")
        del_btn = wx.Button(panel, label="Delete Contact")
        close_btn = wx.Button(panel, label="Close")
        btn_sizer.Add(add_btn, 0, wx.ALL, 5)
        btn_sizer.Add(del_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        
        panel.SetSizer(sizer)

        start_size = (600, 400)
        self.SetSize(start_size)
        self.SetMinSize(start_size)
        
        # Bind events
        add_btn.Bind(wx.EVT_BUTTON, self.on_add)
        del_btn.Bind(wx.EVT_BUTTON, self.on_delete)
        close_btn.Bind(wx.EVT_BUTTON, self.on_close)
        
        self.load_contacts()

    def load_contacts(self):
        """Reload contacts list from storage"""
        self.contacts_list.DeleteAllItems()
        contacts = self.task_manager.get_contacts()
        for address, name in contacts.items():
            index = self.contacts_list.GetItemCount()
            self.contacts_list.InsertItem(index, name)
            self.contacts_list.SetItem(index, 1, address)
        self.contacts_list.Layout()
        self.Layout()

    def on_add(self, event):
        name = self.name_ctrl.GetValue().strip()
        address = self.address_ctrl.GetValue().strip()
        if name and address:
            logger.debug(f"Saving contact: {name} - {address}")
            try:
                self.task_manager.save_contact(address, name)
            except ValueError as e:
                wx.MessageBox(f"Error saving contact: {e}", 'Error', wx.OK | wx.ICON_ERROR)
                return
            else:
                self.load_contacts()
                self.name_ctrl.SetValue("")
                self.address_ctrl.SetValue("")
                self.changes_made = True

    def on_delete(self, event):
        index = self.contacts_list.GetFirstSelected()
        if index >= 0:
            name = self.contacts_list.GetItem(index, 0).GetText()
            address = self.contacts_list.GetItem(index, 1).GetText()
            logger.debug(f"Deleting contact: {name} - {address}")
            self.task_manager.delete_contact(address)
            self.load_contacts()
            self.changes_made = True

    def on_close(self, event):
        """Handle dialog close"""
        if self.changes_made:
            self.EndModal(wx.ID_OK)
        else:
            self.EndModal(wx.ID_CANCEL)

class ConfirmPaymentDialog(wx.Dialog):
    def __init__(self, parent, amount, destination, token_type):
        super().__init__(parent, title="Confirm Payment", style=wx.DEFAULT_DIALOG_STYLE)
        self.task_manager = parent.task_manager
        self.destination = destination
    
        # Check if destination is a known contact
        contacts = self.task_manager.get_contacts()
        contact_name = contacts.get(destination)

        self.InitUI(amount, destination, token_type, contact_name)
        self.Fit()
        self.Center()

    def InitUI(self, amount, destination, token_type, contact_name):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Create messge with contact name if it exists
        if contact_name:
            message = f"Send {amount} {token_type} to {contact_name} ({destination})?"
        else:
            message = f"Send {amount} {token_type} to {destination}?"

        msg_text = wx.StaticText(self, label=message)
        msg_text.Wrap(400)
        sizer.Add(msg_text, 0, wx.ALL | wx.EXPAND, 10)

        # Only show contact controls if this isn't already a contact
        if not contact_name:
            # Add save contact checkbox and name input
            self.save_contact = wx.CheckBox(self, label="Save as contact")
            self.contact_name = wx.TextCtrl(self)
            self.contact_name.Hide()

            sizer.Add(self.save_contact, 0, wx.ALL, 5)
            sizer.Add(self.contact_name, 0, wx.EXPAND | wx.ALL, 5)

            self.save_contact.Bind(wx.EVT_CHECKBOX, self.on_checkbox)

        # Button sizer
        btn_sizer = wx.StdDialogButtonSizer()

        self.ok_btn = wx.Button(self, wx.ID_OK, "Send")
        self.ok_btn.SetDefault()
        btn_sizer.AddButton(self.ok_btn)

        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        btn_sizer.AddButton(cancel_btn)

        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        self.SetSizer(sizer)

    def on_checkbox(self, event):
        """Handle checkbox toggle"""
        self.contact_name.Show(self.save_contact.GetValue())
        self.Fit() # Resize dialog to fit new size

    def get_contact_info(self):
        """Return contact info if saving was requested"""
        if not hasattr(self, 'save_contact') or not self.save_contact.GetValue():
            return None
        name = self.contact_name.GetValue().strip()
        return name if name else None

def main():
    logger.info("Starting Post Fiat Wallet")
    app = PostFiatWalletApp()
    app.MainLoop()

if __name__ == "__main__":
    main()
