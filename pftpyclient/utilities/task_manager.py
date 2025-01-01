# Standard library imports
import os
import binascii
import re
import random 
import string
import datetime
import time
import json
import ast
from decimal import Decimal
import hashlib
import base64
from typing import Union, Optional
import traceback
from typing import List
import math

# Third-party imports
import xrpl
from xrpl.models.requests import AccountTx
from xrpl.models.transactions import Memo
from xrpl.utils import str_to_hex
import nest_asyncio
import pandas as pd
import numpy as np
from loguru import logger
import requests
import brotli
from cryptography.fernet import Fernet

# PftPyclient imports
from pftpyclient.basic_utilities.settings import *
from pftpyclient.user_login.credentials import CredentialManager
from pftpyclient.basic_utilities.settings import DATADUMP_DIRECTORY_PATH
from pftpyclient.utilities.wallet_state import (
    WalletState, 
    requires_wallet_state,
    FUNDED_STATES,
    TRUSTLINED_STATES,
    INITIATED_STATES,
    HANDSHAKED_STATES,
    ACTIVATED_STATES
)
from pftpyclient.performance.monitor import PerformanceMonitor
from pftpyclient.configuration.configuration import ConfigurationManager, get_network_config
from pftpyclient.configuration.constants import *
import pftpyclient.configuration.constants as constants
from pftpyclient.utilities.transaction_requirements import TransactionRequirementService

nest_asyncio.apply()

SAVE_MEMO_TRANSACTIONS = True
SAVE_TASKS = True
SAVE_MEMOS = True
SAVE_SYSTEM_MEMOS = True

