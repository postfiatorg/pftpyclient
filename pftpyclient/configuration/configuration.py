from pathlib import Path
import json
from loguru import logger

GLOBAL_CONFIG = {
    'performance_monitor': False,
    'transaction_cache_format': 'csv', # or pickle
    'last_logged_in_user': ''
}

USER_CONFIG = {
    # template for per-user preferences
}

class ConfigurationManager:
    def __init__(self):
        self.config_dir = Path.home().joinpath("postfiatcreds")
        self.config_file = self.config_dir / "pft_config.json"
        self.config = self._load_config()

    def _load_config(self):
        """Load config from file or create with defaults"""
        if not self.config_file.exists():
            config = {
                'global': GLOBAL_CONFIG.copy(),
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
        return self.config['global'].get(key, GLOBAL_CONFIG.get(key))
    
    def set_global_config(self, key, value):
        """Set a global config value"""
        if key in GLOBAL_CONFIG:
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
