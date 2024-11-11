from pftpyclient.user_login.credential_input import CredentialManager, cache_credentials
import xrpl
from xrpl.wallet import Wallet
from xrpl.models.requests import AccountTx
from xrpl.models.transactions import Payment, Memo
from xrpl.utils import str_to_hex
from pftpyclient.basic_utilities.settings import *
import asyncio
import nest_asyncio
import pandas as pd
import numpy as np
import requests 
import binascii
import re
import random 
import string
import re
from browser_history import get_history
from sec_cik_mapper import StockMapper
import datetime
import os 
from pftpyclient.basic_utilities.settings import DATADUMP_DIRECTORY_PATH
from loguru import logger
import time
import json
import ast
from decimal import Decimal
import hashlib
import base64
import brotli
from pftpyclient.task_manager.wallet_state import (
    WalletState, 
    requires_wallet_state,
    FUNDED_STATES,
    TRUSTLINED_STATES,
    INITIATED_STATES,
    GOOGLE_DOC_SENT_STATES,
    PFT_STATES
)
from pftpyclient.performance.monitor import PerformanceMonitor

nest_asyncio.apply()

MAX_CHUNK_SIZE = 760

SAVE_MEMOS_TO_CSV = True
SAVE_TASKS_TO_CSV = True

AUTO_INITIALIZE = False

class WalletInitiationFunctions:
    def __init__(self, input_map, network_url, user_commitment=""):
        """
        input_map = {
            'Username_Input': Username,
            'Password_Input': Password,
            'Google Doc Share Link_Input': Google Doc Share Link,
            'XRP Address_Input': XRP Address,
            'XRP Secret_Input': XRP Secret,
        }
        """
        self.network_url = network_url
        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'
        self.username = input_map['Username_Input']
        self.google_doc_share_link = input_map.get('Google Doc Share Link_Input', None)
        self.xrp_address = input_map['XRP Address_Input']
        self.wallet = xrpl.wallet.Wallet.from_seed(input_map['XRP Secret_Input'])
        self.user_commitment = user_commitment
        self.pft_issuer = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'

    def get_xrp_balance(self):
        return get_xrp_balance(self.network_url, self.wallet.classic_address)

    def handle_trust_line(self):
        return handle_trust_line(self.network_url, self.pft_issuer, self.wallet)
    
    def get_google_doc_text(self, share_link):
        return get_google_doc_text(share_link)

    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('send_initiation_rite')
    def send_initiation_rite(self):
        memo = construct_initiation_rite_memo(user=self.username, commitment=self.user_commitment)
        return send_xrp(network_url=self.network_url,
                        wallet=self.wallet, 
                        amount=1, 
                        destination=self.default_node, 
                        memo=memo)

    def get_account_info(self, accountId):
        """get_account_info"""
        client = xrpl.clients.JsonRpcClient(self.network_url)
        acct_info = xrpl.models.requests.account_info.AccountInfo(
            account=accountId,
            ledger_index="validated"
        )
        response = client.request(acct_info)
        return response.result['account_data']
    
    def check_if_google_doc_is_valid(self):
        """ Checks if the google doc is valid by """

        # Check 1: google doc is a valid url
        if not self.google_doc_share_link.startswith('https://docs.google.com/document/d/'):
            raise InvalidGoogleDocException(self.google_doc_share_link)
        
        google_doc_text = self.get_google_doc_text(self.google_doc_share_link)

        # Check 2: google doc exists
        if google_doc_text == "Failed to retrieve the document. Status code: 404":
            raise GoogleDocNotFoundException(self.google_doc_share_link)

        # Check 3: google doc is shared
        if google_doc_text == "Failed to retrieve the document. Status code: 401":
            raise GoogleDocIsNotSharedException(self.google_doc_share_link)
        
        # Check 4: google doc contains the correct XRP address at the top
        if retrieve_xrp_address_from_google_doc(google_doc_text) != self.xrp_address:
            raise GoogleDocDoesNotContainXrpAddressException(self.xrp_address)
        
        # Check 5: XRP address has a balance
        if self.get_xrp_balance() == 0:
            raise GoogleDocIsNotFundedException(self.google_doc_share_link)
    
    @staticmethod
    def cache_credentials(input_map):
        """ Caches the user's credentials """
        return cache_credentials(input_map)