class PostFiatTaskManager:
    
    def __init__(self, username, password, network_url, config: ConfigurationManager):
        self.credential_manager=CredentialManager(username,password)
        self.config = config
        self.network_config = get_network_config()
        self.network_url = self.config.get_current_endpoint()
        self.default_node = self.network_config.node_address
        self.pft_issuer = self.network_config.issuer_address

        self.user_wallet = self.spawn_user_wallet()

        # initialize dataframe filepaths for caching
        use_testnet = self.config.get_global_config('use_testnet')
        network_suffix = '_TESTNET' if use_testnet else ''
        file_extension = 'pkl' if self.config.get_global_config('transaction_cache_format') == 'pickle' else 'csv'
        self.tx_history_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.address}{network_suffix}_transaction_history.{file_extension}")
        self.memo_tx_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.address}{network_suffix}_memo_transactions.{file_extension}")
        self.memos_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.address}{network_suffix}_memos.{file_extension}")  # only used for debugging
        self.tasks_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.address}{network_suffix}_tasks.{file_extension}")  # only used for debugging
        self.system_memos_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.address}{network_suffix}_system_memos.{file_extension}")  # only used for debugging
        
        # initialize dataframes for caching
        self.transactions = pd.DataFrame()
        self.memo_transactions = pd.DataFrame()
        self.tasks = pd.DataFrame()
        self.memos = pd.DataFrame()
        self.system_memos = pd.DataFrame()

        self.handshake_cache = {}  # Address -> (handshake_sent, received_key)

        # Initialize client for blockchain queries
        self.client = xrpl.clients.JsonRpcClient(self.network_url)
        
        # Initialize transactions
        self.sync_transactions()

        # Initialize transaction requirement service
        self.transaction_requirements = TransactionRequirementService(self.network_config)

        # Initialize wallet state based on account status
        self.determine_wallet_state()

    def get_xrp_balance(self):
        return get_xrp_balance(self.network_url, self.user_wallet.classic_address)
    
    def determine_wallet_state(self):
        """Determine the current state of the wallet based on blockhain"""
        logger.debug(f"Determining wallet state for {self.user_wallet.classic_address}")
        client = xrpl.clients.JsonRpcClient(self.network_url)
        self.wallet_state = WalletState.UNFUNDED
        try:
            # Check if account exists on XRPL
            response = client.request(
                xrpl.models.requests.AccountInfo(
                    account=self.user_wallet.classic_address,
                    ledger_index="validated"
                )
            )

            if response.is_successful() and 'account_data' in response.result:
                balance = int(response.result['account_data']['Balance'])
                if balance > 0:
                    self.wallet_state = WalletState.FUNDED
                    if self.has_trust_line():
                        self.wallet_state = WalletState.TRUSTLINED
                        if self.initiation_rite_sent():
                            self.wallet_state = WalletState.INITIATED
                            if self.handshake_sent():
                                self.wallet_state = WalletState.HANDSHAKE_SENT
                                if self.handshake_received():
                                    self.wallet_state = WalletState.HANDSHAKE_RECEIVED
                                    if self.google_doc_sent():
                                        self.wallet_state = WalletState.ACTIVE
            else:
                logger.warning(f"Account {self.user_wallet.classic_address} does not exist on XRPL")
        
        except xrpl.clients.XRPLRequestFailureException as e:
            logger.error(f"Error determining wallet state: {e}")
        
    def get_required_action(self):
        """Returns the next required action to take to unlock the wallet"""
        match self.wallet_state:
            case WalletState.UNFUNDED:
                return "Fund wallet with XRP"
            case WalletState.FUNDED:
                return "Set PFT trust line"
            case WalletState.TRUSTLINED:
                return "Send initiation rite"
            case WalletState.INITIATED:
                return "Send handshake to node"
            case WalletState.HANDSHAKE_SENT:
                return "Await handshake response from node"
            case WalletState.HANDSHAKE_RECEIVED:
                return "Send google doc link"
            case WalletState.ACTIVE:
                return "No action required, Wallet is fully initialized"
            case _:
                return "Unknown wallet state"
        
    @requires_wallet_state(TRUSTLINED_STATES)
    def send_initiation_rite(self, commitment):
        memo = construct_initiation_rite_memo(user=self.credential_manager.postfiat_username, commitment=commitment)
        return send_xrp(network_url=self.network_url,
                        wallet=self.user_wallet, 
                        amount=1, 
                        destination=self.default_node, 
                        memo=memo)

    def save_dataframe(self, df, filepath, description):
        """
        Generic method to save a dataframe with error handling and logging
        
        :param dataframe: pandas Dataframe to save
        :param filepath: str, path to the file (will adjust extension based on format)
        :param description: str, description of the data being saved (i.e. "transactions" or "memos")
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            # Get base path without extension
            base_path = os.path.splitext(filepath)[0]

            # Determine format from preferences
            format_type = self.config.get_global_config('transaction_cache_format')

            if format_type == 'pickle':
                final_path = f"{base_path}.pkl"
                temp_path = f"{final_path}.tmp"
                df.to_pickle(temp_path)
            else:
                final_path = f"{base_path}.csv"
                temp_path = f"{final_path}.tmp"
                df.to_csv(temp_path, index=False)

            # Rplace existing file if save was successful
            os.replace(temp_path, final_path)
            logger.info(f"Successfully saved {description} to {final_path}")

        except PermissionError:
            logger.error(f"Permission denied when trying to save {description} to {filepath}")
        except IOError as e:
            logger.error(f"IOError when trying to save {description} to {filepath}: {e}")
        except pd.errors.EmptyDataError:
            logger.warning(f"No {description} to save. The dataframe is empty.")
        except Exception as e:
            logger.error(f"Unexpected error saving {description} to {filepath}: {e}")

    @PerformanceMonitor.measure('save_transactions')
    def save_transactions(self):
        self.save_dataframe(self.transactions, self.tx_history_filepath, "transactions")

    @PerformanceMonitor.measure('save_memo_transactions')
    def save_memo_transactions(self):
        self.save_dataframe(self.memo_transactions, self.memo_tx_filepath, "memo_transactions")

    @PerformanceMonitor.measure('save_tasks')
    def save_tasks(self):
        self.save_dataframe(self.tasks, self.tasks_filepath, "tasks")

    @PerformanceMonitor.measure('save_memos')
    def save_memos(self):
        self.save_dataframe(self.memos, self.memos_filepath, "memos")

    @PerformanceMonitor.measure('save_system_memos')
    def save_system_memos(self):
        self.save_dataframe(self.system_memos, self.system_memos_filepath, "system_memos")

    @PerformanceMonitor.measure('load_transactions')
    def load_transactions(self):
        """ Loads the transactions from file into a dataframe, and deserializes some columns"""
        tx_df = None
        base_path = os.path.splitext(self.tx_history_filepath)[0]
        format_type = self.config.get_global_config('transaction_cache_format')
        
        # Determine which file to try loading
        if format_type == 'pickle':
            file_path = f"{base_path}.pkl"
        else:
            file_path = f"{base_path}.csv"

        if os.path.exists(file_path):
            logger.debug(f"Loading transactions from {file_path}")
            try:
                if format_type == 'pickle':
                    tx_df = pd.read_pickle(file_path)
                else:
                    tx_df = pd.read_csv(file_path)

                    # deserialize columns for CSV
                    for col in ['meta', 'tx_json']:
                        if col in tx_df.columns:
                            tx_df[col] = tx_df[col].apply(lambda x: ast.literal_eval(x) if pd.notna(x) else x)

            except pd.errors.EmptyDataError:
                logger.warning(f"The file {file_path} is empty. Creating a new DataFrame.")
                return pd.DataFrame()
            except (IOError, pd.errors.ParserError) as e:
                logger.error(f"Error reading {file_path}: {e}. Deleting and creating a new DataFrame.")
                os.remove(file_path)
                return pd.DataFrame()
            except Exception as e:
                logger.error(f"Unexpected error loading transactions from {file_path}: {e}")
                return pd.DataFrame()
            else:
                return tx_df

        logger.warning(f"No existing transaction history file found at {self.tx_history_filepath}")
        return pd.DataFrame() # empty dataframe if file does not exist
    
    @PerformanceMonitor.measure('get_new_transactions')
    def get_new_transactions(self, last_known_ledger_index):
        """Retrieves new transactions from the node after the last known transaction date"""
        logger.debug(f"Getting new transactions after ledger index {last_known_ledger_index}")
        return self.get_account_transactions(
            account_address=self.user_wallet.classic_address,
            ledger_index_min=last_known_ledger_index,
            ledger_index_max=-1,
            limit=1000  # adjust as needed
        )

    @PerformanceMonitor.measure('sync_transactions')
    def sync_transactions(self) -> bool:
        """ Checks for new transactions and caches them locally. Also triggers memo update"""
        logger.debug("Updating transactions")

        # Check if account exists and is funded before proceeding
        try:
            # Get server state to determine available ledger range
            server_state = self.client.request(
                xrpl.models.requests.ServerState()
            )
            if server_state.is_successful():
                complete_ledgers = server_state.result['state']['complete_ledgers']
                # complete_ledgers is typically returned as a string like "32570-94329899"
                if '-' in complete_ledgers:
                    earliest_ledger, latest_ledger = map(int, complete_ledgers.split('-'))
                    logger.debug(f"Server ledger range: {earliest_ledger} to {latest_ledger}")
                else:
                    logger.debug(f"Unexpected complete_ledgers format: {complete_ledgers}")
            else:
                logger.debug("Could not fetch server state")
                return False
            
            response = self.client.request(
                xrpl.models.requests.AccountInfo(
                    account=self.user_wallet.classic_address,
                    ledger_index="validated"
                )
            )
            if not response.is_successful():
                logger.debug("Account not found or not funded, skipping transaction sync")
                return False

        except Exception as e:
            logger.error(f"Error checking account status: {e}")
            return False

        try:
            # Attempt to load transactions from local csv
            if self.transactions.empty: 
                loaded_tx_df = self.load_transactions()
                if not loaded_tx_df.empty:
                    logger.debug(f"Loaded {len(loaded_tx_df)} transactions from {self.tx_history_filepath}")
                    self.transactions = loaded_tx_df
                    self.sync_memo_transactions(loaded_tx_df)

            # Log local ledger index range
            if not self.transactions.empty:
                local_min_index = self.transactions['ledger_index'].min()
                local_max_index = self.transactions['ledger_index'].max()
                logger.debug(f"Local ledger index range: {local_min_index} to {local_max_index}")
            else:
                logger.debug("No local transactions found")

            # Choose ledger index to start sync from
            if self.transactions.empty:
                next_ledger_index = earliest_ledger  # Start from earliest available ledger
                logger.debug(f"Starting fresh sync from earliest available ledger: {earliest_ledger}")
            else:   
                next_ledger_index = self.transactions['ledger_index'].max() + 1
                logger.debug(f"Next ledger index: {next_ledger_index}")

            # fetch new transactions from the node
            new_transactions = self.get_new_transactions(next_ledger_index)
            
            # Convert list of transactions to DataFrame
            if new_transactions and len(new_transactions) > 0:  # Check if list is not empty
                new_tx_df = pd.DataFrame(new_transactions)
                logger.debug(f"Adding {len(new_tx_df)} new transactions...")
                
                # Add new transactions to the dataframe
                self.transactions = pd.concat([self.transactions, new_tx_df], ignore_index=True).drop_duplicates(subset=['hash'])
                self.save_transactions()
                self.sync_memo_transactions(new_tx_df)
                return True
            else:
                logger.debug("No new transactions found. Finished updating local tx history")
                return False
                
        except (TypeError, AttributeError) as e:
            logger.error(f"Error processing transaction history: {e}")
            logger.warning("Corrupted transaction history file detected. Deleting and starting fresh.")

            try:
                if os.path.exists(self.tx_history_filepath):
                    os.remove(self.tx_history_filepath)
                    logger.debug(f"Deleted corrupted cache file: {self.tx_history_filepath}")
            except Exception as delete_error:
                logger.error(f"Error deleting corrupted cache file: {delete_error}")

            # Reset dataframes
            self.transactions = pd.DataFrame()
            self.memo_transactions = pd.DataFrame()
            self.tasks = pd.DataFrame()
            self.memos = pd.DataFrame()
            self.system_memos = pd.DataFrame()

            # Try syncing transactions again with fresh state
            return self.sync_transactions()

    @PerformanceMonitor.measure('sync_memo_transactions')
    def sync_memo_transactions(self, new_tx_df):
        """Enriches transactions that contain memos with additional columns for easier processing"""
        logger.debug(f"Syncing transactions with memos")
        
        # Guard against empty input DataFrame
        if new_tx_df.empty or len(new_tx_df) == 0:
            logger.debug("Input DataFrame is empty - no memos to process")
            return

        # flag rows with memos
        new_tx_df['has_memos'] = new_tx_df['tx_json'].apply(lambda x: 'Memos' in x)

        # filter for rows with memos and convert to dataframe
        memo_tx_df = new_tx_df[new_tx_df['has_memos']== True].copy()
        
        # Guard against no memos found
        if memo_tx_df.empty or len(memo_tx_df) == 0:
            logger.debug("No transactions with memos found")
            return

        # Continue with processing only if we have memos
        try:
            # Extract first memo into a new column, serialize to dict
            memo_tx_df['memo_data'] = memo_tx_df['tx_json'].apply(
                lambda x: self.decode_memo_fields_to_dict(x['Memos'][0]['Memo'])
            )
            
            # Extract account and destination
            memo_tx_df['account'] = memo_tx_df['tx_json'].apply(lambda x: x['Account'])
            memo_tx_df['destination'] = memo_tx_df['tx_json'].apply(lambda x: x['Destination'])
            
            # Determine direction
            memo_tx_df['direction'] = np.where(
                memo_tx_df['destination']==self.user_wallet.classic_address, 
                'INCOMING',
                'OUTGOING'
            )
            
            # Derive counterparty address
            memo_tx_df['counterparty_address'] = memo_tx_df[['destination','account']].sum(1).apply(
                lambda x: str(x).replace(self.user_wallet.classic_address,'')
            )
            
            # Convert ripple timestamp to datetime
            memo_tx_df['datetime'] = memo_tx_df['tx_json'].apply(
                lambda x: self.convert_ripple_timestamp_to_datetime(x['date'])
            )
            
            # Get ledger_index from either tx_json or root level
            memo_tx_df['ledger_index'] = memo_tx_df.apply(
                lambda row: int(row['tx_json'].get('ledger_index', row.get('ledger_index', 0))), 
                axis=1
            )

            # Flag rows with PFT
            memo_tx_df['is_pft'] = memo_tx_df['tx_json'].apply(is_pft_transaction)

            # Update memo_transactions DataFrame
            if not memo_tx_df.empty and len(memo_tx_df) > 0:
                if self.memo_transactions.empty:
                    self.memo_transactions = memo_tx_df
                else:
                    self.memo_transactions = pd.concat(
                        [self.memo_transactions, memo_tx_df], 
                        ignore_index=True
                    ).drop_duplicates(subset=['hash'])

                logger.debug(f"Added {len(memo_tx_df)} memos to local memos dataframe")

                # Save if configured to do so
                if SAVE_MEMO_TRANSACTIONS:
                    self.save_memo_transactions()

                # Process derived data
                self.sync_tasks(memo_tx_df)
                self.sync_memos(memo_tx_df)
                self.sync_system_memos(memo_tx_df)

        except Exception as e:
            logger.error(f"Error processing memo transactions: {e}")
            logger.error(traceback.format_exc())

    @PerformanceMonitor.measure('sync_tasks')
    def sync_tasks(self, new_memo_tx_df):
        """ Updates the tasks dataframe with new tasks from the new memos.
        Task dataframe contains columns: user,task_id,full_output,hash,counterparty_address,datetime,task_type"""
        logger.debug(f"Syncing tasks")
        if new_memo_tx_df.empty or len(new_memo_tx_df) == 0:
            logger.debug("No new memos to process for tasks")
            return

        # Filter for memos that have a valid ID pattern 
        valid_id_df = new_memo_tx_df[
            new_memo_tx_df['memo_data'].apply(is_valid_id)
        ].copy()

        if valid_id_df.empty or len(valid_id_df) == 0:
            logger.debug("No memos with valid IDs found")
            return

        # Filter for task-specific content
        task_df = valid_id_df[
            valid_id_df['memo_data'].apply(lambda x: any(
                task_indicator in str(x['full_output'])
                for task_indicator in TASK_INDICATORS
            ))
        ].copy()

        if task_df.empty or len(task_df) == 0:
            logger.debug("No task-related memos found")
            return

        # Enrich memo_data with transaction metadata
        fields_to_add = ['hash','counterparty_address','datetime']
        task_df['memo_data'] = task_df.apply(
            lambda row: {
                **row['memo_data'], 
                **{field: row[field] for field in fields_to_add if field in row}
            } # ** is used to unpack the dictionaries
            , axis=1
        ).copy()

        # Convert the memo_data to a dataframe and add the task type
        task_df = pd.DataFrame(task_df['memo_data'].tolist())
        task_df['task_type'] = task_df['full_output'].apply(classify_task_string)

        # Concatenate new tasks to existing tasks and drop duplicates
        self.tasks = pd.concat([self.tasks, task_df], ignore_index=True).drop_duplicates(subset=['hash'])

        # for debugging purposes only
        if SAVE_TASKS:
            self.save_tasks()

    @PerformanceMonitor.measure('sync_memos')
    def sync_memos(self, new_memo_tx_df):
        """Updates messages dataframe with P2P message data"""
        logger.debug(f"Syncing memos")
        if new_memo_tx_df.empty or len(new_memo_tx_df) == 0:
            logger.debug("No new memos to process for messages")
            return
        
        # Filter for valid IDs first
        valid_id_df = new_memo_tx_df[
            new_memo_tx_df['memo_data'].apply(is_valid_id)
        ].copy()

        if valid_id_df.empty or len(valid_id_df) == 0:
            logger.debug("No memos with valid IDs found")
            return

        # Filter for message-specific content
        memo_df = valid_id_df[
            valid_id_df['memo_data'].apply(lambda x: any(
                message_indicator in str(x['full_output'])
                for message_indicator in MESSAGE_INDICATORS
            ))
        ].copy()

        if memo_df.empty or len(memo_df) == 0:
            logger.debug("No message-related memos found")
            return
        
        # Enrich memo_data with transaction metadata
        fields_to_add = ['hash','counterparty_address','datetime', 'direction']
        memo_df['memo_data'] = memo_df.apply(
            lambda row: {
                **row['memo_data'], 
                **{field: row[field] for field in fields_to_add if field in row}
            }, 
            axis=1
        ).copy()

        # Convert memo_data to new dataframe with all fields
        memo_df = pd.DataFrame(memo_df['memo_data'].tolist())

        # Process chunked messages, etc.
        self.memos = pd.concat([self.memos, memo_df], ignore_index=True)

        if SAVE_MEMOS:
            self.save_memos()

    @PerformanceMonitor.measure('sync_system_memos')
    def sync_system_memos(self, new_memo_tx_df):
        """Updates system_memos dataframe with special system messages"""
        logger.debug(f"Syncing system memos")
        if new_memo_tx_df.empty or len(new_memo_tx_df) == 0:
            logger.debug("No new memos to process for system messages")
            return
        
        # Filter for system message types
        system_df = new_memo_tx_df[
            new_memo_tx_df['memo_data'].apply(lambda x: any(
                indicator in str(x['task_id'])
                for indicator in SYSTEM_MEMO_TYPES
            ))
        ].copy()

        if system_df.empty or len(system_df) == 0:
            logger.debug("No system messages found")
            return

        # Enrich memo_data with transaction metadata
        fields_to_add = ['hash', 'counterparty_address', 'datetime', 'direction']
        system_df['memo_data'] = system_df.apply(
            lambda row: {
                **row['memo_data'],
                **{field: row[field] for field in fields_to_add if field in row}
            },
            axis=1
        ).copy()

        # Convert memo_data to new dataframe with all fields
        system_df = pd.DataFrame(system_df['memo_data'].tolist())

        # Update system_memos dataframe with deduplication
        self.system_memos = pd.concat(
            [self.system_memos, system_df], 
            ignore_index=True
        ).drop_duplicates(subset=['hash'])

        logger.debug(f"Added {len(system_df)} new system messages")

        if SAVE_SYSTEM_MEMOS: 
            self.save_system_memos()

    def get_task(self, task_id):
        """ Returns the task dataframe for a given task ID """
        task_df = self.tasks[self.tasks['task_id'] == task_id]
        if task_df.empty or len(task_df) == 0:
            raise NoMatchingTaskException(f"No task found with task_id {task_id}")
        return task_df
    
    def get_memo(self, memo_id):
        """Returns the memo dataframe for a given memo ID """
        memo_df = self.memos[self.memos['memo_id'] == memo_id]
        if memo_df.empty or len(memo_df) == 0:
            raise NoMatchingMemoException(f"No memo found with memo_id {memo_id}")
        return memo_df
    
    def get_task_state(self, task_df):
        """ Returns the latest state of a task given a task dataframe containing a single task_id """
        if task_df.empty or len(task_df) == 0:
            raise ValueError("The task dataframe is empty")

        # Confirm that the task_id column only has a single value
        if task_df['task_id'].nunique() != 1:
            raise ValueError("The task_id column must contain only one unique value")
        
        return task_df.sort_values(by='datetime').iloc[-1]['task_type']
    
    def get_task_state_using_task_id(self, task_id):
        """ Returns the latest state of a task given a task ID """
        return self.get_task_state(self.get_task(task_id))

    def convert_ripple_timestamp_to_datetime(self, ripple_timestamp = 768602652):
        ripple_epoch_offset = 946684800  # January 1, 2000 (00:00 UTC)
        
        
        unix_timestamp = ripple_timestamp + ripple_epoch_offset
        date_object = datetime.datetime.fromtimestamp(unix_timestamp)
        return date_object

    @staticmethod
    def hex_to_text(hex_string):
        bytes_object = bytes.fromhex(hex_string)
        ascii_string = bytes_object.decode("utf-8")
        return ascii_string
    
    @staticmethod
    def generate_custom_id():
        """ These are the custom IDs generated for each task that is generated
        in a Post Fiat Node """ 
        letters = ''.join(random.choices(string.ascii_uppercase, k=2))
        numbers = ''.join(random.choices(string.digits, k=2))
        second_part = letters + numbers
        date_string = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        output= date_string+'__'+second_part
        output = output.replace(' ',"_")
        return output
    
    @PerformanceMonitor.measure('send_xrp')
    def send_xrp(self, amount, destination, memo="", destination_tag=None):
        return send_xrp(self.network_url, self.user_wallet, amount, destination, memo, destination_tag=destination_tag)

    @staticmethod
    def decode_memo_fields_to_dict(memo: Union[xrpl.models.transactions.Memo, dict]):
        """Decodes hex-encoded XRP memo fields from a dictionary to a more readable dictionary format.
        
        The mapping from XRP memo fields to our internal format:
        - MemoFormat -> user: The username that sent the memo
        - MemoType -> task_id: Either:
            a) For task messages: A datetime-based ID (e.g., "2024-01-01_12:00")
            b) For system messages: The system message type (e.g., "HANDSHAKE", "INITIATION_RITE")
        - MemoData -> full_output: The actual content/payload of the memo
        """
        # TODO: Remove key changes and rely on MemoFormat, MemoType, MemoData to avoid confusion from context-switching
        # Handle xrpl.models.transactions.Memo objects
        if hasattr(memo, 'memo_format'):  # This is a Memo object
            fields = {
                'user': memo.memo_format,
                'task_id': memo.memo_type,
                'full_output': memo.memo_data
            }
        else:  # This is a dictionary from transaction JSON
            fields = {
                'user': memo.get('MemoFormat', ''),
                'task_id': memo.get('MemoType', ''),
                'full_output': memo.get('MemoData', '')
            }
        
        return {
            key: PostFiatTaskManager.hex_to_text(value or '')
            for key, value in fields.items()
        }

    def spawn_user_wallet(self):
        """ This takes the credential manager and loads the wallet from the
        stored seed associated with the user name"""
        seed = self.credential_manager.get_credential('v1xrpsecret')
        live_wallet = xrpl.wallet.Wallet.from_seed(seed)
        return live_wallet

    @PerformanceMonitor.measure('send_pft')
    def send_pft(self, amount, destination, memo=""):
        """ Sends PFT tokens to a destination address with optional memo. 
        If the memo is over 1 KB, it is split into multiple memos and response will be a list of responses"""

        # Check if the memo is a string and exceeds 1 KB
        # TODO: This is a temporary fix to handle the memo type
        # TODO: We need to handle the memo type properly
        if isinstance(memo, str) and is_over_1kb(memo):
            response = []
            logger.debug("Memo exceeds 1 KB, splitting into chunks")
            chunked_memo = self._chunk_memos(memo)

            # Split amount by number of chunks
            amount_per_chunk = amount / len(chunked_memo)

            # Send each chunk in a separate transaction
            for memo_chunk in chunked_memo:
                response.append(self._send_pft_single(amount_per_chunk, destination, memo_chunk))
        
        else:
            logger.debug("Memo is under 1 KB, sending in a single transaction")
            response = self._send_pft_single(amount, destination, memo)

        return response

    def _send_pft_single(self, amount, destination, memo):
        """Helper method to send a single PFT transaction"""
        client = xrpl.clients.JsonRpcClient(self.network_url)

        # Handle memo
        if isinstance(memo, Memo):
            memos = [memo]
        elif isinstance(memo, str):
            memos = [Memo(memo_data=str_to_hex(memo))]
        else:
            logger.error("Memo is not a string or a Memo object, raising ValueError")
            raise ValueError("Memo must be either a string or a Memo object")

        amount_to_send = xrpl.models.amounts.IssuedCurrencyAmount(
            currency="PFT",
            issuer=self.pft_issuer,
            value=str(amount)
        )

        payment = xrpl.models.transactions.Payment(
            account=self.user_wallet.address,
            amount=amount_to_send,
            destination=destination,
            memos=memos,
        )

        # Sign the transaction to get the hash
        # We need to derive the hash because the submit_and_wait function doesn't return a hash if transaction fails
        # TODO: tx_hash does not match the hash in the response
        # signed_tx = xrpl.transaction.sign(payment, self.user_wallet)
        # tx_hash = signed_tx.get_hash()

        try:
            logger.debug("Submitting and waiting for transaction")
            response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            response = f"Transaction submission failed: {e}"
            logger.error(response)
        except Exception as e:
            response = f"Unexpected error: {e}"
            logger.error(response)
            logger.error(traceback.format_exc())

        return response
    
    def handshake_sent(self):
        """Checks if the user has sent a handshake to the node"""
        logger.debug(f"Checking if user has sent handshake to the node. Wallet state: {self.wallet_state}")
        handshake_sent, _ = self.get_handshake_for_address(self.default_node)
        return handshake_sent
    
    def handshake_received(self):
        """Checks if the user has received a handshake from the node"""
        logger.debug(f"Checking if user has received handshake from the node. Wallet state: {self.wallet_state}")
        _, received_key = self.get_handshake_for_address(self.default_node)
        return received_key is not None
    
    @PerformanceMonitor.measure('get_handshakes')
    def get_handshakes(self):
        """ Returns a DataFrame of all handshake interactions with their current status"""
        if self.system_memos.empty or len(self.system_memos) == 0:
            return pd.DataFrame()
        
        # Get all handshakes (both incoming and outgoing)
        handshakes = self.system_memos[
            self.system_memos['task_id'].str.contains(SystemMemoType.HANDSHAKE.value, na=False)
        ]

        if handshakes.empty or len(handshakes) == 0:
            return pd.DataFrame()
        
        # Process each unique counterparty address
        results = []
        unique_addresses = handshakes['counterparty_address'].unique()

        for address in unique_addresses:
            # Get the earliest incoming handshake from this address (if any)
            incoming = handshakes[
                (handshakes['counterparty_address'] == address) &
                (handshakes['direction'] == 'INCOMING')
            ].sort_values('datetime')

            # Get the outgoing handshake to this address (if any)
            outgoing = handshakes[
                (handshakes['counterparty_address'] == address) &
                (handshakes['direction'] == 'OUTGOING')
            ].sort_values('datetime')

            result = {
                'address': address,
                'received_at': incoming.iloc[0]['datetime'] if not incoming.empty else None,
                'sent_at': outgoing.iloc[0]['datetime'] if not outgoing.empty else None,
                'encryption_ready': not incoming.empty and not outgoing.empty
            }

            results.append(result)

        # Create DataFrame and add contact information if available
        df = pd.DataFrame(results)
        contacts = self.credential_manager.get_contacts()
        df['contact_name'] = df['address'].map(contacts)
        df['display_address'] = df.apply(
            lambda x: x['contact_name'] + ' (' + x['address'] + ')' 
                if pd.notna(x['contact_name']) else x['address'],
            axis=1
        )

        return df

    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_handshake_for_address')
    def get_handshake_for_address(self, address: str) -> tuple[bool, str]:
        """Returns (handshake_sent, their_public_key) tuple where:
        - handshake_sent: Whether we've already sent our public key
        - received_key: Their ECDH public key if they've sent it, None otherwise
        """
        
        # attempt handshake cache first
        handshake_sent, received_key = self.handshake_cache.get(address, (False, None))
        if handshake_sent and received_key is not None:
            return handshake_sent, received_key

        if self.system_memos.empty or len(self.system_memos) == 0:
            logger.debug("No system memos found")
            return False, None
        
        # Filter for handshakes
        handshakes = self.system_memos[
            self.system_memos['task_id'].str.contains(SystemMemoType.HANDSHAKE.value, na=False)
        ]

        if handshakes.empty or len(handshakes) == 0:
            logger.debug("No handshakes found")
            return False, None

        # Get handshakes sent FROM the user TO this address
        sent_handshakes = handshakes[
            (handshakes['counterparty_address'] == address) & 
            (handshakes['direction'] == 'OUTGOING')
        ]
        handshake_sent = not sent_handshakes.empty

        # Get handshakes received FROM this address TO the user
        received_handshakes = handshakes[
            (handshakes['counterparty_address'] == address) &
            (handshakes['direction'] == 'INCOMING')
        ]
   
        received_key = None
        if not received_handshakes.empty and len(received_handshakes) > 0:
            logger.debug(f"Found {len(received_handshakes)} received handshakes from {address}")
            latest_received_handshake = received_handshakes.sort_values('datetime').iloc[-1]
            received_key = latest_received_handshake['full_output']
            logger.debug(f"Most recent received handshake: {received_key[:8]}...")

        result = (handshake_sent, received_key)
        self.handshake_cache[address] = result
        return result
    
    @requires_wallet_state(FUNDED_STATES)
    @PerformanceMonitor.measure('send_handshake')
    def send_handshake(self, destination):
        """Sends a handshake memo to establish encrypted communication"""
        logger.debug(f"Sending handshake to {destination}...")
        ecdh_public_key = self.credential_manager.get_ecdh_public_key()
        logger.debug(f"ECDH public key: {ecdh_public_key}, username: {self.credential_manager.postfiat_username}")
        handshake = construct_handshake_memo(
            user=self.credential_manager.postfiat_username,
            ecdh_public_key=ecdh_public_key
        )
        return self.send_memo(destination, handshake, compress=False)
    
    @staticmethod
    def calculate_memo_size(memo_format: str, memo_type: str, memo_data: str) -> dict:
        return calculate_memo_size(memo_format, memo_type, memo_data)

    @staticmethod
    def calculate_required_chunks(memo: Memo, max_size: int = constants.MAX_CHUNK_SIZE) -> int:
        """
        Calculates how many chunks will be needed to send a memo.
        
        Args:
            memo: Original Memo object to analyze
            max_size: Maximum size in bytes for each complete Memo object
            
        Returns:
            int: Number of chunks required
            
        Raises:
            ValueError: If the memo cannot be chunked (overhead too large)
        """
        # Extract memo components
        memo_dict = PostFiatTaskManager.decode_memo_fields_to_dict(memo)
        memo_format = memo_dict['user']
        memo_type = memo_dict['task_id']
        memo_data = memo_dict['full_output']

        logger.debug(f"Deconstructed (plaintext) memo sizes: "
                    f"memo_format: {len(memo_format)}, "
                    f"memo_type: {len(memo_type)}, "
                    f"memo_data: {len(memo_data)}")

        # Calculate overhead sizes
        size_info = PostFiatTaskManager.calculate_memo_size(memo_format, memo_type, "chunk_999__")  # assuming chunk_999__ is worst-case chunk label overhead
        max_data_size = max_size - size_info['total_size']

        logger.debug(f"Size allocation:")
        logger.debug(f"  Max size: {max_size}")
        logger.debug(f"  Total overhead: {size_info['total_size']}")
        logger.debug(f"  Available for data: {max_size} - {size_info['total_size']} = {max_data_size}")

        if max_data_size <= 0:
            raise ValueError(
                f"No space for data: max_size={max_size}, total_overhead={size_info['total_size']}"
            )
        
        # Calculate number of chunks needed
        data_bytes = memo_data.encode('utf-8')
        return math.ceil(len(data_bytes) / max_data_size)
    
    @staticmethod
    def _chunk_memos(memo: Memo, max_size: int = constants.MAX_CHUNK_SIZE) -> List[Memo]:
        """
        Splits a Memo object into multiple Memo objects, each under MAX_CHUNK_SIZE bytes.
        Only chunks the memo_data field while preserving memo_format and memo_type.
        
        Args:
            memo: Original Memo object to split
            max_size: Maximum size in bytes for each complete Memo object
            
        Returns:
            List of Memo objects, each under max_size bytes
        """
        logger.debug("Chunking memo...")

        # Extract memo components
        memo_dict = PostFiatTaskManager.decode_memo_fields_to_dict(memo)
        memo_format = memo_dict['user']
        memo_type = memo_dict['task_id']
        memo_data = memo_dict['full_output']

        # Calculate chunks needed and validate size
        num_chunks = PostFiatTaskManager.calculate_required_chunks(memo, max_size)
        chunk_size = len(memo_data.encode('utf-8')) // num_chunks
                
        # Split into chunks
        chunked_memos = []
        data_bytes = memo_data.encode('utf-8')
        for chunk_number in range(1, num_chunks + 1):
            start_idx = (chunk_number - 1) * chunk_size
            end_idx = start_idx + chunk_size if chunk_number < num_chunks else len(data_bytes)
            chunk = data_bytes[start_idx:end_idx]
            chunk_with_label = f"chunk_{chunk_number}__{chunk.decode('utf-8', errors='ignore')}"

            # Debug the sizes
            test_format = str_to_hex(memo_format)
            test_type = str_to_hex(memo_type)
            test_data = str_to_hex(chunk_with_label)
            
            logger.debug(f"Chunk {chunk_number} sizes:")
            logger.debug(f"  Plaintext Format size: {len(memo_format)}")
            logger.debug(f"  Plaintext Type size: {len(memo_type)}")
            logger.debug(f"  Plaintext Data size: {len(chunk_with_label)}")
            logger.debug(f"  Plaintext Total size: {len(memo_format) + len(memo_type) + len(chunk_with_label)}")
            logger.debug(f"  Hex Format size: {len(test_format)}")
            logger.debug(f"  Hex Type size: {len(test_type)}")
            logger.debug(f"  Hex Data size: {len(test_data)}")
            logger.debug(f"  Hex Total size: {len(test_format) + len(test_type) + len(test_data)}")
            
            chunk_memo = construct_memo(
                memo_format=memo_format,
                memo_type=memo_type,
                memo_data=chunk_with_label,
                validate_size=False  # TODO: The size validation appears too conservative
            )

            chunked_memos.append(chunk_memo)

        return chunked_memos

    @requires_wallet_state(FUNDED_STATES)
    @PerformanceMonitor.measure('encrypt_memo')
    def encrypt_memo(self, memo: str, shared_secret: str) -> str:
        """ Encrypts a memo using a shared secret """
        # Convert shared_secret to bytes if it isn't already
        if isinstance(shared_secret, str):
            shared_secret = shared_secret.encode()

        # Generate the Fernet key from shared secret
        key = base64.urlsafe_b64encode(hashlib.sha256(shared_secret).digest())
        fernet = Fernet(key)

        # Ensure memo is str before encoding to bytes
        if isinstance(memo, str):
            memo = memo.encode()
        elif isinstance(memo, bytes):
            pass
        else:
            raise ValueError(f"Memo must be string or bytes, not {type(memo)}")
        
        # Encrypt and return as string
        encrypted_bytes = fernet.encrypt(memo)
        return encrypted_bytes.decode()

    @requires_wallet_state(FUNDED_STATES)
    @PerformanceMonitor.measure('send_memo')
    def send_memo(
        self, 
        destination: str, 
        memo: Union[str, Memo], 
        username: Optional[str] = None,
        message_id: Optional[str] = None,
        chunk: bool = False,
        compress: bool = True, 
        encrypt: bool = False,
        pft_amount: Optional[Decimal] = None
    ):
        """Sends a memo to a destination with optional encryption, compression, and chunking.
        
        Args:
            destination: XRPL destination address
            memo: Either a string message or pre-constructed Memo object
            username: Optional user identifier for memo format field
            message_id: Optional custom ID for memo type field
            chunk: Whether to chunk the memo data (default False)
            compress: Whether to compress the memo data (default True)
            encrypt: Whether to encrypt the memo data (default False)
            pft_amount: Optional specific PFT amount to send
        """
        
        message_id = self.generate_custom_id()

        logger.debug(f"Memo getting sent: {memo}")

        # Extract or create memo components
        if isinstance(memo, Memo):
            memo_data = self.hex_to_text(memo.memo_data)
            memo_type = self.hex_to_text(memo.memo_type)
            memo_format = self.hex_to_text(memo.memo_format)
        else:
            memo_data = str(memo)
            memo_type = message_id or self.generate_custom_id()
            memo_format = username or self.credential_manager.postfiat_username

        # Get per-tx PFT requirement
        # TODO: Make this reject if passed pft_amount is too low
        pft_amount = pft_amount or self.transaction_requirements.get_pft_requirement(
            address=destination,
            memo_type=memo_type
        )

        # Check if this is a system memo type, which requires special handling
        is_system_memo = any(
            memo_type == system_type.value
            for system_type in SystemMemoType
        )

        # Handle encryption if requested
        if encrypt:
            # Check handshake status
            _, received_key = self.get_handshake_for_address(destination)
            if not received_key:
                raise HandshakeRequiredError(destination)
            
            # Derive the shared secret
            shared_secret = self.credential_manager.get_shared_secret(received_key)
            logger.debug(f"Shared secret: {shared_secret[:8]}...")

            # Encrypt the memo using the shared secret
            logger.debug(f"Encrypting memo: {memo_data[:8]}...")
            encrypted_memo = self.encrypt_memo(memo_data, shared_secret)
            logger.debug(f"Encrypted memo: {encrypted_memo[:8]}...")
            memo_data = "WHISPER__" + encrypted_memo

        # Handle compression if requested
        if compress:
            logger.debug(f"Compressing memo of length {len(memo_data)}")
            compressed_data = compress_string(memo_data)
            logger.debug(f"Compressed to length {len(compressed_data)}")
            memo_data = "COMPRESSED__" + compressed_data

        # For system memos, verify size and prevent chunking
        # construct_memo will raise ValueError if size exceeds limit, since SystemMemoTypes cannot be chunked due to collision risk
        memo = construct_memo(
            memo_format=memo_format,
            memo_type=memo_type,
            memo_data=memo_data,
            validate_size=(is_system_memo and chunk) or not chunk
        )

        if is_system_memo and chunk:
            return self._send_memo_single(destination, memo_data)

        # Handle chunking for non-system memos if requested
        if chunk:
            try:
                chunk_memos = self._chunk_memos(memo)
                responses = []

                for idx, chunk_memo in enumerate(chunk_memos):
                    logger.debug(f"Sending chunk {idx+1} of {len(chunk_memos)}: {chunk_memo.memo_data[:100]}...")
                    responses.append(self._send_memo_single(destination, chunk_memo, pft_amount))

                return responses
            except Exception as e:
                logger.error(f"Error chunking memo: {e}")
                logger.error(f"traceback: {traceback.format_exc()}")

        else:
            return self._send_memo_single(destination, memo, pft_amount)
    
    def _send_memo_single(self, destination: str, memo: Memo, pft_amount: Decimal):
        """ Sends a memo to a destination. """
        client = xrpl.clients.JsonRpcClient(self.network_url)

        payment_args = {
            "account": self.user_wallet.address,
            "destination": destination,
            "memos": [memo]
        }
        
        if pft_amount > 0:
            payment_args["amount"] = xrpl.models.amounts.IssuedCurrencyAmount(
                currency="PFT",
                issuer=self.pft_issuer,
                value=str(pft_amount)
            )
        else:
            # Send minimum XRP amount for memo-only transactions
            payment_args["amount"] = xrpl.utils.xrp_to_drops(Decimal(constants.MIN_XRP_PER_TRANSACTION))

        payment = xrpl.models.transactions.Payment(**payment_args)

        try:
            logger.debug("Submitting and waiting for transaction")
            response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            response = f"Transaction submission failed: {e}"
            logger.error(response)
        except Exception as e:
            response = f"Unexpected error: {e}"
            logger.error(response)
            logger.error(traceback.format_exc())

        return response

    def _reconstruct_chunked_message(
        self,
        memo_type: str,
        memos: pd.DataFrame
    ) -> str:
        """Reconstruct a message from its chunks.
        
        Args:
            memo_type: Message ID to reconstruct
            memo_history: DataFrame containing memo history
            
        Returns:
            str: Reconstructed message or None if reconstruction fails
        """
        try: 

            # Get all chunks with this memo type
            memo_chunks = memos[
                (memos['task_id'] == memo_type) &
                (memos['full_output'].str.match(r'^chunk_\d+__'))  # Only get actual chunks
            ].copy()

            if memo_chunks.empty:
                return None

            # Extract chunk numbers and sort
            def extract_chunk_number(x):
                match = re.search(r'chunk_(\d+)__', x)
                return int(match.group(1)) if match else 0
            
            memo_chunks['chunk_number'] = memo_chunks['full_output'].apply(extract_chunk_number)
            memo_chunks.sort_values(by='datetime', ascending=True, inplace=True)

            # Detect and handle multiple chunk sequences
            current_sequence = []
            highest_chunk_num = 0

            for _, chunk in memo_chunks.iterrows():
                # If we see a chunk_1 and already have chunks, this is a new sequence
                if chunk['chunk_number'] == 1 and current_sequence:
                    # Check if previous sequence was complete (no gaps)
                    expected_chunks = set(range(1, highest_chunk_num + 1))
                    actual_chunks = set(chunk['chunk_number'] for chunk in current_sequence)

                    if expected_chunks == actual_chunks:
                        # First sequence is complete, ignore all subsequent chunks
                        logger.warning(f"Found complete sequence for {memo_type}, ignoring new sequence")
                        break
                    else:
                        # First sequence was incomplete, start fresh with new sequence
                        logger.warning(f"Previous sequence incomplete for {memo_type}, starting new sequence")
                        current_sequence = []
                        highest_chunk_num = 0

                current_sequence.append(chunk)
                highest_chunk_num = max(highest_chunk_num, chunk['chunk_number'])

            # Verify final sequence is complete
            expected_chunks = set(range(1, highest_chunk_num + 1))
            actual_chunks = set(chunk['chunk_number'] for chunk in current_sequence)
            if expected_chunks != actual_chunks:
                logger.warning(f"Missing chunks for {memo_type}. Expected {expected_chunks}, got {actual_chunks}")
                return None
            
            # Combine chunks in order
            current_sequence.sort(key=lambda x: x['chunk_number'])
            reconstructed_parts = []
            for chunk in current_sequence:
                chunk_data = re.sub(r'^chunk_\d+__', '', chunk['full_output'])
                reconstructed_parts.append(chunk_data)

            return ''.join(reconstructed_parts)

        except Exception as e:
            logger.error(f"Error reconstructing message {memo_type}: {e}")
            return None
        
    @staticmethod
    def decrypt_memo(encrypted_content: str, shared_secret: Union[str, bytes]) -> Optional[str]:
        """
        Decrypt a memo using a shared secret.
        
        Args:
            encrypted_content: The encrypted memo content (without WHISPER prefix)
            shared_secret: The shared secret derived from ECDH
            
        Returns:
            Decrypted message or None if decryption fails
        """
        try:
            # Ensure shared_secret is bytes
            if isinstance(shared_secret, str):
                shared_secret = shared_secret.encode()

            # Generate a Fernet key from the shared secret
            key = base64.urlsafe_b64encode(hashlib.sha256(shared_secret).digest())
            fernet = Fernet(key)

            # Decrypt the message
            decrypted_bytes = fernet.decrypt(encrypted_content.encode())
            return decrypted_bytes.decode()

        except Exception as e:
            logger.error(f"Error decrypting message: {e}")
            return None
    
    def process_memo_data(
            self,
            memo_type: str,
            memo_data: str,
            decompress: bool = True,
            decrypt: bool = True,
            full_unchunk: bool = False, 
            memo_history: Optional[pd.DataFrame] = None,
            channel_counterparty: Optional[str] = None,
        ) -> str:
        """Process memo data, handling compression, encryption, and chunking.
        
        For encrypted messages (WHISPER__ prefix), this method handles decryption using ECDH
        from the perspective of the logged-in user's wallet.

        Args:
            memo_type: The memo type to identify related chunks
            memo_data: Initial memo data string
            decompress: If True, decompresses data if COMPRESSED__ prefix is present
            decrypt: If True, decrypts data if WHISPER__ prefix is present
            full_unchunk: If True, will attempt to unchunk by referencing memo history
            memo_history: Pre-filtered memo history required for chunk lookup
            channel_counterparty: The other end of the encryption channel
        """
        try: 
            processed_data = memo_data

            # Handle chunking
            if full_unchunk and memo_history is not None:
                # Skip chunk processing for SystemMemoType messages
                is_system_memo = any(
                    memo_type == system_type.value
                    for system_type in SystemMemoType
                )

                # Handle chunking for non-system messages only
                if not is_system_memo:
                    # Check if this is a chunked message
                    chunk_match = re.match(r'^chunk_\d+__', memo_data)
                    if chunk_match:
                        reconstructed = self._reconstruct_chunked_message(
                            memo_type=memo_type,
                            memos=memo_history
                        )
                        if reconstructed:
                            processed_data = reconstructed
                        else:
                            # If reconstruction fails, just clean the prefix from the single message
                            logger.debug(f"Reconstruction of chunked message {memo_type} failed. Cleaning prefix from single message.")
                            processed_data = re.sub(r'^chunk_\d+__', '', memo_data)

            elif isinstance(processed_data, str):
                # Simple chunk prefix removal (no full unchunking)
                processed_data = re.sub(r'^chunk_\d+__', '', processed_data)

            # Handle decompression
            if decompress and processed_data.startswith('COMPRESSED__'):
                processed_data = processed_data.replace('COMPRESSED__', '', 1)
                # logger.debug(f"Decompressing data of length {len(processed_data)}")
                processed_data = decompress_string(processed_data)

            # Handle decryption
            if decrypt and processed_data.startswith('WHISPER__'):
                if not channel_counterparty:
                    logger.warning(f"Cannot decrypt message {memo_type} - missing channel_counterparty")
                    return processed_data
                
                # Get handshake status
                _, received_key = self.get_handshake_for_address(channel_counterparty)
                if not received_key:
                    logger.warning(f"Cannot decrypt message {memo_type} - no handshake found")
                    return processed_data
                
                # Get the shared secret
                shared_secret = self.credential_manager.get_shared_secret(received_key)
                
                # Remove the WHISPER__ prefix and decrypt
                processed_data = processed_data.replace('WHISPER__', '', 1)
                processed_data = '[DECRYPTED] ' + self.decrypt_memo(processed_data, shared_secret)

            return processed_data
        
        except Exception as e:
            logger.error(f"Error processing memo data: {e}")
            return None
    
    def get_account_transactions(self, account_address='r3UHe45BzAVB3ENd21X9LeQngr4ofRJo5n', 
                                ledger_index_min=-1, 
                                ledger_index_max=-1, 
                                limit=10
                                ):
        logger.debug(f"Getting transactions for account {account_address} with ledger index min {ledger_index_min} and max {ledger_index_max} and limit {limit}")
        client = xrpl.clients.JsonRpcClient(self.network_url)
        all_transactions = []
        marker = None
        previous_marker = None
        max_iterations = 1000
        iteration_count = 0

        # Convert NumPy int64 to Python int
        if isinstance(ledger_index_min, np.int64):
            ledger_index_min = int(ledger_index_min)
        if isinstance(ledger_index_max, np.int64):
            ledger_index_max = int(ledger_index_max)

        while iteration_count < max_iterations:
            iteration_count += 1
            logger.debug(f"Iteration {iteration_count}")
            print(f"current marker: {marker}")

            request = AccountTx(
                account=account_address,
                ledger_index_min=ledger_index_min, # Use -1 for the earliest ledger index
                ledger_index_max=ledger_index_max, # Use -1 for the latest ledger index
                limit=limit, # adjust as needed
                marker=marker, # Used for pagination
                forward=True # Set to True to return results in ascending order 
            )

            try:
                # Convert the request to a dict and then to a JSON to check for serialization
                request_dict = request.to_dict()
                json.dumps(request_dict)  # This will raise an error if the request is not serializable
            except TypeError as e:
                logger.error(f"Request is not serializable: {e}")
                logger.error(f"Problematic request data: {request_dict}")
                break # stop if request is not serializable

            try:
                response = client.request(request)
                
                # debugging
                # logger.debug(f"Full XRPL response: {response}")

                if response.is_successful():
                    transactions = response.result.get("transactions", [])
                    logger.debug(f"Retrieved {len(transactions)} transactions")

                    # debugging
                    logger.debug(f"Full websocket transaction message: {transactions}")

                    all_transactions.extend(transactions)
                else:
                    logger.error(f"Error in XRPL response: {response}")
                    break
            except Exception as e:
                logger.error(f"Error making XRPL request: {e}")
                break

            if "marker" in response.result:
                if response.result["marker"] == previous_marker:
                    logger.warning("Marker not advancing, stopping iteration")
                    break # stop if marker not advancing
                previous_marker = marker
                marker = response.result["marker"] # Update marker for next iteration
                logger.debug("More transactions available. Fetching next batch...")
            else:
                logger.debug("No more transactions available")
                break
        
        if iteration_count == max_iterations:
            logger.warning("Reached maximum iteration count. Stopping loop...")

        return all_transactions
    
    # TODO: Attempt to apply DRY principle with get_account_transactions
    def get_account_transactions__exhaustive(self,account_address='r3UHe45BzAVB3ENd21X9LeQngr4ofRJo5n',
                                ledger_index_min=-1,
                                ledger_index_max=-1,
                                max_attempts=3,
                                retry_delay=.2):

        client = xrpl.clients.JsonRpcClient(self.network_url)  # Using a public server; adjust as necessary
        all_transactions = []  # List to store all transactions

        # Fetch transactions using marker pagination
        marker = None
        attempt = 0
        while attempt < max_attempts:
            try:
                request = xrpl.models.requests.account_tx.AccountTx(
                    account=account_address,
                    ledger_index_min=ledger_index_min,
                    ledger_index_max=ledger_index_max,
                    limit=1000,
                    marker=marker,
                    forward=True
                )
                response = client.request(request)
                transactions = response.result["transactions"]
                all_transactions.extend(transactions)

                if "marker" not in response.result:
                    break
                marker = response.result["marker"]

            except Exception as e:
                print(f"Error occurred while fetching transactions (attempt {attempt + 1}): {str(e)}")
                attempt += 1
                if attempt < max_attempts:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print("Max attempts reached. Transactions may be incomplete.")
                    break

        return all_transactions

    # TODO: Not used, deprecate
    def get_account_transactions__retry_version(self, account_address='r3UHe45BzAVB3ENd21X9LeQngr4ofRJo5n',
                                ledger_index_min=-1,
                                ledger_index_max=-1,
                                max_attempts=3,
                                retry_delay=.2,
                                num_runs=5):
        
        longest_transactions = []
        
        for i in range(num_runs):
            print(f"Run {i+1}/{num_runs}")
            
            transactions = self.get_account_transactions__exhaustive(
                account_address=account_address,
                ledger_index_min=ledger_index_min,
                ledger_index_max=ledger_index_max,
                max_attempts=max_attempts,
                retry_delay=retry_delay
            )
            
            num_transactions = len(transactions)
            print(f"Number of transactions: {num_transactions}")
            
            if num_transactions > len(longest_transactions):
                longest_transactions = transactions
            
            if i < num_runs - 1:
                print(f"Waiting for {retry_delay} seconds before the next run...")
                time.sleep(retry_delay)
        
        print(f"Longest list of transactions: {len(longest_transactions)} transactions")
        return longest_transactions
    
    def get_latest_outgoing_context_doc_link(self):
        """ This function gets the most recent google doc context link for a given account address """

        logger.debug("Getting latest outgoing context doc link...")
        if self.system_memos.empty or len(self.system_memos) == 0:
            logger.warning("System memos dataframe is empty. No context doc link found.")
            return None

        # Filter for outgoing google doc context links
        redux_tx_list = self.system_memos[
            (self.system_memos['task_id'] == SystemMemoType.GOOGLE_DOC_CONTEXT_LINK.value) &
            (self.system_memos['direction'] == 'OUTGOING')
        ]
        
        logger.debug(f"Found {len(redux_tx_list)} outgoing context doc links")
        
        if len(redux_tx_list) == 0:
            logger.warning("No Google Doc context link found")
            return None
        
        # Get the most recent google doc context link
        most_recent_context_link = redux_tx_list.sort_values('datetime', ascending=False).iloc[0]
        memo_data = most_recent_context_link['full_output']

        logger.debug(f"Most recent google doc link: {memo_data}")

        # Process the memo data to handle both encrypted and unencrypted links
        try:
            link = self.process_memo_data(
                memo_type=SystemMemoType.GOOGLE_DOC_CONTEXT_LINK.value,
                memo_data=memo_data,
                channel_counterparty=self.default_node
            )
        except Exception as e:
            logger.error(f"Error processing memo data: {e}")
            return None

        return link

    def output_account_address_node_association(self):
        """this takes the account info frame and figures out what nodes
         the account is associating with and returns them in a dataframe """
        self.memo_transactions['valid_task_id']=self.memo_transactions['memo_data'].apply(is_valid_id)
        node_output_df = self.memo_transactions[self.memo_transactions['direction']=='INCOMING'][['valid_task_id','account']].groupby('account').sum()
   
        return node_output_df[node_output_df['valid_task_id']>0]
    
    def get_user_initiation_rites_destinations(self):
        """Returns all the addresses that have received a user initiation rite"""
        all_user_initiation_rites = self.memo_transactions[
            self.memo_transactions['memo_data'].apply(lambda x: x.get('task_id') == SystemMemoType.INITIATION_RITE.value)
        ]
        return list(all_user_initiation_rites['destination'])
    
    def initiation_rite_sent(self):
        logger.debug("Checking if user has sent initiation rite...")

        # Check if memos dataframe is empty or missing required columns
        if self.memo_transactions.empty or not all(col in self.memo_transactions.columns for col in ['destination', 'memo_data']):
            logger.debug("Memos dataframe is empty or missing required columns, returning False")
            return False

        user_initiation_rites_destinations = self.get_user_initiation_rites_destinations()
        return self.default_node in user_initiation_rites_destinations
    
    def google_doc_sent(self):
        """Checks if the user has ever sent a google doc context link"""
        return self.get_latest_outgoing_context_doc_link() is not None

    def check_if_google_doc_is_valid(self, google_doc_link):
        """ Checks if the google doc is valid by """

        # Check 1: google doc is a valid url
        if not google_doc_link.startswith('https://docs.google.com/document/d/'):
            raise InvalidGoogleDocException(google_doc_link)
        
        google_doc_text = get_google_doc_text(google_doc_link)

        # Check 2: google doc exists
        if google_doc_text == "Failed to retrieve the document. Status code: 404":
            raise GoogleDocNotFoundException(google_doc_link)

        # Check 3: google doc is shared
        if google_doc_text == "Failed to retrieve the document. Status code: 401":
            raise GoogleDocIsNotSharedException(google_doc_link)
        
        # # Check 4: google doc contains the correct XRP address at the top
        # if retrieve_xrp_address_from_google_doc(google_doc_text) != self.user_wallet.classic_address:
        #     raise GoogleDocDoesNotContainXrpAddressException(self.user_wallet.classic_address)
        
        # # Check 5: XRP address has a balance
        # if self.get_xrp_balance() == 0:
        #     raise GoogleDocIsNotFundedException(google_doc_link)    
    
    @requires_wallet_state(INITIATED_STATES)
    def handle_google_doc_setup(self, google_doc_link):
        """Validates, caches, and sends the Google Doc link"""
        logger.debug("Checking Google Doc link for validity and sending if valid...")
        self.check_if_google_doc_is_valid(google_doc_link)
        return self.send_google_doc(google_doc_link)
    
    def send_google_doc(self, google_doc_link: str):
        """ Sends the Google Doc context link to the node """
        logger.debug(f"Sending Google Doc context link to the node: {google_doc_link}")
        google_doc_memo = construct_google_doc_context_memo(
            user=self.credential_manager.postfiat_username,
            google_doc_link=google_doc_link
        )
        return self.send_memo(
            destination=self.default_node,
            memo=google_doc_memo,
            chunk=False,
            compress=False,
            encrypt=True  # Always encrypt Google Doc links
        )

    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_proposals_df')
    def get_proposals_df(self, include_refused=False):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, proposal, and acceptance""" 

        if self.tasks.empty:
            return pd.DataFrame()

        # Filter tasks with task_type in ['PROPOSAL','ACCEPTANCE']
        filtered_tasks = self.tasks[self.tasks['task_type'].isin([
            TaskType.PROPOSAL.name, 
            TaskType.ACCEPTANCE.name,
            TaskType.REFUSAL.name
        ])]

        # Get task_ids where:
        # 1. The latest state is 'PROPOSAL' or 'ACCEPTANCE' (or REFUSAL if include_refused=True) AND
        # 2. if include_refused=False, exclude tasks that have ever been refused
        proposal_task_ids = []
        valid_states = [TaskType.PROPOSAL.name, TaskType.ACCEPTANCE.name]
        if include_refused:
            valid_states.append(TaskType.REFUSAL.name)

        for task_id in filtered_tasks['task_id'].unique():
            task_df = self.get_task(task_id)
            latest_state = self.get_task_state(task_df)
            
            if latest_state in valid_states:
                if include_refused or TaskType.REFUSAL.name not in task_df['task_type'].values:
                    proposal_task_ids.append(task_id)

        # Filter for these tasks
        filtered_df = self.tasks[(self.tasks['task_id'].isin(proposal_task_ids))].copy()

        if filtered_df.empty:
            return pd.DataFrame()

        # Create new 'RESPONSE' column to combine acceptance and refusal
        filtered_df['response_type'] = filtered_df['task_type'].apply(
            lambda x: 'RESPONSE' if x in [TaskType.ACCEPTANCE.name, TaskType.REFUSAL.name] else x
        )

        # Pivot the dataframe to get proposals and responses side by side and reset index to make task_id a column
        pivoted_df = filtered_df.pivot_table(index='task_id', columns='response_type', values='full_output', aggfunc='first').reset_index().copy()

        # Rename the columns for clarity
        pivoted_df.rename(columns={'REQUEST_POST_FIAT':'request', 'PROPOSAL':'proposal', 'RESPONSE':'response'}, inplace=True)

        # Clean up the proposal column
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace(TaskType.PROPOSAL.value,'').replace('nan',''))

        # Clean up the request column
        pivoted_df['request'] = pivoted_df['request'].apply(lambda x: str(x).replace(TaskType.REQUEST_POST_FIAT.value,'').replace('nan',''))
        
        # Clean up the response column, if it exists (does not exist for the first proposal)
        if 'response' in pivoted_df.columns:
            pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace(TaskType.ACCEPTANCE.value,'ACCEPTED: ').replace('nan',''))
            pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace(TaskType.REFUSAL.value,'REFUSED: ').replace('nan',''))
        else:
            pivoted_df['response'] = ''
        
        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_verification_df')
    def get_verification_df(self):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, original_task, and verification""" 

        if self.tasks.empty:
            return pd.DataFrame()

        # Filter tasks with task_type in ['PROPOSAL','VERIFICATION_PROMPT']
        filtered_tasks = self.tasks[self.tasks['task_type'].isin([TaskType.PROPOSAL.name, TaskType.VERIFICATION_PROMPT.name])]

        # Get task_ids where the latest state is 'VERIFICATION_PROMPT'
        verification_task_ids = [
            task_id for task_id in filtered_tasks['task_id'].unique()
            if self.get_task_state_using_task_id(task_id) == TaskType.VERIFICATION_PROMPT.name
        ]

        # Filter for these tasks
        filtered_df = self.tasks[(self.tasks['task_id'].isin(verification_task_ids))].copy()

        if filtered_df.empty:
            return pd.DataFrame()

        # Pivot the dataframe to get proposals and verification prompts side by side and reset index to make task_id a column
        pivoted_df = filtered_df.pivot_table(index='task_id', columns='task_type', values='full_output', aggfunc='first').reset_index().copy()

        # Rename columns for clarity
        pivoted_df.rename(columns={'PROPOSAL':'proposal', 'VERIFICATION_PROMPT':'verification'}, inplace=True)

        # clean up the proposal and verification columns
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace(TaskType.PROPOSAL.value,''))
        pivoted_df['verification'] = pivoted_df['verification'].apply(lambda x: str(x).replace(TaskType.VERIFICATION_PROMPT.value,''))

        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_rewards_df')
    def get_rewards_df(self):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, proposal, and reward""" 

        if self.tasks.empty:
            return pd.DataFrame()

        # Filter for only PROPOSAL and REWARD rows
        filtered_df = self.tasks[self.tasks['task_type'].isin([TaskType.PROPOSAL.name, TaskType.REWARD.name])]

        # Get task_ids where the latest state is 'REWARD'
        reward_task_ids = [
            task_id for task_id in filtered_df['task_id'].unique()
            if self.get_task_state_using_task_id(task_id) == TaskType.REWARD.name
        ]

        # Filter for these tasks
        filtered_df = self.tasks[(self.tasks['task_id'].isin(reward_task_ids))].copy()

        if filtered_df.empty:
            return pd.DataFrame()

        # Pivot the dataframe to get proposals and rewards side by side and reset index to make task_id a column
        pivoted_df = filtered_df.pivot_table(index='task_id', columns='task_type', values='full_output', aggfunc='first').reset_index().copy()

        # Rename the columns for clarity # TODO: this seems pointless
        pivoted_df.rename(columns={'PROPOSAL':'proposal', 'REWARD':'reward'}, inplace=True)

        # Clean up the proposal and reward columns
        pivoted_df['reward'] = pivoted_df['reward'].apply(lambda x: str(x).replace(TaskType.REWARD.value,''))
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace(TaskType.PROPOSAL.value,'').replace('nan',''))

        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        # Add PFT value information
        pft_only = self.memo_transactions[self.memo_transactions['tx_json'].apply(is_pft_transaction)].copy()
        pft_only['pft_value'] = pft_only['tx_json'].apply(
            lambda x: x['DeliverMax']['value']).astype(float) * pft_only['direction'].map({'INCOMING':1,'OUTGOING':-1}
        )
        pft_only['task_id'] = pft_only['memo_data'].apply(lambda x: x['task_id'])
        
        pft_rewards_only = pft_only[pft_only['memo_data'].apply(lambda x: TaskType.REWARD.value in x['full_output'])].copy()
        task_id_to_payout = pft_rewards_only.groupby('task_id').last()['pft_value']
        
        result_df['payout'] = result_df['task_id'].map(task_id_to_payout)

        # Remove rows where payout is NaN
        result_df = result_df[result_df['payout'].notna()].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_payments_df')
    def get_payments_df(self):
        """ Returns a dataframe containing payment transaction details"""
        if self.memo_transactions.empty:
            return pd.DataFrame()
        
        df = self.memo_transactions.copy()

        # Extract delivered amoutn and determine token type
        def get_payment_details(row):
            meta = row['meta']  # meta is already deserialized in memory
            delivered = meta.get('delivered_amount', None)

            if isinstance(delivered, dict):  # PFT payment
                # Convert to float and format to prevent scientific notation
                amount = float(delivered['value'])
                amount_str = f"{amount:f}".rstrip('0').rstrip('.')  # Remove trailing zeros and decimal point if whole number
                return {
                    'amount': amount_str,
                    'token': delivered['currency']
                }
            elif delivered:  # XRP payment
                amount = float(delivered) / 1000000
                amount_str = f"{amount:f}".rstrip('0').rstrip('.') 
                return {
                    'amount': amount_str,
                    'token': 'XRP'
                }
            return {'amount': None, 'token': None}

        # Extract payment details
        payment_details = df.apply(get_payment_details, axis=1)
        df['amount'] = payment_details.apply(lambda x: x['amount'])
        df['token'] = payment_details.apply(lambda x: x['token'])

        # Replace direction with to/from
        df['direction'] = df['direction'].map({'INCOMING': 'From', 'OUTGOING': 'To'})
        
        # Add contact names where available
        contacts = self.credential_manager.get_contacts()
        df['contact_name'] = df['counterparty_address'].map(contacts)
        df['display_address'] = df.apply(
            lambda x: x['contact_name'] + ' (' + x['counterparty_address'] + ')' 
                if pd.notna(x['contact_name']) else x['counterparty_address'],
            axis=1
        )

        df['tx_hash'] = df['hash']

        # Select and rename columns
        result_df = df[['datetime', 'amount', 'token', 'direction', 'display_address', 'tx_hash']]

        # Sort by datetime descending
        result_df = result_df.sort_values(by='datetime', ascending=False).reset_index(drop=True)

        return result_df
    
    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_memos_df')
    def get_memos_df(self):
        """Returns a dataframe containing only P2P messages (excluding handshakes)"""
        if self.memos.empty:
            logger.debug("No memos or handshakes found")
            return pd.DataFrame()

        # Filter for only MEMO type messages 
        memo_history = self.memos[
            self.memos['full_output'].str.contains(MessageType.MEMO.value, na=False)
        ].copy()

        if memo_history.empty:
            logger.debug("No memos found")
            return pd.DataFrame()
        
        processed_messages = []
        for msg_id in memo_history['task_id'].unique():
            msg_txns = memo_history[memo_history['task_id'] == msg_id]
            first_txn = msg_txns.iloc[0]

            try:
                # process the message (chunking, compression, encryption)
                processed_message = self.process_memo_data(
                    memo_type=msg_id,
                    memo_data=first_txn['full_output'],
                    full_unchunk=True,
                    memo_history=memo_history,
                    channel_counterparty=first_txn['counterparty_address']
                )
            except Exception as e:
                logger.error(f"Error processing message {msg_id}: {e}")
                processed_message = "[PROCESSING FAILED]"

            processed_messages.append({
                'memo_id': msg_id,
                'memo': processed_message,
                'direction': 'From' if first_txn['direction'] == 'INCOMING' else 'To',
                'counterparty_address': first_txn['counterparty_address'],
                'datetime': first_txn['datetime']
            })

        # Create DataFrame and sort by datetime
        result = pd.DataFrame(processed_messages)
        result = result.sort_values(by='datetime', ascending=False).reset_index(drop=True)

        # Add contact names where available
        contacts = self.credential_manager.get_contacts()
        result['contact_name'] = result['counterparty_address'].map(contacts)
        result['display_address'] = result.apply(
            lambda x: f"{x['contact_name']} ({x['counterparty_address']})" 
                if pd.notna(x['contact_name']) else x['counterparty_address'],
            axis=1
        )

        return result[['memo_id', 'memo', 'direction', 'display_address']]

    @PerformanceMonitor.measure('send_acceptance_for_task_id')
    def send_acceptance_for_task_id(self, task_id, acceptance_string):
        """This function accepts a task. It requires the most recent task status to be PROPOSAL"""
        task_df = self.get_task(task_id)
        most_recent_status = self.get_task_state(task_df)

        if most_recent_status != TaskType.PROPOSAL.name:
            raise WrongTaskStateException(TaskType.PROPOSAL.name, most_recent_status)

        proposal_source = task_df.iloc[0]['counterparty_address']
        if TaskType.ACCEPTANCE.value not in acceptance_string:
            classified_string=TaskType.ACCEPTANCE.value + acceptance_string
        else:
            classified_string=acceptance_string
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                    task_id=task_id, full_output=classified_string)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_acceptance_for_task_id response: {response}")
        return response

    @PerformanceMonitor.measure('send_refusal_for_task')
    def send_refusal_for_task(self, task_id, refusal_reason):
        """This function refuses a task. It requires the most recent task status to be PROPOSAL"""
        # Check if the task ID exists
        task_df = self.get_task(task_id)
        most_recent_status = self.get_task_state(task_df)
        
        # Only prevent refusal if the task has already been rewarded
        if most_recent_status == TaskType.REWARD.name:
            raise WrongTaskStateException(TaskType.REWARD.name, most_recent_status, restricted_flag=True)
        
        proposal_source = task_df.iloc[0]['counterparty_address']
        if TaskType.REFUSAL.value not in refusal_reason:
            refusal_reason = TaskType.REFUSAL.value + refusal_reason
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                        task_id=task_id, 
                                                        full_output=refusal_reason)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_refusal_for_task response: {response}")
        return response

    @PerformanceMonitor.measure('request_post_fiat')
    def request_post_fiat(self, request_message ):
        """ 
        This requests a task known as a Post Fiat from the default node you are on
        
        request_message = 'I would like a new task related to the creation of my public facing wallet', 
        all_account_info=all_account_info

        This function sends a request for post-fiat tasks to the node.
        
        EXAMPLE PARAMETERS
        request_message = 'Please provide details for the upcoming project.'
        """
        
        # Generate a custom task ID for this request
        while True:
            task_id = self.generate_custom_id()
            try:
                task_df = self.tasks[self.tasks['task_id'] == task_id]
                if task_df.empty:
                    break
                logger.debug(f"Task ID {task_id} already exists, generating new ID")
            except Exception as e:
                logger.debug(f"Error checking task ID {task_id}: {e}")
                break

        # Construct the memo with the request message
        if TaskType.REQUEST_POST_FIAT.value not in request_message:
            classified_request_msg = TaskType.REQUEST_POST_FIAT.value + request_message
        else:
            classified_request_msg = request_message
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                        task_id=task_id, 
                                                        full_output=classified_request_msg)
        response = self.send_pft(amount=1, destination=self.default_node, memo=constructed_memo)
        logger.debug(f"request_post_fiat response: {response}")
        return response

    @PerformanceMonitor.measure('submit_initial_completion')
    def submit_initial_completion(self, completion_string, task_id):
        """
        This function sends an initial completion for a given task back to a node.
        The most recent task status must be 'ACCEPTANCE' to trigger the initial completion.
        
        EXAMPLE PARAMETERS
        completion_string = 'I have completed the task as requested'
        task_id = '2024-05-14_19:10__ME26'
        """

        task_df = self.get_task(task_id)
        most_recent_status = self.get_task_state(task_df)

        if most_recent_status != TaskType.ACCEPTANCE.name:
            raise WrongTaskStateException(TaskType.ACCEPTANCE.name, most_recent_status)
        
        proposal_source = task_df.iloc[0]['counterparty_address']
        if TaskType.TASK_OUTPUT.value not in completion_string:
            classified_completion_str = TaskType.TASK_OUTPUT.value + completion_string
        else:
            classified_completion_str = completion_string
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                        task_id=task_id, 
                                                        full_output=classified_completion_str)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"submit_initial_completion Response: {response}")
        return response
        
    @PerformanceMonitor.measure('send_verification_response')
    def send_verification_response(self, response_string, task_id):
        """
        This function sends a verification response for a given task back to a node.
        The most recent task status must be 'VERIFICATION_PROMPT' to trigger the verification response.
        
        EXAMPLE PARAMETERS
        response_string = 'This link https://livenet.xrpl.org/accounts/rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW is the PFT token mint. You can see that the issuer wallet has been blackholed per lsfDisableMaster'
        task_id = '2024-05-10_00:19__CJ33'
        """
        
        task_df = self.get_task(task_id)
        most_recent_status = self.get_task_state(task_df)
        
        if most_recent_status != TaskType.VERIFICATION_PROMPT.name:
            raise WrongTaskStateException(TaskType.VERIFICATION_PROMPT.name, most_recent_status)
        
        proposal_source = task_df.iloc[0]['counterparty_address']
        if TaskType.VERIFICATION_RESPONSE.value not in response_string:
            classified_response_str = TaskType.VERIFICATION_RESPONSE.value + response_string
        else:
            classified_response_str = response_string
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                        task_id=task_id, 
                                                        full_output=classified_response_str)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_verification_response Response: {response}")
        return response

    ## WALLET UX POPULATION 
    def ux__1_get_user_pft_balance(self):
        """Returns the balance of PFT for the user."""
        client = xrpl.clients.JsonRpcClient(self.network_url)
        account_lines = xrpl.models.requests.AccountLines(
            account=self.user_wallet.classic_address,
            ledger_index="validated"
        )
        response = client.request(account_lines)
        lines = response.result.get('lines', [])
        for line in lines:
            if line['currency'] == 'PFT':
                return float(line['balance'])
        return 0.0

    @requires_wallet_state(FUNDED_STATES)
    @PerformanceMonitor.measure('process_account_info')
    def process_account_info(self):
        logger.debug(f"Processing account info for {self.user_wallet.classic_address}")

        account_info = {
            'Account Address': self.user_wallet.classic_address,
            'Default Node': self.default_node
        }

        try:

            # attempt to retrieve the username from the initiation rite transaction
            if (not self.system_memos.empty and len(self.system_memos) > 0 and 'task_id' in self.system_memos.columns):
                initiation_rite = self.system_memos[
                    self.system_memos['task_id'] == SystemMemoType.INITIATION_RITE.value
                ]
                if not initiation_rite.empty:
                    initiated_username = initiation_rite.iloc[0]['user']
                    if initiated_username:
                        account_info['Initiated Username'] = initiated_username

            # attempt to retrieve the decrypted google doc link
            google_doc_link = self.get_latest_outgoing_context_doc_link()
            if google_doc_link:
                account_info['Google Doc'] = google_doc_link

            def extract_latest_message(df, direction, node):
                """
                Extract the latest message of a given type for a specific node.
                """
                is_outgoing = direction == 'OUTGOING'
                field = 'destination' if is_outgoing else 'account'
                latest_message = df[
                    (df['direction'] == direction) &
                    (df[field] == node)
                ].tail(1)
                
                if not latest_message.empty:
                    return latest_message.iloc[0].to_dict()
                else:
                    return {}

            def format_dict(data):
                if data:
                    standard_format = self.get_explorer_transaction_url(data.get('hash', ''))
                    full_output = data.get('memo_data', {}).get('full_output', 'N/A')
                    task_id = data.get('memo_data', {}).get('task_id', 'N/A')
                    formatted_string = (
                        f"Task ID: {task_id}\n"
                        f"Full Output: {full_output}\n"
                        f"Hash: {standard_format}\n"
                        f"Datetime: {pd.Timestamp(data['datetime']).strftime('%Y-%m-%d %H:%M:%S') if 'datetime' in data else 'N/A'}\n"
                    )
                    return formatted_string
                
            # Sorting account info by datetime
            if not self.memo_transactions.empty and len(self.memo_transactions) > 0 and 'datetime' in self.memo_transactions.columns:
                sorted_account_info = self.memo_transactions.sort_values('datetime', ascending=True).copy()

                # Extracting most recent messages
                most_recent_outgoing_message = extract_latest_message(sorted_account_info, 'OUTGOING', self.default_node)
                most_recent_incoming_message = extract_latest_message(sorted_account_info, 'INCOMING', self.default_node)
                
                # Formatting messages
                incoming_message = format_dict(most_recent_incoming_message)
                outgoing_message = format_dict(most_recent_outgoing_message)
                if incoming_message:
                    account_info['Incoming Message'] = incoming_message
                if outgoing_message:
                    account_info['Outgoing Message'] = outgoing_message

        except Exception as e:
            logger.error(f"Error processing account info: {e}")
            logger.error(traceback.format_exc())
        
        finally:
            return account_info

    @PerformanceMonitor.measure('send_pomodoro_for_task_id')
    def send_pomodoro_for_task_id(self,task_id = '2024-05-19_10:27__LL78',pomodoro_text= 'spent last 30 mins doing a ton of UX debugging'):
        pomodoro_id = task_id.replace('__','==')
        memo_to_send = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username,
                                           task_id=pomodoro_id, full_output=pomodoro_text)
        response = self.send_pft(amount=1, destination=self.default_node, memo=memo_to_send)
        return response

    def get_all_pomodoros(self):
        task_id_only = self.memo_transactions[self.memo_transactions['memo_data'].apply(lambda x: 'task_id' in str(x))].copy()
        pomodoros_only = task_id_only[task_id_only['memo_data'].apply(lambda x: '==' in x['task_id'])].copy()
        pomodoros_only['parent_task_id']=pomodoros_only['memo_data'].apply(lambda x: x['task_id'].replace('==','__'))
        return pomodoros_only
    
    def verify_password(self, password):
        """Verifies password for current user"""
        return self.credential_manager.verify_password(password)
    
    def change_password(self, new_password):
        """Changes password for current user"""
        return self.credential_manager.change_password(new_password)
        
    def get_contacts(self):
        return self.credential_manager.get_contacts()
    
    def save_contact(self, address, name):
        return self.credential_manager.save_contact(address, name)
    
    def delete_contact(self, address):
        return self.credential_manager.delete_contact(address)
    
    def get_explorer_transaction_url(self, tx_hash: str) -> str:
        """Returns the appropriate explorer URL for a transaction based on network configuration"""
        template = self.network_config.explorer_tx_url_mask
        return template.format(hash=tx_hash)
    
    def get_explorer_account_url(self, address: str) -> str:
        """Returns the appropriate explorer URL for an account based on network configuration"""
        template = self.network_config.explorer_account_url_mask
        return template.format(address=address)
    
    def get_current_trust_limit(self):
        """Gets the current trust line limit for PFT token"""
        try:
            client = xrpl.clients.JsonRpcClient(self.network_url)
            request = xrpl.models.requests.AccountLines(
                account=self.user_wallet.address,
                peer=self.pft_issuer
            )
            
            response = client.request(request)
            if not response.is_successful():
                logger.error(f"Failed to get account lines: {response}")
                return "0"
                
            # Find the PFT trust line
            for line in response.result.get('lines', []):
                if line.get('currency') == 'PFT':
                    return line.get('limit', "0")
            
            logger.debug(f"No PFT trust line found for {self.user_wallet.address}")
            return "0"

        except Exception as e:
            logger.error(f"Error getting trust line limit: {e}")
            return "0"
    
    def has_trust_line(self):
        """ Checks if the user has a trust line to the PFT token"""
        try:
            client = xrpl.clients.JsonRpcClient(self.network_url)
            request = xrpl.models.requests.AccountLines(
                account=self.user_wallet.address,
                peer=self.pft_issuer  # Only get trust lines with PFT issuer
            )
            
            response = client.request(request)
            if not response.is_successful():
                logger.error(f"Failed to get account lines: {response}")
                return False
                
            # Check if any of the lines are for PFT
            lines = response.result.get('lines', [])
            has_pft = any(
                line.get('currency') == 'PFT' 
                for line in lines
            )
            
            logger.debug(f"Trust line check for {self.user_wallet.address}: {'Found' if has_pft else 'Not found'}")
            return has_pft

        except Exception as e:
            logger.error(f"Error checking trust line: {e}")
            logger.error(traceback.format_exc())
            return False

    @requires_wallet_state(FUNDED_STATES)
    def handle_trust_line(self):
        """ Handles the creation of a trust line to the PFT token if it doesn't exist"""
        logger.debug("Checking if trust line exists...")
        if not self.has_trust_line():
            _ = self.update_trust_line_limit()
            logger.debug("Trust line created")
        else:
            logger.debug("Trust line already exists")

    def update_trust_line_limit(self, new_limit = constants.DEFAULT_PFT_LIMIT):
        """Updates the trust line limit for PFT token
        
        Args:
            new_limit: New limit value
        
        Returns:
            Transaction response
        """
        client = xrpl.clients.JsonRpcClient(self.network_url)
        trust_set_tx = xrpl.models.transactions.TrustSet(
            account=self.user_wallet.address,
            limit_amount=xrpl.models.amounts.issued_currency_amount.IssuedCurrencyAmount(
                currency="PFT",
                issuer=self.pft_issuer,
                value=new_limit,
            )
        )
        logger.debug(f"Creating trust line from {self.user_wallet.address} to issuer...")
        try:
            response = xrpl.transaction.submit_and_wait(trust_set_tx, client, self.user_wallet)
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            response = f"Submit failed: {e}"
            logger.error(f"Trust line creation failed: {response}")
        return response

