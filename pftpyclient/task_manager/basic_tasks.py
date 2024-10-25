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

nest_asyncio.apply()

class WalletInitiationFunctions:
    def __init__(self, input_map, user_commitment=""):
        """
        input_map = {
            'Username_Input': Username,
            'Password_Input': Password,
            'Google Doc Share Link_Input': Google Doc Share Link,
            'XRP Address_Input': XRP Address,
            'XRP Secret_Input': XRP Secret,
        }
        """
        self.mainnet_url="https://s2.ripple.com:51234"
        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'
        self.username = input_map['Username_Input']
        self.google_doc_share_link = input_map['Google Doc Share Link_Input']
        self.xrp_address = input_map['XRP Address_Input']
        self.wallet = xrpl.wallet.Wallet.from_seed(input_map['XRP Secret_Input'])
        self.user_commitment = user_commitment
        self.pft_issuer = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'

    def get_xrp_balance(self):
        return get_xrp_balance(self.mainnet_url, self.wallet.classic_address)

    def handle_trust_line(self):
        return handle_trust_line(self.mainnet_url, self.pft_issuer, self.wallet)

    def get_google_doc_text(self,share_link):
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

    def send_initiation_rite(self):
        memo = Memo(memo_data=self.user_commitment, memo_type='INITIATION_RITE', memo_format=to_hex(self.username))
        return send_xrp(mainnet_url=self.mainnet_url,
                        sending_wallet=self.wallet, 
                        amount=1, 
                        destination=self.default_node, 
                        memo=memo)

    def get_account_info(self, accountId):
        """get_account_info"""
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
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
        if self.retrieve_xrp_address_from_google_doc(google_doc_text) != self.xrp_address:
            raise GoogleDocDoesNotContainXrpAddressException(self.xrp_address)
        
        # Check 5: XRP address has a balance
        if self.get_xrp_balance() == 0:
            raise GoogleDocIsNotFundedException(self.google_doc_share_link)

    @staticmethod
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
    
    @staticmethod
    def cache_credentials(input_map):
        """ Caches the user's credentials """
        return cache_credentials(input_map)


    # def check_if_there_is_funded_account_at_front_of_google_doc(self, google_url):
    #     """
    #     Checks if there is a balance bearing XRP account address at the front of the google document 
    #     This is required for the user 

    #     Returns the balance in XRP drops 
    #     EXAMPLE
    #     google_url = 'https://docs.google.com/document/d/1MwO8kHny7MtU0LgKsFTBqamfuUad0UXNet1wr59iRCA/edit'
    #     """
    #     balance = 0
    #     try:
    #         google_doc_text = self.get_google_doc_text(google_url)

    #         # Split the text into lines
    #         lines = google_doc_text.split('\n')

    #         # Regular expression for XRP address
    #         xrp_address_pattern = r'r[1-9A-HJ-NP-Za-km-z]{25,34}'

    #         wallet_at_front_of_doc = None
    #         # look through the first 5 lines for an XRP address
    #         for line in lines[:5]:
    #             match = re.search(xrp_address_pattern, line)
    #             if match:
    #                 wallet_at_front_of_doc = match.group()
    #                 break

    #         if not wallet_at_front_of_doc:
    #             logger.warning(f"No XRP address found in the first 5 lines of the document")
    #             return balance

    #         account_info = self.get_account_info(wallet_at_front_of_doc)
    #         balance = Decimal(account_info['Balance'])

    #     except Exception as e:
    #         logger.error(f"Error: {e}")

    #     return balance

    # def given_input_map_cache_credentials_locally(self, input_map):
    #     """ EXAMPLE 
    #     input_map = {'Username_Input': 'goodalexander',
    #                 'Password_Input': 'everythingIsRigged1a',
    #                 'Google Doc Share Link_Input':'https://docs.google.com/document/d/1MwO8kHny7MtU0LgKsFTBqamfuUad0UXNet1wr59iRCA/edit',
    #                  'XRP Address_Input':'r3UHe45BzAVB3ENd21X9LeQngr4ofRJo5n',
    #                  'XRP Secret_Input': '<USER SEED ENTER HERE>'}
    #     """ 
        
    #     has_variables_defined = False
    #     zero_balance = True
    #     balance = self.check_if_there_is_funded_account_at_front_of_google_doc(google_url=input_map['Google Doc Share Link_Input'])
    #     logger.debug(f"balance: {balance}")

    #     if balance > 0:
    #         zero_balance = False
    #     existing_keys= list(output_cred_map().keys())
    #     if 'postfiatusername' in existing_keys:
    #         has_variables_defined = True
    #     output_string = ''
    #     if zero_balance == True:
    #         output_string=output_string+f"""XRP Wallet at Top of Google Doc {input_map['Google Doc Share Link_Input']} Has No Balance
    #         Fund Your XRP Wallet and Place at Top of Google Doc
    #         """
    #     if has_variables_defined == True:
    #         output_string=output_string+f""" 
    #     Variables are already defined in {CREDENTIAL_FILE_PATH}"""
    #     error_message = output_string.strip()

    #     print(f"error_message: {error_message}")

    #     if error_message == '':
    #         print("CACHING CREDENTIALS")
    #         key_to_input1= f'{input_map['Username_Input']}__v1xrpaddress'
    #         key_to_input2= f'{input_map['Username_Input']}__v1xrpsecret'
    #         key_to_input3='postfiatusername'
    #         key_to_input4 = f'{input_map['Username_Input']}__googledoc'
    #         enter_and_encrypt_credential__variable_based(credential_ref=key_to_input1, 
    #                                                      pw_data=input_map['XRP Address_Input'], 
    #                                                      pw_encryptor=input_map['Password_Input'])
    #         enter_and_encrypt_credential__variable_based(credential_ref=key_to_input2, 
    #                                                      pw_data=input_map['XRP Secret_Input'], 
    #                                                      pw_encryptor=input_map['Password_Input'])
            
    #         enter_and_encrypt_credential__variable_based(credential_ref=key_to_input3, 
    #                                                      pw_data=input_map['Username_Input'], 
    #                                                      pw_encryptor=input_map['Password_Input'])
    #         enter_and_encrypt_credential__variable_based(credential_ref=key_to_input4, 
    #                                                      pw_data=input_map['Google Doc Share Link_Input'], 
    #                                                      pw_encryptor=input_map['Password_Input'])
    #         error_message = f'Information Cached and Encrypted Locally Using Password at {CREDENTIAL_FILE_PATH}'

    #     return error_message