class PostFiatTaskManager:
    
    def __init__(self, username, password, network_url):
        self.credential_manager=CredentialManager(username,password)
        self.pw_map = self.credential_manager.decrypt_creds(self.credential_manager.pw_initiator)
        self.network_url= network_url
        self.treasury_wallet_address = 'r46SUhCzyGE4KwBnKQ6LmDmJcECCqdKy4q'
        self.pft_issuer = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'
        self.trust_line_default = '100000000'
        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'
        self.user_wallet = self.spawn_user_wallet()
        self.google_doc_link = self.pw_map.get(self.credential_manager.google_doc_name, None)

        # initialize dataframes
        self.tx_history_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_transaction_history.csv")
        self.memos_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_memos.csv")  # only used for debugging
        self.tasks_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_tasks.csv")  # only used for debugging
        self.transactions = pd.DataFrame()
        self.memos = pd.DataFrame()
        self.tasks = pd.DataFrame()

        # Initialize client for blockchain queries
        self.client = xrpl.clients.JsonRpcClient(self.network_url)
        
        # Initialize transactions
        self.sync_transactions()

        # Initialize wallet state based on account status
        self.wallet_state = self.determine_wallet_state()
        
        if AUTO_INITIALIZE:
            self.handle_trust_line()
            self.handle_google_doc()
            self.handle_genesis()

    def get_xrp_balance(self):
        return get_xrp_balance(self.network_url, self.user_wallet.classic_address)
    
    def determine_wallet_state(self):
        """Determine the current state of the wallet based on blockhain"""
        logger.debug(f"Determining wallet state for {self.user_wallet.classic_address}")
        client = xrpl.clients.JsonRpcClient(self.network_url)
        wallet_state = WalletState.UNFUNDED
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
                    wallet_state = WalletState.FUNDED
                    if self.has_trust_line():
                        wallet_state = WalletState.TRUSTLINED
                        if self.initiation_rite_sent():
                            wallet_state = WalletState.INITIATED
                            if self.google_doc_sent():
                                wallet_state = WalletState.GOOGLE_DOC_SENT
                                if self.genesis_sent():
                                    wallet_state = WalletState.ACTIVE
            else:
                logger.warning(f"Account {self.user_wallet.classic_address} does not exist on XRPL")

            return wallet_state
        
        except xrpl.clients.XRPLRequestFailureException as e:
            logger.error(f"Error determining wallet state: {e}")
            return wallet_state
        
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
                return "Send google doc link"
            case WalletState.GOOGLE_DOC_SENT:
                return "Send genesis"
            case WalletState.ACTIVE:
                return "No action required, Wallet is fully initialized"
            case _:
                return "Unknown wallet state"

    def has_trust_line(self):
        """Checks if the user has a trust line"""
        logger.debug(f"Checking if user has a trust line")
        return has_trust_line(self.network_url, self.pft_issuer, self.user_wallet)
        
    @requires_wallet_state(TRUSTLINED_STATES)
    def send_initiation_rite(self, commitment):
        memo = construct_initiation_rite_memo(user=self.credential_manager.postfiat_username, commitment=commitment)
        return send_xrp(network_url=self.network_url,
                        wallet=self.user_wallet, 
                        amount=1, 
                        destination=self.default_node, 
                        memo=memo)

    def save_dataframe_to_csv(self, df, filepath, description):
        """
        Generic method to save a dataframe to a CSV file with error handling and logging
        
        :param dataframe: pandas Dataframe to save
        :param filepath: str, path to the CSV file
        :param description: str, description of the data being saved (i.e. "transactions" or "memos")
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            # save to a temporary file first, and then replace the existing file if successful
            temp_filepath = f"{filepath}.tmp"
            df.to_csv(temp_filepath, index=False)
            os.replace(temp_filepath, filepath)

            logger.info(f"Successfully saved {description} to {filepath}")
        except PermissionError:
            logger.error(f"Permission denied when trying to save {description} to {filepath}")
        except IOError as e:
            logger.error(f"IOError when trying to save {description} to {filepath}: {e}")
        except pd.errors.EmptyDataError:
            logger.warning(f"No {description} to save. The dataframe is empty.")
        except Exception as e:
            logger.error(f"Unexpected error saving {description} to {filepath}: {e}")

    @PerformanceMonitor.measure('save_transactions_to_csv')
    def save_transactions_to_csv(self):
        self.save_dataframe_to_csv(self.transactions, self.tx_history_csv_filepath, "transactions")

    @PerformanceMonitor.measure('save_memos_to_csv')
    def save_memos_to_csv(self):
        self.save_dataframe_to_csv(self.memos, self.memos_csv_filepath, "memos")

    @PerformanceMonitor.measure('save_tasks_to_csv')
    def save_tasks_to_csv(self):
        self.save_dataframe_to_csv(self.tasks, self.tasks_csv_filepath, "tasks")

    @PerformanceMonitor.measure('load_transactions_from_csv')
    def load_transactions_from_csv(self):
        """ Loads the transactions from the CSV file into a dataframe, and deserializes some columns"""
        tx_df = None
        file_path = self.tx_history_csv_filepath
        if os.path.exists(file_path):
            logger.debug(f"Loading transactions from {file_path}")
            try:
                tx_df = pd.read_csv(file_path)

                # deserialize columns
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

        logger.warning(f"No existing transaction history file found at {self.tx_history_csv_filepath}")
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
            response = self.client.request(
                xrpl.models.requests.AccountInfo(
                    account=self.user_wallet.classic_address,
                    ledger_index="validated"
                )
            )
            if not response.is_successful():
                logger.debug("Account not found or not funded, skipping transaction sync")
                return
        except Exception as e:
            logger.error(f"Error checking account status: {e}")
            return

        # Attempt to load transactions from local csv
        if self.transactions.empty: 
            loaded_tx_df = self.load_transactions_from_csv()
            if not loaded_tx_df.empty:
                logger.debug(f"Loaded {len(loaded_tx_df)} transactions from csv file")
                self.transactions = loaded_tx_df
                self.save_transactions_to_csv()
                self.sync_memos(loaded_tx_df)

        # Choose ledger index to start sync from
        if self.transactions.empty:
            next_ledger_index = -1
        else:   # otherwise, use the next index after last known ledger index from the transactions dataframe
            next_ledger_index = self.transactions['ledger_index'].max() + 1
            logger.debug(f"Next ledger index: {next_ledger_index}")

        # fetch new transactions from the node
        new_tx_list = self.get_new_transactions(next_ledger_index)

        # Add new transactions to the dataframe
        if new_tx_list:
            logger.debug(f"Adding {len(new_tx_list)} new transactions...")
            new_tx_df = pd.DataFrame(new_tx_list)
            self.transactions = pd.concat([self.transactions, new_tx_df], ignore_index=True).drop_duplicates(subset=['hash'])
            self.save_transactions_to_csv()
            self.sync_memos(new_tx_df)
            return True
        else:
            logger.debug("No new transactions found. Finished updating local tx history")
            return False

    @PerformanceMonitor.measure('sync_memos')
    def sync_memos(self, new_tx_df):
        """ Updates the memos dataframe with new memos from the new transactions. Memos are serialized into dicts"""
        # flag rows with memos
        new_tx_df['has_memos'] = new_tx_df['tx_json'].apply(lambda x: 'Memos' in x)

        # filter for rows with memos and convert to dataframe
        new_memo_df = new_tx_df[new_tx_df['has_memos']== True].copy()

        # Extract first memo into a new column, serialize to dict
        # Any additional memos are ignored
        new_memo_df['memo_data']=new_memo_df['tx_json'].apply(lambda x: self.convert_memo_dict(x['Memos'][0]['Memo']))
        
        # Extract account and destination from tx_json into new columns
        new_memo_df['account']= new_memo_df['tx_json'].apply(lambda x: x['Account'])
        new_memo_df['destination']=new_memo_df['tx_json'].apply(lambda x: x['Destination'])
        
        # Determine message type
        new_memo_df['message_type']=np.where(new_memo_df['destination']==self.user_wallet.classic_address, 'INCOMING','OUTGOING')
        
        # Derive node account
        new_memo_df['node_account']= new_memo_df[['destination','account']].sum(1).apply(lambda x: 
                                                         str(x).replace(self.user_wallet.classic_address,''))
        
        # Convert ripple timestamp to datetime
        new_memo_df['datetime']= new_memo_df['tx_json'].apply(lambda x: self.convert_ripple_timestamp_to_datetime(x['date']))
        
        # Extract ledger index
        new_memo_df['ledger_index'] = new_memo_df['tx_json'].apply(lambda x: x['ledger_index'])

        # Flag rows with PFT
        new_memo_df['is_pft'] = new_memo_df['tx_json'].apply(is_pft_transaction)

        # Concatenate new memos to existing memos and drop duplicates
        self.memos = pd.concat([self.memos, new_memo_df], ignore_index=True).drop_duplicates(subset=['hash'])

        logger.debug(f"Added {len(new_memo_df)} memos to local memos dataframe")

        # for debugging purposes only
        if SAVE_MEMOS_TO_CSV:
            self.save_memos_to_csv()

        self.sync_tasks(new_memo_df)

    @PerformanceMonitor.measure('sync_tasks')
    def sync_tasks(self, new_memo_df):
        """ Updates the tasks dataframe with new tasks from the new memos.
        Task dataframe contains columns: user,task_id,full_output,hash,node_account,datetime,task_type"""

        # Filter for memos that are task IDs and PFT transactions
        new_task_df = new_memo_df[
            new_memo_df['memo_data'].apply(is_task_id) & 
            new_memo_df['tx_json'].apply(is_pft_transaction)
        ].copy()

        if not new_task_df.empty:
            # Add the transaction hash, node account, and datetime to the dataframe
            fields_to_add = ['hash','node_account','datetime']
            new_task_df['memo_data'] = new_task_df.apply(
            lambda row: {**row['memo_data'], **{field: row[field] for field in fields_to_add if field in row}} # ** is used to unpack the dictionaries
                , axis=1
            )

            # Convert the memo_data to a dataframe and add the task type
            new_task_df = pd.DataFrame(new_task_df['memo_data'].tolist())
            new_task_df['task_type'] = new_task_df['full_output'].apply(classify_task_string)

            # Concatenate new tasks to existing tasks and drop duplicates
            self.tasks = pd.concat([self.tasks, new_task_df], ignore_index=True).drop_duplicates(subset=['hash'])

            # for debugging purposes only
            if SAVE_TASKS_TO_CSV:
                self.save_tasks_to_csv()
        else:
            logger.debug("No new tasks to sync")

    def get_task(self, task_id):
        """ Returns the task dataframe for a given task ID """
        task_df = self.tasks[self.tasks['task_id'] == task_id]
        if task_df.empty:
            raise NoMatchingTaskException(f"No task found with task_id {task_id}")
        return task_df
    
    def get_task_state(self, task_df):
        """ Returns the latest state of a task given a task dataframe containing a single task_id """
        if task_df.empty:
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

    def hex_to_text(self,hex_string):
        bytes_object = bytes.fromhex(hex_string)
        ascii_string = bytes_object.decode("utf-8")
        return ascii_string
    
    def generate_custom_id(self):
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
    def send_xrp(self, amount, destination, memo=""):
        return send_xrp(self.network_url, self.user_wallet, amount, destination, memo)

    def convert_memo_dict(self, memo_dict):
        """Constructs a memo object with user, task_id, and full_output from hex-encoded values."""
        fields = {
            'user': 'MemoFormat',
            'task_id': 'MemoType',
            'full_output': 'MemoData'
        }
        
        return {
            key: self.hex_to_text(memo_dict.get(value, ''))
            for key, value in fields.items()
        }

    def spawn_user_wallet(self):
        """ This takes the credential manager and loads the wallet from the
        stored seed associated with the user name"""
        seed = self.pw_map[self.credential_manager.wallet_secret_name]
        live_wallet = xrpl.wallet.Wallet.from_seed(seed)
        return live_wallet

    @requires_wallet_state(FUNDED_STATES)
    def handle_trust_line(self):
        handle_trust_line(self.network_url, self.pft_issuer, self.user_wallet)

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
            chunked_memo = self._split_text_into_chunks(memo)

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

        return response
    
    @PerformanceMonitor.measure('send_memo')
    def send_memo(self, destination, memo: str, compress=True):
        """ Sends a memo to a destination, chunking by MAX_CHUNK_SIZE"""

        message_id = self.generate_custom_id()

        if compress:
            logger.debug(f"Compressing memo of length {len(memo)}")
            compressed_data = compress_string(memo)
            logger.debug(f"Compressed to length {len(compressed_data)}")
            memo = "COMPRESSED__" + compressed_data

        memo_chunks = self._split_text_into_chunks(memo)

        response = []
        for idx, memo_chunk in enumerate(memo_chunks):
            log_content = memo_chunk
            if compress and idx == 0:
                try:
                    # Only log a preview of the original content
                    log_content = f"[compressed memo preview] {memo[:100]}..."
                except Exception as e:
                    logger.error(f"Error decompressing memo chunk: {e}")
                    log_content = "[compressed content]"
                
            logger.debug(f"Sending chunk {idx+1} of {len(memo_chunks)}: {log_content[:100]}...")
        
            memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                task_id=message_id, 
                                                full_output=memo_chunk)

            response.append(self._send_memo_single(destination, memo))

        return response
    
    def _send_memo_single(self, destination, memo):
        """ Sends a memo to a destination. """
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
            value=str(1)
        )

        payment = xrpl.models.transactions.Payment(
            account=self.user_wallet.address,
            amount=amount_to_send,
            destination=destination,
            memos=memos,
        )

        try:
            logger.debug("Submitting and waiting for transaction")
            response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            response = f"Transaction submission failed: {e}"
            logger.error(response)
        except Exception as e:
            response = f"Unexpected error: {e}"
            logger.error(response)

        return response

    def _split_text_into_chunks(self, text, max_chunk_size=MAX_CHUNK_SIZE):
        """ Helper method to build a list of Memo objects representing a single memo string split into chunks """

        chunks = []

        text_bytes = text.encode('utf-8')

        for i in range(0, len(text_bytes), max_chunk_size):
            chunk = text_bytes[i:i+max_chunk_size]
            chunk_number = i // max_chunk_size + 1
            chunk_label = f"chunk_{chunk_number}__".encode('utf-8')
            chunk_with_label = chunk_label + chunk
            chunks.append(chunk_with_label)

        return [chunk.decode('utf-8', errors='ignore') for chunk in chunks]
    
## MEMO FORMATTING AND MEMO CREATION TOOLS
    
    def get_account_transactions__limited(self, account_address,
                                    ledger_index_min=-1,
                                    ledger_index_max=-1, 
                                    limit=10):
            client = xrpl.clients.JsonRpcClient(self.network_url) # Using a public server; adjust as necessary
        
            request = AccountTx(
                account=account_address,
                ledger_index_min=ledger_index_min,  # Use -1 for the earliest ledger index
                ledger_index_max=ledger_index_max,  # Use -1 for the latest ledger index
                limit=limit,                        # Adjust the limit as needed
                forward=True                        # Set to True to return results in ascending order
            )
        
            response = client.request(request)
            transactions = response.result.get("transactions", [])
        
            if "marker" in response.result:  # Check if a marker is present for pagination
                print("More transactions available. Marker for next batch:", response.result["marker"])
        
            return transactions
    
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
                if response.is_successful():
                    transactions = response.result.get("transactions", [])
                    logger.debug(f"Retrieved {len(transactions)} transactions")
                    all_transactions.extend(transactions)
                else:
                    logger.error(f"Error in XRPL response: {response.status}")
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

        # Filter for memos that are PFT-related, sent to the default node, outgoing, and are google doc context links
        redux_tx_list = self.memos[
            self.memos['is_pft'] & 
            (self.memos['destination']==self.default_node) &
            (self.memos['message_type']=='OUTGOING') & 
            (self.memos['memo_data'].apply(lambda x: x['task_id']) == 'google_doc_context_link')
            ]
        
        logger.debug(f"Found {len(redux_tx_list)} outgoing context doc links")
        
        if len(redux_tx_list) == 0:
            logger.warning("No Google Doc context link found")
            return None
        
        # Get the most recent google doc context link
        most_recent_context_link = redux_tx_list.tail(1)

        link = most_recent_context_link['memo_data'].iloc[0]['full_output']

        logger.debug(f"Most recent context doc link: {link}")

        return link

    def output_account_address_node_association(self):
        """this takes the account info frame and figures out what nodes
         the account is associating with and returns them in a dataframe """
        self.memos['valid_task_id']=self.memos['memo_data'].apply(is_task_id)
        node_output_df = self.memos[self.memos['message_type']=='INCOMING'][['valid_task_id','account']].groupby('account').sum()
   
        return node_output_df[node_output_df['valid_task_id']>0]
    
    def get_user_initiation_rites_destinations(self):
        """Returns all the addresses that have received a user initiation rite"""
        all_user_initiation_rites = self.memos[self.memos['memo_data'].apply(lambda x: x.get('task_id') == 'INITIATION_RITE')]
        return list(all_user_initiation_rites['destination'])
    
    def initiation_rite_sent(self):
        logger.debug("Checking if user has sent initiation rite...")

        # Check if memos dataframe is empty or missing required columns
        if self.memos.empty or not all(col in self.memos.columns for col in ['destination', 'memo_data']):
            logger.debug("Memos dataframe is empty or missing required columns, returning False")
            return False

        user_initiation_rites_destinations = self.get_user_initiation_rites_destinations()
        return self.default_node in user_initiation_rites_destinations

    def get_user_genesis_destinations(self):
        """ Returns all the addresses that have received a user genesis transaction"""
        all_user_genesis = self.memos[self.memos['memo_data'].apply(lambda x: 'USER GENESIS __' in str(x))]
        return list(all_user_genesis['destination'])

    def genesis_sent(self):
        user_genesis_destinations = self.get_user_genesis_destinations()
        return self.default_node in user_genesis_destinations
    
    @requires_wallet_state(INITIATED_STATES)
    def handle_genesis(self):
        """ Checks if the user has sent a genesis to the node, and sends one if not """
        if not self.genesis_sent():
            logger.debug("Genesis not found amongst outgoing memos.")
            self.send_genesis()
        else:
            logger.debug("User has already sent genesis, skipping...")
    
    def send_genesis(self):
        """ Sends a user genesis transaction to the default node 
        Currently requires 7 PFT
        """
        logger.debug("Sending node genesis transaction...")
        genesis_memo = construct_genesis_memo(
            user=self.credential_manager.postfiat_username,
            task_id=self.generate_custom_id(),
            full_output=f'USER GENESIS __ user: {self.credential_manager.postfiat_username}'
        )
        self.send_pft(amount=7, destination=self.default_node, memo=genesis_memo)

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
        
        # Check 4: google doc contains the correct XRP address at the top
        if retrieve_xrp_address_from_google_doc(google_doc_text) != self.user_wallet.classic_address:
            raise GoogleDocDoesNotContainXrpAddressException(self.user_wallet.classic_address)
        
        # Check 5: XRP address has a balance
        if self.get_xrp_balance() == 0:
            raise GoogleDocIsNotFundedException(google_doc_link)    
    
    def google_doc_sent(self):
        """Checks if the user has sent a google doc context link"""
        return self.get_latest_outgoing_context_doc_link() is not None
    
    @requires_wallet_state(INITIATED_STATES)
    def handle_google_doc_setup(self, google_doc_link):
        """Validates, caches, and sends the Google Doc link"""
        logger.info("Setting up Google Doc...")

        # Validate the Google Doc
        self.check_if_google_doc_is_valid(google_doc_link)
        
        # Cache the Google Doc link
        try:
            self.credential_manager.enter_and_encrypt_credential(
                credentials_dict={
                    f'{self.credential_manager.postfiat_username}__googledoc': google_doc_link
                }
            )
            self.google_doc_link = google_doc_link
        except Exception as e:
            logger.error(f"Error caching Google Doc link: {e}")
            return
        
        # Send the Google Doc link to the node
        self.handle_google_doc()
    
    @requires_wallet_state(INITIATED_STATES)
    def handle_google_doc(self):
        """Checks for google doc and prompts user to send if not found"""
        if self.google_doc_link is None:
            logger.warning("Google Doc link not found in credentials")
            return

        if not self.google_doc_sent():
            logger.debug("Google Doc context link not found amongst outgoing memos.")
            self.send_google_doc()
        else:
            logger.debug("User has already sent a Google Doc context link, skipping...")
    
    def send_google_doc(self):
        """ Sends the Google Doc context link to the node """
        logger.debug(f"Sending Google Doc context link to the node: {self.google_doc_link}")
        google_doc_memo = construct_google_doc_context_memo(user=self.credential_manager.postfiat_username,
                                                                    google_doc_link=self.google_doc_link)
        self.send_pft(amount=1, destination=self.default_node, memo=google_doc_memo)
        logger.debug("Google Doc context link sent.")

    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_proposals_df')
    def get_proposals_df(self):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, proposal, and acceptance""" 

        # Filter tasks with task_type in ['PROPOSAL','ACCEPTANCE']
        filtered_tasks = self.tasks[self.tasks['task_type'].isin(['PROPOSAL','ACCEPTANCE'])]

        # Get task_ids where the latest state is 'PROPOSAL' or 'ACCEPTANCE'
        proposal_task_ids = [
            task_id for task_id in filtered_tasks['task_id'].unique()
            if self.get_task_state_using_task_id(task_id) in ['PROPOSAL','ACCEPTANCE']
        ]

        # Filter for these tasks
        filtered_df = self.tasks[(self.tasks['task_id'].isin(proposal_task_ids))].copy()

        if filtered_df.empty:
            return pd.DataFrame()

        # Create new 'RESPONSE' column to combine acceptance and refusal
        filtered_df['response_type'] = filtered_df['task_type'].apply(lambda x: 'RESPONSE' if x in ['ACCEPTANCE','REFUSAL'] else x)

        # Pivot the dataframe to get proposals and responses side by side and reset index to make task_id a column
        pivoted_df = filtered_df.pivot_table(index='task_id', columns='response_type', values='full_output', aggfunc='first').reset_index().copy()

        # Rename the columns for clarity
        pivoted_df.rename(columns={'REQUEST_POST_FIAT':'request', 'PROPOSAL':'proposal', 'RESPONSE':'response'}, inplace=True)

        # Clean up the proposal column
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace('PROPOSED PF ___ ','').replace('nan',''))

        # Clean up the request column
        pivoted_df['request'] = pivoted_df['request'].apply(lambda x: str(x).replace('REQUEST_POST_FIAT ___ ','').replace('nan',''))
        
        # Clean up the response column, if it exists (does not exist for the first proposal)
        if 'response' in pivoted_df.columns:
            pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace('ACCEPTANCE REASON ___ ','ACCEPTED: ').replace('nan',''))
            pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace('REFUSAL REASON ___ ','REFUSED: ').replace('nan',''))
        else:
            pivoted_df['response'] = ''
        
        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_verification_df')
    def get_verification_df(self):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, original_task, and verification""" 

        # Filter tasks with task_type in ['PROPOSAL','VERIFICATION_PROMPT']
        filtered_tasks = self.tasks[self.tasks['task_type'].isin(['PROPOSAL','VERIFICATION_PROMPT'])]

        # Get task_ids where the latest state is 'VERIFICATION_PROMPT'
        verification_task_ids = [
            task_id for task_id in filtered_tasks['task_id'].unique()
            if self.get_task_state_using_task_id(task_id) == 'VERIFICATION_PROMPT'
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
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace('PROPOSED PF ___',''))
        pivoted_df['verification'] = pivoted_df['verification'].apply(lambda x: str(x).replace('VERIFICATION PROMPT ___',''))

        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_rewards_df')
    def get_rewards_df(self):
        """ This reduces tasks dataframe into a dataframe containing the columns task_id, proposal, and reward""" 

        # Filter for only PROPOSAL and REWARD rows
        filtered_df = self.tasks[self.tasks['task_type'].isin(['PROPOSAL','REWARD'])]

        # Get task_ids where the latest state is 'REWARD'
        reward_task_ids = [
            task_id for task_id in filtered_df['task_id'].unique()
            if self.get_task_state_using_task_id(task_id) == 'REWARD'
        ]

        # Filter for these tasks
        filtered_df = self.tasks[(self.tasks['task_id'].isin(reward_task_ids))].copy()

        if filtered_df.empty:
            return pd.DataFrame()

        # Pivot the dataframe to get proposals and rewards side by side and reset index to make task_id a column
        pivoted_df = filtered_df.pivot_table(index='task_id', columns='task_type', values='full_output', aggfunc='first').reset_index().copy()

        # Rename the columns for clarity
        pivoted_df.rename(columns={'PROPOSAL':'proposal', 'REWARD':'reward'}, inplace=True)

        # Clean up the proposal and reward columns
        pivoted_df['reward'] = pivoted_df['reward'].apply(lambda x: str(x).replace('REWARD RESPONSE __ ',''))
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace('PROPOSED PF ___ ','').replace('nan',''))

        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        # Add PFT value information
        pft_only = self.memos[self.memos['tx_json'].apply(is_pft_transaction)].copy()
        pft_only['pft_value'] = pft_only['tx_json'].apply(lambda x: x['DeliverMax']['value']).astype(float) * pft_only['message_type'].map({'INCOMING':1,'OUTGOING':-1})
        pft_only['task_id'] = pft_only['memo_data'].apply(lambda x: x['task_id'])
        
        pft_rewards_only = pft_only[pft_only['memo_data'].apply(lambda x: 'REWARD RESPONSE __' in x['full_output'])].copy()
        task_id_to_payout = pft_rewards_only.groupby('task_id').last()['pft_value']
        
        result_df['payout'] = result_df['task_id'].map(task_id_to_payout)

        # Remove rows where payout is NaN
        result_df = result_df[result_df['payout'].notna()].reset_index(drop=True).copy()

        return result_df
    
    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_memos_df')
    def get_memos_df(self):

        def remove_chunks(text):
            # Use regular expression to remove all occurrences of chunk_1__, chunk_2__, etc.
            cleaned_text = re.sub(r'chunk_\d+__', '', text)
            return cleaned_text
        
        if self.tasks is None or self.tasks.empty or "task_type" not in self.tasks.columns:
            logger.debug("No self.tasks found to get memos from, returning empty dataframe")
            return pd.DataFrame()

        # Filter tasks with task_type in ['MEMO']
        chunked_memos = self.tasks[self.tasks['task_type'].isin(['MEMO'])].copy()

        # Remove "chunk_[index]_" prefix from the 'full_output' column
        chunked_memos['full_output'] = chunked_memos['full_output'].apply(remove_chunks)

        # Rename full_output to memos, and task_id to message_id
        chunked_memos.rename(columns={'task_id' : 'memo_id', 'full_output': 'memo'}, inplace=True)

        # Unchunk the memos
        memos = chunked_memos.groupby('memo_id')['memo'].apply(''.join).reset_index()

        def decompress_memo(memo):
            if memo.startswith("COMPRESSED__"):
                try:
                    return decompress_string(memo.replace("COMPRESSED__", ""))
                except ValueError as e:
                    logger.error(f"Error decompressing memo: {e}")
                    return f"[Decompression failed: {memo[:100]}...]"
            return memo
        
        memos['memo'] = memos['memo'].apply(decompress_memo)

        # Reverse order to get the most recent memos first
        memos = memos.iloc[::-1].reset_index(drop=True).copy()
        
        return memos

    @PerformanceMonitor.measure('send_acceptance_for_task_id')
    def send_acceptance_for_task_id(self, task_id, acceptance_string):
        task_df = self.get_task(task_id)
        most_recent_status = self.get_task_state(task_df)

        if most_recent_status != 'PROPOSAL':
            raise WrongTaskStateException('PROPOSAL', most_recent_status)

        proposal_source = task_df.iloc[0]['node_account']
        if 'ACCEPTANCE REASON ___' not in acceptance_string:
            classified_string='ACCEPTANCE REASON ___ '+acceptance_string
        else:
            classified_string=acceptance_string
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                    task_id=task_id, full_output=classified_string)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_acceptance_for_task_id response: {response}")
        return response

    @PerformanceMonitor.measure('send_refusal_for_task')
    def send_refusal_for_task(self, task_id, refusal_reason):
        # TODO - rewrite this to use the get_task and get_task_state methods
        """ 
        This function refuses a task. The function will not work if the task has already 
        been accepted, refused, or completed. 

        EXAMPLE PARAMETERS
        task_id='2024-05-14_19:10__ME26'
        refusal_reason = 'I cannot accept this task because ...'
        """
        task_df = self.tasks
        task_statuses = task_df[task_df['task_id'] 
        == task_id]['task_type'].unique()

        if any(status in task_statuses for status in ['REFUSAL', 'ACCEPTANCE', 
            'VERIFICATION_RESPONSE', 'USER_GENESIS', 'REWARD']):
            print('Task is not valid for refusal. Its statuses include:')
            print(task_statuses)
            return

        if 'PROPOSAL' not in task_statuses:
            print('Task must have a proposal to be refused. Current statuses include:')
            print(task_statuses)
            return

        print('Proceeding to refuse task')
        node_account = list(task_df[task_df['task_id'] 
            == task_id].tail(1)['node_account'])[0]
        if 'REFUSAL REASON ___' not in refusal_reason:
            refusal_reason = 'REFUSAL REASON ___ ' + refusal_reason
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, full_output=refusal_reason)
        response = self.send_pft(amount=1, destination=node_account, memo=constructed_memo)
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
        task_id = self.generate_custom_id()
        
        # Construct the memo with the request message
        if 'REQUEST_POST_FIAT ___' not in request_message:
            classified_request_msg = 'REQUEST_POST_FIAT ___ ' + request_message
        else:
            classified_request_msg = request_message
        constructed_memo = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, 
                                                               full_output=classified_request_msg)
        # Send the memo to the default node
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

        if most_recent_status != 'ACCEPTANCE':
            raise WrongTaskStateException('ACCEPTANCE', most_recent_status)
        
        proposal_source = task_df.iloc[0]['node_account']
        if 'COMPLETION JUSTIFICATION ___' not in completion_string:
            classified_completion_str = 'COMPLETION JUSTIFICATION ___ ' + completion_string
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
        
        if most_recent_status != 'VERIFICATION_PROMPT':
            raise WrongTaskStateException('VERIFICATION_PROMPT', most_recent_status)
        
        proposal_source = task_df.iloc[0]['node_account']
        if 'VERIFICATION RESPONSE ___' not in response_string:
            classified_response_str = 'VERIFICATION RESPONSE ___ ' + response_string
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
        user_default_node = self.default_node
        # Slicing data based on conditions
        google_doc_slice = self.memos[self.memos['memo_data'].apply(lambda x: 
                                                                   'google_doc_context_link' in str(x))].copy()

        genesis_slice = self.memos[self.memos['memo_data'].apply(lambda x: 
                                                                   'USER GENESIS __' in str(x))].copy()
        
        # Extract genesis username
        genesis_username = "Unknown"
        if not genesis_slice.empty:
            genesis_username = list(genesis_slice['memo_data'])[0]['full_output'].split(' __')[-1].split('user:')[-1].strip()
        
        # Extract Google Doc key
        key_google_doc = "No Google Doc available."
        if not google_doc_slice.empty:
            key_google_doc = list(google_doc_slice['memo_data'])[0]['full_output']

        # Sorting account info by datetime
        sorted_account_info = self.memos.sort_values('datetime', ascending=True).copy()

        def extract_latest_message(message_type, node, is_outgoing):
            """
            Extract the latest message of a given type for a specific node.
            """
            if is_outgoing:
                latest_message = sorted_account_info[
                    (sorted_account_info['message_type'] == message_type) &
                    (sorted_account_info['destination'] == node)
                ].tail(1)
            else:
                latest_message = sorted_account_info[
                    (sorted_account_info['message_type'] == message_type) &
                    (sorted_account_info['account'] == node)
                ].tail(1)
            
            if not latest_message.empty:
                return latest_message.iloc[0].to_dict()
            else:
                return {}

        def format_dict(data):
            if data:
                standard_format = f"https://livenet.xrpl.org/transactions/{data.get('hash', '')}/detailed"
                full_output = data.get('memo_data', {}).get('full_output', 'N/A')
                task_id = data.get('memo_data', {}).get('task_id', 'N/A')
                formatted_string = (
                    f"Task ID: {task_id}\n"
                    f"Full Output: {full_output}\n"
                    f"Hash: {standard_format}\n"
                    f"Datetime: {pd.Timestamp(data['datetime']).strftime('%Y-%m-%d %H:%M:%S') if 'datetime' in data else 'N/A'}\n"
                )
                return formatted_string
            else:
                return "No data available."

        # Extracting most recent messages
        most_recent_outgoing_message = extract_latest_message('OUTGOING', user_default_node, True)
        most_recent_incoming_message = extract_latest_message('INCOMING', user_default_node, False)
        
        # Formatting messages
        incoming_message = format_dict(most_recent_incoming_message)
        outgoing_message = format_dict(most_recent_outgoing_message)
        user_classic_address = self.user_wallet.classic_address
        # Compiling key display information
        key_display_info = {
            'Google Doc': key_google_doc,
            'Genesis Username': genesis_username,
            'Account Address' : user_classic_address,
            'Default Node': user_default_node,
            'Incoming Message': incoming_message,
            'Outgoing Message': outgoing_message
        }
        
        return key_display_info

    def ux__convert_response_object_to_status_message(self, response):
        """ Takes a response object from an XRP transaction and converts it into legible transaction text""" 
        status_constructor = 'unsuccessfully'
        logger.debug(f"Response: {response}")
        if 'success' in response.status:
            status_constructor = 'successfully'
        non_hex_memo = self.convert_memo_dict(response.result['tx_json']['Memos'][0]['Memo'])
        user_string = non_hex_memo['full_output']
        amount_of_pft_sent = response.result['tx_json']['DeliverMax']['value']
        node_name = response.result['tx_json']['Destination']
        output_string = f"""User {status_constructor} sent {amount_of_pft_sent} PFT with request '{user_string}' to Node {node_name}"""
        return output_string

    @PerformanceMonitor.measure('send_pomodoro_for_task_id')
    def send_pomodoro_for_task_id(self,task_id = '2024-05-19_10:27__LL78',pomodoro_text= 'spent last 30 mins doing a ton of UX debugging'):
        pomodoro_id = task_id.replace('__','==')
        memo_to_send = construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username,
                                           task_id=pomodoro_id, full_output=pomodoro_text)
        response = self.send_pft(amount=1, destination=self.default_node, memo=memo_to_send)
        return response

    def get_all_pomodoros(self):
        task_id_only = self.memos[self.memos['memo_data'].apply(lambda x: 'task_id' in str(x))].copy()
        pomodoros_only = task_id_only[task_id_only['memo_data'].apply(lambda x: '==' in x['task_id'])].copy()
        pomodoros_only['parent_task_id']=pomodoros_only['memo_data'].apply(lambda x: x['task_id'].replace('==','__'))
        return pomodoros_only
    
def is_over_1kb(string):
    # 1KB = 1024 bytes
    return len(string.encode('utf-8')) > 1024

def to_hex(string):
    return binascii.hexlify(string.encode()).decode()

def construct_basic_postfiat_memo(user, task_id, full_output):
    return construct_memo(user=user, memo_type=task_id, memo_data=full_output)

def construct_initiation_rite_memo(user='goodalexander', commitment='I commit to generating massive trading profits using AI and investing them to grow the Post Fiat Network'):
    return construct_memo(user=user, memo_type='INITIATION_RITE', memo_data=commitment)

def construct_google_doc_context_memo(user, google_doc_link):                  
    return construct_memo(user=user, memo_type='google_doc_context_link', memo_data=google_doc_link) 

def construct_genesis_memo(user, task_id, full_output):
    return construct_memo(user=user, memo_type=task_id, memo_data=full_output)

def construct_memo(user, memo_type, memo_data):

    if is_over_1kb(memo_data):
        raise ValueError("Memo exceeds 1 KB, raising ValueError")

    return Memo(
        memo_data=to_hex(memo_data),
        memo_type=to_hex(memo_type),
        memo_format=to_hex(user)
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

def send_xrp(network_url, wallet: xrpl.wallet.Wallet, amount, destination, memo=""):
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

    payment = xrpl.models.transactions.Payment(
        account=wallet.address,
        amount=xrpl.utils.xrp_to_drops(Decimal(amount)),
        destination=destination,
        memos=memos,
    )
    # Sign the transaction to get the hash
    # We need to derive the hash because the submit_and_wait function doesn't return a hash if transaction fails
    # TODO: tx_hash currently not used because it doesn't match the hash produced by xrpl.transaction.submit_and_wait
    # signed_tx = xrpl.transaction.sign(payment, wallet)
    # tx_hash = signed_tx.get_hash()

    try:    
        response = xrpl.transaction.submit_and_wait(payment, client, wallet)    
    except xrpl.transaction.XRPLReliableSubmissionException as e:
        response = f"Transaction submission failed: {e}"
        logger.error(response)
    except Exception as e:
        response = f"Unexpected error: {e}"
        logger.error(response)

    return response

def is_task_id(memo_dict) -> bool:
    """ This function checks if a memo dictionary contains a task ID or the required fields
    for a task ID """
    memo_string = str(memo_dict)

    # Check for task ID pattern
    task_id_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}(?:__[A-Z0-9]{4})?)')
    if re.search(task_id_pattern, memo_string):
        return True
    
    # Check for required fields
    required_fields = ['user:', 'full_output:']
    return all(field in memo_string for field in required_fields)

def classify_task_string(string):
    """ These are the canonical classifications for task strings 
    on a Post Fiat Node
    """ 
    categories = {
            'ACCEPTANCE': ['ACCEPTANCE REASON ___'],
            'PROPOSAL': [' .. ','PROPOSED PF ___'],
            'REFUSAL': ['REFUSAL REASON ___'],
            'VERIFICATION_PROMPT': ['VERIFICATION PROMPT ___'],
            'VERIFICATION_RESPONSE': ['VERIFICATION RESPONSE ___'],
            'REWARD': ['REWARD RESPONSE __'],
            'TASK_OUTPUT': ['COMPLETION JUSTIFICATION ___'],
            'USER_GENESIS': ['USER GENESIS __'],
            'REQUEST_POST_FIAT':['REQUEST_POST_FIAT ___'],
            'MEMO': ['chunk_'],
        }

    for category, keywords in categories.items():
        if any(keyword in string for keyword in keywords):
            return category

    return 'UNKNOWN'

def is_pft_transaction(tx) -> bool:
    deliver_max = tx.get('DeliverMax', {})
    return isinstance(deliver_max, dict) and deliver_max.get('currency') == 'PFT'

def get_pft_holder_df(network_url, pft_issuer):
    """ This function outputs a detail of all accounts holding PFT tokens
    with a float of their balances as pft_holdings. note this is from
    the view of the issuer account so balances appear negative so the pft_holdings 
    are reverse signed.
    """
    client = xrpl.clients.JsonRpcClient(network_url)
    logger.debug("Getting dataframe of all accounts holding PFT tokens...")
    response = client.request(xrpl.models.requests.AccountLines(
        account=pft_issuer,
        ledger_index="validated",
        peer=None,
        limit=None))
    if not response.is_successful():
        raise Exception(f"Error fetching PFT holders: {response.result.get('error')}")
    full_post_fiat_holder_df = pd.DataFrame(response.result)
    for xfield in ['account','balance','currency','limit_peer']:
        full_post_fiat_holder_df[xfield] = full_post_fiat_holder_df['lines'].apply(lambda x: x[xfield])
    full_post_fiat_holder_df['pft_holdings']=full_post_fiat_holder_df['balance'].astype(float)*-1
    return full_post_fiat_holder_df
    
def has_trust_line(network_url, pft_issuer, wallet):
    """ This function checks if the user has a trust line to the PFT token"""
    try:
        pft_holders = get_pft_holder_df(network_url, pft_issuer)
        existing_pft_accounts = list(pft_holders['account'])
        user_is_in_pft_accounts = wallet.address in existing_pft_accounts
        return user_is_in_pft_accounts
    except Exception as e:
        logger.error(f"Error checking if user has a trust line: {e}")
        return False

def handle_trust_line(network_url, pft_issuer, wallet):
    """ This function checks if the user has a trust line to the PFT token
    and if not establishes one"""
    logger.debug("Checking if trust line exists...")
    if not has_trust_line(network_url, pft_issuer, wallet):
        _ = generate_trust_line_to_pft_token(network_url, wallet)
        logger.debug("Trust line created")
    else:
        logger.debug("Trust line already exists")

def generate_trust_line_to_pft_token(network_url, wallet: xrpl.wallet.Wallet):
    """ Note this transaction consumes XRP to create a trust
    line for the PFT Token so the holder DF should be checked 
    before this is run
    """ 
    client = xrpl.clients.JsonRpcClient(network_url)
    trust_set_tx = xrpl.models.transactions.TrustSet(
        account=wallet.address,
        limit_amount=xrpl.models.amounts.issued_currency_amount.IssuedCurrencyAmount(
            currency="PFT",
            issuer='rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW',
            value='100000000',  # Large limit, arbitrarily chosen
        )
    )
    logger.debug(f"Creating trust line from {wallet.address} to issuer...")
    try:
        response = xrpl.transaction.submit_and_wait(trust_set_tx, client, wallet)
    except xrpl.transaction.XRPLReliableSubmissionException as e:
        response = f"Submit failed: {e}"
        logger.error(f"Trust line creation failed: {response}")
    return response

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

class WrongTaskStateException(Exception):
    """ This exception is raised when the most recent task status is not the expected status """
    def __init__(self, expected_status, actual_status):
        self.expected_status = expected_status
        self.actual_status = actual_status
        super().__init__(f"Expected status: {expected_status}, actual status: {actual_status}")

class InvalidGoogleDocException(Exception):
    """ This exception is raised when the google doc is not valid """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Invalid Google Doc URL: {google_url}")

class GoogleDocDoesNotContainXrpAddressException(Exception):
    """ This exception is raised when the google doc does not contain the XRP address """
    def __init__(self, xrp_address):
        self.xrp_address = xrp_address
        super().__init__(f"Google Doc does not contain expected XRP address: {xrp_address}")

class GoogleDocIsNotFundedException(Exception):
    """ This exception is raised when the google doc's XRP address is not funded """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc's XRP address is not funded: {google_url}")