def is_over_1kb(value: Union[str, int, float]) -> bool:
    if isinstance(value, str):
        # For strings, convert to bytes and check length
        return len(value.encode('utf-8')) > 1024
    elif isinstance(value, (int, float)):
        # For numbers, compare directly
        return value > 1024
    else:
        raise TypeError(f"Expected string or number, got {type(value)}")
    
def calculate_memo_size(memo_format: str, memo_type: str, memo_data: str) -> dict:
    """
    Calculates the size components of a memo using consistent logic.
    
    Args:
        memo_format: The format field (usually username)
        memo_type: The type field (usually task_id)
        memo_data: The data field (the actual content)
        
    Returns:
        dict: Size breakdown including:
            - format_size: Size of hex-encoded format
            - type_size: Size of hex-encoded type
            - data_size: Size of hex-encoded data
            - structural_overhead: Fixed overhead for JSON structure
            - total_size: Total size including all components
    """
    format_size = len(str_to_hex(memo_format))
    type_size = len(str_to_hex(memo_type))
    data_size = len(str_to_hex(memo_data))
    structural_overhead = constants.XRP_MEMO_STRUCTURAL_OVERHEAD

    logger.debug(f"Memo size breakdown:")
    logger.debug(f"  format_size: {format_size}")
    logger.debug(f"  type_size: {type_size}")
    logger.debug(f"  data_size: {data_size}")
    logger.debug(f"  structural_overhead: {structural_overhead}")
    logger.debug(f"  total_size: {format_size + type_size + data_size + structural_overhead}")

    return {
        'format_size': format_size,
        'type_size': type_size,
        'data_size': data_size,
        'structural_overhead': structural_overhead,
        'total_size': format_size + type_size + data_size + structural_overhead
    }

