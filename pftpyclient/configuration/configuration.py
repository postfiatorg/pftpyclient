from pathlib import Path
import json
from loguru import logger
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass

USER_CONFIG = {
    # template for per-user preferences
}

class ConfigurationManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self.config_dir = Path.home().joinpath("postfiatcreds")
        self.config_file = self.config_dir / "pft_config.json"
        self.config = self._load_config()

    def _load_config(self):
        """Load config from file or create with defaults"""
        if not self.config_file.exists():
            config = {
                'global': GLOBAL_CONFIG_DEFAULTS.copy(),
                'user': USER_CONFIG.copy()
            }
            self._save_config(config)
            return config
        
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            return config
        
    def _save_config(self, config):
        """Save config to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config file: {e}")

    def get_global_config(self, key):
        """Get a global config value"""
        return self.config['global'].get(key, GLOBAL_CONFIG_DEFAULTS.get(key))
    
    def set_global_config(self, key, value):
        """Set a global config value"""
        if key in GLOBAL_CONFIG_DEFAULTS:
            self.config['global'][key] = value
            self._save_config(self.config)

    def get_user_config(self, username, key):
        """Get a user config value"""
        return self.config['user'].get(username, {})
    
    def set_user_config(self, username, key, value):
        """Set a user config value"""
        if username not in self.config['user']:
            self.config['user'][username] = {}
        self.config['user'][username][key] = value
        self._save_config(self.config)

    def get_network_endpoints(self) -> list:
        """Get recent endpoints for a specified network"""
        is_testnet = self.get_global_config('use_testnet')
        network = XRPL_TESTNET if is_testnet else XRPL_MAINNET
        key = 'testnet_rpc_endpoints' if is_testnet else 'mainnet_rpc_endpoints'
        
        stored_endpoints = self.config['global'].get(key, [])
        default_endpoints = network.public_rpc_urls

        all_endpoints = []

        for endpoint in stored_endpoints:
            if endpoint not in all_endpoints:
                all_endpoints.append(endpoint)

        for endpoint in default_endpoints:
            if endpoint not in all_endpoints:
                all_endpoints.append(endpoint)

        return all_endpoints
    
    def get_current_endpoint(self) -> str:
        """Get current endpoint for a specified network"""
        is_testnet = self.get_global_config('use_testnet')
        endpoints = self.get_network_endpoints()

        # If we have a recent endpoint, use it
        if endpoints:
            return endpoints[0]

        # Otherwise use default from constants
        network = XRPL_TESTNET if is_testnet else XRPL_MAINNET
        endpoint = network.public_rpc_urls[0]  # Get first endpoint from list
        self.set_current_endpoint(endpoint)
        return endpoint

    def set_current_endpoint(self, endpoint: str):
        """Set current endpoint for a specified network"""
        is_testnet = self.get_global_config('use_testnet')
        key = 'testnet_rpc_endpoints' if is_testnet else 'mainnet_rpc_endpoints'
        endpoints = self.config['global'].get(key, [])
        
        # Remove endpoint if it exists in the queue
        if endpoint in endpoints:
            endpoints.remove(endpoint)
            
        # Add to front of queue
        endpoints.insert(0, endpoint)

        # Keep only last 5 endpoints
        endpoints = endpoints[:5]
        
        # Ensure the endpoint is saved
        self.config['global'][key] = endpoints
        self._save_config(self.config)

        # Reload config
        self.config = self._load_config()

    def get_ws_endpoints(self) -> list:
        """Get recent WebSocket endpoints for the current network"""
        is_testnet = self.get_global_config('use_testnet')
        network = XRPL_TESTNET if is_testnet else XRPL_MAINNET
        key = 'testnet_ws_endpoints' if is_testnet else 'mainnet_ws_endpoints'

        stored_endpoints = self.config['global'].get(key, [])
        default_endpoints = network.websockets

        all_endpoints = []

        # Add stored endpoints first
        for endpoint in stored_endpoints:
            if endpoint not in all_endpoints:
                all_endpoints.append(endpoint)

        # Then add default endpoints
        for endpoint in default_endpoints:
            if endpoint not in all_endpoints:
                all_endpoints.append(endpoint)

        return all_endpoints
    
    def get_current_ws_endpoint(self) -> str:
        """Get current WebSocket endpoint for the current network"""
        is_testnet = self.get_global_config('use_testnet')
        endpoints = self.get_ws_endpoints()

        # If we have a recent endpoint, use it
        if endpoints:
            return endpoints[0]

        # Otherwise use default from constants
        network = XRPL_TESTNET if is_testnet else XRPL_MAINNET
        endpoint = network.websockets[0]  # Get first endpoint from list
        self.set_current_ws_endpoint(endpoint)
        return endpoint
    
    def set_current_ws_endpoint(self, endpoint: str):
        """Set current WebSocket endpoint for the current network"""
        is_testnet = self.get_global_config('use_testnet')
        key = 'testnet_ws_endpoints' if is_testnet else 'mainnet_ws_endpoints'
        endpoints = self.config['global'].get(key, [])
        
        # Remove endpoint if it exists in the queue
        if endpoint in endpoints:
            endpoints.remove(endpoint)
            
        # Add to front of queue
        endpoints.insert(0, endpoint)

        # Keep only last 5 endpoints
        endpoints = endpoints[:5]
        
        # Update config
        self.config['global'][key] = endpoints
        self._save_config(self.config)

        # Reload config
        self.config = self._load_config()

@dataclass
class NetworkConfig:
    """Configuration for an XRPL network (mainnet or testnet)"""
    name: str
    node_name: str
    node_address: str
    remembrancer_name: str
    remembrancer_address: str
    issuer_address: str
    websockets: List[str]
    public_rpc_urls: List[str]
    explorer_tx_url_mask: str
    explorer_account_url_mask: str
    local_rpc_url: Optional[str] = None

XRPL_MAINNET = NetworkConfig(
    name="mainnet",
    node_name="postfiatfoundation",
    node_address="r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD",
    remembrancer_name="postfiatfoundation_remembrancer",
    remembrancer_address="rJ1mBMhEBKack5uTQvM8vWoAntbufyG9Yn",
    issuer_address="rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW",
    websockets=[
        "wss://xrpl.postfiat.org:6007",
        "wss://xrplcluster.com", 
        "wss://xrpl.ws/", 
        "wss://s1.ripple.com/", 
        "wss://s2.ripple.com/"
    ],
    public_rpc_urls=[
        "https://xrplcluster.com/",
        "https://s2.ripple.com:51234"
    ],
    local_rpc_url=None,  # No local node for mainnet yet
    explorer_tx_url_mask='https://livenet.xrpl.org/transactions/{hash}/detailed',
    explorer_account_url_mask='https://livenet.xrpl.org/accounts/{address}'
)

XRPL_TESTNET = NetworkConfig(
    name="testnet",
    node_name="postfiatfoundation_testnet",
    node_address="rUWuJJLLSH5TUdajVqsHx7M59Vj3P7giQV",
    remembrancer_name="postfiatfoundation_testnet_remembrancer",
    remembrancer_address="rN2oaXBhFE9urGN5hXup937XpoFVkrnUhu",
    issuer_address="rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN",
    websockets=[
        "wss://s.altnet.rippletest.net:51233"
    ],
    public_rpc_urls=[
        "https://clio.altnet.rippletest.net:51234/",
        "https://testnet.xrpl-labs.com/",
        "https://s.altnet.rippletest.net:51234"
    ],
    local_rpc_url=None,  # No local node for testnet yet
    explorer_tx_url_mask='https://testnet.xrpl.org/transactions/{hash}/detailed',
    explorer_account_url_mask='https://testnet.xrpl.org/accounts/{address}'
)

class Network(Enum):
    XRPL_MAINNET = XRPL_MAINNET
    XRPL_TESTNET = XRPL_TESTNET

# Helper function to get current network config
def get_network_config(network: Optional[Network] = None) -> NetworkConfig:
    """Get network configuration based on Network enum.
    
    Args:
        network: Optional Network enum value. If None, uses configuration setting
                to determine network.
        
    Returns:
        NetworkConfig: Configuration for the specified network
    """
    if network is None:
        config = ConfigurationManager()
        use_testnet = config.get_global_config('use_testnet')
        network = Network.XRPL_TESTNET if use_testnet else Network.XRPL_MAINNET

    return network.value

GLOBAL_CONFIG_DEFAULTS = {
    'performance_monitor': False,
    'transaction_cache_format': 'csv', # or pickle
    'last_logged_in_user': '',
    'require_password_for_payment': True,
    'use_testnet': False,
    'mainnet_rpc_endpoints': Network.XRPL_MAINNET.value.public_rpc_urls,
    'testnet_rpc_endpoints': Network.XRPL_TESTNET.value.public_rpc_urls,
    'mainnet_ws_endpoints': Network.XRPL_MAINNET.value.websockets,
    'testnet_ws_endpoints': Network.XRPL_TESTNET.value.websockets,
    'update_branch': 'main'  # or 'dev'
}
