from loguru import logger
import pandas as pd
import wx
import webbrowser
from typing import Optional, TYPE_CHECKING
from .dialog_parent import WalletDialogParent
import traceback

if TYPE_CHECKING:
    from pftpyclient.utilities.task_manager import PostFiatTaskManager
    from pftpyclient.configuration.configuration import ConfigurationManager

class ConfirmPaymentDialog(wx.Dialog):
    """Dialog to confirm payment details and optionally save new contacts"""

    def __init__(
            self, 
            parent: WalletDialogParent, 
            amount: str, 
            destination: str, 
            token_type: str
        ) -> None:
        """Initialize the payment confirmation dialog
        
        Args:
            parent: Parent window implementing WalletAppProtocol
            amount: Amount of tokens to send
            destination: Destination address
            token_type: Type of token being sent (e.g. 'XRP', 'PFT')
        """
        super().__init__(parent, title="Confirm Payment", style=wx.DEFAULT_DIALOG_STYLE)
        self.task_manager = parent.task_manager
        self.destination = destination

        # Check if destination is a known contact
        contacts = self.task_manager.get_contacts()
        contact_name = contacts.get(destination)

        self.InitUI(amount, destination, token_type, contact_name)
        self.Fit()
        self.Center()

    def InitUI(
            self, 
            amount: str, 
            destination: str, 
            token_type: str, 
            contact_name: Optional[str]
        ) -> None:
        """Initialize the dialog UI
        
        Args:
            amount: Amount of tokens to send
            destination: Destination address
            token_type: Type of token being sent
            contact_name: Name of contact if destination is known, None otherwise
        """
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

    def on_checkbox(self, event: wx.CommandEvent) -> None:
        """Handle checkbox toggle"""
        self.contact_name.Show(self.save_contact.GetValue())
        self.Fit() # Resize dialog to fit new size

    def get_contact_info(self) -> Optional[str]:
        """Return contact info if saving was requested
        
        Returns:
            Contact name if saving was requested and name provided, None otherwise
        """
        if not hasattr(self, 'save_contact') or not self.save_contact.GetValue():
            return None
        name = self.contact_name.GetValue().strip()
        return name if name else None

