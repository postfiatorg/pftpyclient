import wx
import wx.adv
import wx.grid as gridlib
import wx.html
import xrpl
from xrpl.wallet import Wallet
import asyncio
from threading import Thread
import json
import wx.lib.newevent
import nest_asyncio
import logging
from pftpyclient.task_manager.basic_tasks import PostFiatTaskManager  # Adjust the import path as needed
from pftpyclient.task_manager.basic_tasks import WalletInitiationFunctions
import webbrowser
import os
from pftpyclient.basic_utilities.create_shortcut import create_shortcut

# Try to use the default browser
if os.name == 'nt':
    try: 
        webbrowser.get('windows-default')
    except webbrowser.Error:
        pass

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Apply the nest_asyncio patch
nest_asyncio.apply()

# JSON data to be rendered in the table
json_data = '{"proposal":{"2024-05-09_23:55__CF24":"Sample task .. 850"},"acceptance":{"2024-05-09_23:55__CF24":"I agree that net income and fcf extraction are important and urgent and will work the weekend doing this"}}'

UpdateGridEvent, EVT_UPDATE_GRID = wx.lib.newevent.NewEvent()

class XRPLMonitorThread(Thread):
    def __init__(self, url, gui):
        Thread.__init__(self, daemon=True)
        self.gui = gui
        self.url = url
        self.loop = asyncio.new_event_loop()
        self.context = None

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.context = self.loop.run_until_complete(self.monitor())

    async def monitor(self):
        return await self.watch_xrpl_account(self.gui.wallet.classic_address, self.gui.wallet)

    async def watch_xrpl_account(self, address, wallet=None):
        self.account = address
        self.wallet = wallet
        async with xrpl.asyncio.clients.AsyncWebsocketClient(self.url) as self.client:
            await self.on_connected()
            async for message in self.client:
                mtype = message.get("type")
                if mtype == "ledgerClosed":
                    wx.CallAfter(self.gui.update_ledger, message)
                elif mtype == "transaction":
                    response = await self.client.request(xrpl.models.requests.AccountInfo(
                        account=self.account,
                        ledger_index=message["ledger_index"]
                    ))
                    wx.CallAfter(self.gui.update_account, response.result["account_data"])
                    wx.CallAfter(self.gui.run_bg_job, self.gui.update_tokens(self.account))

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
    def __init__(self, url):
        wx.Frame.__init__(self, None, title="Post Fiat Client Wallet Beta v.0.1", size=(800, 700))
        self.url = url
        self.wallet = None
        self.build_ui()
        self.worker = None
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(EVT_UPDATE_GRID, self.on_update_grid)

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

        # Summary tab
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
        self.summary_grid = gridlib.Grid(self.summary_tab)
        self.summary_grid.CreateGrid(0, 2)  # 2 columns for Key and Value
        self.summary_grid.SetColLabelValue(0, "Key")
        self.summary_grid.SetColLabelValue(1, "Value")

        # Accepted tab
        self.accepted_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.accepted_tab, "Accepted")
        self.accepted_sizer = wx.BoxSizer(wx.VERTICAL)
        self.accepted_tab.SetSizer(self.accepted_sizer)

        # Add the task management buttons in the Accepted tab
        self.button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ask_for_task = wx.Button(self.accepted_tab, label="Ask For Task")
        self.button_sizer.Add(self.btn_ask_for_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_ask_for_task.Bind(wx.EVT_BUTTON, self.on_ask_for_task)

        self.btn_accept_task = wx.Button(self.accepted_tab, label="Accept Task")
        self.button_sizer.Add(self.btn_accept_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_accept_task.Bind(wx.EVT_BUTTON, self.on_accept_task)

        self.accepted_sizer.Add(self.button_sizer, 0, wx.EXPAND)

        self.button_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refuse_task = wx.Button(self.accepted_tab, label="Refuse Task")
        self.button_sizer2.Add(self.btn_refuse_task, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_refuse_task.Bind(wx.EVT_BUTTON, self.on_refuse_task)

        self.btn_submit_for_verification = wx.Button(self.accepted_tab, label="Submit for Verification")
        self.button_sizer2.Add(self.btn_submit_for_verification, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_submit_for_verification.Bind(wx.EVT_BUTTON, self.on_submit_for_verification)

        self.accepted_sizer.Add(self.button_sizer2, 0, wx.EXPAND)

        # Add grid to Accepted tab
        self.accepted_grid = gridlib.Grid(self.accepted_tab)
        self.accepted_grid.CreateGrid(0, 3)
        self.accepted_grid.SetColLabelValue(0, "task_id")
        self.accepted_grid.SetColLabelValue(1, "proposal")
        self.accepted_grid.SetColLabelValue(2, "acceptance")
        self.accepted_sizer.Add(self.accepted_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Verification tab
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
        self.verification_grid = gridlib.Grid(self.verification_tab)
        self.verification_grid.CreateGrid(0, 3)
        self.verification_grid.SetColLabelValue(0, "task_id")
        self.verification_grid.SetColLabelValue(1, "original_task")
        self.verification_grid.SetColLabelValue(2, "verification")
        self.verification_sizer.Add(self.verification_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Rewards tab
        self.rewards_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.rewards_tab, "Rewards")
        self.rewards_sizer = wx.BoxSizer(wx.VERTICAL)
        self.rewards_tab.SetSizer(self.rewards_sizer)

        # Add grid to Rewards tab
        self.rewards_grid = gridlib.Grid(self.rewards_tab)
        self.rewards_grid.CreateGrid(0, 4)
        self.rewards_grid.SetColLabelValue(0, "task_id")
        self.rewards_grid.SetColLabelValue(1, "proposal")
        self.rewards_grid.SetColLabelValue(2, "reward")
        self.rewards_grid.SetColLabelValue(3, "payout")  # Label the new column
        self.rewards_sizer.Add(self.rewards_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Payments tab
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

        # Populate Accepted tab grids
        self.populate_accepted_grid(json_data)

    def create_login_panel(self):
        panel = wx.Panel(self.panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Username
        self.lbl_user = wx.StaticText(panel, label="Username:")
        sizer.Add(self.lbl_user, flag=wx.ALL, border=5)
        self.txt_user = wx.TextCtrl(panel)
        sizer.Add(self.txt_user, flag=wx.EXPAND | wx.ALL, border=5)

        # Password
        self.lbl_pass = wx.StaticText(panel, label="Password:")
        sizer.Add(self.lbl_pass, flag=wx.ALL, border=5)
        self.txt_pass = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        sizer.Add(self.txt_pass, flag=wx.EXPAND | wx.ALL, border=5)

        # Login button
        self.btn_login = wx.Button(panel, label="Login")
        sizer.Add(self.btn_login, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_login.Bind(wx.EVT_BUTTON, self.on_login)

        # Create New User button
        self.btn_new_user = wx.Button(panel, label="Create New User")
        sizer.Add(self.btn_new_user, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_new_user.Bind(wx.EVT_BUTTON, self.on_create_new_user)

        panel.SetSizer(sizer)

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

        # Username
        self.lbl_username = wx.StaticText(panel, label="Username:")
        user_details_sizer.Add(self.lbl_username, flag=wx.ALL, border=5)
        self.txt_username = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_username, flag=wx.EXPAND | wx.ALL, border=5)

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

        # XRP Address
        self.lbl_xrp_address = wx.StaticText(panel, label="XRP Address:")
        user_details_sizer.Add(self.lbl_xrp_address, flag=wx.ALL, border=5)
        self.txt_xrp_address = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_xrp_address, flag=wx.EXPAND | wx.ALL, border=5)

        # XRP Secret
        self.lbl_xrp_secret = wx.StaticText(panel, label="XRP Secret:")
        user_details_sizer.Add(self.lbl_xrp_secret, flag=wx.ALL, border=5)
        self.txt_xrp_secret = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        user_details_sizer.Add(self.txt_xrp_secret, flag=wx.EXPAND | wx.ALL, border=5)

        # Commitment
        self.lbl_commitment = wx.StaticText(panel, label="Please write 1 sentence committing to a long term objective of your choosing:")
        user_details_sizer.Add(self.lbl_commitment, flag=wx.ALL, border=5)
        self.txt_commitment = wx.TextCtrl(panel)
        user_details_sizer.Add(self.txt_commitment, flag=wx.EXPAND | wx.ALL, border=5)

        # Info
        self.lbl_info = wx.StaticText(panel, label="Paste Your XRP Address in the first line of your Google Doc and make sure that anyone who has the link can view Before Genesis")
        user_details_sizer.Add(self.lbl_info, flag=wx.ALL, border=5)

        # Buttons
        self.btn_generate_wallet = wx.Button(panel, label="Generate New XRP Wallet")
        user_details_sizer.Add(self.btn_generate_wallet, flag=wx.ALL, border=5)
        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)

        self.btn_genesis = wx.Button(panel, label="Genesis")
        user_details_sizer.Add(self.btn_genesis, flag=wx.ALL, border=5)
        self.btn_genesis.Bind(wx.EVT_BUTTON, self.on_genesis)

        self.btn_existing_user = wx.Button(panel, label="Cache Existing User")
        user_details_sizer.Add(self.btn_existing_user, flag=wx.ALL, border=5)
        self.btn_existing_user.Bind(wx.EVT_BUTTON, self.on_existing_user)

        self.btn_delete_user = wx.Button(panel, label="Delete Existing User")
        user_details_sizer.Add(self.btn_delete_user, flag=wx.ALL, border=5)
        self.btn_delete_user.Bind(wx.EVT_BUTTON, self.on_delete_user)

        sizer.Add(user_details_sizer, 1, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)

        return panel
    
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
            wx.MessageBox('Passwords Do Not Match Please Retry', 'Info', wx.OK | wx.ICON_INFORMATION)

        if self.txt_password.GetValue() == self.txt_confirm_password.GetValue():
            # Call the caching function
            wallet_functions = WalletInitiationFunctions()
            output_string = wallet_functions.given_input_map_cache_credentials_locally(input_map)


            # Display the output string in a message box
            wx.MessageBox(output_string, 'Genesis Result', wx.OK | wx.ICON_INFORMATION)

            # Call send_initiation_rite with the gathered data
            wallet_functions.send_initiation_rite(
                wallet_seed=self.txt_xrp_secret.GetValue(),
                user=self.txt_username.GetValue(),
                user_response=commitment
            )

    def on_delete_user(self, event):
        self.clear_credential_file()
        wx.MessageBox('User Credential Cache Deleted', 'Info', wx.OK | wx.ICON_INFORMATION)

    def on_existing_user(self, event):
        input_map = {
            'Username_Input': self.txt_username.GetValue(),
            'Password_Input': self.txt_password.GetValue(),
            'Google Doc Share Link_Input': self.txt_google_doc.GetValue(),
            'XRP Address_Input': self.txt_xrp_address.GetValue(),
            'XRP Secret_Input': self.txt_xrp_secret.GetValue(),
            'Confirm Password_Input': self.txt_confirm_password.GetValue(),
        }

        if self.txt_password.GetValue() != self.txt_confirm_password.GetValue():
            wx.MessageBox('Passwords Do Not Match Please Retry', 'Info', wx.OK | wx.ICON_INFORMATION)

        if self.txt_password.GetValue() == self.txt_confirm_password.GetValue():
            wallet_functions = WalletInitiationFunctions()
            output_string = wallet_functions.given_input_map_cache_credentials_locally(input_map)
            wx.MessageBox('Existing User Information Cached. Please proceed to Log In', 'Info', wx.OK | wx.ICON_INFORMATION)

    def clear_credential_file(self):
        self.wallet = WalletInitiationFunctions()
        self.wallet.clear_credential_file()

    def on_login(self, event):
        username = self.txt_user.GetValue()
        password = self.txt_pass.GetValue()
        self.task_manager = PostFiatTaskManager(username=username, password=password)
        self.wallet = self.task_manager.user_wallet
        classic_address = self.wallet.classic_address

        logging.info(f"Logged in as {username}")

        # Hide login panel and show tabs
        self.login_panel.Hide()
        self.tabs.Show()

        self.populate_summary_tab(username, classic_address)

        # Update layout and ensure correct sizing
        self.panel.Layout()
        self.Layout()
        self.Fit()

        # Fetch and display key account details
        all_account_info = self.task_manager.get_memo_detail_df_for_account()
        key_account_details = self.task_manager.process_account_info(all_account_info)

        self.populate_summary_grid(key_account_details)

        self.summary_tab.Layout()  # Update the layout

        self.worker = XRPLMonitorThread(self.url, self)
        self.worker.start()

        # Immediately populate the grid with current data
        self.update_json_data(None)

        # Start timers
        self.start_json_update_timer()
        self.start_force_update_timer()
        self.start_pft_update_timer()
        self.start_transaction_update_timer()

    def on_create_new_user(self, event):
        self.login_panel.Hide()
        self.user_details_panel.Show()
        self.panel.Layout()
        # self.Layout()
        # self.Fit()
        self.Refresh()

    def on_return_to_login(self, event):
        self.user_details_panel.Hide()
        self.login_panel.Show()
        self.panel.Layout()
        # self.Layout()
        # self.Fit()
        self.Refresh()

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

        # Update Key Account Details
        self.update_key_account_details()

    def update_key_account_details(self):
        if self.task_manager:
            all_account_info = self.task_manager.get_memo_detail_df_for_account()
            key_account_details = self.task_manager.process_account_info(all_account_info)
            self.populate_summary_grid(key_account_details)

    def run_bg_job(self, job):
        if self.worker.context:
            asyncio.run_coroutine_threadsafe(job, self.worker.loop)

    def update_ledger(self, message):
        pass  # Simplified for this version

    def update_account(self, acct):
        xrp_balance = str(xrpl.utils.drops_to_xrp(acct["Balance"]))
        self.lbl_xrp_balance.SetLabel(f"XRP Balance: {xrp_balance}")

    def update_tokens(self, account_address):
        logging.debug(f"Fetching token balances for account: {account_address}")
        try:
            client = xrpl.clients.JsonRpcClient("https://s2.ripple.com:51234")
            account_lines = xrpl.models.requests.AccountLines(
                account=account_address,
                ledger_index="validated"
            )
            response = client.request(account_lines)
            logging.debug(f"AccountLines response: {response.result}")

            if not response.is_successful():
                logging.error(f"Error fetching AccountLines: {response}")
                return

            lines = response.result.get('lines', [])
            logging.debug(f"Account lines: {lines}")

            pft_balance = 0.0
            issuer_address = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'
            for line in lines:
                logging.debug(f"Processing line: {line}")
                if line['currency'] == 'PFT' and line['account'] == issuer_address:
                    pft_balance = float(line['balance'])
                    logging.debug(f"Found PFT balance: {pft_balance}")

            self.lbl_pft_balance.SetLabel(f"PFT Balance: {pft_balance}")

        except Exception as e:
            logging.exception(f"Exception in update_tokens: {e}")

    def on_close(self, event):
        if self.worker:
            self.worker.loop.stop()
        self.Destroy()

    def start_json_update_timer(self):
        self.json_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.update_json_data, self.json_update_timer)
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
        logging.debug("Transaction update timer triggered")
        self.task_manager.update_transactions()

    def update_json_data(self, event):
        try:
            all_account_info = self.task_manager.get_memo_detail_df_for_account()

            # Update Accepted tab
            json_data = self.task_manager.convert_all_account_info_into_outstanding_task_df(
                all_account_info=all_account_info
            ).to_json()
            logging.debug(f"Updating Accepted tab with JSON data: {json_data}")
            wx.PostEvent(self, UpdateGridEvent(json_data=json_data, target="accepted"))

            # Update Rewards tab
            rewards_data = self.task_manager.convert_all_account_info_into_rewarded_task_df(
                all_account_info=all_account_info
            ).to_json()
            logging.debug(f"Updating Rewards tab with JSON data: {rewards_data}")
            wx.PostEvent(self, UpdateGridEvent(json_data=rewards_data, target="rewards"))

            # Update Verification tab
            verification_data = self.task_manager.convert_all_account_info_into_required_verification_df(
                all_account_info=all_account_info
            ).to_json()
            logging.debug(f"Updating Verification tab with JSON data: {verification_data}")
            wx.PostEvent(self, UpdateGridEvent(json_data=verification_data, target="verification"))
            logging.debug("UpdateGridEvent posted for verification")

        except Exception as e:
            logging.exception(f"Error updating JSON data: {e}")

    def on_update_grid(self, event):
        logging.debug(f"Updating grid with target: {getattr(event, 'target', 'accepted')}")
        if hasattr(event, 'target'):
            if event.target == "rewards":
                self.populate_rewards_grid(event.json_data)
            elif event.target == "verification":
                logging.debug("Updating verification grid")
                self.populate_verification_grid(event.json_data)
            else:
                self.populate_accepted_grid(event.json_data)
        else:
            self.populate_accepted_grid(event.json_data)

    def on_pft_update_timer(self, event):
        if self.wallet:
            self.update_tokens(self.wallet.classic_address)

    def populate_summary_grid(self, key_account_details):
        self.summary_grid.ClearGrid()

        current_rows = self.summary_grid.GetNumberRows()
        needed_rows = len(key_account_details)

        if current_rows < needed_rows:
            self.summary_grid.AppendRows(needed_rows - current_rows)
        elif current_rows > needed_rows:
            for row in range(needed_rows, current_rows):
                self.summary_grid.SetCellValue(row, 0, "")
                self.summary_grid.SetCellValue(row, 1, "")

        for idx, (key, value) in enumerate(key_account_details.items()):
            self.summary_grid.SetCellValue(idx, 0, str(key))
            self.summary_grid.SetCellValue(idx, 1, str(value))
            # enable text wrapping in the 'Value' column
            self.summary_grid.SetCellRenderer(idx, 1, gridlib.GridCellAutoWrapStringRenderer())
            # manually set row height for better display
            self.summary_grid.SetRowSize(idx, 300)  # Adjust the height as needed

        # Set column width to ensure proper wrapping
        self.summary_grid.SetColSize(0, 100)
        self.summary_grid.SetColSize(1, 550)  # Adjust width as needed
        self.summary_grid.AutoSizeRows()
        self.summary_grid.ForceRefresh()

    def populate_accepted_grid(self, json_data):
        data = json.loads(json_data)
        proposals = data['proposal']
        acceptances = data['acceptance']

        self.accepted_grid.ClearGrid()
        while self.accepted_grid.GetNumberRows() > 0:
            self.accepted_grid.DeleteRows(0, 1, False)

        for task_id, proposal in proposals.items():
            acceptance = acceptances.get(task_id, "")
            self.accepted_grid.AppendRows(1)
            row = self.accepted_grid.GetNumberRows() - 1
            self.accepted_grid.SetCellValue(row, 0, task_id)
            self.accepted_grid.SetCellValue(row, 1, proposal)
            self.accepted_grid.SetCellValue(row, 2, acceptance)

            # Enable text wrapping in the 'proposal' and 'acceptance' columns
            self.accepted_grid.SetCellRenderer(row, 1, gridlib.GridCellAutoWrapStringRenderer())
            self.accepted_grid.SetCellRenderer(row, 2, gridlib.GridCellAutoWrapStringRenderer())
            
            # Manually set row height for better display
            self.accepted_grid.SetRowSize(row, 65)  # Adjust the height as needed

        # Set column width to ensure proper wrapping
        self.accepted_grid.SetColSize(0, 170)
        self.accepted_grid.SetColSize(1, 400)  # Adjust width as needed
        self.accepted_grid.SetColSize(2, 300)  # Adjust width as needed

    def populate_rewards_grid(self, json_data):
        logging.debug("Populating rewards grid")
        try:
            data = json.loads(json_data)
            if not data: 
                logging.debug("No data to populate rewards grid")
                self.rewards_grid.ClearGrid()
                return
            
            proposals = data.get('proposal', {})
            rewards = data.get('reward', {})
            payouts = data.get('payout', {})

            self.rewards_grid.ClearGrid()
            self.rewards_grid.DeleteRows(0, self.rewards_grid.GetNumberRows())

            for task_id in proposals.key():
                self.rewards_grid.AppendRows(1)
                row = self.rewards_grid.GetNumberRows() - 1
                self.rewards_grid.SetCellValue(row, 0, task_id)
                self.rewards_grid.SetCellValue(row, 1, proposals.get(task_id, ""))
                self.rewards_grid.SetCellValue(row, 2, rewards.get(task_id, ""))
                self.rewards_grid.SetCellValue(row, 3, payouts.get(task_id, ""))

                # Enable text wrapping in the 'proposal', 'reward', and 'payout' columns
                self.rewards_grid.SetCellRenderer(row, 1, gridlib.GridCellAutoWrapStringRenderer())
                self.rewards_grid.SetCellRenderer(row, 2, gridlib.GridCellAutoWrapStringRenderer())
                self.rewards_grid.SetCellRenderer(row, 3, gridlib.GridCellAutoWrapStringRenderer())
                
                # Manually set row height for better display
                self.rewards_grid.SetRowSize(row, 65)  # Adjust the height as needed

            # Set column width to ensure proper wrapping
            self.rewards_grid.SetColSize(0, 170)
            self.rewards_grid.SetColSize(1, 400)  # Adjust width as needed
            self.rewards_grid.SetColSize(2, 300)  # Adjust width as needed
            self.rewards_grid.SetColSize(3, 100)  # Adjust width as needed for payout

        except Exception as e:
            logging.error(f"Error populating rewards grid: {e}")

    def populate_verification_grid(self, json_data):
        logging.debug("Updating verification grid")
        try:
            data = json.loads(json_data)
            if not data:
                logging.debug("No data to populate verification grid")
                self.verification_grid.ClearGrid()
                return

            task_ids = data.get('task_id', {})
            original_tasks = data.get('original_task', {})
            verifications = data.get('verification', {})

            self.verification_grid.ClearGrid()
            self.verification_grid.DeleteRows(0, self.verification_grid.GetNumberRows())

            for idx, task_id in task_ids.items():
                self.verification_grid.AppendRows(1)
                row = self.verification_grid.GetNumberRows() - 1
                self.verification_grid.SetCellValue(row, 0, task_ids.get(idx, ""))
                self.verification_grid.SetCellValue(row, 1, original_tasks.get(task_id, ""))
                self.verification_grid.SetCellValue(row, 2, verifications.get(task_id, ""))

                # Enable text wrapping in the 'original_task' and 'verification' columns
                self.verification_grid.SetCellRenderer(row, 1, gridlib.GridCellAutoWrapStringRenderer())
                self.verification_grid.SetCellRenderer(row, 2, gridlib.GridCellAutoWrapStringRenderer())
                
                # Manually set row height for better display
                self.verification_grid.SetRowSize(row, 65)  # Adjust the height as needed

            # Set column width to ensure proper wrapping
            self.verification_grid.SetColSize(0, 170)
            self.verification_grid.SetColSize(1, 400)  # Adjust width as needed
            self.verification_grid.SetColSize(2, 300)  # Adjust width as needed

        except Exception as e:
            logging.error(f"Error populating verification grid: {e}")

    def on_ask_for_task(self, event):
        dialog = CustomDialog("Ask For Task", ["Task Request"])
        if dialog.ShowModal() == wx.ID_OK:
            request_message = dialog.GetValues()["Task Request"]
            all_account_info = self.task_manager.get_memo_detail_df_for_account()
            response = self.task_manager.request_post_fiat(request_message=request_message, all_account_info=all_account_info)
            message = self.task_manager.ux__convert_response_object_to_status_message(response)
            wx.MessageBox(message, 'Task Request Result', wx.OK | wx.ICON_INFORMATION)
            wx.CallLater(30000, self.update_json_data, None)
        dialog.Destroy()

    def on_accept_task(self, event):
        dialog = CustomDialog("Accept Task", ["Task ID", "Acceptance String"])
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            acceptance_string = values["Acceptance String"]
            all_account_info = self.task_manager.get_memo_detail_df_for_account()
            response = self.task_manager.send_acceptance_for_task_id(
                task_id=task_id,
                acceptance_string=acceptance_string,
                all_account_info=all_account_info
            )
            message = self.task_manager.ux__convert_response_object_to_status_message(response)
            wx.MessageBox(message, 'Task Acceptance Result', wx.OK | wx.ICON_INFORMATION)
            wx.CallLater(5000, self.update_json_data, None)
        dialog.Destroy()

    def on_refuse_task(self, event):
        dialog = CustomDialog("Refuse Task", ["Task ID", "Refusal Reason"])
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            refusal_reason = values["Refusal Reason"]
            all_account_info = self.task_manager.get_memo_detail_df_for_account()
            response = self.task_manager.send_refusal_for_task(
                task_id=task_id,
                refusal_reason=refusal_reason,
                all_account_info=all_account_info
            )
            message = self.task_manager.ux__convert_response_object_to_status_message(response)
            wx.MessageBox(message, 'Task Refusal Result', wx.OK | wx.ICON_INFORMATION)
            wx.CallLater(5000, self.update_json_data, None)
        dialog.Destroy()

    def on_submit_for_verification(self, event):
        dialog = CustomDialog("Submit for Verification", ["Task ID", "Completion String"])
        if dialog.ShowModal() == wx.ID_OK:
            values = dialog.GetValues()
            task_id = values["Task ID"]
            completion_string = values["Completion String"]
            all_account_info = self.task_manager.get_memo_detail_df_for_account()
            response = self.task_manager.send_post_fiat_initial_completion(
                completion_string=completion_string,
                task_id=task_id,
                all_account_info=all_account_info
            )
            message = self.task_manager.ux__convert_response_object_to_status_message(response)
            wx.MessageBox(message, 'Task Submission Result', wx.OK | wx.ICON_INFORMATION)
            wx.CallLater(5000, self.update_json_data, None)
        dialog.Destroy()

    def on_force_update(self, event):
        logging.info("Kicking off Force Update")
        all_account_info = self.task_manager.get_memo_detail_df_for_account()

        try:
            verification_data = self.task_manager.convert_all_account_info_into_required_verification_df(
                all_account_info=all_account_info
            ).to_json()
            self.populate_verification_grid(verification_data)
        except:
            logging.error("FAILED VERIFICATION UPDATE")

        try:
            key_account_details = self.task_manager.process_account_info(all_account_info)
            self.populate_summary_grid(key_account_details)
        except:
            logging.error("FAILED UPDATING SUMMARY DATA")

        try:
            rewards_data = self.task_manager.convert_all_account_info_into_rewarded_task_df(
                all_account_info=all_account_info
            ).to_json()
            self.populate_rewards_grid(rewards_data)
        except:
            logging.error("FAILED UPDATING REWARDS DATA")

        try:
            acceptance_data = self.task_manager.convert_all_account_info_into_outstanding_task_df(
                all_account_info=all_account_info
            ).to_json()
            self.populate_accepted_grid(acceptance_data)
        except:
            logging.error("FAILED UPDATING ACCEPTANCE DATA")

    def on_submit_verification_details(self, event):
        task_id = self.txt_task_id.GetValue()
        response_string = self.txt_verification_details.GetValue()
        all_account_info = self.task_manager.get_memo_detail_df_for_account()
        response = self.task_manager.send_post_fiat_verification_response(
            response_string=response_string,
            task_id=task_id,
            all_account_info=all_account_info
        )
        message = self.task_manager.ux__convert_response_object_to_status_message(response)
        wx.MessageBox(message, 'Verification Submission Result', wx.OK | wx.ICON_INFORMATION)

    def on_log_pomodoro(self, event):
        task_id = self.txt_task_id.GetValue()
        pomodoro_text = self.txt_verification_details.GetValue()
        response = self.task_manager.send_pomodoro_for_task_id(task_id=task_id, pomodoro_text=pomodoro_text)
        message = self.task_manager.ux__convert_response_object_to_status_message(response)
        wx.MessageBox(message, 'Pomodoro Log Result', wx.OK | wx.ICON_INFORMATION)

    def on_submit_xrp_payment(self, event):
        response = self.task_manager.send_xrp(amount=self.txt_xrp_amount.GetValue(), 
                                                destination=self.txt_xrp_address_payment.GetValue(), 
                                                memo=self.txt_xrp_memo.GetValue()
        )
        formatted_response = self.format_response(response)

        logging.info(f"XRP Payment Result: {formatted_response}")

        dialog = SelectableMessageDialog(self, "XRP Payment Result", formatted_response)
        dialog.ShowModal()
        dialog.Destroy()

    def on_submit_pft_payment(self, event):
        response = self.task_manager.send_pft(amount=self.txt_pft_amount.GetValue(), 
                                                destination=self.txt_pft_address_payment.GetValue(), 
                                                memo=self.txt_pft_memo.GetValue()
        )

        logging.debug(f"PFT Payment Response: {response}")

        formatted_response = self.format_response(response)

        logging.info(f"PFT Payment Result: {formatted_response}")

        dialog = SelectableMessageDialog(self, "PFT Payment Result", formatted_response)
        dialog.ShowModal()
        dialog.Destroy()

    def on_show_secret(self, event):
        classic_address = self.wallet.classic_address
        secret = self.wallet.seed
        wx.MessageBox(f"Classic Address: {classic_address}\nSecret: {secret}", 'Wallet Secret', wx.OK | wx.ICON_INFORMATION)

    def format_response(self, response):
        if response.status == "success":
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

            logging.debug(f"Formatted Response: {formatted_response}")

            # Add memo if present
            if tx_json.get('Memos'):
                memo_data = tx_json['Memos'][0]['Memo'].get('MemoData', '')
                decoded_memo = bytes.fromhex(memo_data).decode('utf-8', errors='ignore')
                formatted_response += f"Memo: {decoded_memo}\n"

            # Add transaction result
            if meta:
                formatted_response += f"Transaction Result: {meta.get('TransactionResult', 'N/A')}\n"

            return formatted_response
        else:
            return f"Transaction Failed\nError: {response.result.get('error message', 'Unknown error')}\n"
        
class LinkOpeningHtmlWindow(wx.html.HtmlWindow):
    def OnLinkClicked(self, link):
        url = link.GetHref()
        logging.debug(f"Link clicked: {url}")
        try:
            webbrowser.open(url, new=2)
            logging.debug(f"Attempted to open URL: {url}")
        except Exception as e:
            logging.error(f"Failed to open URL {url}. Error: {str(e)}")

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
    networks = {
        "mainnet": "wss://xrplcluster.com",
        "testnet": "wss://s.altnet.rippletest.net:51233",
    }

    logging.info("Starting Post Fiat Wallet")
    app = wx.App()
    frame = WalletApp(networks["mainnet"])
    frame.Show()
    app.MainLoop()

if __name__ == "__main__":
    main()
