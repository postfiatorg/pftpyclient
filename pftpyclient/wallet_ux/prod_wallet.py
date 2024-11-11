import time
import wx
import wx.adv
import wx.grid as gridlib
import wx.html
import xrpl
from xrpl.wallet import Wallet
import asyncio
from threading import Thread
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
    GoogleDocNotFoundException, 
    InvalidGoogleDocException, 
    PostFiatTaskManager, 
    WalletInitiationFunctions, 
    NoMatchingTaskException, 
    WrongTaskStateException, 
    is_over_1kb, 
    MAX_CHUNK_SIZE, 
    compress_string
)
from pftpyclient.user_login.credential_input import get_cached_usernames
import webbrowser
import os
from pftpyclient.basic_utilities.configure_logger import configure_logger, update_wx_sink
from pftpyclient.performance.monitor import PerformanceMonitor
from loguru import logger
from pathlib import Path
from cryptography.fernet import InvalidToken
import pandas as pd
import inspect
# Configure the logger at module level
wx_sink = configure_logger(
    log_to_file=True,
    output_directory=Path.cwd() / "pftpyclient",
    log_filename="prod_wallet.log",
    level="DEBUG"
)

MAINNET_WEBSOCKETS = [
    "wss://xrplcluster.com",
    "wss://xrpl.ws/",
    "wss://s1.ripple.com/",
    "wss://s2.ripple.com/"
]
TESTNET_WEBSOCKETS = [
    "wss://s.altnet.rippletest.net:51233"
]
MAINNET_URL = "https://s2.ripple.com:51234"
TESTNET_URL = "https://s.altnet.rippletest.net:51234"
USE_TESTNET = False

REMEMBRANCER_ADDRESS = "rJ1mBMhEBKack5uTQvM8vWoAntbufyG9Yn"
ISSUER_ADDRESS = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'