def to_hex(string):
    return binascii.hexlify(string.encode()).decode()

def construct_handshake_memo(user, ecdh_public_key) -> str:
    return construct_memo(memo_format=user, memo_type=SystemMemoType.HANDSHAKE.value, memo_data=ecdh_public_key)

def construct_basic_postfiat_memo(user, task_id, full_output):
    return construct_memo(memo_format=user, memo_type=task_id, memo_data=full_output)

def construct_initiation_rite_memo(user='goodalexander', commitment='I commit to generating massive trading profits using AI and investing them to grow the Post Fiat Network'):
    return construct_memo(memo_format=user, memo_type=SystemMemoType.INITIATION_RITE.value, memo_data=commitment)

def construct_google_doc_context_memo(user, google_doc_link):                  
    return construct_memo(memo_format=user, memo_type=SystemMemoType.GOOGLE_DOC_CONTEXT_LINK.value, memo_data=google_doc_link) 

def construct_memo(memo_format, memo_type, memo_data, validate_size=False):
    """Constructs a memo object, checking total size"""
    # NOTE: This is a hack and appears too conservative
    # NOTE: We don't know if this is the correct way calculate the XRPL size limits
    # NOTE: This will raise an error even when a transaction might otherwise succeed
    if validate_size:
        size_info = calculate_memo_size(memo_format, memo_type, memo_data)
        if is_over_1kb(size_info['total_size']):
            raise ValueError(f"Memo exceeds 1 KB, raising ValueError: {size_info['total_size']}")

    # Convert to hex
    hex_format = to_hex(memo_format)
    hex_type = to_hex(memo_type)
    hex_data = to_hex(memo_data)

    return Memo(
        memo_data=hex_data,
        memo_type=hex_type,
        memo_format=hex_format
    )

