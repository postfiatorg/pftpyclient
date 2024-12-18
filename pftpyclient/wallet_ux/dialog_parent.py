from typing import Protocol, Any
from pftpyclient.utilities.task_manager import PostFiatTaskManager
from pftpyclient.configuration.configuration import ConfigurationManager

class WalletDialogParent(Protocol):
    """Protocol defining the interface that dialogs need from WalletApp"""

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
        """Try to connect to a new XRPL endpoint
        
        Args:
            endpoint: URL of the endpoint to connect to
            
        Returns:
            True if connection successful, False otherwise
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