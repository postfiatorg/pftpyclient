from pftpyclient.user_login.credential_input import CredentialManager
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
import logging
import time
import json
import ast
from decimal import Decimal

nest_asyncio.apply()

from pathlib import Path

class WalletInitiationFunctions:
    def __init__(self):
        print('no vars')
        self.mainnet_url="https://s2.ripple.com:51234"
        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'

    def to_hex(self,string):
        return binascii.hexlify(string.encode()).decode()

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

    def send_xrp_with_info(self,wallet_seed, amount, destination, memo):
        sending_wallet = xrpl.wallet.Wallet.from_seed(wallet_seed)
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        payment = xrpl.models.transactions.Payment(
            account=sending_wallet.address,
            amount=xrpl.utils.xrp_to_drops(int(amount)),
            destination=destination,
            memos=[memo],
        )
        try:    
            response = xrpl.transaction.submit_and_wait(payment, client, sending_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:    
            response = f"Submit failed: {e}"
    
        return response

    def generate_initiation_rite_context_memo(self,user='goodalexander',
                                         user_response=
                                         'I commit to generating massive trading profits using AI and investing them to grow the Post Fiat Network'):
        """  Please write 1 sentence committing to a long term objective of your choosing.
        This will be logged publically and immutably and sent with 1 XRP to receive an initial Post Fiat (PFT) grant """
                                                 
        user_hex = self.to_hex(user)
        task_id_hex = self.to_hex('INITIATION_RITE')
        full_output_hex = self.to_hex(user_response)

        memo = Memo(
        memo_data=full_output_hex,
        memo_type=task_id_hex,
        memo_format=user_hex) 
        return memo

    def send_initiation_rite(self, wallet_seed, user='goodalexander', 
        user_response='I commit to generating massive trading profits using AI and investing them to grow the Post Fiat Network'):
        memo_to_send = self.generate_initiation_rite_context_memo(user=user, user_response=user_response)
        self.send_xrp_with_info(wallet_seed=wallet_seed, amount=1, destination=self.default_node, memo=memo_to_send)
        self.generate_trust_line_to_pft_token(wallet_seed=wallet_seed)

    def get_account_info(self, accountId):
        """get_account_info"""
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        acct_info = xrpl.models.requests.account_info.AccountInfo(
            account=accountId,
            ledger_index="validated"
        )
        response = client.request(acct_info)
        return response.result['account_data']

    def check_if_there_is_funded_account_at_front_of_google_doc(self, google_url):
        """
        Checks if there is a balance bearing XRP account address at the front of the google document 
        This is required for the user 

        Returns the balance in XRP drops 
        EXAMPLE
        google_url = 'https://docs.google.com/document/d/1MwO8kHny7MtU0LgKsFTBqamfuUad0UXNet1wr59iRCA/edit'
        """
        balance = 0
        try:
            wallet_at_front_of_doc =self.get_google_doc_text(google_url).split('\ufeff')[-1:][0][0:34]
            balance = float(self.get_account_info(wallet_at_front_of_doc)['Balance'])
        except:
            pass
        return balance

    def clear_credential_file(self):
        # Define the path to the file
        file_path = CREDENTIAL_FILE_PATH
        
        # Clear the contents of the file
        file_path.write_text('')

    def given_input_map_cache_credentials_locally(self, input_map):
        """ EXAMPLE INPUT MAP
        input_map = {'Username_Input': 'goodalexander',
                    'Password_Input': 'everythingIsRigged1a',
                    'Google Doc Share Link_Input':'https://docs.google.com/document/d/1MwO8kHny7MtU0LgKsFTBqamfuUad0UXNet1wr59iRCA/edit',
                     'XRP Address_Input':'r3UHe45BzAVB3ENd21X9LeQngr4ofRJo5n',
                     'XRP Secret_Input': '<USER SEED ENTER HERE>'}

        Note the output is returned as the error_message. If everything went well it will say the information was cached 
        """ 
        
        has_variables_defined = False
        zero_balance = True
        balance = self.check_if_there_is_funded_account_at_front_of_google_doc(google_url=input_map['Google Doc Share Link_Input'])
        if balance > 0:
            zero_balance = False
        existing_keys= list(output_cred_map().keys())
        if 'postfiatusername' in existing_keys:
            has_variables_defined = True
        output_string = ''
        if zero_balance == True:
            output_string=output_string+f"""XRP Wallet at Top of Google Doc {input_map['Google Doc Share Link_Input']} Has No Balance
            Fund Your XRP Wallet and Place at Top of Google Doc
            """
        if has_variables_defined == True:
            output_string=output_string+f""" 
        Variables are already defined in {CREDENTIAL_FILE_PATH}"""
        error_message = output_string.strip()
        if error_message == '':
            print("CACHING CREDENTIALS")
            key_to_input1= f'{input_map['Username_Input']}__v1xrpaddress'
            key_to_input2= f'{input_map['Username_Input']}__v1xrpsecret'
            key_to_input3='postfiatusername'
            key_to_input4 = f'{input_map['Username_Input']}__googledoc'
            enter_and_encrypt_credential__variable_based(credential_ref=key_to_input1, 
                                                         pw_data=input_map['XRP Address_Input'], 
                                                         pw_encryptor=input_map['Password_Input'])
            enter_and_encrypt_credential__variable_based(credential_ref=key_to_input2, 
                                                         pw_data=input_map['XRP Secret_Input'], 
                                                         pw_encryptor=input_map['Password_Input'])
            
            enter_and_encrypt_credential__variable_based(credential_ref=key_to_input3, 
                                                         pw_data=input_map['Username_Input'], 
                                                         pw_encryptor=input_map['Password_Input'])
            enter_and_encrypt_credential__variable_based(credential_ref=key_to_input4, 
                                                         pw_data=input_map['Google Doc Share Link_Input'], 
                                                         pw_encryptor=input_map['Password_Input'])
            error_message = f'Information Cached and Encrypted Locally Using Password at {CREDENTIAL_FILE_PATH}'

        return error_message

    def generate_trust_line_to_pft_token(self, wallet_seed):
        """ Note this transaction consumes XRP to create a trust
        line for the PFT Token so the holder DF should be checked 
        before this is run
        """ 
        
        #wallet_to_link =self.user_wallet
        wallet_to_link = xrpl.wallet.Wallet.from_seed(wallet_seed)
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        #currency_code = "PFT"
        trust_set_tx = xrpl.models.transactions.TrustSet(
                        account=wallet_to_link.classic_address,
                    limit_amount=xrpl.models.amounts.issued_currency_amount.IssuedCurrencyAmount(
                            currency="PFT",
                            issuer='rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW',
                            value='100000000',  # Large limit, arbitrarily chosen
                        )
                    )
        print("Creating trust line from chosen seed to issuer...")
        
        response = xrpl.transaction.submit_and_wait(trust_set_tx, client, wallet_to_link)
        return response


class PostFiatTaskManager:
    def __init__(self,username,password):
        self.credential_manager=CredentialManager(username=username,password=password)
        self.pw_map = self.credential_manager.output_fully_decrypted_cred_map(pw_decryptor=
                                                   self.credential_manager.pw_initiator)
        self.mainnet_url= "https://s2.ripple.com:51234"
        self.treasury_wallet_address = 'r46SUhCzyGE4KwBnKQ6LmDmJcECCqdKy4q'
        self.pft_issuer = 'rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW'
        self.trust_line_default = '100000000'
        self.user_wallet = self.spawn_user_wallet()
        self.establish_trust_line_if_needed()
        self.user_google_doc = self.pw_map[self.credential_manager.google_doc_name]
        self.csv_file_path = os.path.join(DATADUMP_DIRECTORY_PATH, f"{self.user_wallet.classic_address}_transaction_history.csv")

        self.default_node = 'r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD'

        self.transactions = pd.DataFrame()
        self.update_transactions()
        
        # to-do: see if all_account_info needs to be initialized in this way
        all_account_info = self.get_memo_detail_df_for_account()

        # this initializes the user to the node 
        self.send_genesis_to_default_node_if_not_sent(all_account_info=all_account_info)
        # and also sends the node the google doc for the user 
        self.check_and_prompt_google_doc(all_account_info=all_account_info)

    def get_xrp_balance(self):
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        account_info = xrpl.models.requests.account_info.AccountInfo(
            account=self.user_wallet.classic_address,
            ledger_index="validated"
        )
        response = client.request(account_info)
        return response.result['account_data']['Balance']

    ## GENERIC UTILITY FUNCTIONS 

    def save_transactions_to_csv(self):
        self.transactions.to_csv(self.csv_file_path, index=False)
        logging.debug(f"Saved {len(self.transactions)} transactions to {self.csv_file_path}")

    def load_transactions_from_csv(self):
        """ Loads the transactions from the CSV file into a dataframe, and deserializes some columns"""
        tx_df = None
        if os.path.exists(self.csv_file_path):
            logging.debug(f"Loading transactions from {self.csv_file_path}")
            try:
                tx_df = pd.read_csv(self.csv_file_path)

                # deserialize columns
                for col in ['meta', 'tx_json']:
                    if col in tx_df.columns:
                        tx_df[col] = tx_df[col].apply(lambda x: ast.literal_eval(x) if pd.notna(x) else x)

            except Exception as e:
                logging.error(f"Error loading transactions from {self.csv_file_path}: {e}")
                os.remove(self.csv_file_path) # delete the file, it's corrupt or empty
                return pd.DataFrame()
            else:
                return tx_df

        logging.debug(f"No existing transaction history file found at {self.csv_file_path}")
        return pd.DataFrame() # empty dataframe if file does not exist

    def get_new_transactions(self, last_known_ledger_index):
        """Retrieves new transactions from the node after the last known transaction date"""
        logging.debug(f"Getting new transactions after ledger index {last_known_ledger_index}")
        return self.get_account_transactions(
            account_address=self.user_wallet.classic_address,
            ledger_index_min=last_known_ledger_index,
            ledger_index_max=-1,
            limit=1000  # adjust as needed
        )

    def update_transactions(self):
        """ Checks for new transactions and caches them locally """
        logging.debug("Updating transactions")
        
        # if the transactions dataframe is empty, attempt to load from local csv
        if self.transactions.empty: 
            self.transactions = self.load_transactions_from_csv()

        # if the transactions dataframe is still empty, use ledger index of -1 to fetch all transactions
        if self.transactions.empty:
            last_known_ledger_index = -1

        # otherwise, use the last known ledger index from the transactions dataframe
        else:
            last_known_ledger_index = self.transactions['ledger_index'].max()
            logging.debug(f"Last known ledger index: {last_known_ledger_index}")

        # fetch new transactions from the node
        new_tx_list = self.get_new_transactions(last_known_ledger_index)

        # # DEBUGGING
        # logging.debug(f"New transactions: {new_tx_list}")

        if new_tx_list:
            logging.debug(f"Adding {len(new_tx_list)} new transactions...")
            self.transactions = pd.concat([self.transactions, pd.DataFrame(new_tx_list)], ignore_index=True).drop_duplicates(subset=['hash'])
            self.save_transactions_to_csv()
            logging.debug("Added new transactions")
        else:
            logging.debug("No new transactions found. Finished updating local tx history")


    def to_hex(self,string):
        return binascii.hexlify(string.encode()).decode()

    def convert_ripple_timestamp_to_datetime(self, ripple_timestamp = 768602652):
        ripple_epoch_offset = 946684800  # January 1, 2000 (00:00 UTC)
        
        
        unix_timestamp = ripple_timestamp + ripple_epoch_offset
        date_object = datetime.datetime.fromtimestamp(unix_timestamp)
        return date_object


    
    def hex_to_text(self,hex_string):
        bytes_object = bytes.fromhex(hex_string)
        ascii_string = bytes_object.decode("utf-8")
        return ascii_string
    
    def check_if_tx_pft(self,tx):
        ret= False
        try:
            if tx['DeliverMax']['currency'] == "PFT":
                ret = True
        except:
            pass
        return ret
    
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
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        payment = xrpl.models.transactions.Payment(
            account=self.user_wallet.address,
            amount=xrpl.utils.xrp_to_drops(Decimal(amount)),
            destination=destination,
            memos=[Memo(memo_data=str_to_hex(memo))] if memo else None,
        )

        try:    
            response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:    
            response = f"Submit failed: {e}"
    
        return response

    def classify_task_string(self,string):
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

       


    def determine_if_map_is_task_id(self,memo_dict):
        """ Note that technically only the task ID recognition is needed
        at a later date might want to implement forced user and output delineators 
        if someone spams the system with task IDs
        """
        full_memo_string = str(memo_dict)
        task_id_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}(?:__[A-Z0-9]{4})?)')
        has_task_id = False
        if re.search(task_id_pattern, full_memo_string):
            return True
        has_user_identified = 'user:' in full_memo_string
        has_full_output_identified = 'full_output:' in full_memo_string
        if (has_user_identified) and (has_full_output_identified) and has_task_id:
            return True
        return False

    def convert_memo_dict(self, memo_dict):
        """Constructs a memo object with user, task_id, and full_output from hex-encoded values."""
        user= ''
        task_id=''
        full_output=''
        try:
            user = self.hex_to_text(memo_dict['MemoFormat'])
        except:
            pass
        try:
            task_id = self.hex_to_text(memo_dict['MemoType'])
        except:
            pass
        try:
            full_output = self.hex_to_text(memo_dict['MemoData'])
        except:
            pass
        
        return {
            'user': user,
            'task_id': task_id,
            'full_output': full_output
        }
    ## BLOCKCHAIN FUNCTIONS

    def spawn_user_wallet(self):
        """ This takes the credential manager and loads the wallet from the
        stored seed associated with the user name"""
        seed = self.pw_map[self.credential_manager.wallet_secret_name]
        live_wallet = xrpl.wallet.Wallet.from_seed(seed)
        return live_wallet
    
    def generate_trust_line_to_pft_token(self):
        """ Note this transaction consumes XRP to create a trust
        line for the PFT Token so the holder DF should be checked 
        before this is run
        """ 
        
        wallet_to_link =self.user_wallet
        
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        #currency_code = "PFT"
        trust_set_tx = xrpl.models.transactions.TrustSet(
                        account=wallet_to_link.classic_address,
                    limit_amount=xrpl.models.amounts.issued_currency_amount.IssuedCurrencyAmount(
                            currency="PFT",
                            issuer=self.pft_issuer,
                            value=self.trust_line_default,  # Large limit, arbitrarily chosen
                        )
                    )
        print("Creating trust line from chosen seed to issuer...")
        
        response = xrpl.transaction.submit_and_wait(trust_set_tx, client, wallet_to_link)
        return response
    
    def output_post_fiat_holder_df(self):
        """ This function outputs a detail of all accounts holding PFT tokens
        with a float of their balances as pft_holdings. note this is from
        the view of the issuer account so balances appear negative so the pft_holdings 
        are reverse signed.
        """
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)
        print("Getting all accounts holding PFT tokens...")
        response = client.request(xrpl.models.requests.AccountLines(
            account=self.pft_issuer,
            ledger_index="validated",
            peer=None,
            limit=None))
        full_post_fiat_holder_df = pd.DataFrame(response.result)
        for xfield in ['account','balance','currency','limit_peer']:
            full_post_fiat_holder_df[xfield] = full_post_fiat_holder_df['lines'].apply(lambda x: x[xfield])
        full_post_fiat_holder_df['pft_holdings']=full_post_fiat_holder_df['balance'].astype(float)*-1
        return full_post_fiat_holder_df
    
    def check_if_user_has_trust_line(self):
        """ This function checks if the user has a trust line to the PFT token"""
        pft_holders = self.output_post_fiat_holder_df()
        existing_pft_accounts = list(pft_holders['account'])
        user_is_in_pft_accounts = self.user_wallet.classic_address in existing_pft_accounts
        return user_is_in_pft_accounts

    def establish_trust_line_if_needed(self):
        """ This function checks if the user has a trust line to the PFT token
        and if not establishes one"""
        print("Checking if trust line exists...")
        if not self.check_if_user_has_trust_line():
            self.generate_trust_line_to_pft_token()
            print("Trust line created")
        else:
            print("Trust line already exists")

    def send_pft(self, amount, destination, memo="", batch=False):
        """ Sends PFT tokens to a destination address with optional memo """
        client = xrpl.clients.JsonRpcClient(self.mainnet_url)

        # if memo is not empty and batch is true, memo is already pre-converted to hex and doesn't need to be converted again
        memo_data = memo if (memo and batch) else str_to_hex(memo)

        amount_to_send = xrpl.models.amounts.IssuedCurrencyAmount(
            currency="PFT",
            issuer=self.pft_issuer,
            value=str(amount)
        )

        payment = xrpl.models.transactions.Payment(
            account=self.user_wallet.address,
            amount=amount_to_send,
            destination=destination,
            memos=[Memo(memo_data=memo_data)] if memo else None,
        )

        try:    
            response = xrpl.transaction.submit_and_wait(payment, client, self.user_wallet)    
        except xrpl.transaction.XRPLReliableSubmissionException as e:    
            response = f"Submit failed: {e}"
    
        return response

    def send_PFT_with_info_batch(self, amount, destination, memo):
        """ 
        Sends PFT tokens to a destination address with memo information split into multiple batches.
        The memo is split into chunks that fit within the 1 KB limit.
        """
        # Function to split memo into chunks of specified size (1 KB here)
        def chunk_string(string, chunk_size):
            return [string[i:i + chunk_size] for i in range(0, len(string), chunk_size)]

        # Convert the memo to a hex string
        memo_hex = self.to_hex(memo)
        # Define the chunk size (1 KB in bytes, then converted to hex characters)
        chunk_size = 1024 * 2  # 1 KB in bytes is 1024, and each byte is 2 hex characters

        # Split the memo into chunks
        memo_chunks = chunk_string(memo_hex, chunk_size)

        # Send each chunk in a separate transaction
        for index, chunk in enumerate(memo_chunks):
            memo_obj = Memo(
                memo_data=chunk,
                memo_type=self.to_hex(f'part_{index + 1}_of_{len(memo_chunks)}'),
                memo_format=self.to_hex('text/plain')
            )
            
            self.send_pft(amount, destination, memo_obj, batch=True)
    