def get_xrp_balance(network_url, address):
    client = xrpl.clients.JsonRpcClient(network_url)
    account_info = xrpl.models.requests.account_info.AccountInfo(
        account=address,
        ledger_index="validated"
    )
    try:
        response = client.request(account_info)
        if response.is_successful():
            return response.result['account_data']['Balance']
        else:
            if response.result.get('error') == 'actNotFound':
                logger.warning(f"XRP account not found: {address}. It may not be activated yet.")
                return 0
            else:
                logger.error(f"Error fetching account info: {response.result.get('error_message', 'Unknown error')}")
                return None
    except Exception as e:
        logger.error(f"Exception when fetching XRP balance: {e}")
        return None

def send_xrp(network_url, wallet: xrpl.wallet.Wallet, amount, destination, memo="", destination_tag=None):
    client = xrpl.clients.JsonRpcClient(network_url)

    logger.debug(f"Sending {amount} XRP to {destination} with memo {memo}")

    # Handle memo
    if isinstance(memo, Memo):
        memos = [memo]
    elif isinstance(memo, str):
        memos = [Memo(memo_data=str_to_hex(memo))]
    else:
        logger.error("Memo is not a string or a Memo object, raising ValueError")
        raise ValueError("Memo must be either a string or a Memo object")
    
    # Create payment transaction args
    payment_args = {
        'account': wallet.address,
        'amount': xrpl.utils.xrp_to_drops(Decimal(amount)),
        'destination': destination,
        'memos': memos,
    }

    # Add destination_tag if provided, converting to int
    if destination_tag:
        payment_args['destination_tag'] = int(destination_tag)
    
    # Sign the transaction to get the hash
    # We need to derive the hash because the submit_and_wait function doesn't return a hash if transaction fails
    # TODO: tx_hash currently not used because it doesn't match the hash produced by xrpl.transaction.submit_and_wait
    # signed_tx = xrpl.transaction.sign(payment, wallet)
    # tx_hash = signed_tx.get_hash()

    payment = xrpl.models.transactions.Payment(**payment_args)

    try:    
        response = xrpl.transaction.submit_and_wait(payment, client, wallet)    
    except xrpl.transaction.XRPLReliableSubmissionException as e:
        logger.error(f"Transaction submission failed: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise

    return response

def is_valid_id(memo_dict: dict) -> bool:
    """ This function checks if a memo dictionary contains a valid ID pattern (used for both tasks and messages)"""
    memo_string = str(memo_dict)

    # Check for task ID pattern
    id_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}(?:__[A-Z0-9]{4})?)')
    has_valid_pattern = bool(re.search(id_pattern, memo_string))

    return has_valid_pattern