class GoogleDocIsNotSharedException(Exception):
    """ This exception is raised when the google doc is not shared """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc is not shared: {google_url}")

# class ProcessUserWebData:
#     def __init__(self):
#         print('kick off web history')
#         self.ticker_regex = re.compile(r'\b[A-Z]{1,5}\b')
#         #self.cik_regex = re.compile(r'CIK=(\d{10})|data/(\d{10})')
#         self.cik_regex = re.compile(r'CIK=(\d+)|data/(\d+)')
#         # THIS DOES NOT WORK FOR 'https://www.sec.gov/edgar/browse/?CIK=1409375&owner=exclude'
#         mapper = StockMapper()
#         self.cik_to_ticker_map = mapper.cik_to_tickers
#     def get_user_web_history_df(self):
#         outputs = get_history()
#         historical_info = pd.DataFrame(outputs.histories)
#         historical_info.columns=['date','url','content']
#         return historical_info
#     def get_primary_ticker_for_cik(self, cik):
#         ret = ''
#         try:
#             ret = list(self.cik_to_ticker_map[cik])[0]
#         except:
#             pass
#         return ret

#     def extract_cik_to_ticker(self, input_string):
#         # Define a regex pattern to match CIKs
#         cik_regex = self.cik_regex
        
#         # Find all matches in the input string
#         matches = cik_regex.findall(input_string)
        
