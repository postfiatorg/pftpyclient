import wx
import wx.grid as gridlib
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

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Apply the nest_asyncio patch
nest_asyncio.apply()

# JSON data to be rendered in the table
json_data = '{"proposal":{"2024-05-09_23:55__CF24":"Implement a batch job that runs the net income and FCF extractors on the latest earnings transcripts, logs the results, and flags any extraction errors for manual review. .. 850"},"acceptance":{"2024-05-09_23:55__CF24":"I agree that net income and fcf extraction are important and urgent and will work the weekend doing this"}}'

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
        wx.Frame.__init__(self, None, title="Post Fiat Client Wallet Beta v.0.1", size=(800, 600))
        self.url = url
        self.wallet = None
        self.build_ui()
        self.worker = None
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(EVT_UPDATE_GRID, self.on_update_grid)

        # Start the force update timer
        self.start_force_update_timer()

    def build_ui(self):
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.tabs = wx.Notebook(self.panel)

        # User Details tab
        self.user_details_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.user_details_tab, "User Details")
        self.user_details_sizer = wx.BoxSizer(wx.VERTICAL)
        self.user_details_tab.SetSizer(self.user_details_sizer)

        self.lbl_username = wx.StaticText(self.user_details_tab, label="Username:")
        self.user_details_sizer.Add(self.lbl_username, flag=wx.ALL, border=5)
        self.txt_username = wx.TextCtrl(self.user_details_tab)
        self.user_details_sizer.Add(self.txt_username, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_password = wx.StaticText(self.user_details_tab, label="Password:")
        self.user_details_sizer.Add(self.lbl_password, flag=wx.ALL, border=5)
        self.txt_password = wx.TextCtrl(self.user_details_tab, style=wx.TE_PASSWORD)
        self.user_details_sizer.Add(self.txt_password, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_google_doc = wx.StaticText(self.user_details_tab, label="Google Doc Share Link:")
        self.user_details_sizer.Add(self.lbl_google_doc, flag=wx.ALL, border=5)
        self.txt_google_doc = wx.TextCtrl(self.user_details_tab)
        self.user_details_sizer.Add(self.txt_google_doc, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_xrp_address = wx.StaticText(self.user_details_tab, label="XRP Address:")
        self.user_details_sizer.Add(self.lbl_xrp_address, flag=wx.ALL, border=5)
        self.txt_xrp_address = wx.TextCtrl(self.user_details_tab)
        self.user_details_sizer.Add(self.txt_xrp_address, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_xrp_secret = wx.StaticText(self.user_details_tab, label="XRP Secret:")
        self.user_details_sizer.Add(self.lbl_xrp_secret, flag=wx.ALL, border=5)
        self.txt_xrp_secret = wx.TextCtrl(self.user_details_tab, style=wx.TE_PASSWORD)
        self.user_details_sizer.Add(self.txt_xrp_secret, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_commitment = wx.StaticText(self.user_details_tab, label="Please write 1 sentence committing to a long term objective of your choosing:")
        self.user_details_sizer.Add(self.lbl_commitment, flag=wx.ALL, border=5)
        self.txt_commitment = wx.TextCtrl(self.user_details_tab)
        self.user_details_sizer.Add(self.txt_commitment, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_info = wx.StaticText(self.user_details_tab, label="Paste Your XRP Address in the first line of your Google Doc and make sure that anyone who has the link can view Before Genesis")
        self.user_details_sizer.Add(self.lbl_info, flag=wx.ALL, border=5)

        self.btn_generate_wallet = wx.Button(self.user_details_tab, label="Generate New XRP Wallet")
        self.user_details_sizer.Add(self.btn_generate_wallet, flag=wx.ALL, border=5)
        self.btn_generate_wallet.Bind(wx.EVT_BUTTON, self.on_generate_wallet)

        self.btn_genesis = wx.Button(self.user_details_tab, label="Genesis")
        self.user_details_sizer.Add(self.btn_genesis, flag=wx.ALL, border=5)
        self.btn_genesis.Bind(wx.EVT_BUTTON, self.on_genesis)

        self.btn_delete_user = wx.Button(self.user_details_tab, label="Delete Existing User")
        self.user_details_sizer.Add(self.btn_delete_user, flag=wx.ALL, border=5)
        self.btn_delete_user.Bind(wx.EVT_BUTTON, self.on_delete_user)

        # Summary tab
        self.summary_tab = wx.Panel(self.tabs)
        self.tabs.AddPage(self.summary_tab, "Summary")
        self.summary_sizer = wx.BoxSizer(wx.VERTICAL)
        self.summary_tab.SetSizer(self.summary_sizer)

        self.lbl_user = wx.StaticText(self.summary_tab, label="Username:")
        self.summary_sizer.Add(self.lbl_user, flag=wx.ALL, border=5)
        self.txt_user = wx.TextCtrl(self.summary_tab)
        self.summary_sizer.Add(self.txt_user, flag=wx.EXPAND | wx.ALL, border=5)

        self.lbl_pass = wx.StaticText(self.summary_tab, label="Password:")
        self.summary_sizer.Add(self.lbl_pass, flag=wx.ALL, border=5)
        self.txt_pass = wx.TextCtrl(self.summary_tab, style=wx.TE_PASSWORD)
        self.summary_sizer.Add(self.txt_pass, flag=wx.EXPAND | wx.ALL, border=5)

        self.btn_login = wx.Button(self.summary_tab, label="Login")
        self.summary_sizer.Add(self.btn_login, flag=wx.EXPAND | wx.ALL, border=5)
        self.btn_login.Bind(wx.EVT_BUTTON, self.on_login)

        # Add grid for Key Account Details
        self.summary_grid = gridlib.Grid(self.summary_tab)
        self.summary_grid.CreateGrid(0, 2)  # 2 columns for Key and Value
        self.summary_grid.SetColLabelValue(0, "Key")
        self.summary_grid.SetColLabelValue(1, "Value")
        self.summary_sizer.Add(self.summary_grid, 1, wx.EXPAND | wx.ALL, 5)

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

        # Call the caching function
        wallet_functions = WalletInitiationFunctions()
        output_string = wallet_functions.cache_credentials(input_map)

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

    def clear_credential_file(self):
        self.wallet = WalletInitiationFunctions()
        self.wallet.clear_credential_file()

    def on_login(self, event):
        username = self.txt_user.GetValue()
        password = self.txt_pass.GetValue()
        self.task_manager = PostFiatTaskManager(username=username, password=password)
        self.wallet = self.task_manager.user_wallet
        classic_address = self.wallet.classic_address

        # Clear the summary tab
        self.summary_sizer.Clear(True)

        # Display wallet information
        self.lbl_balance = wx.StaticText(self.summary_tab, label="XRP Balance: N/A")
        self.summary_sizer.Add(self.lbl_balance, flag=wx.ALL, border=5)
        self.lbl_address = wx.StaticText(self.summary_tab, label=f"Classic Address: {classic_address}")
        self.summary_sizer.Add(self.lbl_address, flag=wx.ALL, border=5)

        self.lbl_pft_balance = wx.StaticText(self.summary_tab, label="PFT Balance: N/A")
        self.summary_sizer.Add(self.lbl_pft_balance, flag=wx.ALL, border=5)

        # Create a heading for Key Account Details
        self.lbl_key_details = wx.StaticText(self.summary_tab, label="Key Account Details:")
        self.summary_sizer.Add(self.lbl_key_details, flag=wx.ALL, border=5)

        # Grid for Key Account Details
        self.summary_grid = gridlib.Grid(self.summary_tab)
        self.summary_grid.CreateGrid(0, 2)
        self.summary_grid.SetColLabelValue(0, "Key")
        self.summary_grid.SetColLabelValue(1, "Value")
        self.summary_sizer.Add(self.summary_grid, 1, wx.EXPAND | wx.ALL, 5)

        # Fetch and display key account details
        all_account_info = self.task_manager.get_memo_detail_df_for_account()
        key_account_details = self.task_manager.process_account_info(all_account_info)

        self.populate_summary_grid(key_account_details)

        self.summary_tab.Layout()  # Update the layout

        self.worker = XRPLMonitorThread(self.url, self)
        self.worker.start()

        # Immediately populate the grid with current data
        self.update_json_data(None)

        # Start the timer to fetch and update JSON data
        self.start_json_update_timer()

    def run_bg_job(self, job):
        if self.worker.context:
            asyncio.run_coroutine_threadsafe(job, self.worker.loop)

    def update_ledger(self, message):
        pass  # Simplified for this version

    def update_account(self, acct):
        xrp_balance = str(xrpl.utils.drops_to_xrp(acct["Balance"]))
        self.lbl_balance.SetLabel(f"XRP Balance: {xrp_balance}")

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

            wx.CallAfter(self.lbl_pft_balance.SetLabel, f"PFT Balance: {pft_balance}")

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

    def update_json_data(self, event):
        try:
            all_account_info = self.task_manager.get_memo_detail_df_for_account(
                account_address=self.wallet.classic_address, transaction_limit=5000
            )

            # Update Accepted tab
            json_data = self.task_manager.get_proposals_df(
                all_account_info=all_account_info
            ).to_json()
            logging.debug(f"Updating Accepted tab with JSON data: {json_data}")
            wx.PostEvent(self, UpdateGridEvent(json_data=json_data, target="accepted"))

            # Update Rewards tab
            rewards_data = self.task_manager.get_rewards_df(
                all_account_info=all_account_info
            ).to_json()
            logging.debug(f"Updating Rewards tab with JSON data: {rewards_data}")
            wx.PostEvent(self, UpdateGridEvent(json_data=rewards_data, target="rewards"))

            # Update Verification tab
            verification_data = self.task_manager.get_verification_df(
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

    def populate_summary_grid(self, key_account_details):
        self.summary_grid.ClearGrid()
        while self.summary_grid.GetNumberRows() > 0:
            self.summary_grid.DeleteRows(0, 1, False)

        for key, value in key_account_details.items():
            self.summary_grid.AppendRows(1)
            row = self.summary_grid.GetNumberRows() - 1
            self.summary_grid.SetCellValue(row, 0, key)
            self.summary_grid.SetCellValue(row, 1, str(value))

            # Enable text wrapping in the 'Value' column
            self.summary_grid.SetCellRenderer(row, 1, gridlib.GridCellAutoWrapStringRenderer())
            
            # Manually set row height for better display
            self.summary_grid.SetRowSize(row, 40)  # Adjust the height as needed

        # Set column width to ensure proper wrapping
        self.summary_grid.SetColSize(0, 200)
        self.summary_grid.SetColSize(1, 600)  # Adjust width as needed

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
        data = json.loads(json_data)
        proposals = data['proposal']
        rewards = data['reward']
        payouts = data.get('payout', {})  # Adding the payout data

        self.rewards_grid.ClearGrid()
        while self.rewards_grid.GetNumberRows() > 0:
            self.rewards_grid.DeleteRows(0, 1, False)

        for task_id, proposal in proposals.items():
            reward = rewards.get(task_id, "")
            payout = payouts.get(task_id, "")  # Getting the payout value
            self.rewards_grid.AppendRows(1)
            row = self.rewards_grid.GetNumberRows() - 1
            self.rewards_grid.SetCellValue(row, 0, task_id)
            self.rewards_grid.SetCellValue(row, 1, proposal)
            self.rewards_grid.SetCellValue(row, 2, reward)
            self.rewards_grid.SetCellValue(row, 3, str(payout))  # Setting the payout value

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

    def populate_verification_grid(self, json_data):
        data = json.loads(json_data)
        task_ids = data['task_id']
        original_tasks = data['original_task']
        verifications = data['verification']

        self.verification_grid.ClearGrid()
        while self.verification_grid.GetNumberRows() > 0:
            self.verification_grid.DeleteRows(0, 1, False)

        for idx, task_id in task_ids.items():
            original_task = original_tasks.get(idx, "")
            verification = verifications.get(idx, "")
            self.verification_grid.AppendRows(1)
            row = self.verification_grid.GetNumberRows() - 1
            self.verification_grid.SetCellValue(row, 0, task_id)
            self.verification_grid.SetCellValue(row, 1, original_task)
            self.verification_grid.SetCellValue(row, 2, verification)

            # Enable text wrapping in the 'original_task' and 'verification' columns
            self.verification_grid.SetCellRenderer(row, 1, gridlib.GridCellAutoWrapStringRenderer())
            self.verification_grid.SetCellRenderer(row, 2, gridlib.GridCellAutoWrapStringRenderer())
            
            # Manually set row height for better display
            self.verification_grid.SetRowSize(row, 65)  # Adjust the height as needed

        # Set column width to ensure proper wrapping
        self.verification_grid.SetColSize(0, 170)
        self.verification_grid.SetColSize(1, 400)  # Adjust width as needed
        self.verification_grid.SetColSize(2, 300)  # Adjust width as needed

    def on_ask_for_task(self, event):
        dialog = CustomDialog("Ask For Task", ["Task Request"])
        if dialog.ShowModal() == wx.ID_OK:
            request_message = dialog.GetValues()["Task Request"]
            all_account_info = self.task_manager.get_memo_detail_df_for_account(
                account_address=self.wallet.classic_address, transaction_limit=5000
            )
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
            all_account_info = self.task_manager.get_memo_detail_df_for_account(
                account_address=self.wallet.classic_address, transaction_limit=5000
            )
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
            all_account_info = self.task_manager.get_memo_detail_df_for_account(
                account_address=self.wallet.classic_address, transaction_limit=5000
            )
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
            all_account_info = self.task_manager.get_memo_detail_df_for_account(
                account_address=self.wallet.classic_address, transaction_limit=5000
            )
            response = self.task_manager.submit_initial_completion(
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
        all_account_info = self.task_manager.get_memo_detail_df_for_account(
            account_address=self.wallet.classic_address, transaction_limit=5000
        )

        try:
            verification_data = self.task_manager.get_verification_df(
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
            rewards_data = self.task_manager.get_rewards_df(
                all_account_info=all_account_info
            ).to_json()
            self.populate_rewards_grid(rewards_data)
        except:
            logging.error("FAILED UPDATING REWARDS DATA")

        try:
            acceptance_data = self.task_manager.get_proposals_df(
                all_account_info=all_account_info
            ).to_json()
            self.populate_accepted_grid(acceptance_data)
        except:
            logging.error("FAILED UPDATING ACCEPTANCE DATA")

    def on_submit_verification_details(self, event):
        task_id = self.txt_task_id.GetValue()
        response_string = self.txt_verification_details.GetValue()
        all_account_info = self.task_manager.get_memo_detail_df_for_account(
            account_address=self.wallet.classic_address, transaction_limit=5000
        )
        response = self.task_manager.send_verification_response(
            response_string=response_string,
            task_id=task_id,
            all_account_info=all_account_info
        )
        wx.MessageBox(str(response), 'Verification Submission Result', wx.OK | wx.ICON_INFORMATION)

    def on_log_pomodoro(self, event):
        task_id = self.txt_task_id.GetValue()
        pomodoro_text = self.txt_verification_details.GetValue()
        response = self.task_manager.send_pomodoro_for_task_id(task_id=task_id, pomodoro_text=pomodoro_text)
        message = self.task_manager.ux__convert_response_object_to_status_message(response)
        wx.MessageBox(message, 'Pomodoro Log Result', wx.OK | wx.ICON_INFORMATION)

    def on_submit_xrp_payment(self, event):
        amount = self.txt_xrp_amount.GetValue()
        address = self.txt_xrp_address_payment.GetValue()
        response = self.task_manager.send_xrp(amount, address)
        wx.MessageBox(str(response), 'XRP Payment Result', wx.OK | wx.ICON_INFORMATION)

    def on_submit_pft_payment(self, event):
        amount = self.txt_pft_amount.GetValue()
        address = self.txt_pft_address_payment.GetValue()
        response = self.task_manager.send_PFT_from_one_account_to_other(amount, address)
        wx.MessageBox(str(response), 'PFT Payment Result', wx.OK | wx.ICON_INFORMATION)

    def on_show_secret(self, event):
        classic_address = self.wallet.classic_address
        secret = self.wallet.seed
        wx.MessageBox(f"Classic Address: {classic_address}\nSecret: {secret}", 'Wallet Secret', wx.OK | wx.ICON_INFORMATION)

if __name__ == "__main__":
    networks = {
        "mainnet": "wss://xrplcluster.com",
        "testnet": "wss://s.altnet.rippletest.net:51233",
    }
    app = wx.App()
    frame = WalletApp(networks["mainnet"])
    frame.Show()
    app.MainLoop()