def classify_task_string(string: str) -> str:
    """ 
    Classifies a task string using TaskType enum patterns.
    Returns the string name of the task type
    """ 

    for task_type, patterns in TASK_PATTERNS.items():
        if any(pattern in string for pattern in patterns):
            return task_type.name

    return 'UNKNOWN'

def is_pft_transaction(tx) -> bool:
    deliver_max = tx.get('DeliverMax', {})
    return isinstance(deliver_max, dict) and deliver_max.get('currency') == 'PFT'

def generate_random_utf8_friendly_hash(length=6):
    # Generate a random sequence of bytes
    random_bytes = os.urandom(16)  # 16 bytes of randomness
    # Create a SHA-256 hash of the random bytes
    hash_object = hashlib.sha256(random_bytes)
    hash_bytes = hash_object.digest()
    # Encode the hash to base64 to make it URL-safe and readable
    base64_hash = base64.urlsafe_b64encode(hash_bytes).decode('utf-8')
    # Take the first `length` characters of the base64-encoded hash
    utf8_friendly_hash = base64_hash[:length]
    return utf8_friendly_hash

def compress_string(input_string):
    try:
        # Compress the string using Brotli
        compressed_data = brotli.compress(input_string.encode('utf-8'))
        # Encode the compressed data to a Base64 string
        base64_encoded_data = base64.b64encode(compressed_data)
        # Convert the Base64 bytes to a string
        compressed_string = base64_encoded_data.decode('utf-8')
        return compressed_string
    except Exception as e:
        raise ValueError(f"Compression failed: {e}")