class PostFiatTaskManager:
    
    def __init__(self,username,password):
        self.credential_manager=CredentialManager(username,password)
        self.pw_map = self.credential_manager.decrypt_creds(self.credential_manager.pw_initiator)
        self.mainnet_url= "https://s2.ripple.com:51234"
        self.treasury_wallet_address = 'r46SUhCzyGE4KwBnKQ6LmDmJcECCqdKy4q'
        self.pft_issuer = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'
        self.trust_line_default = '100000000'
        self.user_wallet = self.spawn_user_wallet()
        
        # TODO: Find a use for this or delete
        # self.user_google_doc = self.pw_map[self.credential_manager.google_doc_name]

        self.tx_history_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_transaction_history.csv")

        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'

        self.transactions = pd.DataFrame()
        self.memos = pd.DataFrame()
        self.tasks = pd.DataFrame()

        self.sync_transactions()

        # for debugging purposes only
        # self.memos_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_memos.csv")
        # self.tasks_csv_filepath = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_tasks.csv")
        # self.save_memos_to_csv()
        # self.save_tasks_to_csv()

        # CHECKS
        # checks if the user has a trust line to the PFT token, and creates one if not
        self.handle_trust_line()

        # check if the user has sent a genesis to the node, and sends one if not
        self.handle_genesis()

        # TODO: Prompt user for google doc through the UI, not through the code
        # check if the user has sent a google doc to the node, and sends one if not
        # self.handle_google_doc()

    def get_xrp_balance(self):
        return get_xrp_balance(self.mainnet_url, self.user_wallet.classic_address)

    ## GENERIC UTILITY FUNCTIONS 

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

    def save_transactions_to_csv(self):
        self.save_dataframe_to_csv(self.transactions, self.tx_history_csv_filepath, "transactions")

    def save_memos_to_csv(self):
        self.save_dataframe_to_csv(self.memos, self.memos_csv_filepath, "memos")

    def save_tasks_to_csv(self):
        self.save_dataframe_to_csv(self.tasks, self.tasks_csv_filepath, "tasks")

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
    
    def get_new_transactions(self, last_known_ledger_index):
        """Retrieves new transactions from the node after the last known transaction date"""
        logger.debug(f"Getting new transactions after ledger index {last_known_ledger_index}")
        return self.get_account_transactions(
            account_address=self.user_wallet.classic_address,
            ledger_index_min=last_known_ledger_index,
            ledger_index_max=-1,
            limit=1000  # adjust as needed
        )

    def sync_transactions(self):
        """ Checks for new transactions and caches them locally. Also triggers memo update"""
        logger.debug("Updating transactions")

        # Attempt to load transactions from local csv
        if self.transactions.empty: 
            new_tx_df = self.load_transactions_from_csv()

        # Choose ledger index to start sync from
        if self.transactions.empty:
            last_known_ledger_index = -1
        else:   # otherwise, use the next index after last known ledger index from the transactions dataframe
            last_known_ledger_index = self.transactions['ledger_index'].max() + 1
            logger.debug(f"Last known ledger index: {last_known_ledger_index}")

        # fetch new transactions from the node
        new_tx_list = self.get_new_transactions(last_known_ledger_index)

        # Add new transactions to the dataframe
        if new_tx_list:
            logger.debug(f"Adding {len(new_tx_list)} new transactions...")
            new_tx_df = pd.DataFrame(new_tx_list)
            self.transactions = pd.concat([self.transactions, new_tx_df], ignore_index=True).drop_duplicates(subset=['hash'])
            self.save_transactions_to_csv()
            self.sync_memos(new_tx_df)
        else:
            logger.debug("No new transactions found. Finished updating local tx history")
        
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

        self.sync_tasks(new_memo_df)

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
    
    def send_xrp(self, amount, destination, memo=""):
        return send_xrp(self.mainnet_url, self.user_wallet, amount, destination, memo)

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
    ## BLOCKCHAIN FUNCTIONS

    def spawn_user_wallet(self):
        """ This takes the credential manager and loads the wallet from the
        stored seed associated with the user name"""
        seed = self.pw_map[self.credential_manager.wallet_secret_name]
        live_wallet = xrpl.wallet.Wallet.from_seed(seed)
        return live_wallet

    def handle_trust_line(self):
        handle_trust_line(self.mainnet_url, self.pft_issuer, self.user_wallet)

    def send_pft(self, amount, destination, memo=""):
        """ Sends PFT tokens to a destination address with optional memo. 
        If the memo is over 1 KB, it is split into multiple memos"""

        response = []

        # Check if the memo is a string and exceeds 1 KB
        if is_over_1kb(memo):
            logger.debug("Memo exceeds 1 KB, splitting into chunks")
            chunked_memo = self._build_chunked_memo(memo)

            # Split amount by number of chunks
            amount_per_chunk = amount / len(chunked_memo)

            # Send each chunk in a separate transaction
            for memo_chunk in chunked_memo:
                response.append(self._send_pft_single(amount_per_chunk, destination, memo_chunk))
        
        else:
            logger.debug("Memo is under 1 KB, sending in a single transaction")
            response.append(self._send_pft_single(amount, destination, memo))

        return response

    def _send_pft_single(self, amount, destination, memo):
        """Helper method to send a single PFT transaction"""
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)

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
        signed_tx = xrpl.transaction.sign(payment, self.user_wallet)
        tx_hash = signed_tx.get_hash()

        try:
            logger.debug("Submitting and waiting for transaction")
            response = xrpl.transaction.submit_and_wait(signed_tx, client, self.user_wallet)    
            # response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:
            response = f"Transaction submission failed: {e}"
            logger.error(response)
        except Exception as e:
            response = f"Unexpected error: {e}"
            logger.error(response)

        return tx_hash, response
    
    def _get_memo_chunks(self, memo):
        """Helper method to split a memo into chunks of 1 KB """

        # Function to split memo into chunks of specified size (1 KB here)
        def chunk_string(string, chunk_size):
            return [string[i:i + chunk_size] for i in range(0, len(string), chunk_size)]
        
        # Convert the memo to a hex string
        memo_hex = to_hex(memo)
        # Define the chunk size (1 KB in bytes, then converted to hex characters)
        chunk_size = 1024 * 2  # 1 KB in bytes is 1024, and each byte is 2 hex characters

        # Split the memo into chunks
        memo_chunks = chunk_string(memo_hex, chunk_size)

        return memo_chunks

    def _build_chunked_memo(self, memo):
        """ Helper method to build a list of Memo objects representing a single memo string split into chunks """
        memo_chunks = self._get_memo_chunks(memo)
        chunked_memo = []
        for index, chunk in enumerate(memo_chunks):
            chunked_memo.append(
                Memo(
                    memo_data=chunk, 
                    memo_type=to_hex(f'part_{index + 1}_of_{len(memo_chunks)}'), 
                    memo_format=to_hex('text/plain')
                )
            )
        return chunked_memo
    
