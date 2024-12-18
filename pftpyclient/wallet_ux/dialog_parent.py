from typing import Protocol, Any
from pftpyclient.utilities.task_manager import PostFiatTaskManager
from pftpyclient.configuration.configuration import ConfigurationManager

class WalletDialogParent(Protocol):
    """Protocol defining the interface that dialogs need from WalletApp"""

    @property
    def ws_url(self) -> str:
        """Access to the WebSocket URL"""
        ...

    @property
    def network_url(self) -> str:
        """Access to the XRPL network URL"""
        ...

    @property
    def task_manager(self) -> PostFiatTaskManager:
        """Access to the task manager"""
        ...

    @property
    def config(self) -> ConfigurationManager:
        """Access to the configuration manager"""
        ...

    def format_response(self, response: Any) -> str:
        """Format a transaction response for display
        
        Args:
            response: Response from XRPL transaction
            
        Returns:
            Formatted string for display in dialog
        """
        ...
    
    def _sync_and_refresh(self) -> None:
        """Sync wallet state and refresh UI"""
        ...
    
    def try_connect_endpoint(self, endpoint: str) -> bool:
        """
        Attempt to connect to a new RPC endpoint with timeout.
        
        Args:
            endpoint: The RPC endpoint URL to test
            timeout: Maximum time to wait for connection in seconds
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        ...

    def try_connect_ws_endpoint(self, endpoint: str) -> bool:
        """
        Attempt to connect to a new WebSocket endpoint.
        
        Args:
            endpoint: The WebSocket endpoint URL to test
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        ...
    
    def update_network_display(self) -> None:
        """Update the network display in the UI"""
        ...

    def refresh_grids(self) -> None:
        """Refresh all data grids"""
        ...

    def update_all_destination_comboboxes(self) -> None:
        """Update all comboboxes containing destination addresses"""
        ...

    def restart_xrpl_monitor(self) -> None:
        """Restart the XRPL monitor thread"""
        ...