## MEMO FORMATTING AND MEMO CREATION TOOLS
    def construct_basic_postfiat_memo(self, user, task_id, full_output):
        user_hex = self.to_hex(user)
        task_id_hex = self.to_hex(task_id)
        full_output_hex = self.to_hex(full_output)
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
        logging.debug(f"Getting transactions for account {account_address} with ledger index min {ledger_index_min} and max {ledger_index_max} and limit {limit}")
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
            logging.debug(f"Iteration {iteration_count}")
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
                logging.error(f"Request is not serializable: {e}")
                logging.error(f"Problematic request data: {request_dict}")
                break # stop if request is not serializable

            try:
                response = client.request(request)
                if response.is_successful():
                    transactions = response.result.get("transactions", [])
                    logging.debug(f"Retrieved {len(transactions)} transactions")
                    all_transactions.extend(transactions)
                else:
                    logging.error(f"Error in XRPL response: {response.status}")
                    break
            except Exception as e:
                logging.error(f"Error making XRPL request: {e}")
                break

            if "marker" in response.result:
                if response.result["marker"] == previous_marker:
                    logging.warning("Marker not advancing, stopping iteration")
                    break # stop if marker not advancing
                previous_marker = marker
                marker = response.result["marker"] # Update marker for next iteration
                logging.debug("More transactions available. Fetching next batch...")
            else:
                logging.debug("No more transactions available")
                break
        
        if iteration_count == max_iterations:
            logging.warning("Reached maximum iteration count. Stopping loop...")

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
        
    def get_memo_detail_df_for_account(self):
        """ This function gets all the memo details for a given account """
        logging.debug(f"Getting memo details for account {self.user_wallet.classic_address}")

        validated_tx = pd.DataFrame(self.transactions)

        validated_tx['has_memos']=validated_tx['tx_json'].apply(lambda x: 'Memos' in x)

        # #DEBUGGING
        # validated_tx.to_csv(os.path.join(DATADUMP_DIRECTORY_PATH, f"validated_tx.csv"))

        live_memo_tx = validated_tx[validated_tx['has_memos']== True].copy()

        live_memo_tx['main_memo_data']=live_memo_tx['tx_json'].apply(lambda x: x['Memos'][0]['Memo'])
        live_memo_tx['converted_memos']=live_memo_tx['main_memo_data'].apply(lambda x: 
                                                                             self.convert_memo_dict(x))
        live_memo_tx['account']= live_memo_tx['tx_json'].apply(lambda x: x['Account'])
        live_memo_tx['destination']=live_memo_tx['tx_json'].apply(lambda x: x['Destination'])
        
        live_memo_tx['message_type']=np.where(live_memo_tx['destination']==self.user_wallet.classic_address, 'INCOMING','OUTGOING')
        live_memo_tx['node_account']= live_memo_tx[['destination','account']].sum(1).apply(lambda x: 
                                                         str(x).replace(self.user_wallet.classic_address,''))
        live_memo_tx['datetime']= live_memo_tx['tx_json'].apply(lambda x: self.convert_ripple_timestamp_to_datetime(x['date']))
        live_memo_tx['ledger_index'] = live_memo_tx['tx_json'].apply(lambda x: x['ledger_index'])

        # #DEBUGGING
        # live_memo_tx.to_csv(os.path.join(DATADUMP_DIRECTORY_PATH, f"live_memo_tx.csv"))
        return live_memo_tx
    
    def get_post_fiat_context_doc_for_address(self,all_account_info):
        """ This function gets the most recent google doc context link for a given account address """

        all_account_info['is_post_fiat']=all_account_info['tx_json'].apply(lambda x: self.check_if_tx_pft(x))

        # Filter for transactions that are PFT and sent to the default node
        redux_tx_list = all_account_info[(all_account_info['is_post_fiat']== True)&
                                        (all_account_info['destination']==self.default_node)].copy()

        outgoing_messages_only= redux_tx_list[redux_tx_list['message_type']=='OUTGOING'].copy()

        most_recent_context_link=''
        most_recent_context_link = outgoing_messages_only[outgoing_messages_only['converted_memos'].apply(lambda x: x['task_id']) 
        == 'google_doc_context_link'].tail(1)
        link= ''

        if len(most_recent_context_link) >0:
            link = list(most_recent_context_link['converted_memos'])[0]['full_output']
            
        if len(most_recent_context_link) == 0:
            print("NO GOOGLE DOC CONTEXT")
        return link
    
    def generate_google_doc_context_memo(self,user,google_doc_link):
                                                 
        user_hex = self.to_hex(user)
        task_id_hex = self.to_hex('google_doc_context_link')
        full_output_hex = self.to_hex(google_doc_link)
        memo = Memo(
        memo_data=full_output_hex,
        memo_type=task_id_hex,
        memo_format=user_hex)  
        return memo
        #self.wallet_google_doc_map[account_address]=most_recent_context_link

    #user_memos= self.get_memo_detail_df_for_account(transaction_limit=5000)
    def output_account_address_node_association(self, all_account_info):
        """this takes the account info frame and figures out what nodes
         the account is associating with and returns them in a dataframe """
        all_account_info['valid_task_id']=all_account_info['converted_memos'].apply(lambda x:self.determine_if_map_is_task_id(x))
        node_output_df = all_account_info[all_account_info['message_type']=='INCOMING'][['valid_task_id',
                                                            'account']].groupby('account').sum()
        return node_output_df[node_output_df['valid_task_id']>0]
    
    def get_user_genesis_destinations(self, all_account_info):
        """ Returns all the addresses that have received a user genesis transaction"""
        all_user_genesis_transactions = all_account_info[all_account_info['converted_memos'].apply(lambda x: 
                                                                'USER GENESIS __' in str(x))]
        all_user_genesis_destinations = list(all_user_genesis_transactions['destination'])
        return {'destinations': all_user_genesis_destinations,
                'raw_details': all_user_genesis_transactions}
    
    def send_user_genesis_to_default_node(self):
        """ this takes the user default node and sends a user genesis transaction to it
        
        note should check that the user genesis transaction has not already been sent
        """
        print("Initializing Node Genesis Transaction...")
        print("minimum 7 PFT tokens required for user genesis")
        task_id = self.generate_custom_id()
        user_name_to_send = self.credential_manager.postfiat_username

        full_output = f'USER GENESIS __ user: {user_name_to_send}'
        
        genesis_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username,
                                        task_id=task_id, 
                                        full_output=full_output)
        self.send_pft(amount=7, destination=self.default_node, memo=genesis_memo)

    def send_genesis_to_default_node_if_not_sent(self, all_account_info):
        """ Pulls in the memo details and sends the genesis to the default node if 
        it has not already been sent"""
        user_genesis = self.get_user_genesis_destinations(all_account_info=all_account_info)
        if self.default_node in user_genesis['destinations']:
            print("User Genesis already sent to default node")
        if self.default_node not in user_genesis['destinations']:
            self.send_user_genesis_to_default_node()

    def send_google_doc_to_node_if_not_sent(self, all_account_info, user_google_doc):
        """
        Sends the Google Doc context link to the node if it hasn't been sent already.
        """
        print("Checking if Google Doc context link has already been sent...")
        
        # Check if the Google Doc context link has been sent
        existing_link = self.get_post_fiat_context_doc_for_address(all_account_info)
        
        if existing_link:
            print("Google Doc context link already sent:", existing_link)
        else:
            print("Google Doc context link not found. Sending now...")
            google_doc_link = user_google_doc
            user_name_to_send = self.credential_manager.postfiat_username
            
            # Construct the memo
            google_doc_memo = self.generate_google_doc_context_memo(user=user_name_to_send,
                                                                    google_doc_link=google_doc_link)
            
            # Send the memo to the default node
            self.send_pft(amount=1, destination=self.default_node, memo=google_doc_memo)
            print("Google Doc context link sent.")

    def check_and_prompt_google_doc(self, all_account_info):
        """
        Checks if the Google Doc context link exists for the account on the chain.
        If it doesn't exist, prompts the user to enter the Google Doc string and sends it.
        """
        # Get memo details for the user's account
        

        # Check if the Google Doc context link exists
        existing_link = self.get_post_fiat_context_doc_for_address(all_account_info)

        if existing_link:
            print("Google Doc context link already exists:", existing_link)
        else:
            # Prompt the user to enter the Google Doc string
            user_google_doc = input("Enter the Google Doc string: ")
            
            # Send the Google Doc context link to the default node
            self.send_google_doc_to_node_if_not_sent(all_account_info =all_account_info, user_google_doc = user_google_doc)



    def convert_all_account_info_into_simplified_task_frame(self, all_account_info):
        """ This takes all the Post Fiat Tasks and outputs them into a simplified
        dataframe of task information with embedded classifications 
        
        Runs on all_account_info generated by
        all_account_info =self.get_memo_detail_df_for_account()
        """ 

        simplified_task_frame = all_account_info[all_account_info['converted_memos'].apply(lambda x: 
                                                                self.determine_if_map_is_task_id(x))].copy()
        simplified_task_frame = simplified_task_frame[simplified_task_frame['tx_json'].apply(lambda 
                                                                                        x: x['DeliverMax']).apply(lambda x: 
                                                                                                                    "'currency': 'PFT'" in str(x))].copy()
        def add_field_to_map(xmap, field, field_value):
            xmap[field] = field_value
            return xmap
        
        simplified_task_frame['pft_abs']= simplified_task_frame['tx_json'].apply(lambda x: x['DeliverMax']['value']).astype(float)
        simplified_task_frame['directional_pft']=simplified_task_frame['message_type'].map({'INCOMING':1,
            'OUTGOING':-1}) * simplified_task_frame['pft_abs']
        
        for xfield in ['hash','node_account','datetime']:
            simplified_task_frame['converted_memos'] = simplified_task_frame.apply(lambda x: add_field_to_map(x['converted_memos'],
                xfield,x[xfield]),1)
            
        core_task_df = pd.DataFrame(list(simplified_task_frame['converted_memos'])).copy()
        core_task_df['task_type']=core_task_df['full_output'].apply(lambda x: self.classify_task_string(x))
        

        return core_task_df


    def convert_all_account_info_into_outstanding_task_df(self, all_account_info):
        """ This reduces all account info into a simplified dataframe of proposed 
        and accepted tasks """ 
        task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)
        task_type_map = task_frame.groupby('task_id').last()[['task_type']].copy()
        task_id_to_proposal = task_frame[task_frame['task_type']
        =='PROPOSAL'].groupby('task_id').first()['full_output']
        
        task_id_to_acceptance = task_frame[task_frame['task_type']
        =='ACCEPTANCE'].groupby('task_id').first()['full_output']
        acceptance_frame = pd.concat([task_id_to_proposal,task_id_to_acceptance],axis=1)
        acceptance_frame.columns=['proposal','acceptance_raw']
        acceptance_frame['acceptance']=acceptance_frame['acceptance_raw'].apply(lambda x: str(x).replace('ACCEPTANCE REASON ___ ',
                                                                                                         '').replace('nan',''))
        acceptance_frame['proposal']=acceptance_frame['proposal'].apply(lambda x: str(x).replace('PROPOSED PF ___ ',
                                                                                                         '').replace('nan',''))
        raw_proposals_and_acceptances = acceptance_frame[['proposal','acceptance']].copy()
        proposed_or_accepted_only = list(task_type_map[(task_type_map['task_type']=='ACCEPTANCE')|
        (task_type_map['task_type']=='PROPOSAL')].index)
        op= raw_proposals_and_acceptances[raw_proposals_and_acceptances.index.get_level_values(0).isin(proposed_or_accepted_only)]
        return op

    def send_acceptance_for_task_id(self, task_id, acceptance_string, all_account_info):
        """ 
        This function accepts a task. The function will not work 

        EXAMPLE PARAMETERS
        task_id='2024-05-14_19:10__ME26'
        acceptance_string = 'I agree and accept 2024-05-14_19:10__ME26 - want to finalize reward testing'
        all_account_info =self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address,
                transaction_limit=5000)
        """
        all_account_info = all_account_info
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)
        all_task_types = simplified_task_frame[simplified_task_frame['task_id']
         == task_id]['task_type'].unique()
        if (('REFUSAL' in all_task_types) 
        | ('ACCEPTANCE' in all_task_types)
       | ('VERIFICATION_RESPONSE' in all_task_types)
       | ('USER_GENESIS' in all_task_types)
       | ('REWARD' in all_task_types)):
            print('task is not valid for acceptance. Its statuses include')
            print(all_task_types)
            
        if (('REFUSAL' not in all_task_types) 
        & ('ACCEPTANCE' not in all_task_types)
       & ('VERIFICATION_RESPONSE' not in all_task_types)
       & ('USER_GENESIS' not in all_task_types)
       & ('REWARD' not in all_task_types)):
            print('Proceeding to accept task')
            node_account = list(simplified_task_frame[simplified_task_frame['task_id']==task_id].tail(1)['node_account'])[0]
            if 'ACCEPTANCE REASON ___' not in acceptance_string:
                acceptance_string='ACCEPTANCE REASON ___ '+acceptance_string
            constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                       task_id=task_id, full_output=acceptance_string)
            response = self.send_pft(amount=1, destination=node_account, memo=constructed_memo)
            account = response.result['Account']
            destination = response.result['Destination']
            memo_map = response.result['Memos'][0]['Memo']
            #memo_map.keys()
            print(f"{account} sent 1 PFT to {destination} with memo")
            print(self.convert_memo_dict(memo_map))
        return response

    def send_refusal_for_task(self, task_id, refusal_reason, all_account_info):
        """ 
        This function refuses a task. The function will not work if the task has already 
        been accepted, refused, or completed. 

        EXAMPLE PARAMETERS
        task_id='2024-05-14_19:10__ME26'
        refusal_reason = 'I cannot accept this task because ...'
        all_account_info =self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address,
                transaction_limit=5000)
        """
        all_account_info = all_account_info
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)
        task_statuses = simplified_task_frame[simplified_task_frame['task_id'] 
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
        node_account = list(simplified_task_frame[simplified_task_frame['task_id'] 
            == task_id].tail(1)['node_account'])[0]
        if 'REFUSAL REASON ___' not in refusal_reason:
            refusal_reason = 'REFUSAL REASON ___ ' + refusal_reason
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, full_output=refusal_reason)
        response = self.send_pft(amount=1, destination=node_account, memo=constructed_memo)
        account = response.result['Account']
        destination = response.result['Destination']
        memo_map = response.result['Memos'][0]['Memo']
        print(f"{account} sent 1 PFT to {destination} with memo")
        print(self.convert_memo_dict(memo_map))
        return response

    def request_post_fiat(self, request_message, 
                          all_account_info):
        """ 
        This requests a task known as a Post Fiat from the default node you are on
        
        request_message = 'I would like a new task related to the creation of my public facing wallet', 
        all_account_info=all_account_info

        """
        all_account_info =self.get_memo_detail_df_for_account()
        
        all_account_info = all_account_info
        """ 
        This function sends a request for post-fiat tasks to the node.
        
        EXAMPLE PARAMETERS
        request_message = 'Please provide details for the upcoming project.'
        all_account_info =self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address,
                transaction_limit=5000)
        """
        all_account_info = all_account_info
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=
            all_account_info)
        
        # Ensure the message has the correct prefix
        if 'REQUEST_POST_FIAT ___' not in request_message:
            request_message = 'REQUEST_POST_FIAT ___ ' + request_message
        
        # Generate a custom task ID for this request
        task_id = self.generate_custom_id()
        
        # Construct the memo with the request message
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                               task_id=task_id, full_output=request_message)
        # Send the memo to the default node
        response = self.send_pft(amount=1, destination=self.default_node, memo=constructed_memo)
        account = response.result['Account']
        destination = response.result['Destination']
        memo_map = response.result['Memos'][0]['Memo']
        print(f"{account} sent 1 PFT to {destination} with memo")
        print(self.convert_memo_dict(memo_map))
        return response

    def send_post_fiat_initial_completion(self, completion_string, task_id, all_account_info):
        """
        This function sends an initial completion for a given task back to a node.
        The most recent task status must be 'ACCEPTANCE' to trigger the initial completion.
        
        EXAMPLE PARAMETERS
        completion_string = 'I have completed the task as requested'
        task_id = '2024-05-14_19:10__ME26'
        all_account_info = self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address,
                                                                transaction_limit=5000)
        """
        all_account_info = all_account_info
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info
            =all_account_info)
        matching_task = simplified_task_frame[simplified_task_frame['task_id'] == task_id]#
        
        if matching_task.empty:
            print(f"No task found with task ID: {task_id}")
            return
        
        most_recent_status = matching_task.sort_values(by='datetime').iloc[-1]['task_type']
        
        if most_recent_status != 'ACCEPTANCE':
            print(f"The most recent status for task ID {task_id} is not 'ACCEPTANCE'. Current status: {most_recent_status}")
            return
        
        source_of_command = matching_task.iloc[0]['node_account']
        acceptance_string = 'COMPLETION JUSTIFICATION ___ ' + completion_string
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                              task_id=task_id, 
                                                              full_output=acceptance_string)
        print(acceptance_string)
        print('converted to memo')

        response = self.send_pft(amount=1, destination=source_of_command, memo=constructed_memo)
        account = response.result['Account']
        destination = response.result['Destination']
        memo_map = response.result['Memos'][0]['Memo']
        print(f"{account} sent 1 PFT to {destination} with memo")
        print(self.convert_memo_dict(memo_map))
        return response

    def convert_all_account_info_into_required_verification_df(self,all_account_info):
        """ 
        This function pulls in all account info and converts it into a list

        all_account_info = self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address,
                                                                transaction_limit=5000)

        """ 
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)
        verification_frame = simplified_task_frame[simplified_task_frame['full_output'].apply(lambda x: 
                                                                         'VERIFICATION PROMPT ___' in x)].groupby('task_id').last()[['full_output']]
        if len(verification_frame) == 0:
            return verification_frame

        if len(verification_frame)> 0:
            verification_frame['verification']=verification_frame['full_output'].apply(lambda x: x.replace('VERIFICATION PROMPT ___',''))
            verification_frame['original_task']=simplified_task_frame[simplified_task_frame['task_type'] == 'PROPOSAL'].groupby('task_id').first()['full_output']
            verification_frame[['original_task','verification']].copy()
            last_task_status=simplified_task_frame.sort_values('datetime').groupby('task_id').last()['task_type']
            verification_frame['last_task_status']=last_task_status
            outstanding_verification = verification_frame[verification_frame['last_task_status']=='VERIFICATION_PROMPT'].copy()
            outstanding_verification= outstanding_verification[['original_task','verification']].reset_index().copy()

        return outstanding_verification
        
    def send_post_fiat_verification_response(self, response_string, task_id, all_account_info):
        """
        This function sends a verification response for a given task back to a node.
        The most recent task status must be 'VERIFICATION_PROMPT' to trigger the verification response.
        
        EXAMPLE PARAMETERS
        response_string = 'This link https://livenet.xrpl.org/accounts/rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW is the PFT token mint. You can see that the issuer wallet has been blackholed per lsfDisableMaster'
        task_id = '2024-05-10_00:19__CJ33'
        all_account_info = self.get_memo_detail_df_for_account(account_address=self.user_wallet.classic_address, transaction_limit=5000)
        """
        print("""Note - for the verification response - provide a brief description of your response but
            also feel free to include supplemental information in your google doc 

            wrapped in 
            ___x TASK VERIFICATION SECTION START x___ 

            ___x TASK VERIFICATION SECTION END x___

            """ )
        all_account_info = all_account_info
        simplified_task_frame = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)
        matching_task = simplified_task_frame[simplified_task_frame['task_id'] == task_id]
        
        if matching_task.empty:
            print(f"No task found with task ID: {task_id}")
            return
        
        most_recent_status = matching_task.sort_values(by='datetime').iloc[-1]['task_type']
        
        if most_recent_status != 'VERIFICATION_PROMPT':
            print(f"The most recent status for task ID {task_id} is not 'VERIFICATION_PROMPT'. Current status: {most_recent_status}")
            return 
        
        source_of_command = matching_task.iloc[0]['node_account']
        verification_response = 'VERIFICATION RESPONSE ___ ' + response_string
        constructed_memo = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username, 
                                                              task_id=task_id, 
                                                              full_output=verification_response)
        print(verification_response)
        print('converted to memo')

        response = self.send_pft(amount=1, destination=source_of_command, memo=constructed_memo)
        account = response.result['Account']
        destination = response.result['Destination']
        memo_map = response.result['Memos'][0]['Memo']
        print(f"{account} sent 1 PFT to {destination} with memo")
        print(self.convert_memo_dict(memo_map))
        return response


    def convert_all_account_info_into_rewarded_task_df(self, all_account_info):
        """ outputs all reward df""" 
        all_tasks = self.convert_all_account_info_into_simplified_task_frame(all_account_info=all_account_info)

        # Group by task_type and task_id, then take the last entry for each group and unstack
        unstacked = all_tasks.groupby(['task_type', 'task_id']).last()['full_output'].unstack(0)

        # Check if 'PROPOSAL' and 'REWARD' columns exist, if not, return empty df
        if 'PROPOSAL' not in unstacked.columns or 'REWARD' not in unstacked.columns:
            return pd.DataFrame()

        reward_df = unstacked[['PROPOSAL', 'REWARD']].dropna().copy()

        # Apply the lambda function to prepend 'REWARD RESPONSE __' to each REWARD entry
        reward_df['REWARD'] = reward_df['REWARD'].astype(object).apply(lambda x: x.replace('REWARD RESPONSE __ ',''))
        reward_df.columns=['proposal','reward']
        pft_only=all_account_info[all_account_info['tx'].apply(lambda x: "PFT" in str(x['DeliverMax']))].copy()
        pft_only['pft_value']=pft_only['tx'].apply(lambda x: x['Amount']['value']).astype(float)*pft_only['message_type'].map({'INCOMING':1,'OUTGOING':-1})
        pft_only['task_id']=pft_only['converted_memos'].apply(lambda x: x['task_id'])
        task_id_hash = all_tasks[all_tasks['task_type']=='REWARD'].groupby('task_id').last()[['hash']]
        pft_rewards_only = pft_only[pft_only['converted_memos'].apply(lambda x: 'REWARD RESPONSE __' in 
                                                   x['full_output'])].copy()
        task_id_to_payout = pft_rewards_only.groupby('task_id').last()['pft_value']
        reward_df['payout']=task_id_to_payout
        reward_df = reward_df.tail(15)
        return reward_df

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



    def process_account_info(self, all_account_info):
        user_default_node = self.default_node
        # Slicing data based on conditions
        google_doc_slice = all_account_info[all_account_info['converted_memos'].apply(lambda x: 
                                                                   'google_doc_context_link' in str(x))].copy()

        genesis_slice = all_account_info[all_account_info['converted_memos'].apply(lambda x: 
                                                                   'USER GENESIS __' in str(x))].copy()
        
        # Extract genesis username
        genesis_username = "Unknown"
        if not genesis_slice.empty:
            genesis_username = list(genesis_slice['converted_memos'])[0]['full_output'].split(' __')[-1].split('user:')[-1].strip()
        
        # Extract Google Doc key
        key_google_doc = "No Google Doc available."
        if not google_doc_slice.empty:
            key_google_doc = list(google_doc_slice['converted_memos'])[0]['full_output']

        # Sorting account info by datetime
        sorted_account_info = all_account_info.sort_values('datetime', ascending=True).copy()

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
                full_output = data.get('converted_memos', {}).get('full_output', 'N/A')
                task_id = data.get('converted_memos', {}).get('task_id', 'N/A')
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
        if 'success' in response.status:
            status_constructor = 'successfully'
        non_hex_memo = self.convert_memo_dict(response.result['Memos'][0]['Memo'])
        user_string = non_hex_memo['full_output']
        amount_of_pft_sent = response.result['DeliverMax']['value']
        node_name = response.result['Destination']
        output_string = f"""User {status_constructor} sent {amount_of_pft_sent} PFT with request '{user_string}' to Node {node_name}"""
        return output_string

    def send_pomodoro_for_task_id(self,task_id = '2024-05-19_10:27__LL78',pomodoro_text= 'spent last 30 mins doing a ton of UX debugging'):
        pomodoro_id = task_id.replace('__','==')
        memo_to_send = self.construct_basic_postfiat_memo(user=self.credential_manager.postfiat_username,
                                           task_id=pomodoro_id, full_output=pomodoro_text)
        response = self.send_pft(amount=1, destination=self.default_node, memo=memo_to_send)
        return response

    def get_all_pomodoros(self, all_account_info):
        task_id_only = all_account_info[all_account_info['converted_memos'].apply(lambda x: 'task_id' in str(x))].copy()
        pomodoros_only = task_id_only[task_id_only['converted_memos'].apply(lambda x: '==' in x['task_id'])].copy()
        pomodoros_only['parent_task_id']=pomodoros_only['converted_memos'].apply(lambda x: x['task_id'].replace('==','__'))
        return pomodoros_only