## MEMO FORMATTING AND MEMO CREATION TOOLS
    def construct_basic_postfiat_memo(self, user, task_id, full_output):
        user_hex = to_hex(user)
        task_id_hex = to_hex(task_id)
        full_output_hex = to_hex(full_output)
        memo = Memo(
        memo_data=full_output_hex,
        memo_type=task_id_hex,
        memo_format=user_hex)  
        return memo
    
    def get_account_transactions__limited(self, account_address,
                                    ledger_index_min=-1,
                                    ledger_index_max=-1, 
                                    limit=10):
            client = xrpl.clients.JsonRpcClient(self.mainnet_url) # Using a public server; adjust as necessary
        
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
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
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

        client = xrpl.clients.JsonRpcClient(self.mainnet_url)  # Using a public server; adjust as necessary
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
    
    def retrieve_context_doc(self):
        """ This function gets the most recent google doc context link for a given account address """

        most_recent_context_link=''

        # Filter for memos that are PFT-related, sent to the default node, outgoing, and are google doc context links
        redux_tx_list = self.memos[
            self.memos['is_pft'] & 
            (self.memos['destination']==self.default_node) &
            (self.memos['message_type']=='OUTGOING') & 
            (self.memos['task_id']=='google_doc_context_link')
            ]
        
        if len(redux_tx_list) == 0:
            logger.warning("No Google Doc context link found")
            return None
        
        # Get the most recent google doc context link
        most_recent_context_link = redux_tx_list.tail(1)
        # Get the full output from the most recent google doc context link
        link = most_recent_context_link['memo_data'].apply(lambda x: x['full_output'])[0]

        return link
    
    def generate_google_doc_context_memo(self,user,google_doc_link):                  
        return construct_memo(user, 'google_doc_context_link', google_doc_link) 

    def output_account_address_node_association(self):
        """this takes the account info frame and figures out what nodes
         the account is associating with and returns them in a dataframe """
        self.memos['valid_task_id']=self.memos['memo_data'].apply(is_task_id)
        node_output_df = self.memos[self.memos['message_type']=='INCOMING'][['valid_task_id','account']].groupby('account').sum()
   
        return node_output_df[node_output_df['valid_task_id']>0]
    
    def get_user_genesis_destinations(self):
        """ Returns all the addresses that have received a user genesis transaction"""
        all_user_genesis_transactions = self.memos[self.memos['memo_data'].apply(lambda x: 'USER GENESIS __' in str(x))]
        all_user_genesis_destinations = list(all_user_genesis_transactions['destination'])
        return {'destinations': all_user_genesis_destinations, 'raw_details': all_user_genesis_transactions}
    
    def handle_genesis(self):
        """ Checks if the user has sent a genesis to the node, and sends one if not """
        if not self.genesis_sent():
            logger.debug("User has not sent genesis, sending...")
            self.send_genesis()
        else:
            logger.debug("User has already sent genesis, skipping...")

    def genesis_sent(self):
        logger.debug("Checking if user has sent genesis...")
        user_genesis = self.get_user_genesis_destinations()
        return self.default_node in user_genesis['destinations']
    
    def send_genesis(self):
        """ Sends a user genesis transaction to the default node 
        Currently requires 7 PFT
        """
        logger.debug("Initializing Node Genesis Transaction...")
        genesis_memo = self.construct_basic_postfiat_memo(
            user=self.credential_manager.postfiat_username,
            task_id=self.generate_custom_id(), 
            full_output=f'USER GENESIS __ user: {self.credential_manager.postfiat_username}'
            )
        self.send_pft(amount=7, destination=self.default_node, memo=genesis_memo)

    # def handle_google_doc(self):
    #     """Checks for google doc and prompts user to send if not found"""
    #     if not self.google_doc_sent():
    #         logger.debug("Google Doc not found.")
    #         self.send_google_doc()
    #     else:
    #         logger.debug("Google Doc already sent, skipping...")

    # def google_doc_sent(self):
    #     return self.default_node in self.retrieve_context_doc()
    
    # def send_google_doc(self, user_google_doc):
    #     """ Sends the Google Doc context link to the node """
    #     google_doc_memo = self.generate_google_doc_context_memo(user=self.credential_manager.postfiat_username,
    #                                                                 google_doc_link=user_google_doc)
    #     self.send_pft(amount=1, destination=self.default_node, memo=google_doc_memo)

    # def send_google_doc_to_node_if_not_sent(self, user_google_doc):
    #     """
    #     Sends the Google Doc context link to the node if it hasn't been sent already.
    #     """
    #     print("Checking if Google Doc context link has already been sent...")
        
    #     # Check if the Google Doc context link has been sent
    #     existing_link = self.retrieve_context_doc()
        
    #     if existing_link:
    #         print("Google Doc context link already sent:", existing_link)
    #     else:
    #         print("Google Doc context link not found. Sending now...")
    #         google_doc_link = user_google_doc
    #         user_name_to_send = self.credential_manager.postfiat_username
            
    #         # Construct the memo
    #         google_doc_memo = self.generate_google_doc_context_memo(user=user_name_to_send,
    #                                                                 google_doc_link=google_doc_link)
            
    #         # Send the memo to the default node
    #         self.send_pft(amount=1, destination=self.default_node, memo=google_doc_memo)
    #         print("Google Doc context link sent.")

    # def check_and_prompt_google_doc(self):
    #     """
    #     Checks if the Google Doc context link exists for the account on the chain.
    #     If it doesn't exist, prompts the user to enter the Google Doc string and sends it.
    #     """
    #     # Get memo details for the user's account
        

    #     # Check if the Google Doc context link exists
    #     existing_link = self.retrieve_context_doc()

    #     if existing_link:
    #         print("Google Doc context link already exists:", existing_link)
    #     else:
    #         # Prompt the user to enter the Google Doc string
    #         user_google_doc = input("Enter the Google Doc string: ")
            
    #         # Send the Google Doc context link to the default node
    #         self.send_google_doc_to_node_if_not_sent(user_google_doc = user_google_doc)


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
        pivoted_df.rename(columns={'PROPOSAL':'proposal', 'RESPONSE':'response'}, inplace=True)

        # Clean up the proposal and response columns
        pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace('ACCEPTANCE REASON ___ ','ACCEPTED: ').replace('nan',''))
        pivoted_df['response'] = pivoted_df['response'].apply(lambda x: str(x).replace('REFUSAL REASON ___ ','REFUSED: ').replace('nan',''))
        pivoted_df['proposal'] = pivoted_df['proposal'].apply(lambda x: str(x).replace('PROPOSED PF ___ ','').replace('nan',''))

        # Reverse order to get the most recent proposals first
        result_df = pivoted_df.iloc[::-1].reset_index(drop=True).copy()

        return result_df
    
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

        # Remove rows where verification is "nan"
        # result_df = result_df[result_df['verification'] != 'nan'].reset_index(drop=True)

        return result_df
    
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

        # debugging
        filtered_df.to_csv('filtered_df.csv', index=False)

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
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                    task_id=task_id, full_output=classified_string)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_acceptance_for_task_id response: {response}")
        # account = response.result['tx_json']['Account']
        # destination = response.result['tx_json']['Destination']
        # memo_map = response.result['tx_json']['Memos'][0]['Memo']
        # logger.debug(f"{account} sent 1 PFT to {destination} with memo {memo_map}")
        return response

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
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, full_output=refusal_reason)
        response = self.send_pft(amount=1, destination=node_account, memo=constructed_memo)
        logger.debug(f"send_refusal_for_task response: {response}")
        # account = response.result['tx_json']['Account']
        # destination = response.result['tx_json']['Destination']
        # memo_map = response.result['tx_json']['Memos'][0]['Memo']
        # logger.info(f"{account} sent 1 PFT to {destination} with memo {memo_map}")
        return response

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
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, 
                                                               full_output=classified_request_msg)
        # Send the memo to the default node
        response = self.send_pft(amount=1, destination=self.default_node, memo=constructed_memo)
        logger.debug(f"request_post_fiat response: {response}")
        # account = response.result['tx_json']['Account']
        # destination = response.result['tx_json']['Destination']
        # memo_map = response.result['tx_json']['Memos'][0]['Memo']
        # logger.info(f"{account} sent 1 PFT to {destination} with memo {memo_map}")
        return response

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
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                              task_id=task_id, 
                                                              full_output=classified_completion_str)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"submit_initial_completion Response: {response}")
        # account = response.result['tx_json']['Account']
        # destination = response.result['tx_json']['Destination']
        # memo_map = response.result['tx_json']['Memos'][0]['Memo']
        # logger.debug(f"{account} sent 1 PFT to {destination} with memo {memo_map}")
        return response
        
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
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                              task_id=task_id, 
                                                              full_output=classified_response_str)
        response = self.send_pft(amount=1, destination=proposal_source, memo=constructed_memo)
        logger.debug(f"send_verification_response Response: {response}")
        # account = response.result['Account']
        # destination = response.result['Destination']
        # memo_map = response.result['Memos'][0]['Memo']
        # print(f"{account} sent 1 PFT to {destination} with memo")
        # print(self.convert_memo_dict(memo_map))
        return response

    ## WALLET UX POPULATION 
    def ux__1_get_user_pft_balance(self):
        """Returns the balance of PFT for the user."""
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
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

    def process_account_info(self):
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

    def send_pomodoro_for_task_id(self,task_id = '2024-05-19_10:27__LL78',pomodoro_text= 'spent last 30 mins doing a ton of UX debugging'):
        pomodoro_id = task_id.replace('__','==')
        memo_to_send = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username,
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