#         # Extract CIKs from the matches and zfill to 10 characters
#         ciks = [match[0] or match[1] for match in matches]
#         padded_ciks = [cik.zfill(10) for cik in ciks]
#         output = ''
#         if len(padded_ciks) > 0:
#             output = self.get_primary_ticker_for_cik(padded_ciks[0])
        
#         return output
    

#     def extract_tickers(self, stringer):
#         tickers = list(set(self.ticker_regex.findall(stringer)))
#         return tickers

#     def create_basic_web_history_frame(self):
#         all_web_history_df = self.get_user_web_history_df()
#         all_web_history_df['cik_ticker_extraction']= all_web_history_df['url'].apply(lambda x: [self.extract_cik_to_ticker(x)])
#         all_web_history_df['content_tickers']=all_web_history_df['content'].apply(lambda x: self.extract_tickers(x))#.tail(20)
#         all_web_history_df['url_tickers']=all_web_history_df['url'].apply(lambda x: self.extract_tickers(x))#.tail(20)
#         all_web_history_df['all_tickers']=all_web_history_df['content_tickers']+all_web_history_df['url_tickers']+all_web_history_df['cik_ticker_extraction']
#         all_web_history_df['date_str']=all_web_history_df['date'].apply(lambda x: x.strftime('%Y-%m-%d'))
#         str_map = pd.DataFrame(all_web_history_df['date_str'].unique())
#         str_map.columns=['date_str']
#         str_map['date']=pd.to_datetime(str_map['date_str'])
#         all_web_history_df['simplified_date']=all_web_history_df['date_str'].map(str_map.groupby('date_str').last()['date'])
#         all_web_history_df['all_tickers']=all_web_history_df['all_tickers'].apply(lambda x: list(set(x)))
#         return all_web_history_df