def decompress_string(compressed_string):
    try:
        # Ensure correct padding for Base64 decoding
        missing_padding = len(compressed_string) % 4
        if missing_padding:
            compressed_string += '=' * (4 - missing_padding)
        
        # Validate the string contains only valid Base64 characters
        if not all(c in string.ascii_letters + string.digits + '+/=' for c in compressed_string):
            raise ValueError("Invalid Base64 characters in compressed string")

        # Decode the Base64 string to bytes
        base64_decoded_data = base64.b64decode(compressed_string)
        # Decompress the data using Brotli
        decompressed_data = brotli.decompress(base64_decoded_data)
        # Convert the decompressed bytes to a string
        decompressed_string = decompressed_data.decode('utf-8')
        return decompressed_string
    except (binascii.Error, brotli.error, UnicodeDecodeError) as e:
        raise ValueError(f"Decompression failed: {e}")
    
def get_google_doc_text(share_link):
    """ Gets the Google Doc Text """ 
    # Extract the document ID from the share link
    doc_id = share_link.split('/')[5]

    # Construct the Google Docs API URL
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    # Send a GET request to the API URL
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Return the plain text content of the document
        return response.text
    else:
        # Return an error message if the request was unsuccessful
        return f"Failed to retrieve the document. Status code: {response.status_code}"
    