def construct_memo(user, memo_type, memo_data):
    return Memo(
        memo_data=to_hex(memo_data),
        memo_type=to_hex(memo_type),
        memo_format=to_hex(user)
    )

def get_xrp_balance(mainnet_url, address):
    client = xrpl.clients.JsonRpcClient(mainnet_url)
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

def send_xrp(mainnet_url, wallet: xrpl.wallet.Wallet, amount, destination, memo=""):
    client = xrpl.clients.JsonRpcClient(mainnet_url)
    payment = xrpl.models.transactions.Payment(
        account=wallet.address,
        amount=xrpl.utils.xrp_to_drops(Decimal(amount)),
        destination=destination,
        memos=[Memo(memo_data=str_to_hex(memo))] if memo else None,
    )
    # Sign the transaction to get the hash
    # We need to derive the hash because the submit_and_wait function doesn't return a hash if transaction fails
    signed_tx = xrpl.transaction.sign(payment, wallet)
    tx_hash = signed_tx.get_hash()

    try:    
        response = xrpl.transaction.submit_and_wait(payment, client, wallet)    
    except xrpl.transaction.XRPLReliableSubmissionException as e:
        response = f"Transaction submission failed: {e}"
        logger.error(response)
    except Exception as e:
        response = f"Unexpected error: {e}"
        logger.error(response)

    return tx_hash, response

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
            'REQUEST_POST_FIAT ':['REQUEST_POST_FIAT ___']
        }

    for category, keywords in categories.items():
        if any(keyword in string for keyword in keywords):
            return category

    return 'UNKNOWN'