#     def convert_all_web_history_to_simple_web_data_json(self,all_web_history):
#         recent_slice = all_web_history[all_web_history['simplified_date']>=datetime.datetime.now()-datetime.timedelta(7)].copy()
#         recent_slice['explode_block']=recent_slice.apply(lambda x: pd.DataFrame(([[i,x['simplified_date']] for i in x['all_tickers']])),axis=1)
        
#         full_ticker_history  =pd.concat(list(recent_slice['explode_block']))
#         full_ticker_history.columns=['ticker','date']
#         full_ticker_history['included']=1
#         stop_tickers=['EDGAR','CIK','ETF','FORM','API','HOME','GAAP','EPS','NYSE','XBRL','AI','SBF','I','US','USD','SEO','','A','X','SEC','PC','EX','UTF','SIC']
#         multidex = full_ticker_history.groupby(['ticker','date']).last().sort_index()
#         financial_attention_df = multidex[~multidex.index.get_level_values(0).isin(stop_tickers)]['included'].unstack(0).sort_values('date').resample('D').last()
#         last_day = financial_attention_df[-1:].sum()
#         last_week = financial_attention_df[-7:].sum()
        
#         ld_lw = pd.concat([last_day, last_week],axis=1)
#         ld_lw.columns=['last_day','last_week']
#         ld_lw=ld_lw.astype(int)
#         ld_lw[ld_lw.sum(1)>0].to_json()
#         return ld_lw