class ProcessUserWebData:
    def __init__(self):
        print('kick off web history')
        self.ticker_regex = re.compile(r'\b[A-Z]{1,5}\b')
        #self.cik_regex = re.compile(r'CIK=(\d{10})|data/(\d{10})')
        self.cik_regex = re.compile(r'CIK=(\d+)|data/(\d+)')
        # THIS DOES NOT WORK FOR 'https://www.sec.gov/edgar/browse/?CIK=1409375&owner=exclude'
        mapper = StockMapper()
        self.cik_to_ticker_map = mapper.cik_to_tickers
    def get_user_web_history_df(self):
        outputs = get_history()
        historical_info = pd.DataFrame(outputs.histories)
        historical_info.columns=['date','url','content']
        return historical_info
    def get_primary_ticker_for_cik(self, cik):
        ret = ''
        try:
            ret = list(self.cik_to_ticker_map[cik])[0]
        except:
            pass
        return ret

    def extract_cik_to_ticker(self, input_string):
        # Define a regex pattern to match CIKs
        cik_regex = self.cik_regex
        
        # Find all matches in the input string
        matches = cik_regex.findall(input_string)
        
        # Extract CIKs from the matches and zfill to 10 characters
        ciks = [match[0] or match[1] for match in matches]
        padded_ciks = [cik.zfill(10) for cik in ciks]
        output = ''
        if len(padded_ciks) > 0:
            output = self.get_primary_ticker_for_cik(padded_ciks[0])
        
        return output
    

    def extract_tickers(self, stringer):
        tickers = list(set(self.ticker_regex.findall(stringer)))
        return tickers

    def create_basic_web_history_frame(self):
        all_web_history_df = self.get_user_web_history_df()
        all_web_history_df['cik_ticker_extraction']= all_web_history_df['url'].apply(lambda x: [self.extract_cik_to_ticker(x)])
        all_web_history_df['content_tickers']=all_web_history_df['content'].apply(lambda x: self.extract_tickers(x))#.tail(20)
        all_web_history_df['url_tickers']=all_web_history_df['url'].apply(lambda x: self.extract_tickers(x))#.tail(20)
        all_web_history_df['all_tickers']=all_web_history_df['content_tickers']+all_web_history_df['url_tickers']+all_web_history_df['cik_ticker_extraction']
        all_web_history_df['date_str']=all_web_history_df['date'].apply(lambda x: x.strftime('%Y-%m-%d'))
        str_map = pd.DataFrame(all_web_history_df['date_str'].unique())
        str_map.columns=['date_str']
        str_map['date']=pd.to_datetime(str_map['date_str'])
        all_web_history_df['simplified_date']=all_web_history_df['date_str'].map(str_map.groupby('date_str').last()['date'])
        all_web_history_df['all_tickers']=all_web_history_df['all_tickers'].apply(lambda x: list(set(x)))
        return all_web_history_df

    def convert_all_web_history_to_simple_web_data_json(self,all_web_history):
        recent_slice = all_web_history[all_web_history['simplified_date']>=datetime.datetime.now()-datetime.timedelta(7)].copy()
        recent_slice['explode_block']=recent_slice.apply(lambda x: pd.DataFrame(([[i,x['simplified_date']] for i in x['all_tickers']])),axis=1)
        
        full_ticker_history  =pd.concat(list(recent_slice['explode_block']))
        full_ticker_history.columns=['ticker','date']
        full_ticker_history['included']=1
        stop_tickers=['EDGAR','CIK','ETF','FORM','API','HOME','GAAP','EPS','NYSE','XBRL','AI','SBF','I','US','USD','SEO','','A','X','SEC','PC','EX','UTF','SIC']
        multidex = full_ticker_history.groupby(['ticker','date']).last().sort_index()
        financial_attention_df = multidex[~multidex.index.get_level_values(0).isin(stop_tickers)]['included'].unstack(0).sort_values('date').resample('D').last()
        last_day = financial_attention_df[-1:].sum()
        last_week = financial_attention_df[-7:].sum()
        
        ld_lw = pd.concat([last_day, last_week],axis=1)
        ld_lw.columns=['last_day','last_week']
        ld_lw=ld_lw.astype(int)
        ld_lw[ld_lw.sum(1)>0].to_json()
        return ld_lw