def is_pft_transaction(tx) -> bool:
    deliver_max = tx.get('DeliverMax', {})
    return isinstance(deliver_max, dict) and deliver_max.get('currency') == 'PFT'

def get_pft_holder_df(mainnet_url, pft_issuer):
    """ This function outputs a detail of all accounts holding PFT tokens
    with a float of their balances as pft_holdings. note this is from
    the view of the issuer account so balances appear negative so the pft_holdings 
    are reverse signed.
    """
    client = xrpl.clients.JsonRpcClient(mainnet_url)
    logger.debug("Getting dataframe of all accounts holding PFT tokens...")
    response = client.request(xrpl.models.requests.AccountLines(
        account=pft_issuer,
        ledger_index="validated",
        peer=None,
        limit=None))
    full_post_fiat_holder_df = pd.DataFrame(response.result)
    for xfield in ['account','balance','currency','limit_peer']:
        full_post_fiat_holder_df[xfield] = full_post_fiat_holder_df['lines'].apply(lambda x: x[xfield])
    full_post_fiat_holder_df['pft_holdings']=full_post_fiat_holder_df['balance'].astype(float)*-1
    return full_post_fiat_holder_df
    
def has_trust_line(mainnet_url, pft_issuer, wallet):
    """ This function checks if the user has a trust line to the PFT token"""
    pft_holders = get_pft_holder_df(mainnet_url, pft_issuer)
    existing_pft_accounts = list(pft_holders['account'])
    user_is_in_pft_accounts = wallet.address in existing_pft_accounts
    return user_is_in_pft_accounts

def handle_trust_line(mainnet_url, pft_issuer, wallet):
    """ This function checks if the user has a trust line to the PFT token
    and if not establishes one"""
    logger.debug("Checking if trust line exists...")
    if not has_trust_line(mainnet_url, pft_issuer, wallet):
        _ = generate_trust_line_to_pft_token(mainnet_url, wallet)
        logger.debug("Trust line created")
    else:
        logger.debug("Trust line already exists")

def generate_trust_line_to_pft_token(mainnet_url, wallet: xrpl.wallet.Wallet):
    """ Note this transaction consumes XRP to create a trust
    line for the PFT Token so the holder DF should be checked 
    before this is run
    """ 
    client = xrpl.clients.JsonRpcClient(mainnet_url)
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