UPDATE_TIMER_INTERVAL_SEC = 60  # Every 60 Seconds

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
        self.nodes = MAINNET_WEBSOCKETS if not USE_TESTNET else TESTNET_WEBSOCKETS
        self.current_node_index = 0
        self.url = self.nodes[self.current_node_index]
        self.loop = asyncio.new_event_loop()
        self.context = None
        self.expecting_state_change = False

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.context = self.loop.run_until_complete(self.monitor())

    async def monitor(self):
        while True:
            try:
                await self.watch_xrpl_account(self.gui.wallet.classic_address, self.gui.wallet)
            except Exception as e:
                logger.error(f"Error in monitor: {e}. Switching to next node.")
                self.switch_node()
                await asyncio.sleep(5)
    
    def switch_node(self):
        self.current_node_index = (self.current_node_index + 1) % len(self.nodes)
        self.url = self.nodes[self.current_node_index]
        logger.info(f"Switching to next node: {self.url}")

    async def watch_xrpl_account(self, address, wallet=None):
        self.account = address
        self.wallet = wallet
        check_interval = 10

        while True:
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
                ('memo', 'Memo', 700)
            ]
        },
        'summary': {
            'columns': [
                ('Key', 'Key', 125),
                ('Value', 'Value', 550)
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

        self.perf_monitor = None

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

        self.network_url = MAINNET_URL if not USE_TESTNET else TESTNET_URL

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
        menubar = wx.MenuBar()

        # File menu
        file_menu = wx.Menu()
        quit_item = file_menu.Append(wx.ID_EXIT, "Quit", "Quit the application")
        self.Bind(wx.EVT_MENU, self.on_close, quit_item)
        menubar.Append(file_menu, "File")

        # Extras menu
        extras_menu = wx.Menu()
        self.perf_monitor_item = extras_menu.Append(wx.ID_ANY, "Performance Monitor", "Monitor client's performance")
        self.Bind(wx.EVT_MENU, self.launch_perf_monitor, self.perf_monitor_item)
        menubar.Append(extras_menu, "Extras")

        self.SetMenuBar(menubar)

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

        # Create Summary tab elements but don't add them to sizer yet
        self.lbl_username = wx.StaticText(self.summary_tab, label="Username: ")
        self.lbl_xrp_balance = wx.StaticText(self.summary_tab, label="XRP Balance: ")
        self.lbl_pft_balance = wx.StaticText(self.summary_tab, label="PFT Balance: ")
        self.lbl_address = wx.StaticText(self.summary_tab, label="XRP Address: ")

        self.lbl_wallet_state = wx.StaticText(self.summary_tab, label="Wallet State: ")
        self.lbl_next_action = wx.StaticText(self.summary_tab, label="Next Action: ")
        self.btn_wallet_action = wx.Button(self.summary_tab, label="Take Action")
        self.btn_wallet_action.Bind(wx.EVT_BUTTON, self.on_take_action)

        font = self.lbl_next_action.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lbl_next_action.SetFont(font)
        self.btn_wallet_action.SetFont(font)

        # Add grid for Key Account Details
        self.summary_grid = self.setup_grid(gridlib.Grid(self.summary_tab), 'summary')

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
        self.button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_request_task = wx.Button(self.proposals_tab, label="Request Task")
        self.button_sizer.Add(self.btn_request_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_request_task.Bind(wx.EVT_BUTTON, self.on_request_task)

        self.btn_accept_task = wx.Button(self.proposals_tab, label="Accept Task")
        self.button_sizer.Add(self.btn_accept_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_accept_task.Bind(wx.EVT_BUTTON, self.on_accept_task)

        self.proposals_sizer.Add(self.button_sizer, 0, wx.EXPAND)

        self.button_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refuse_task = wx.Button(self.proposals_tab, label="Refuse Task")
        self.button_sizer2.Add(self.btn_refuse_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_refuse_task.Bind(wx.EVT_BUTTON, self.on_refuse_task)

        self.btn_submit_for_verification = wx.Button(self.proposals_tab, label="Submit for Verification")
        self.button_sizer2.Add(self.btn_submit_for_verification, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_for_verification.Bind(wx.EVT_BUTTON, self.on_submit_for_verification)

        self.proposals_sizer.Add(self.button_sizer2, 0, wx.EXPAND)

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
        self.lbl_task_id = wx.StaticText(self.verification_tab, label="Task ID:")
        self.verification_sizer.Add(self.lbl_task_id, flag=wx.ALL, border=5)
        self.txt_task_id = wx.TextCtrl(self.verification_tab)
        self.verification_sizer.Add(self.txt_task_id, flag=wx.EXPAND | wx.ALL, border=5)

        # Verification Details input box
        self.lbl_verification_details = wx.StaticText(self.verification_tab, label="Verification Details:")
        self.verification_sizer.Add(self.lbl_verification_details, flag=wx.ALL, border=5)
        self.txt_verification_details = wx.TextCtrl(self.verification_tab, style=wx.TE_MULTILINE, size=(-1, 100))
        self.verification_sizer.Add(self.txt_verification_details, flag=wx.EXPAND | wx.ALL, border=5)

        # Submit Verification Details and Log Pomodoro buttons
        self.button_sizer_verification = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_submit_verification_details = wx.Button(self.verification_tab, label="Submit Verification Details")
        self.button_sizer_verification.Add(self.btn_submit_verification_details, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_verification_details.Bind(wx.EVT_BUTTON, self.on_submit_verification_details)

        self.btn_log_pomodoro = wx.Button(self.verification_tab, label="Log Pomodoro")
        self.button_sizer_verification.Add(self.btn_log_pomodoro, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_log_pomodoro.Bind(wx.EVT_BUTTON, self.on_log_pomodoro)

        self.verification_sizer.Add(self.button_sizer_verification, 0, wx.EXPAND)

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

        self.payments_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.payments_tab, "Payments")
        self.payments_sizer = wx.BoxSizer(wx.VERTICAL)
        self.payments_tab.SetSizer(self.payments_sizer)

        # XRP Payment section
        self.lbl_xrp_payment = wx.StaticText(self.payments_tab, label="XRP Payments:")
        self.payments_sizer.Add(self.lbl_xrp_payment, flag=wx.ALL, border=5)

        self.lbl_xrp_amount = wx.StaticText(self.payments_tab, label="Amount of XRP:")
        self.payments_sizer.Add(self.lbl_xrp_amount, flag=wx.ALL, border=5)
        self.txt_xrp_amount = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_xrp_amount, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_xrp_address = wx.StaticText(self.payments_tab, label="Payment Address:")
        self.payments_sizer.Add(self.lbl_xrp_address, flag=wx.ALL, border=5)
        self.txt_xrp_address_payment = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_xrp_address_payment, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_xrp_memo = wx.StaticText(self.payments_tab, label="Memo (Optional):")
        self.payments_sizer.Add(self.lbl_xrp_memo, flag=wx.ALL, border=5)
        self.txt_xrp_memo = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_xrp_memo, flag=wx.EXPAND | wx.ALL, border=5)

        self.btn_submit_xrp_payment = wx.Button(self.payments_tab, label="Submit Payment")
        self.payments_sizer.Add(self.btn_submit_xrp_payment, flag=wx.ALL, border=5)
        self.btn_submit_xrp_payment.Bind(wx.EVT_BUTTON, self.on_submit_xrp_payment)

        # PFT Payment section
        self.lbl_pft_payment = wx.StaticText(self.payments_tab, label="PFT Payments:")
        self.payments_sizer.Add(self.lbl_pft_payment, flag=wx.ALL, border=5)

        self.lbl_pft_amount = wx.StaticText(self.payments_tab, label="Amount of PFT:")
        self.payments_sizer.Add(self.lbl_pft_amount, flag=wx.ALL, border=5)
        self.txt_pft_amount = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_pft_amount, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_pft_address = wx.StaticText(self.payments_tab, label="Payment Address:")
        self.payments_sizer.Add(self.lbl_pft_address, flag=wx.ALL, border=5)
        self.txt_pft_address_payment = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_pft_address_payment, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_pft_memo = wx.StaticText(self.payments_tab, label="Memo (Optional):")
        self.payments_sizer.Add(self.lbl_pft_memo, flag=wx.ALL, border=5)
        self.txt_pft_memo = wx.TextCtrl(self.payments_tab)
        self.payments_sizer.Add(self.txt_pft_memo, flag=wx.EXPAND | wx.ALL, border=5)

        self.btn_submit_pft_payment = wx.Button(self.payments_tab, label="Submit Payment")
        self.payments_sizer.Add(self.btn_submit_pft_payment, flag=wx.ALL, border=5)
        self.btn_submit_pft_payment.Bind(wx.EVT_BUTTON, self.on_submit_pft_payment)

        # # Add "Show Secret" button
        # self.btn_show_secret = wx.Button(self.payments_tab, label="Show Secret")
        # self.payments_sizer.Add(self.btn_show_secret, flag=wx.ALL, border=5)
        # self.btn_show_secret.Bind(wx.EVT_BUTTON, self.on_show_secret)

        self.panel.SetSizer(self.sizer)

        # Store reference to payments tab page
        self.tab_pages["Payments"] = self.payments_tab

        #################################
        # MEMOS
        #################################

        self.memos_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.memos_tab, "Memos")
        self.memos_sizer = wx.BoxSizer(wx.VERTICAL)
        self.memos_tab.SetSizer(self.memos_sizer)

        # Add memo input box
        self.lbl_memo = wx.StaticText(self.memos_tab, label="Enter your memo:")
        self.memos_sizer.Add(self.lbl_memo, 0, wx.EXPAND | wx.ALL, border=5)
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
        # sizer.Add(logo_ctrl, 0, wx.ALIGN_CENTER | wx.TOP, 20)

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
        self.lbl_user = wx.StaticText(box, label="Username:")
        box_sizer.Add(self.lbl_user, flag=wx.ALL, border=5)

        # Create combobox for username dropdown
        self.txt_user = wx.ComboBox(box, style=wx.CB_DROPDOWN)
        box_sizer.Add(self.txt_user, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        # Password
        self.lbl_pass = wx.StaticText(box, label="Password:")
        box_sizer.Add(self.lbl_pass, flag=wx.ALL, border=5)
        self.txt_pass = wx.TextCtrl(box, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        box_sizer.Add(self.txt_pass, flag=wx.EXPAND | wx.ALL, border=5)

        # Error label
        self.error_label = wx.StaticText(box, label="")
        self.error_label.SetForegroundColour(wx.RED)
        box_sizer.Add(self.error_label, flag=wx.EXPAND |wx.ALL, border=5)
        # self.error_label.Hide()

        # Login button
        self.btn_login = wx.Button(box, label="Login")
        box_sizer.Add(self.btn_login, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_login.Bind(wx.EVT_BUTTON, self.on_login)

        # Create New User button
        self.btn_new_user = wx.Button(box, label="Create New User")
        box_sizer.Add(self.btn_new_user, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_new_user.Bind(wx.EVT_BUTTON, self.on_create_new_user)
        # box_sizer.Add(wx.StaticLine(box), 0, wx.EXPAND | wx.TOP, 5)

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
        self.txt_user.Bind(wx.EVT_COMBOBOX_DROPDOWN, self.on_dropdown_opened)
        self.txt_user.Bind(wx.EVT_COMBOBOX, self.on_username_selected)
        self.txt_user.Bind(wx.EVT_TEXT, self.on_clear_error)
        self.txt_pass.Bind(wx.EVT_TEXT, self.on_clear_error)

        # Add Enter key bindings
        self.txt_user.Bind(wx.EVT_TEXT_ENTER, self.on_login)
        self.txt_pass.Bind(wx.EVT_TEXT_ENTER, self.on_login)

        self.populate_username_dropdown()

        return panel
    
    def populate_username_dropdown(self):
        """Populates the username dropdown with cached usernames"""
        try:
            current_value = self.txt_user.GetValue()
            cached_usernames = get_cached_usernames()
            self.txt_user.Clear()
            self.txt_user.AppendItems(cached_usernames)

            if current_value and current_value in cached_usernames:
                self.txt_user.SetValue(current_value)
            elif cached_usernames:
                self.txt_user.SetValue(cached_usernames[0])
        except Exception as e:
            logger.error(f"Error populating username dropdown: {e}")
            self.show_error("Error loading cached usernames")

    def on_dropdown_opened(self, event):
        """Handle dropdown being opened"""
        self.populate_username_dropdown()
        event.Skip()

    def on_username_selected(self, event):
        """Handle username selection from dropdown"""
        self.txt_pass.SetFocus()
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
        self.lbl_xrp_address = wx.StaticText(panel, label="XRP Address:")
        user_details_sizer.Add(self.lbl_xrp_address, flag=wx.ALL, border=5)
        self.txt_xrp_address = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_xrp_address, flag=wx.EXPAND | wx.ALL, border=5)

        # XRP Secret
        secret_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_xrp_secret = wx.StaticText(panel, label="XRP Secret:")
        user_details_sizer.Add(self.lbl_xrp_secret, flag=wx.ALL, border=5)
        self.txt_xrp_secret = wx.TextCtrl(panel, style=wx.TE_PASSWORD)  # TODO: make a checkbox to show/hide the secret
        secret_sizer.Add(self.txt_xrp_secret, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
        self.chk_show_secret = wx.CheckBox(panel, label="Show Secret")
        secret_sizer.Add(self.chk_show_secret, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        user_details_sizer.Add(secret_sizer, flag=wx.EXPAND)

        self.chk_show_secret.Bind(wx.EVT_CHECKBOX, self.on_toggle_secret_visibility_user_details)

        # Username
        self.lbl_username = wx.StaticText(panel, label="Username:")
        user_details_sizer.Add(self.lbl_username, flag=wx.ALL, border=5)
        self.txt_username = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        user_details_sizer.Add(self.txt_username, flag=wx.EXPAND | wx.ALL, border=5)

        # Bind event to force lowercase
        self.txt_username.Bind(wx.EVT_TEXT, self.on_force_lowercase)

        # Password
        self.lbl_password = wx.StaticText(panel, label="Password:")
        user_details_sizer.Add(self.lbl_password, flag=wx.ALL, border=5)
        self.txt_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        user_details_sizer.Add(self.txt_password, flag=wx.EXPAND | wx.ALL, border=5)

        # Confirm Password
        self.lbl_confirm_password = wx.StaticText(panel, label="Confirm Password:")
        user_details_sizer.Add(self.lbl_confirm_password, flag=wx.ALL, border=5)
        self.txt_confirm_password = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        user_details_sizer.Add(self.txt_confirm_password, flag=wx.EXPAND | wx.ALL, border=5)

        # Tooltips
        self.tooltip_xrp_address = wx.ToolTip("This is your XRP address. It is used to receive XRP or PFT.")
        self.tooltip_xrp_secret = wx.ToolTip("This is your XRP secret. NEVER SHARE THIS SECRET WITH ANYONE! NEVER LOSE THIS SECRET!")
        self.tooltip_username = wx.ToolTip("Set a username that you will use to log in with. You can use lowercase letters, numbers, and underscores.")
        self.tooltip_password = wx.ToolTip("Set a password that you will use to log in with. This password is used to encrypt your XRP address and secret.")
        self.tooltip_confirm_password = wx.ToolTip("Confirm your password.")
        # self.tooltip_google_doc = wx.ToolTip("This is the link to your Google Doc. 1) It must be a shareable link. 2) The first line of the document must be your XRP address.")
        self.txt_xrp_address.SetToolTip(self.tooltip_xrp_address)
        self.txt_xrp_secret.SetToolTip(self.tooltip_xrp_secret)
        self.txt_username.SetToolTip(self.tooltip_username)
        self.txt_password.SetToolTip(self.tooltip_password)
        self.txt_confirm_password.SetToolTip(self.tooltip_confirm_password)
        # self.txt_google_doc.SetToolTip(self.tooltip_google_doc)

        # Buttons
        wallet_buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_generate_wallet = wx.Button(panel, label="Generate New XRP Wallet")
        wallet_buttons_sizer.Add(self.btn_generate_wallet, 1, flag=wx.EXPAND | wx.RIGHT, border=5)
        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)

        self.btn_restore_wallet = wx.Button(panel, label="Restore from Seed")
        wallet_buttons_sizer.Add(self.btn_restore_wallet, 1, flag=wx.EXPAND | wx.LEFT, border=5)
        self.btn_restore_wallet.Bind(wx.EVT_BUTTON, self.on_restore_wallet)

        user_details_sizer.Add(wallet_buttons_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        self.btn_existing_user = wx.Button(panel, label="Cache Credentials")
        user_details_sizer.Add(self.btn_existing_user, flag=wx.EXPAND, border=15)
        self.btn_existing_user.Bind(wx.EVT_BUTTON, self.on_cache_user)

        sizer.Add(user_details_sizer, 1, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)

        return panel
    
    def on_force_lowercase(self, event):
        value = self.txt_username.GetValue()
        lowercase_value = value.lower()
        if value != lowercase_value:
            self.txt_username.SetValue(lowercase_value)
            self.txt_username.SetInsertionPointEnd()
    
    def on_toggle_secret_visibility_user_details(self, event):
        if self.chk_show_secret.IsChecked():
            self.txt_xrp_secret.SetWindowStyle(wx.TE_PROCESS_ENTER)  # Default style
        else:
            self.txt_xrp_secret.SetWindowStyle(wx.TE_PASSWORD)

        # Store the current value and cursor position
        current_value = self.txt_xrp_secret.GetValue()

        # Recreate the text control with the new style
        new_txt_xrp_secret = wx.TextCtrl(self.txt_xrp_secret.GetParent(), 
                                        value=current_value,
                                        style=self.txt_xrp_secret.GetWindowStyle())
        
        # Replace the old control with the new one in the sizer
        self.txt_xrp_secret.GetContainingSizer().Replace(self.txt_xrp_secret, new_txt_xrp_secret)
        self.txt_xrp_secret.Destroy()
        self.txt_xrp_secret = new_txt_xrp_secret

        # Refresh the layout
        self.txt_xrp_secret.GetParent().Layout()

    def on_generate_wallet(self, event):
        # Generate a new XRP wallet
        self.wallet = Wallet.create()
        self.txt_xrp_address.SetValue(self.wallet.classic_address)
        self.txt_xrp_secret.SetValue(self.wallet.seed)

    def on_restore_wallet(self, event):
        """Restore wallet from existing seed"""
        dialog = CustomDialog("Restore Wallet", ["XRP Secret"])
        if dialog.ShowModal() == wx.ID_OK:
            seed = dialog.GetValues()["XRP Secret"]
            try:
                # Attempt to create wallet from seed
                wallet = Wallet.from_seed(seed)

                # Update the UI with the restored wallet details
                self.txt_xrp_address.SetValue(wallet.classic_address)
                self.txt_xrp_secret.SetValue(wallet.seed)

                wx.MessageBox("Wallet restored successfully!", "Success", wx.OK | wx.ICON_INFORMATION)

            except Exception as e:
                logger.error(f"Error restoring wallet: {e}")
                wx.MessageBox("Invalid seed format. Please check your seed and try again.", "Error", wx.OK | wx.ICON_ERROR)

        dialog.Destroy()

    def on_cache_user(self, event):
        #TODO: Phase out this method in favor of automatic caching on genesis
        logger.debug("User clicked Cache Credentials button")
        """Caches the user's credentials"""
        input_map = {
            'Username_Input': self.txt_username.GetValue(),
            'Password_Input': self.txt_password.GetValue(),
            'XRP Address_Input': self.txt_xrp_address.GetValue(),
            'XRP Secret_Input': self.txt_xrp_secret.GetValue(),
            'Confirm Password_Input': self.txt_confirm_password.GetValue(),
        }

        if self.txt_password.GetValue() != self.txt_confirm_password.GetValue():
            logger.error("Passwords Do Not Match! Please Retry.")
            wx.MessageBox('Passwords Do Not Match! Please Retry.', 'Error', wx.OK | wx.ICON_ERROR)
        elif any(not value for value in input_map.values()):
            logger.error("All fields are required for caching!")
            wx.MessageBox('All fields are required for caching!', 'Error', wx.OK | wx.ICON_ERROR)
        else:
            wallet_functions = WalletInitiationFunctions(input_map, self.network_url)
            try:
                response = wallet_functions.cache_credentials(input_map)
                wx.MessageBox(response, 'Info', wx.OK | wx.ICON_INFORMATION)
            except Exception as e:
                logger.error(f"{e}")
                # TODO: This check was inserted for instances when Google Docs were still being checked and were invalid
                # TODO: Since google docs were removed, this check is probably no longer needed
                if wx.YES == wx.MessageBox(f"{e}. \n\nContinue caching anyway?", 'Error', wx.YES_NO | wx.ICON_ERROR):
                    try:
                        logger.debug("Attempting to cache credentials despite error")
                        response = wallet_functions.cache_credentials(input_map)
                    except Exception as e:
                        logger.error(f"Error caching credentials: {e}")
                        wx.MessageBox(f"Error caching credentials: {e}", 'Error', wx.OK | wx.ICON_ERROR)
                    else:
                        wx.MessageBox(response, 'Info', wx.OK | wx.ICON_INFORMATION)

    def on_login(self, event):
        self.btn_login.SetLabel("Logging in...")
        self.btn_login.Update()

        username = self.txt_user.GetValue()
        password = self.txt_pass.GetValue()

        try:
            self.task_manager = PostFiatTaskManager(
                username=username, 
                password=password,
                network_url=self.network_url
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
        
        self.wallet = self.task_manager.user_wallet
        classic_address = self.wallet.classic_address

        self.update_ui_based_on_wallet_state()

        logger.info(f"Logged in as {username}")

        # Hide login panel and show tabs
        self.login_panel.Hide()
        self.tabs.Show()

        # TODO: rename method to better reflect its function
        self.populate_summary_tab(username, classic_address)

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
        if hasattr(self, 'lbl_wallet_state'):
            self.lbl_wallet_state.SetLabel(f"Wallet State: {current_state.value}")

        if current_state == WalletState.ACTIVE:
            if hasattr(self, 'lbl_next_action'):
                self.lbl_next_action.Hide()
            if hasattr(self, 'btn_wallet_action'):
                self.btn_wallet_action.Hide()
        else:
            if hasattr(self, 'lbl_next_action'):
                self.lbl_next_action.Show()
                self.lbl_next_action.SetLabel(f"Next Action: {self.task_manager.get_required_action()}")
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
        self.error_label.SetLabel(message)

        # Simple shake animation
        original_pos = self.error_label.GetPosition()
        for i in range(5):
            self.error_label.Move(original_pos.x + 2, original_pos.y)
            wx.MilliSleep(40)
            self.error_label.Move(original_pos.x - 2, original_pos.y)
            wx.MilliSleep(40)
        self.error_label.Move(original_pos)

        self.login_panel.Layout()

    def on_clear_error(self, event):
        self.error_label.SetLabel("")
        event.Skip()

    # TODO: rename method to better reflect its function
    def populate_summary_tab(self, username, classic_address):
        # Clear existing content
        self.summary_sizer.Clear(True)

        # Create a horizontal box sizer for the username and wallet state
        username_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        username_row_sizer.Add(self.lbl_username, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        username_row_sizer.AddStretchSpacer()
        username_row_sizer.Add(self.lbl_wallet_state, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(username_row_sizer, 0, wx.EXPAND)

        # Create a horizontal sizer for the xrp balance and next action
        xrp_balance_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        xrp_balance_row_sizer.Add(self.lbl_xrp_balance, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        xrp_balance_row_sizer.AddStretchSpacer()
        xrp_balance_row_sizer.Add(self.lbl_next_action, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(xrp_balance_row_sizer, 0, wx.EXPAND)

        # Create a horizontal sizer for the PFT balance and take action button
        pft_balance_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pft_balance_row_sizer.Add(self.lbl_pft_balance, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        pft_balance_row_sizer.AddStretchSpacer()
        pft_balance_row_sizer.Add(self.btn_wallet_action, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(pft_balance_row_sizer, 0, wx.EXPAND)

        # Create a horizontal sizer for the address and show secret button
        address_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        address_row_sizer.Add(self.lbl_address, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        address_row_sizer.AddStretchSpacer()
        self.btn_show_secret = wx.Button(self.summary_tab, label="Show Secret")
        self.btn_show_secret.Bind(wx.EVT_BUTTON, self.on_show_secret)
        address_row_sizer.Add(self.btn_show_secret, 0, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        self.summary_sizer.Add(address_row_sizer, 0, wx.EXPAND)

        # Create a heading for Key Account Details
        lbl_key_details = wx.StaticText(self.summary_tab, label="Key Account Details:")
        self.summary_sizer.Add(lbl_key_details, flag=wx.ALL, border=5)

        self.summary_sizer.Add(self.summary_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Update labels
        self.lbl_username.SetLabel(f"Username: {username}")
        self.lbl_address.SetLabel(f"XRP Address: {classic_address}")

        # Update account info
        self.update_account_info()

    @PerformanceMonitor.measure('update_account_info')
    def update_account_info(self):
        if self.task_manager:
            xrp_balance = self.task_manager.get_xrp_balance()
            logger.debug(f"XRP Balance: {xrp_balance}")
            xrp_balance = xrpl.utils.drops_to_xrp(str(xrp_balance))
            self.lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

            # PFT balance update (placeholder, as it's not streamed)
            self.lbl_pft_balance.SetLabel(f"PFT Balance: Updating...")

            if hasattr(self.task_manager, 'wallet_state'):
                self.lbl_wallet_state.SetLabel(f"Wallet State: {self.task_manager.wallet_state.value}")
                self.lbl_next_action.SetLabel(f"Next Action: {self.task_manager.get_required_action()}")

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
        self.lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

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
                if line['currency'] == 'PFT' and line['account'] == ISSUER_ADDRESS:
                    pft_balance = float(line['balance'])
                    logger.debug(f"Found PFT balance: {pft_balance}")

            self.lbl_pft_balance.SetLabel(f"PFT Balance: {pft_balance}")

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
            if self.task_manager.sync_transactions():
                logger.debug("New transactions found, updating grids")
                self.refresh_grids()
            else:
                logger.debug("No new transactions found, skipping grid updates.")
        except Exception as e:
            logger.error(f"Error during sync and refresh cycle: {e}")
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

        # Memos grid (available in TRUSTLINED_STATES)
        if current_state in TRUSTLINED_STATES:
            try:
                memos_df = self.task_manager.get_memos_df()
                wx.PostEvent(self, UpdateGridEvent(data=memos_df, target="memos", caller=f"{self.__class__.__name__}.refresh_grids"))
            except Exception as e:
                logger.error(f"Failed updating memos grid: {e}")

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
        caller = getattr(event, 'caller', 'Unknown')
        logger.debug(f"Grid update triggered by {caller} for target: {getattr(event, 'target', 'Unknown')}")
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
            case "memos":
                self.populate_grid_generic(self.memos_grid, event.data, 'memos')
            case "summary":
                self.populate_summary_grid(event.data)
            case _:
                logger.error(f"Unknown grid target: {event.target}")

        self.auto_size_window()

    @PerformanceMonitor.measure('on_pft_update_timer')
    def on_pft_update_timer(self, event):
        if self.wallet:
            self.update_tokens(self.wallet.classic_address)

    @PerformanceMonitor.measure('populate_grid_generic')
    def populate_grid_generic(self, grid: wx.grid.Grid, data: pd.DataFrame, grid_name: str):
        """Generic grid population method that respects zoom settings"""
        # frame = inspect.currentframe()
        # caller_frame = frame.f_back
        # while caller_frame.f_code.co_name == "wrapper":
        #     caller_frame = caller_frame.f_back
        # caller = caller_frame.f_code.co_name
        # logger.debug(f"populate_grid_generic called from {caller} for grid {grid_name}", stack_info=True)

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
        self.btn_request_task.SetLabel("Requesting Task...")
        self.btn_request_task.Update()

        dialog = CustomDialog("Request Task", ["Task Request"])
        if dialog.ShowModal() == wx.ID_OK:
            request_message = dialog.GetValues()["Task Request"]
            response = self.task_manager.request_post_fiat(request_message=request_message)
            try:
                if response:
                    message = self.task_manager.ux__convert_response_object_to_status_message(response)
                    wx.MessageBox(message, 'Task Request Result', wx.OK | wx.ICON_INFORMATION)
            except Exception as e:
                logger.error(f"Error converting response to status message: {e}")
            wx.CallLater(30000, self.refresh_grids, None)
        dialog.Destroy()

        self.btn_request_task.SetLabel("Request Task")
        self.btn_request_task.Update()

    def on_accept_task(self, event):
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
                try:
                    if response:
                        message = self.task_manager.ux__convert_response_object_to_status_message(response)
                        wx.MessageBox(message, 'Task Acceptance Result', wx.OK | wx.ICON_INFORMATION)
                except Exception as e:
                    logger.error(f"Error converting response to status message: {e}")
                wx.CallLater(5000, self.refresh_grids, None)
        dialog.Destroy()

        self.btn_accept_task.SetLabel("Accept Task")
        self.btn_accept_task.Update()

    def on_refuse_task(self, event):
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
            except Exception as e:
                logger.error(f"Error sending refusal for task: {e}")
                wx.MessageBox(f"Error sending refusal for task: {e}", 'Task Refusal Error', wx.OK | wx.ICON_ERROR)
            else:
                try:
                    if response:
                        message = self.task_manager.ux__convert_response_object_to_status_message(response)
                        wx.MessageBox(message, 'Task Refusal Result', wx.OK | wx.ICON_INFORMATION)
                    else:
                        logger.error("No response from send_refusal_for_task")
                except Exception as e:
                    logger.error(f"Error converting response to status message: {e}")
                wx.CallLater(5000, self.refresh_grids, None)
        dialog.Destroy()

        self.btn_refuse_task.SetLabel("Refuse Task")
        self.btn_refuse_task.Update()

    def on_submit_for_verification(self, event):
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
                try:
                    if response:
                        message = self.task_manager.ux__convert_response_object_to_status_message(response)
                        wx.MessageBox(message, 'Task Submission Result', wx.OK | wx.ICON_INFORMATION)
                    else:
                        logger.error("No response from submit_initial_completion")
                except Exception as e:
                    logger.error(f"Error converting response to status message: {e}")
                wx.CallLater(5000, self.refresh_grids, None)
            
        dialog.Destroy()

        self.btn_submit_for_verification.SetLabel("Submit for Verification")
        self.btn_submit_for_verification.Update()

    def on_submit_verification_details(self, event):
        self.btn_submit_verification_details.SetLabel("Submitting Verification Details...")
        self.btn_submit_verification_details.Update()

        task_id = self.txt_task_id.GetValue()
        response_string = self.txt_verification_details.GetValue()

        if not task_id or not response_string:
            wx.MessageBox("Please enter a task ID and verification details", "Error", wx.OK | wx.ICON_ERROR)
        else:
            try:
                response = self.task_manager.send_verification_response(
                    response_string=response_string,
                    task_id=task_id
                )
            except Exception as e:
                logger.error(f"Error sending verification response: {e}")
                wx.MessageBox(f"Error sending verification response: {e}", 'Verification Submission Error', wx.OK | wx.ICON_ERROR)
            else:
                try:
                    if response:
                        message = self.task_manager.ux__convert_response_object_to_status_message(response)
                        wx.MessageBox(message, 'Verification Submission Result', wx.OK | wx.ICON_INFORMATION)
                    else:
                            logger.error("No response from send_verification_response")
                except Exception as e:
                    logger.error(f"Error converting response to status message: {e}")

        self.txt_verification_details.SetValue("")
        self.btn_submit_verification_details.SetLabel("Submit Verification Details")
        self.btn_submit_verification_details.Update()

    def on_log_pomodoro(self, event):
        self.btn_log_pomodoro.SetLabel("Logging Pomodoro...")
        self.btn_log_pomodoro.Update()

        task_id = self.txt_task_id.GetValue()
        pomodoro_text = self.txt_verification_details.GetValue()

        if not task_id or not pomodoro_text:
            wx.MessageBox("Please enter a task ID and pomodoro text", "Error", wx.OK | wx.ICON_ERROR)
        else:
            response = self.task_manager.send_pomodoro_for_task_id(task_id=task_id, pomodoro_text=pomodoro_text)
            message = self.task_manager.ux__convert_response_object_to_status_message(response)
            wx.MessageBox(message, 'Pomodoro Log Result', wx.OK | wx.ICON_INFORMATION)

        self.txt_verification_details.SetValue("")
        self.btn_log_pomodoro.SetLabel("Log Pomodoro")
        self.btn_log_pomodoro.Update()

    def on_submit_memo(self, event):
        """Submits a memo to the remembrancer."""
        self.btn_submit_memo.SetLabel("Submitting...")
        self.btn_submit_memo.Update()

        logger.info("Submitting Memo")

        memo_text = self.txt_memo_input.GetValue()

        if not memo_text:
            wx.MessageBox("Please enter a memo", "Error", wx.OK | wx.ICON_ERROR)
        else:
            logger.info(f"Memo Text: {memo_text}")

            # Estimate chunks needed with compression
            compressed_text = compress_string(memo_text)
            compressed_bytes = compressed_text.encode('utf-8')
            num_chunks = len(compressed_bytes) // MAX_CHUNK_SIZE
            if len(compressed_bytes) % MAX_CHUNK_SIZE != 0:
                num_chunks += 1

            # Calculate uncompressed chunks for comparison
            uncompressed_bytes = memo_text.encode('utf-8')
            uncompressed_chunks = len(uncompressed_bytes) // MAX_CHUNK_SIZE
            if len(uncompressed_bytes) % MAX_CHUNK_SIZE != 0:
                uncompressed_chunks += 1

            if num_chunks > 1:
                message = (
                    f"Memo will be compressed and sent over {num_chunks} transactions "
                    f"(reduced from {uncompressed_chunks} without compression) and "
                    f"cost 1 PFT per chunk ({num_chunks} PFT total). Continue?"
                )
                if wx.NO == wx.MessageBox(message, "Confirmation", wx.YES_NO | wx.ICON_QUESTION):
                    self.btn_submit_memo.SetLabel("Submit Memo")
                    return
                    
            logger.info("User confirmed, submitting memo")

            try:
                responses = self.task_manager.send_memo(REMEMBRANCER_ADDRESS, memo_text)
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
            
        self.btn_submit_memo.SetLabel("Submit Memo")
        self.txt_memo_input.SetValue("")

    def on_submit_xrp_payment(self, event):
        self.btn_submit_xrp_payment.SetLabel("Submitting...")
        self.btn_submit_xrp_payment.Update()

        # Check that Amount and Destination are valid
        if not self.txt_xrp_amount.GetValue() or not self.txt_xrp_address_payment.GetValue():
            wx.MessageBox("Please enter a valid amount and destination", "Error", wx.OK | wx.ICON_ERROR)
        else:
            response = self.task_manager.send_xrp(amount=self.txt_xrp_amount.GetValue(), 
                                                            destination=self.txt_xrp_address_payment.GetValue(), 
                                                            memo=self.txt_xrp_memo.GetValue()
            )
            logger.debug(f"response: {response}")
            formatted_response = self.format_response(response)

            logger.info(f"XRP Payment Result: {formatted_response}")

            dialog = SelectableMessageDialog(self, "XRP Payment Result", formatted_response)
            dialog.ShowModal()
            dialog.Destroy()

        self.btn_submit_xrp_payment.SetLabel("Submit Payment")
        self.btn_submit_xrp_payment.Update()

    def on_submit_pft_payment(self, event):
        self.btn_submit_pft_payment.SetLabel("Submitting...")
        self.btn_submit_pft_payment.Update()

        # Check that Amount and Destination are valid
        if not self.txt_pft_amount.GetValue() or not self.txt_pft_address_payment.GetValue():
            wx.MessageBox("Please enter a valid amount and destination", "Error", wx.OK | wx.ICON_ERROR)
        else:
            if is_over_1kb(self.txt_pft_memo.GetValue()):
                memo_chunks = self.task_manager._get_memo_chunks(self.txt_pft_memo.GetValue())
                message = f"Memo is over 1 KB, transaction will be batch-sent over {len(memo_chunks)} transactions. Continue?"
                if wx.YES == wx.MessageBox(message, "Confirmation", wx.YES_NO | wx.ICON_QUESTION):
                    pass
                else:
                    self.btn_submit_pft_payment.SetLabel("Submit Payment")
                    return

            response = self.task_manager.send_pft(amount=self.txt_pft_amount.GetValue(), 
                                                    destination=self.txt_pft_address_payment.GetValue(), 
                                                    memo=self.txt_pft_memo.GetValue()
            )
            formatted_response = self.format_response(response)

            logger.info(f"PFT Payment Result: {formatted_response}")

            dialog = SelectableMessageDialog(self, "PFT Payment Result", formatted_response)
            dialog.ShowModal()
            dialog.Destroy()

        self.btn_submit_pft_payment.SetLabel("Submit Payment")
        self.btn_submit_pft_payment.Update()

    def on_show_secret(self, event):
        self.btn_show_secret.SetLabel("Showing...")
        self.btn_show_secret.Update()

        dialog = wx.PasswordEntryDialog(self, "Enter Password", "Please enter your password to view your seed.")

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

        self.btn_show_secret.SetLabel("Show Secret")
        self.btn_show_secret.Update()

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
    
    def launch_perf_monitor(self, event):
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

def main():
    logger.info("Starting Post Fiat Wallet")
    app = PostFiatWalletApp()
    app.MainLoop()

if __name__ == "__main__":
    main()
