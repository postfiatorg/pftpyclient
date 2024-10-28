import time
import wx
import wx.adv
import wx.grid as gridlib
import wx.html
import wx.lib.scrolledpanel as scrolled
import xrpl
from xrpl.wallet import Wallet
import asyncio
from threading import Thread
import wx.lib.newevent
import nest_asyncio
from pftpyclient.task_manager.basic_tasks import GoogleDocNotFoundException, InvalidGoogleDocException, PostFiatTaskManager, WalletInitiationFunctions, NoMatchingTaskException, WrongTaskStateException, is_over_1kb
from pftpyclient.user_login.credential_input import cache_credentials, get_credential_file_path
import webbrowser
import os
from pftpyclient.basic_utilities.configure_logger import configure_logger, update_wx_sink
from loguru import logger
from pathlib import Path
from cryptography.fernet import InvalidToken
import pandas as pd

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
        self.nodes = MAINNET_WEBSOCKETS
        self.current_node_index = 0
        self.url = self.nodes[self.current_node_index]
        self.loop = asyncio.new_event_loop()
        self.context = None

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
        try:
            async with xrpl.asyncio.clients.AsyncWebsocketClient(self.url) as self.client:
                try: 
                    await asyncio.wait_for(self.on_connected(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning(f"Node {self.url} timed out. Switching to next node.")
                    self.switch_node()
                    return

                async for message in self.client:
                    mtype = message.get("type")
                    if mtype == "ledgerClosed":
                        wx.CallAfter(self.gui.update_ledger, message)
                    elif mtype == "transaction":
                        try: 
                            response = await asyncio.wait_for(
                                self.client.request(xrpl.models.requests.AccountInfo(
                                    account=self.account,
                                    ledger_index=message["ledger_index"]
                                )),
                                timeout=10
                            )
                            wx.CallAfter(self.gui.update_account, response.result["account_data"])
                            wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))                    
                        except asyncio.TimeoutError:
                            logger.warning(f"Request to {self.url} timed out. Switching to next node.")
                        except Exception as e:
                            logger.error(f"Error processing request: {e}")
        except Exception as e:
            logger.error(f"Error in watch_xrpl_account: {e}")

    async def on_connected(self):
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
            wx.CallAfter(self.gui.update_account, response.result["account_data"])
            wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))

class CustomDialog(wx.Dialog):
    def __init__(self, title, fields):
        super(CustomDialog, self).__init__(None, title=title, size=(400, 200))
        self.fields = fields
        self.InitUI()
        self.SetSize((400, 200))

    def InitUI(self):
        pnl = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        self.text_controls = {}
        for field in self.fields:
            hbox = wx.BoxSizer(wx.HORIZONTAL)
            label = wx.StaticText(pnl, label=field)
            hbox.Add(label, flag=wx.RIGHT, border=8)
            text_ctrl = wx.TextCtrl(pnl)
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

        self.submit_button.Bind(wx.EVT_BUTTON, self.OnSubmit)
        self.close_button.Bind(wx.EVT_BUTTON, self.OnClose)

    def OnSubmit(self, e):
        self.EndModal(wx.ID_OK)

    def OnClose(self, e):
        self.EndModal(wx.ID_CANCEL)

    def GetValues(self):
        return {field: text_ctrl.GetValue() for field, text_ctrl in self.text_controls.items()}