class ContactsDialog(wx.Dialog):
    """Dialog for managing wallet contacts"""

    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the contacts management dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
        super().__init__(
            parent, 
            title="Manage Contacts", 
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.task_manager: 'PostFiatTaskManager' = parent.task_manager
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

    def load_contacts(self) -> None:
        """Reload contacts list from storage"""
        self.contacts_list.DeleteAllItems()
        contacts = self.task_manager.get_contacts()
        for address, name in contacts.items():
            index = self.contacts_list.GetItemCount()
            self.contacts_list.InsertItem(index, name)
            self.contacts_list.SetItem(index, 1, address)
        self.contacts_list.Layout()
        self.Layout()

    def on_add(self, event: wx.CommandEvent) -> None:
        """Handle adding a new contact"""
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

    def on_delete(self, event: wx.CommandEvent) -> None:
        """Handle deleting a selected contact"""
        index = self.contacts_list.GetFirstSelected()
        if index >= 0:
            name = self.contacts_list.GetItem(index, 0).GetText()
            address = self.contacts_list.GetItem(index, 1).GetText()
            logger.debug(f"Deleting contact: {name} - {address}")
            self.task_manager.delete_contact(address)
            self.load_contacts()
            self.changes_made = True

    def on_close(self, event: wx.CommandEvent) -> None:
        """Handle dialog close"""
        if self.changes_made:
            self.EndModal(wx.ID_OK)
        else:
            self.EndModal(wx.ID_CANCEL)

class EndpointControl:
    """Helper class to manage endpoint UI controls and logic"""
    
    def __init__(
            self,
            parent: wx.Window,
            config: 'ConfigurationManager',
            label: str,
            get_current_fn: str,
            get_recent_fn: str,
            set_current_fn: str
        ) -> None:
        """Initialize endpoint control
        
        Args:
            parent: Parent window
            config: Configuration manager instance
            label: Label for the endpoint control
            get_current_fn: Name of config method to get current endpoint
            get_recent_fn: Name of config method to get recent endpoints
            set_current_fn: Name of config method to set current endpoint
        """
        self.parent = parent
        self.config = config
        self.get_current_fn = get_current_fn
        self.get_recent_fn = get_recent_fn
        self.set_current_fn = set_current_fn
        
        # Create UI elements
        self.sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.sizer.Add(wx.StaticText(parent, label=label), 0, wx.CENTER | wx.ALL, 5)
        self.combo = wx.ComboBox(parent, style=wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER)
        self.sizer.Add(self.combo, 1, wx.EXPAND | wx.ALL, 5)
        
        # Initial update
        self.update_combo()
        
    def update_combo(self) -> None:
        """Update endpoint combobox with current and recent endpoints"""
        current = getattr(self.config, self.get_current_fn)()
        recent = getattr(self.config, self.get_recent_fn)()
        
        # Get desired items (current first, then others)
        desired_items = [current] + [ep for ep in recent if ep != current]
        
        # Remove items that shouldn't be there
        count = self.combo.GetCount()
        for i in range(count-1, -1, -1):
            if self.combo.GetString(i) not in desired_items:
                self.combo.Delete(i)
                
        # Add missing items
        existing_items = [self.combo.GetString(i) for i in range(self.combo.GetCount())]
        for item in desired_items:
            if item not in existing_items:
                self.combo.Append(item)
                
        # Set current value
        self.combo.SetValue(current)
        self.combo.Refresh()
        self.combo.Update()
        
    def get_value(self) -> str:
        """Get current endpoint value"""
        return self.combo.GetValue().strip()
        
    def set_value(self, value: str) -> None:
        """Set current endpoint value"""
        self.combo.SetValue(value)
        
class PreferencesDialog(wx.Dialog):
    """Dialog for managing wallet preferences and settings"""

    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the preferences dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
        super().__init__(parent, title="Preferences")
        self.config: 'ConfigurationManager' = parent.config
        self.parent: 'WalletDialogParent' = parent

        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Application Settings Box
        app_sb = wx.StaticBox(panel, label="Application Settings")
        app_sbs = wx.StaticBoxSizer(app_sb, wx.VERTICAL)

        # Update Branch selection
        branch_box = wx.StaticBox(panel, label="Check for Updates")
        branch_sbs = wx.StaticBoxSizer(branch_box, wx.HORIZONTAL)
        self.main_branch = wx.RadioButton(panel, label="Main", style=wx.RB_GROUP)
        self.dev_branch = wx.RadioButton(panel, label="Development")
        current_branch = self.config.get_global_config('update_branch')
        self.main_branch.SetValue(current_branch == 'main')
        self.dev_branch.SetValue(current_branch == 'dev')
        branch_sbs.Add(self.main_branch, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        branch_sbs.Add(self.dev_branch, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        app_sbs.Add(branch_sbs, 0, wx.ALL | wx.EXPAND, 5)  

        # Require password for payment checkbox
        self.require_password_for_payment = wx.CheckBox(panel, label="Require password for payment")
        self.require_password_for_payment.SetValue(self.config.get_global_config('require_password_for_payment'))
        app_sbs.Add(self.require_password_for_payment, 0, wx.ALL | wx.EXPAND, 5)

        # Performance Monitor checkbox
        self.perf_monitor = wx.CheckBox(panel, label="Enable Performance Monitor")
        self.perf_monitor.SetValue(self.config.get_global_config('performance_monitor'))
        app_sbs.Add(self.perf_monitor, 0, wx.ALL | wx.EXPAND, 5)

        # Cache Format radio buttons
        cache_box = wx.StaticBox(panel, label="Transaction Cache Format")
        cache_sbs = wx.StaticBoxSizer(cache_box, wx.HORIZONTAL)
        self.cache_csv = wx.RadioButton(panel, label="CSV", style=wx.RB_GROUP)
        self.cache_pickle = wx.RadioButton(panel, label="Pickle")
        current_format = self.config.get_global_config("transaction_cache_format")
        self.cache_csv.SetValue(current_format == "csv")
        self.cache_pickle.SetValue(current_format != "csv")
        cache_sbs.Add(self.cache_csv, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        cache_sbs.Add(self.cache_pickle, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        app_sbs.Add(cache_sbs, 0, wx.ALL | wx.EXPAND, 5)

        vbox.Add(app_sbs, 0, wx.ALL | wx.EXPAND, 10)

        # Network Settings Box
        net_sb = wx.StaticBox(panel, label="Network Settings")
        net_sbs = wx.StaticBoxSizer(net_sb, wx.VERTICAL)

        # Network selection radio buttons
        network_box = wx.StaticBox(panel, label="XRPL Network")
        network_sbs = wx.StaticBoxSizer(network_box, wx.HORIZONTAL)
        self.mainnet_radio = wx.RadioButton(panel, label="Mainnet", style=wx.RB_GROUP)
        self.testnet_radio = wx.RadioButton(panel, label="Testnet")
        use_testnet = self.config.get_global_config('use_testnet')
        self.testnet_radio.SetValue(use_testnet)
        self.mainnet_radio.SetValue(not use_testnet)
        network_sbs.Add(self.mainnet_radio, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        network_sbs.Add(self.testnet_radio, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        net_sbs.Add(network_sbs, 0, wx.ALL | wx.EXPAND, 5)

        # Endpoints box
        endpoints_box = wx.StaticBox(panel, label="Network Endpoints")
        endpoints_sbs = wx.StaticBoxSizer(endpoints_box, wx.VERTICAL)

        # RPC Endpoint selection
        self.http_endpoint = EndpointControl(
            parent=panel,
            config=self.config,
            label="HTTP Endpoint:",
            get_current_fn='get_current_endpoint',
            get_recent_fn='get_network_endpoints',
            set_current_fn='set_current_endpoint'
        )
        endpoints_sbs.Add(self.http_endpoint.sizer, 0, wx.EXPAND | wx.ALL, 5)

        # WebSocket Endpoint control
        self.ws_endpoint = EndpointControl(
            parent=panel,
            config=self.config,
            label="WebSocket Endpoint:",
            get_current_fn='get_current_ws_endpoint',
            get_recent_fn='get_ws_endpoints',
            set_current_fn='set_current_ws_endpoint'
        )
        endpoints_sbs.Add(self.ws_endpoint.sizer, 0, wx.EXPAND | wx.ALL, 5)

        net_sbs.Add(endpoints_sbs, 0, wx.ALL | wx.EXPAND, 5)
        vbox.Add(net_sbs, 0, wx.ALL | wx.EXPAND, 10)

        # Network toggle events
        self.mainnet_radio.Bind(wx.EVT_RADIOBUTTON, self.on_network_changed)
        self.testnet_radio.Bind(wx.EVT_RADIOBUTTON, self.on_network_changed)

        # HTTP endpoint events
        self.http_endpoint.combo.Bind(wx.EVT_COMBOBOX, 
            lambda evt: self.on_endpoint_selected(evt, 'http'))
        self.http_endpoint.combo.Bind(wx.EVT_TEXT_ENTER, 
            lambda evt: self.on_endpoint_text_enter(evt, 'http'))
            
        # WebSocket endpoint events
        self.ws_endpoint.combo.Bind(wx.EVT_COMBOBOX, 
            lambda evt: self.on_endpoint_selected(evt, 'ws'))
        self.ws_endpoint.combo.Bind(wx.EVT_TEXT_ENTER, 
            lambda evt: self.on_endpoint_text_enter(evt, 'ws'))

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

        self.SetMinSize((500, -1))
        self.SetSize(self.GetBestSize())
        self.Center()

    def update_endpoint_combo(self) -> None:
        """Update endpoint combobox based on selected network"""
        self.http_endpoint.update_combo()
        self.ws_endpoint.update_combo()

    def on_network_changed(self, event: wx.CommandEvent) -> None:
        """Handle network selection change"""
        self.update_endpoint_combo()

    def on_endpoint_selected(self, event: wx.CommandEvent, endpoint_type: str) -> None:
        """Handle endpoint selection from dropdown
        
        Args:
            event: The event that triggered this callback
            endpoint_type: Type of endpoint ('http' or 'ws')
        """
        control = self.http_endpoint if endpoint_type == 'http' else self.ws_endpoint
        selected_endpoint = control.get_value()
        self.handle_endpoint_change(selected_endpoint, endpoint_type)

    def on_endpoint_text_enter(self, event: wx.CommandEvent, endpoint_type: str) -> None:
        """Handle endpoint text entry
        
        Args:
            event: The event that triggered this callback
            endpoint_type: Type of endpoint ('http' or 'ws')
        """
        control = self.http_endpoint if endpoint_type == 'http' else self.ws_endpoint
        self.handle_endpoint_change(control.get_value(), endpoint_type)

    def handle_endpoint_change(self, new_endpoint: str, endpoint_type: str) -> None:
        """Handle endpoint selection/entry
        
        Args:
            new_endpoint: The new endpoint URL to connect to
            endpoint_type: Type of endpoint ('http' or 'ws')
        """
        new_endpoint = new_endpoint.strip()
        if not new_endpoint:
            return

        try:
            # Store current endpoint for fallback
            control = self.http_endpoint if endpoint_type == 'http' else self.ws_endpoint
            get_current_fn = control.get_current_fn
            set_current_fn = control.set_current_fn
            current_endpoint = getattr(self.config, get_current_fn)()

            # Attempt to connect with timeout
            if endpoint_type == 'http':
                success = self.parent.try_connect_endpoint(new_endpoint)
            else:  # ws
                success = self.parent.try_connect_ws_endpoint(new_endpoint)

            if success:
                # Update configuration
                getattr(self.config, set_current_fn)(new_endpoint)
                control.update_combo()

                # Update the main WalletApp's endpoint
                if endpoint_type == 'http':
                    self.parent.network_url = new_endpoint
                else:
                    self.parent.ws_url = new_endpoint
                    self.parent.restart_xrpl_monitor()
                self.parent.update_network_display()
                logger.debug(f"Updated WalletApp {endpoint_type} endpoint to: {new_endpoint}")

            else:
                wx.MessageBox(
                    f"Failed to connect to {endpoint_type.upper()} endpoint. Reverting to previous endpoint.",
                    "Connection Failed",
                    wx.OK | wx.ICON_ERROR
                )
                # Revert to previous endpoint
                getattr(self.config, set_current_fn)(current_endpoint)
                control.update_combo()
        except Exception as e:
            wx.MessageBox(
                f"Error connecting to {endpoint_type.upper()} endpoint: {e}",
                "Connection Error",
                wx.OK | wx.ICON_ERROR
            )
            control.update_combo()

    def on_ok(self, event: wx.CommandEvent) -> None:
        """Save config when OK is clicked"""
        # Check if network setting changed
        old_network = self.config.get_global_config('use_testnet')
        new_network = self.testnet_radio.GetValue()

        if old_network != new_network:
            wx.MessageBox("Network change requires a restart to take effect", "Restart Required", wx.OK | wx.ICON_WARNING)

        self.config.set_global_config('update_branch', 'main' if self.main_branch.GetValue() else 'dev')
        self.config.set_global_config('use_testnet', new_network)
        self.config.set_global_config('require_password_for_payment', self.require_password_for_payment.GetValue())
        self.config.set_global_config('performance_monitor', self.perf_monitor.GetValue())
        self.config.set_global_config('transaction_cache_format', 'csv' if self.cache_csv.GetValue() else 'pickle')
        self.EndModal(wx.ID_OK)

class LinkOpeningHtmlWindow(wx.html.HtmlWindow):
    """Custom HtmlWindow that opens links in the default web browser"""

    def OnLinkClicked(self, link: wx.html.HtmlLinkEvent) -> None:
        """Handle clicked links by opening them in the default browser
        
        Args:
            link: The clicked link information
        """
        url = link.GetHref()
        logger.debug(f"Link clicked: {url}")
        try:
            webbrowser.open(url, new=2)
            logger.debug(f"Attempted to open URL: {url}")
        except Exception as e:
            logger.error(f"Failed to open URL {url}. Error: {str(e)}")

class SelectableMessageDialog(wx.Dialog):
    """Dialog for displaying selectable HTML content with clickable links"""
    
    def __init__(
            self,
            parent: WalletDialogParent,
            title: str,
            message: str
        ) -> None:
        """Initialize the selectable message dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
            title: Dialog title
            message: HTML content to display
        """
        super().__init__(parent, title=title, size=(500, 400))

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.html_window = LinkOpeningHtmlWindow(panel, style=wx.html.HW_SCROLLBAR_AUTO)
        sizer.Add(self.html_window, 1, wx.EXPAND | wx.ALL, 10)

        ok_button = wx.Button(panel, wx.ID_OK, label="OK")
        cancel_button = wx.Button(panel, wx.ID_CANCEL, label="Cancel")

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(ok_button, 0, wx.ALL, 5)
        button_sizer.Add(cancel_button, 0, wx.ALL, 5)
        sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)

        self.SetContent(message)
        self.Center()
        
        # Bind the close event
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def on_close(self, event):
        """Handle window close button"""
        self.EndModal(wx.ID_CANCEL)

    def SetContent(self, message: str) -> None:
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

class EncryptionRequestsDialog(wx.Dialog):
    """Dialog for managing encryption requests"""

    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the encryption requests dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
        super().__init__(parent, title="Encryption Requests", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent: 'WalletDialogParent' = parent
        self.task_manager: 'PostFiatTaskManager' = parent.task_manager

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

    def on_selection_changed(self, event: wx.ListEvent) -> None:
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

    def on_accept(self, event: wx.CommandEvent) -> None:
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

    def on_close(self, event: wx.CommandEvent) -> None:
        self.Close()


class DeleteCredentialsDialog(wx.Dialog):
    """Dialog for deleting credentials"""

    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the delete credentials dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
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

class ChangePasswordDialog(wx.Dialog):
    """Dialog for changing the password"""
    
    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the change password dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
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

class GoogleDocSetupDialog(wx.Dialog):
    """Dialog for setting up or updating the Google Doc link"""

    def __init__(self, parent: WalletDialogParent, is_initial_setup: bool = False) -> None:
        """Initialize the update Google Doc link dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
        title = "Initial Google Doc Setup" if is_initial_setup else "Update Google Doc"
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.is_initial_setup = is_initial_setup
        self.InitUI()

    def InitUI(self) -> None:
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        # Google Doc link input
        doc_label = wx.StaticText(panel, label="Enter new Google Doc link:")
        self.doc_input = wx.TextCtrl(panel, size=(400, 50), style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        panel_sizer.Add(doc_label, 0, wx.ALL, 5)
        panel_sizer.Add(self.doc_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

        # Error message (hidden by default)
        self.error_label = wx.StaticText(panel, label="", style=wx.ST_NO_AUTORESIZE)
        self.error_label.SetForegroundColour(wx.RED)
        panel_sizer.Add(self.error_label, 1, wx.EXPAND | wx.ALL, 5)

        # Buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, "Update")
        cancel_button = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        button_sizer.Add(ok_button, 1, wx.ALL | wx.EXPAND, 5)
        button_sizer.Add(cancel_button, 1, wx.ALL | wx.EXPAND, 5)
        panel_sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(panel_sizer)

        dialog_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(dialog_sizer)

        self.Fit()
        self.Center()

        # Bind events
        self.doc_input.Bind(wx.EVT_TEXT, self.on_text_change)

    def on_text_change(self, event: wx.CommandEvent) -> None:
        """Clear error message when text changes"""
        if self.error_label.GetLabel():
            self.error_label.SetLabel("")
            self.Layout()
            self.Fit()
            self.Center()
        event.Skip()

    def show_error(self, message: str) -> None:
        """Show error message"""
        self.error_label.SetLabel(message)
        self.Layout()
        self.Fit()
        self.Center()

    def get_link(self) -> str:
        """Return the entered Google Doc link"""
        return self.doc_input.GetValue().strip()
    
    def EndModal(self, retCode: int) -> None:
        """Override EndModal to prevent dialog from closing on error"""
        if retCode == wx.ID_OK and self.error_label.IsShown():
            return
        super().EndModal(retCode)

class UpdateTrustlineDialog(wx.Dialog):
    """Dialog for updating PFT token trust line limit"""

    def __init__(self, parent: WalletDialogParent) -> None:
        """Initialize the update trust line dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
        """
        super().__init__(parent, title="Update Trust Line Limit")
        self.task_manager = parent.task_manager
        self.InitUI()

    def InitUI(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Add explanation text
        help_text = (
            """The trust line limit determines the maximum amount of PFT tokens
            that can be sent to your account.
            Enter a new limit value below:"""
        )
        text = wx.StaticText(panel, label=help_text)
        text.Wrap(400)
        sizer.Add(text, 0, wx.ALL | wx.EXPAND, 10)

        # Show current limit
        current_limit = self.task_manager.get_current_trust_limit()
        current_limit_formatted = "{:,.2f}".format(float(current_limit))
        current_text = wx.StaticText(panel, label=f"Current limit: {current_limit_formatted} PFT")
        sizer.Add(current_text, 0, wx.ALL, 10)

        # New limit input
        limit_sizer = wx.BoxSizer(wx.HORIZONTAL)
        limit_sizer.Add(wx.StaticText(panel, label="New limit:"), 0, wx.CENTER | wx.RIGHT, 5)
        self.limit_input = wx.TextCtrl(panel)
        self.limit_input.Bind(wx.EVT_TEXT, self.on_limit_changed)
        limit_sizer.Add(self.limit_input, 1)
        limit_sizer.Add(wx.StaticText(panel, label="PFT"), 0, wx.CENTER | wx.LEFT, 5)
        sizer.Add(limit_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # Error message (hidden by default)
        self.error_label = wx.StaticText(panel, label="")
        self.error_label.SetForegroundColour(wx.RED)
        sizer.Add(self.error_label, 0, wx.ALL | wx.EXPAND, 5)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.update_btn = wx.Button(panel, wx.ID_OK, "Update")
        self.update_btn.Enable(False)  # Disabled by default until valid input
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_sizer.Add(self.update_btn, 1, wx.RIGHT, 5)
        btn_sizer.Add(cancel_btn, 1, wx.LEFT, 5)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        panel.SetSizer(sizer)
        sizer.Fit(panel)
        self.SetSize((400, 250))
        self.Center()

    def validate_limit(self, limit_str: str) -> tuple[bool, str]:
        """Validate the limit value
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not limit_str:
            return False, "Please enter a limit value"
        
        try:
            limit_float = float(limit_str)
            if limit_float <= 0:
                return False, "Limit must be greater than 0"
            decimal_places = len(limit_str.split('.')[-1]) if '.' in limit_str else 0
            if decimal_places > 6:
                return False, "Maximum 6 decimal places allowed"
            return True, ""
        except ValueError:
            return False, "Please enter a valid number"

    def on_limit_changed(self, event):
        """Handle limit input changes"""
        limit_str = self.limit_input.GetValue().strip()
        is_valid, error_msg = self.validate_limit(limit_str)
        
        self.update_btn.Enable(is_valid)
        if error_msg:
            self.show_error(error_msg)
        else:
            self.show_error("")

    def show_error(self, message: str) -> None:
        """Show error message"""
        self.error_label.SetLabel(message)
        self.Layout()

    def get_new_limit(self) -> str:
        """Return the entered limit value"""
        return self.limit_input.GetValue().strip()    

class CustomDialog(wx.Dialog):
    """Custom dialog for displaying a form with text inputs"""

    def __init__(
            self, 
            parent: WalletDialogParent, 
            title: str, 
            fields: list[str], 
            message: str = None,
            placeholders: Optional[dict[str, str]] = None,
            readonly_values: Optional[dict[str, str]] = None
        ) -> None:
        """Initialize the custom dialog
        
        Args:
            parent: Parent window implementing WalletDialogParent protocol
            title: Dialog title
            fields: List of field names
            message: Optional message to display above the fields
            placeholders: Optional dict mapping field names to placeholder text
        """
        super().__init__(parent, title=title, size=(500, 200))
        self.fields = fields
        self.message = message
        self.placeholders = placeholders or {}
        self.readonly_values = readonly_values or {}
        self.InitUI()

        # For layout update before getting best size
        self.GetSizer().Fit(self)
        self.Layout()

        best_size = self.GetBestSize()
        min_height = best_size.height
        self.SetSize((500, min_height))

    def InitUI(self) -> None:
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

            if field in self.readonly_values:
                value_label = wx.StaticText(pnl, label=self.readonly_values[field])
                hbox.Add(value_label, proportion=1)
                self.text_controls[field] = value_label
            else:
                text_ctrl = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1, 100))
                if field in self.placeholders:
                    text_ctrl.SetHint(self.placeholders[field])
                self.text_controls[field] = text_ctrl
                hbox.Add(text_ctrl, proportion=1)

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

        # Set initial focus to close button so that placeholder text appears
        wx.CallAfter(self.close_button.SetFocus)

    def OnSubmit(self, e: wx.CommandEvent) -> None:
        self.EndModal(wx.ID_OK)

    def OnClose(self, e: wx.CommandEvent) -> None:
        self.EndModal(wx.ID_CANCEL)

    def GetValues(self) -> dict[str, str]:
        """Get values from all controls, including read-only values"""
        values = {}
        for field, control in self.text_controls.items():
            if isinstance(control, wx.TextCtrl):
                values[field] = control.GetValue()
            else:  # wx.StaticText for read-only fields
                values[field] = control.GetLabel()
        return values