def retrieve_xrp_address_from_google_doc(google_doc_text):
    """ Retreives the XRP address from the google doc """
    # Split the text into lines
    lines = google_doc_text.split('\n')      

    # Regular expression for XRP address
    xrp_address_pattern = r'r[1-9A-HJ-NP-Za-km-z]{25,34}'

    wallet_at_front_of_doc = None
    # look through the first 5 lines for an XRP address
    for line in lines[:5]:
        match = re.search(xrp_address_pattern, line)
        if match:
            wallet_at_front_of_doc = match.group()
            break

    return wallet_at_front_of_doc

class GoogleDocNotFoundException(Exception):
    """ This exception is raised when the Google Doc is not found """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc not found: {google_url}")

class XRPAccountNotFoundException(Exception):
    """ This exception is raised when the XRP account is not found """
    def __init__(self, address):
        self.address = address
        super().__init__(f"XRP account not found: {address}")

class NoMatchingTaskException(Exception):
    """ This exception is raised when no matching task is found """
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__(f"No matching task found for task ID: {task_id}")

class NoMatchingMemoException(Exception):
    """ This exception is raised when no matching memo is found """
    def __init__(self, memo_id):
        self.memo_id = memo_id
        super().__init__(f"No matching memo found for memo ID: {memo_id}")

class WrongTaskStateException(Exception):
    # TODO: restricted_flag is a hack and is confusing
    """ This exception is raised when the most recent task status is not the expected status 
    Alternatively, it can be raised when the task status is restricted 
    """
    def __init__(self, expected_status, actual_status, restricted_flag=False):
        self.expected_status = expected_status
        self.actual_status = actual_status
        prefix = "Restricted" if restricted_flag else "Expected"
        super().__init__(f"{prefix} status: {expected_status}, actual status: {actual_status}")

class InvalidGoogleDocException(Exception):
    """ This exception is raised when the google doc is not valid """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Invalid Google Doc URL: {google_url}")

# class GoogleDocDoesNotContainXrpAddressException(Exception):
#     """ This exception is raised when the google doc does not contain the XRP address """
#     def __init__(self, xrp_address):
#         self.xrp_address = xrp_address
#         super().__init__(f"Google Doc does not contain expected XRP address: {xrp_address}")

# class GoogleDocIsNotFundedException(Exception):
#     """ This exception is raised when the google doc's XRP address is not funded """
#     def __init__(self, google_url):
#         self.google_url = google_url
#         super().__init__(f"Google Doc's XRP address is not funded: {google_url}")

class GoogleDocIsNotSharedException(Exception):
    """ This exception is raised when the google doc is not shared """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc is not shared: {google_url}")

class HandshakeRequiredError(Exception):
    """ This exception is raised when a handshake is required """
    def __init__(self, destination):
        self.destination = destination
        super().__init__(f"Cannot encrypt message: no handshake received from {destination}")