class WalletApp(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self, None, title="Post Fiat Client Wallet Beta v.0.1", size=(1150, 700))
        self.default_size = (1150, 700)
        self.min_size = (800, 600)
        self.max_size = (1600, 900)
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

    def setup_grid(self, grid, columns):
        grid.CreateGrid(0, len(columns))
        for idx, (name, width) in enumerate(columns):
            grid.SetColLabelValue(idx, name)
            grid.SetColSize(idx, width)
        return grid

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

        # Add grid for Key Account Details
        summary_columns = [("Key", 125), ("Value", 550)]
        self.summary_grid = self.setup_grid(gridlib.Grid(self.summary_tab), summary_columns)

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
        proposal_columns = [("Task ID", 200), ("Request", 250), ("Proposal", 300), ("Response", 150)]
        self.proposals_grid = self.setup_grid(gridlib.Grid(self.proposals_tab), proposal_columns)
        self.proposals_sizer.Add(self.proposals_grid, 1, wx.EXPAND | wx.ALL, 20)

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
        verification_columns = [("Task ID", 190), ("Proposal", 300), ("Verification", 250)]
        self.verification_grid = self.setup_grid(gridlib.Grid(self.verification_tab), verification_columns)
        self.verification_sizer.Add(self.verification_grid, 1, wx.EXPAND | wx.ALL, 20)

        #################################
        # REWARDS
        #################################

        self.rewards_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.rewards_tab, "Rewards")
        self.rewards_sizer = wx.BoxSizer(wx.VERTICAL)
        self.rewards_tab.SetSizer(self.rewards_sizer)

        # Add grid to Rewards tab
        rewards_columns = [("Task ID", 170), ("Proposal", 300), ("Reward", 250), ("Payout", 75)]
        self.rewards_grid = self.setup_grid(gridlib.Grid(self.rewards_tab), rewards_columns)
        self.rewards_sizer.Add(self.rewards_grid, 1, wx.EXPAND | wx.ALL, 20)

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

        # Add "Show Secret" button
        self.btn_show_secret = wx.Button(self.payments_tab, label="Show Secret")
        self.payments_sizer.Add(self.btn_show_secret, flag=wx.ALL, border=5)
        self.btn_show_secret.Bind(wx.EVT_BUTTON, self.on_show_secret)

        self.sizer.Add(self.tabs, 1, wx.EXPAND)
        self.panel.SetSizer(self.sizer)

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
        box.SetBackgroundColour(wx.Colour(220, 220, 220))
        box_sizer = wx.BoxSizer(wx.VERTICAL)

        # Username
        self.lbl_user = wx.StaticText(box, label="Username:")
        box_sizer.Add(self.lbl_user, flag=wx.ALL, border=5)
        self.txt_user = wx.TextCtrl(box)
        box_sizer.Add(self.txt_user, flag=wx.EXPAND | wx.ALL, border=5)

        # Password
        self.lbl_pass = wx.StaticText(box, label="Password:")
        box_sizer.Add(self.lbl_pass, flag=wx.ALL, border=5)
        self.txt_pass = wx.TextCtrl(box, style=wx.TE_PASSWORD)
        box_sizer.Add(self.txt_pass, flag=wx.EXPAND | wx.ALL, border=5)

        # enter username and password for debug purposes
        # self.txt_user.SetValue('windowstestuser1')
        # self.txt_pass.SetValue('W2g@Y79KD52*fl')

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
        box_sizer.Add(wx.StaticLine(box), 0, wx.EXPAND | wx.TOP, 5)

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

        # Bind text events to clear error message
        self.txt_user.Bind(wx.EVT_TEXT, self.on_clear_error)
        self.txt_pass.Bind(wx.EVT_TEXT, self.on_clear_error)

        return panel
    
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

        # Google Doc Share Link
        self.lbl_google_doc = wx.StaticText(panel, label="Google Doc Share Link:")
        user_details_sizer.Add(self.lbl_google_doc, flag=wx.ALL, border=5)
        self.txt_google_doc = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_google_doc, flag=wx.EXPAND | wx.ALL, border=5)

        # Commitment
        self.lbl_commitment = wx.StaticText(panel, label="Please write 1 sentence committing to a long term objective of your choosing:")
        user_details_sizer.Add(self.lbl_commitment, flag=wx.ALL, border=5)
        self.txt_commitment = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_commitment, flag=wx.EXPAND | wx.ALL, border=5)

        # Info
        # TODO: Move where this is displayed to the login screen
        self.lbl_info = wx.StaticText(panel, label="Paste Your XRP Address in the first line of your Google Doc and make sure that anyone who has the link can view Before Genesis")
        user_details_sizer.Add(self.lbl_info, flag=wx.ALL, border=5)

        # Tooltips
        self.tooltip_xrp_address = wx.ToolTip("This is your XRP address. It is used to receive XRP or PFT.")
        self.tooltip_xrp_secret = wx.ToolTip("This is your XRP secret. NEVER SHARE THIS SECRET WITH ANYONE! NEVER LOSE THIS SECRET!")
        self.tooltip_username = wx.ToolTip("Set a username that you will use to log in with. You can use lowercase letters, numbers, and underscores.")
        self.tooltip_password = wx.ToolTip("Set a password that you will use to log in with. This password is used to encrypt your XRP address and secret.")
        self.tooltip_confirm_password = wx.ToolTip("Confirm your password.")
        self.tooltip_google_doc = wx.ToolTip("This is the link to your Google Doc. 1) It must be a shareable link. 2) The first line of the document must be your XRP address.")
        self.txt_xrp_address.SetToolTip(self.tooltip_xrp_address)
        self.txt_xrp_secret.SetToolTip(self.tooltip_xrp_secret)
        self.txt_username.SetToolTip(self.tooltip_username)
        self.txt_password.SetToolTip(self.tooltip_password)
        self.txt_confirm_password.SetToolTip(self.tooltip_confirm_password)
        self.txt_google_doc.SetToolTip(self.tooltip_google_doc)

        # Buttons
        self.btn_generate_wallet = wx.Button(panel, label="Generate New XRP Wallet")
        user_details_sizer.Add(self.btn_generate_wallet, flag=wx.ALL, border=5)
        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)

        self.btn_existing_user = wx.Button(panel, label="Cache Credentials")
        user_details_sizer.Add(self.btn_existing_user, flag=wx.ALL, border=5)
        self.btn_existing_user.Bind(wx.EVT_BUTTON, self.on_cache_user)

        self.btn_genesis = wx.Button(panel, label="Genesis")
        user_details_sizer.Add(self.btn_genesis, flag=wx.ALL, border=5)
        self.btn_genesis.Bind(wx.EVT_BUTTON, self.on_genesis)

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

    def on_genesis(self, event):
        # Gather input data
        input_map = {
            'Username_Input': self.txt_username.GetValue(),
            'Password_Input': self.txt_password.GetValue(),
            'Google Doc Share Link_Input': self.txt_google_doc.GetValue(),
            'XRP Address_Input': self.txt_xrp_address.GetValue(),
            'XRP Secret_Input': self.txt_xrp_secret.GetValue(),
        }
        commitment = self.txt_commitment.GetValue()  # Get the user commitment

        if self.txt_password.GetValue() != self.txt_confirm_password.GetValue():
            wx.MessageBox('Passwords Do Not Match! Please Retry.', 'Info', wx.OK | wx.ICON_INFORMATION)
        # if any of the fields are empty, show an error message 
        elif any(not value for value in input_map.values()) or commitment == "":
            wx.MessageBox('All fields are required for genesis!', 'Info', wx.OK | wx.ICON_INFORMATION)
        else:
            # initialize "pre-wallet" that helps with initiation
            wallet_functions = WalletInitiationFunctions(input_map, commitment)

            try:
                wallet_functions.check_if_google_doc_is_valid()
            except Exception as e:
                wx.MessageBox(f"{e}", 'Error', wx.OK | wx.ICON_ERROR)
            else:
                try:
                    response = wallet_functions.cache_credentials(input_map)
                except Exception as e:
                    wx.MessageBox(f"Error caching credentials: {e}", 'Error', wx.OK | wx.ICON_ERROR)
                else:
                    wx.MessageBox(response, 'Info', wx.OK | wx.ICON_INFORMATION)

                    # generate trust line to PFT token
                    wallet_functions.handle_trust_line()

                    # Call send_initiation_rite with the gathered data
                    response = wallet_functions.send_initiation_rite()

                    formatted_response = self.format_response(response)

                    logger.info(f"Genesis Result: {formatted_response}")

                    dialog = SelectableMessageDialog(self, "Genesis Result", formatted_response)
                    dialog.ShowModal()
                    dialog.Destroy()

                    wx.MessageBox("Genesis successful! Return to the login screen to and proceed to login.", 'Info', wx.OK | wx.ICON_INFORMATION)

    def on_cache_user(self, event):
        #TODO: Phase out this method in favor of automatic caching on genesis
        """Caches the user's credentials"""
        input_map = {
            'Username_Input': self.txt_username.GetValue(),
            'Password_Input': self.txt_password.GetValue(),
            'Google Doc Share Link_Input': self.txt_google_doc.GetValue(),
            'XRP Address_Input': self.txt_xrp_address.GetValue(),
            'XRP Secret_Input': self.txt_xrp_secret.GetValue(),
            'Confirm Password_Input': self.txt_confirm_password.GetValue(),
        }

        if self.txt_password.GetValue() != self.txt_confirm_password.GetValue():
            wx.MessageBox('Passwords Do Not Match! Please Retry.', 'Error', wx.OK | wx.ICON_ERROR)
        elif any(not value for value in input_map.values()):
            wx.MessageBox('All fields (except commitment) are required for caching!', 'Error', wx.OK | wx.ICON_ERROR)
        else:
            wallet_functions = WalletInitiationFunctions(input_map)
            try:
                wallet_functions.check_if_google_doc_is_valid()
            # Invalid Google Doc URL's are fatal, since they cannot be easily changed once cached
            except (InvalidGoogleDocException, GoogleDocNotFoundException) as e:
                wx.MessageBox(f"{e}", 'Error', wx.OK | wx.ICON_ERROR)
            # Other exceptions are non-fatal, since the user can make adjustments without modifying cached credentials
            except Exception as e:
                # Present the error message to the user, but allow them to continue caching credentials
                if wx.YES == wx.MessageBox(f"{e}. \n\nContinue caching anyway?", 'Error', wx.YES_NO | wx.ICON_ERROR):
                    try:
                        response = wallet_functions.cache_credentials(input_map)
                    except Exception as e:
                        wx.MessageBox(f"Error caching credentials: {e}", 'Error', wx.OK | wx.ICON_ERROR)
                    else:
                        wx.MessageBox(response, 'Info', wx.OK | wx.ICON_INFORMATION)

    def on_login(self, event):
        # change login button to "Logging in..."
        self.btn_login.SetLabel("Logging in...")
        self.btn_login.Update()

        username = self.txt_user.GetValue()
        password = self.txt_pass.GetValue()

        try:
            self.task_manager = PostFiatTaskManager(username=username, password=password)
        except (ValueError, InvalidToken, KeyError) as e:
            logger.error(f"Login failed: {e}")
            self.show_error("Invalid username or password")
            return
        except Exception as e:
            logger.error(f"Login failed: {e}")
            self.show_error(f"Login failed: {e}")
            return
        
        self.wallet = self.task_manager.user_wallet
        classic_address = self.wallet.classic_address

        logger.info(f"Logged in as {username}")

        # Hide login panel and show tabs
        self.login_panel.Hide()
        self.tabs.Show()

        self.populate_summary_tab(username, classic_address)

        # Update layout and ensure correct sizing
        self.panel.Layout()
        self.Layout()
        self.Fit()

        # Fetch and display key account details
        key_account_details = self.task_manager.process_account_info()

        self.populate_summary_grid(key_account_details)

        self.summary_tab.Layout()  # Update the layout

        self.worker = XRPLMonitorThread(self)
        self.worker.start()

        # Immediately populate the grid with current data
        self.update_data(None)

        # Start timers
        self.start_json_update_timer()
        self.start_force_update_timer()
        self.start_pft_update_timer()
        self.start_transaction_update_timer()

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
        # self.error_label.Show()

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
        # self.error_label.Hide()
        event.Skip()

    def populate_summary_tab(self, username, classic_address):
        # Clear existing content
        self.summary_sizer.Clear(True)

        # Add elements to sizer
        self.summary_sizer.Add(self.lbl_username, flag=wx.ALL, border=5)
        self.summary_sizer.Add(self.lbl_xrp_balance, flag=wx.ALL, border=5)
        self.summary_sizer.Add(self.lbl_pft_balance, flag=wx.ALL, border=5)
        self.summary_sizer.Add(self.lbl_address, flag=wx.ALL, border=5)

        # Create a heading for Key Account Details
        lbl_key_details = wx.StaticText(self.summary_tab, label="Key Account Details:")
        self.summary_sizer.Add(lbl_key_details, flag=wx.ALL, border=5)

        self.summary_sizer.Add(self.summary_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Update labels
        self.lbl_username.SetLabel(f"Username: {username}")
        self.lbl_address.SetLabel(f"XRP Address: {classic_address}")

        # Update account info
        self.update_account_info()

    def update_account_info(self):
        if self.task_manager:
            xrp_balance = str(xrpl.utils.drops_to_xrp(self.task_manager.get_xrp_balance()))
            self.lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

            # PFT balance update (placeholder, as it's not streamed)
            self.lbl_pft_balance.SetLabel(f"PFT Balance: Updating...")

    def run_bg_job(self, job):
        if self.worker.context:
            asyncio.run_coroutine_threadsafe(job, self.worker.loop)

    def update_ledger(self, message):
        pass  # Simplified for this version

    def update_account(self, acct):
        xrp_balance = str(xrpl.utils.drops_to_xrp(acct["Balance"]))
        self.lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

    def update_tokens(self, account_address):
        logger.debug(f"Fetching token balances for account: {account_address}")
        try:
            client = xrpl.clients.JsonRpcClient("https://s2.ripple.com:51234")
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
            issuer_address = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'
            for line in lines:
                logger.debug(f"Processing line: {line}")
                if line['currency'] == 'PFT' and line['account'] == issuer_address:
                    pft_balance = float(line['balance'])
                    logger.debug(f"Found PFT balance: {pft_balance}")

            self.lbl_pft_balance.SetLabel(f"PFT Balance: {pft_balance}")

        except Exception as e:
            logger.exception(f"Exception in update_tokens: {e}")

    def on_close(self, event):
        if self.worker:
            self.worker.loop.stop()
        self.Destroy()

    def start_json_update_timer(self):
        self.json_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.update_data, self.json_update_timer)
        self.json_update_timer.Start(60000)  # Update every 60 seconds

    def start_force_update_timer(self):
        self.force_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_force_update, self.force_update_timer)
        self.force_update_timer.Start(60000)  # Update every 60 seconds

    def start_pft_update_timer(self):
        self.pft_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_pft_update_timer, self.pft_update_timer)
        self.pft_update_timer.Start(60000)  # Update every 60 seconds (adjust as needed)

    def start_transaction_update_timer(self):
        self.tx_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_transaction_update_timer, self.tx_update_timer)
        self.tx_update_timer.Start(60000)  # Update every 60 seconds (adjust as needed)

    def on_transaction_update_timer(self, _):
        logger.debug("Transaction update timer triggered")
        self.task_manager.sync_transactions()

    def update_data(self, event):
        try:
            # Get proposals tab data
            proposals_df = self.task_manager.get_proposals_df()
            wx.PostEvent(self, UpdateGridEvent(data=proposals_df, target="proposals"))

            # Get Rewards tab data
            rewards_df = self.task_manager.get_rewards_df()
            wx.PostEvent(self, UpdateGridEvent(data=rewards_df, target="rewards"))

            # Get Verification tab data
            verification_df = self.task_manager.get_verification_df()
            wx.PostEvent(self, UpdateGridEvent(data=verification_df, target="verification"))

        except Exception as e:
            logger.exception(f"Error updating data: {e}")

    def update_grid(self, event):
        logger.debug(f"Updating grid with target: {getattr(event, 'target')}")
        if hasattr(event, 'target'):
            match event.target:
                case "rewards":
                    self.populate_grid_generic(self.rewards_grid, event.data, 'rewards')
                case "verification":
                    self.populate_grid_generic(self.verification_grid, event.data, 'verification')
                case "proposals":
                    self.populate_grid_generic(self.proposals_grid, event.data, 'proposals')
                case "summary":
                    self.populate_summary_grid(event.data)
                case _:
                    logger.error(f"Unknown grid target: {event.target}")

    def on_pft_update_timer(self, event):
        if self.wallet:
            self.update_tokens(self.wallet.classic_address)

    def populate_grid_generic(self, grid: wx.grid.Grid, data: pd.DataFrame, grid_name: str):
        """Generic grid population method that respects zoom settings"""

        logger.debug(f"Populating {grid_name} grid with {data.shape[0]} rows")

        COLUMN_MAPPINGS = {
            'proposals': ['task_id', 'request', 'proposal', 'response'],
            'rewards': ['task_id', 'proposal', 'reward', 'payout'],
            'verification': ['task_id', 'original_task', 'verification'],
            'summary': ['Key', 'Value']  # For our converted dictionary data
        }

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

        # Get the column mapping for this grid
        column_order = COLUMN_MAPPINGS.get(grid_name, [])
        if not column_order:
            logger.error(f"No column mapping found for {grid_name}")
            return

        # Populate data using the column mapping
        for idx in range(len(data)):
            for col, col_name in enumerate(column_order):
                if col_name in data.columns:
                    value = data.iloc[idx][col_name]
                    grid.SetCellValue(idx, col, str(value))
                    grid.SetCellRenderer(idx, col, gridlib.GridCellAutoWrapStringRenderer())
                else:
                    logger.error(f"Column {col_name} not found in data for {grid_name}")

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

        for col, original_width in enumerate(self.grid_column_widths[grid_name]):
            grid.SetColSize(col, int(original_width * self.zoom_factor))

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
                self.zoom_factor *= 1.1
            else:
                self.zoom_factor /= 1.1
            self.zoom_factor = max(0.5, min(self.zoom_factor, 3.0))
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
                    case _:
                        grid_name = None
                        logger.error(f"No grid name found for {window}")

                if grid_name and grid_name in self.grid_column_widths:
                    self.store_grid_dimensions(window, grid_name)
                    for col, original_size in enumerate(self.grid_column_widths[grid_name]):
                        window.SetColSize(col, int(original_size * self.zoom_factor))

            for child in window.GetChildren():
                set_font_recursive(child)

        set_font_recursive(self)

        # Refresh layout
        self.panel.Layout()
        self.tabs.Layout()
        for i in range(self.tabs.GetPageCount()):
            self.tabs.GetPage(i).Layout()

    def on_request_task(self, event):
        # Change button text to "Requesting Task..."
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
            wx.CallLater(30000, self.update_data, None)
        dialog.Destroy()

        # Change button text back to "Request Task"
        self.btn_request_task.SetLabel("Request Task")
        self.btn_request_task.Update()

    def on_accept_task(self, event):
        # Change button text to "Accepting Task..."
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
                wx.CallLater(5000, self.update_data, None)
        dialog.Destroy()

        # Change button text back to "Accept Task"
        self.btn_accept_task.SetLabel("Accept Task")
        self.btn_accept_task.Update()

    def on_refuse_task(self, event):
        # Change button text to "Refusing Task..."
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
                wx.CallLater(5000, self.update_data, None)
        dialog.Destroy()

        # Change button text back to "Refuse Task"
        self.btn_refuse_task.SetLabel("Refuse Task")
        self.btn_refuse_task.Update()

    def on_submit_for_verification(self, event):
        # Change button text to "Submitting for Verification..."
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
                wx.CallLater(5000, self.update_data, None)
            
        dialog.Destroy()

        # Change button text back to "Submit for Verification"
        self.btn_submit_for_verification.SetLabel("Submit for Verification")
        self.btn_submit_for_verification.Update()

    def on_submit_verification_details(self, event):
        # Change button text to "Submitting Verification Details..."
        self.btn_submit_verification_details.SetLabel("Submitting Verification Details...")
        self.btn_submit_verification_details.Update()

        task_id = self.txt_task_id.GetValue()
        response_string = self.txt_verification_details.GetValue()
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

        # Change button text back to "Submit Verification Details"
        self.btn_submit_verification_details.SetLabel("Submit Verification Details")
        self.btn_submit_verification_details.Update()

    def on_force_update(self, event):
        # Change button text to "Updating..."
        self.btn_force_update.SetLabel("Updating...")
        self.btn_force_update.Update()

        logger.info("Kicking off Force Update")

        try:
            key_account_details = self.task_manager.process_account_info()
            self.populate_summary_grid(key_account_details)
        except Exception as e:
            logger.error(f"FAILED UPDATING SUMMARY DATA: {e}")

        try:
            proposals_df = self.task_manager.get_proposals_df()
            self.populate_grid_generic(self.proposals_grid, proposals_df, 'proposals')
        except Exception as e:
            logger.error(f"FAILED UPDATING PROPOSALS DATA: {e}")

        try:
            verification_data = self.task_manager.get_verification_df()
            self.populate_grid_generic(self.verification_grid, verification_data, 'verification')
        except Exception as e:
            logger.error(f"FAILED UPDATING VERIFICATION DATA: {e}")

        try:
            rewards_data = self.task_manager.get_rewards_df()
            self.populate_grid_generic(self.rewards_grid, rewards_data, 'rewards')
        except Exception as e:
            logger.error(f"FAILED UPDATING REWARDS DATA: {e}")

        # Change button text back to "Update"
        self.btn_force_update.SetLabel("Force Update")
        self.btn_force_update.Update()

    def on_log_pomodoro(self, event):
        # Change button text to "Logging Pomodoro..."
        self.btn_log_pomodoro.SetLabel("Logging Pomodoro...")
        self.btn_log_pomodoro.Update()

        task_id = self.txt_task_id.GetValue()
        pomodoro_text = self.txt_verification_details.GetValue()
        response = self.task_manager.send_pomodoro_for_task_id(task_id=task_id, pomodoro_text=pomodoro_text)
        message = self.task_manager.ux__convert_response_object_to_status_message(response)
        wx.MessageBox(message, 'Pomodoro Log Result', wx.OK | wx.ICON_INFORMATION)

        # Change button text back to "Log Pomodoro"
        self.btn_log_pomodoro.SetLabel("Log Pomodoro")
        self.btn_log_pomodoro.Update()

    def on_submit_xrp_payment(self, event):
        # Change button text to "Submitting XRP Payment..."
        self.btn_submit_xrp_payment.SetLabel("Submitting...")
        self.btn_submit_xrp_payment.Update()

        # Check that Amount and Destination are valid
        if not self.txt_xrp_amount.GetValue() or not self.txt_xrp_address_payment.GetValue():
            wx.MessageBox("Please enter a valid amount and destination", "Error", wx.OK | wx.ICON_ERROR)
            return

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

        # Change button text back to "Submit XRP Payment"
        self.btn_submit_xrp_payment.SetLabel("Submit Payment")
        self.btn_submit_xrp_payment.Update()

    def on_submit_pft_payment(self, event):
        # Change button text to "Submitting PFT Payment..."
        self.btn_submit_pft_payment.SetLabel("Submitting...")
        self.btn_submit_pft_payment.Update()

        # Check that Amount and Destination are valid
        if not self.txt_pft_amount.GetValue() or not self.txt_pft_address_payment.GetValue():
            wx.MessageBox("Please enter a valid amount and destination", "Error", wx.OK | wx.ICON_ERROR)
            return

        if is_over_1kb(self.txt_pft_memo.GetValue()):
            if wx.YES == wx.MessageBox("Memo is over 1 KB, transaction will be batch-sent. Continue?", "Confirmation", wx.YES_NO | wx.ICON_QUESTION):
                pass
            else:
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

        # Change button text back to "Submit PFT Payment"
        self.btn_submit_pft_payment.SetLabel("Submit Payment")
        self.btn_submit_pft_payment.Update()

    def on_show_secret(self, event):
        # Change button text to "Showing Secret..."
        self.btn_show_secret.SetLabel("Showing Secret...")
        self.btn_show_secret.Update()

        classic_address = self.wallet.classic_address
        secret = self.wallet.seed
        wx.MessageBox(f"Classic Address: {classic_address}\nSecret: {secret}", 'Wallet Secret', wx.OK | wx.ICON_INFORMATION)

        # Change button text back to "Show Secret"
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
