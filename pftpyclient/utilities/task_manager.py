# Standard library imports
import datetime
from decimal import Decimal
from typing import Union, Optional, List, Optional, Dict
import traceback
from typing import List
import asyncio
from pathlib import Path

# Third-party imports
import xrpl
from xrpl.models.requests import AccountTx
from xrpl.models.transactions import Memo
from xrpl.models.response import Response
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.utils import str_to_hex
import nest_asyncio
import pandas as pd
from loguru import logger
import requests

# PftPyclient imports
from pftpyclient.utilities.exceptions import *
from pftpyclient.configuration.constants import UNIQUE_ID_PATTERN_V1, TaskType, MessageType
from pftpyclient.user_login.credentials import CredentialManager
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
from pftpyclient.utilities.encryption import MessageEncryption
from pftpyclient.models.models import (
    MemoConstructionParameters,
    MemoGroup
)
from pftpyclient.models.task import Task
from pftpyclient.models.memo_processor import MemoProcessor, MemoGroupProcessor, generate_custom_id
from pftpyclient.sql.sql_manager import SQLManager
from pftpyclient.utilities.initiations import InitiationRitePayload


nest_asyncio.apply()

class PostFiatTaskManager:
    INITIATION_RITE_XRP_COST: Decimal = Decimal(1)
    PFT_AMOUNT: Decimal = Decimal(1)
    
    def __init__(self, username, password, network_url, config: ConfigurationManager):
        self.credential_manager=CredentialManager(username,password)
        self.config = config
        self.network_config = get_network_config()
        self.network_url = self.config.get_current_endpoint()
        self.default_node = self.network_config.node_address
        self.pft_issuer = self.network_config.issuer_address

        self.user_wallet = self.spawn_user_wallet()

        # Get pftpyclient root directory and create data directory
        pftpyclient_root = Path.home() / '.pftpyclient'
        data_dir = pftpyclient_root / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)

        # initialize SQLManager with user-specific database
        use_testnet = self.config.get_global_config('use_testnet')
        network_suffix = '_TESTNET' if use_testnet else ''
        db_path = data_dir / f"{self.user_wallet.address}{network_suffix}.db"

        try:
            self.sql_manager = SQLManager(db_path=db_path)
            if not self.sql_manager.verify_database():
                logger.warning("Database verification failed, reinitializing...")
                # If verification fails, try to reinitialize
                self.sql_manager.initialize_database()
                if not self.sql_manager.verify_database():
                    raise RuntimeError("Database initialization failed")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            raise RuntimeError(f"Failed to initialize transaction database: {e}")

        # Initialize services
        self.transaction_requirements = TransactionRequirementService(self.network_config)
        self.message_encryption = MessageEncryption(self.sql_manager)

        # Initialize wallet state based on account status
        # By default, the wallet is considered UNFUNDED
        self.wallet_state = WalletState.UNFUNDED
        self.determine_wallet_state()

    def get_xrp_balance(self):
        return self.fetch_xrp_balance(self.network_url, self.user_wallet.classic_address)
    
    def determine_wallet_state(self) -> bool:
        """Determine the current state of the wallet based on blockhain"""
        logger.debug(f"Determining wallet state for {self.user_wallet.classic_address}")
        client = xrpl.clients.JsonRpcClient(self.network_url)
        new_state = self.wallet_state

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
                    new_state = WalletState.FUNDED
                    if self.has_trust_line():
                        new_state = WalletState.TRUSTLINED
                        if self.initiation_rite_sent():
                            new_state = WalletState.INITIATED
                            if self.handshake_sent():
                                new_state = WalletState.HANDSHAKE_SENT
                                if self.handshake_received():
                                    new_state = WalletState.HANDSHAKE_RECEIVED
                                    if self.google_doc_sent():
                                        new_state = WalletState.ACTIVE
            else:
                logger.warning(f"Account {self.user_wallet.classic_address} does not exist on XRPL")
        
        except xrpl.clients.XRPLRequestFailureException as e:
            logger.error(f"Error determining wallet state: {e}")

        if new_state != self.wallet_state:
            logger.info(f"Wallet state changed from {self.wallet_state} to {new_state}")
            self.wallet_state = new_state
            return True
        else:
            logger.debug(f"Wallet state unchanged: {self.wallet_state}")
            return False
        
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
    def send_initiation_rite(self, commitment: str) -> dict:
        """Send initiation rite to node.
        
        Args:
            commitment: User's commitment message
            
        Returns:
            dict: Transaction response
            
        Raises:
            Exception: If there is an error sending the initiation rite
        """
        try:
            # Create payload
            payload = InitiationRitePayload(
                username=self.credential_manager.postfiat_username,
                commitment=commitment
            )

            # Generate memo type with unique ID
            memo_type = f"{generate_custom_id()}__{SystemMemoType.INITIATION_RITE.value}"

            # Send transaction
            response = self.send_xrp(
                amount=self.INITIATION_RITE_XRP_COST,
                destination=self.default_node,
                memo_data=payload.to_json(),
                memo_type=memo_type
            )

            return response

        except Exception as e:
            logger.error(f"Error sending initiation rite: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _get_last_ledger_index(self) -> Optional[int]:
        """Get the last processed ledger index from the database"""
        try:
            with self.sql_manager.get_connection() as conn:
                result = conn.execute("""
                    SELECT MAX(ledger_index) as last_ledger 
                    FROM postfiat_tx_cache
                """).fetchone()
                return result['last_ledger'] if result else None
        except Exception as e:
            logger.error(f"Error getting last ledger index: {e}")
            return None

    def sync_transactions(self):
        """Sync transactions from XRPL to local database"""
        asyncio.run(self.sync_transactions_async())

    async def sync_transactions_async(self):
        """Sync transactions from XRPL to local database"""
        try:
            # Get last processed ledger index
            last_ledger = self._get_last_ledger_index()
            
            # Fetch and process new transactions
            transactions = await self._fetch_account_transactions(
                account_address=self.user_wallet.classic_address,
                ledger_index_min=last_ledger + 1 if last_ledger else -1
            )
            
            if transactions:
                formatted_txs = self._format_transactions(transactions)
                await self._store_transactions(formatted_txs)
                
        except Exception as e:
            logger.error(f"Error syncing transactions: {e}")
            raise

    def _get_last_ledger_index(self) -> Optional[int]:
        """Get the last processed ledger index from the database"""
        try:
            with self.sql_manager.get_connection() as conn:
                result = conn.execute("""
                    SELECT MAX(ledger_index) as last_ledger 
                    FROM postfiat_tx_cache
                """).fetchone()
                return result['last_ledger'] if result else None
        except Exception as e:
            logger.error(f"Error getting last ledger index: {e}")
            return None

    async def _fetch_account_transactions(
        self,
        account_address: str,
        ledger_index_min: int = -1,
        ledger_index_max: int = -1,
        max_attempts: int = 3,
        retry_delay: float = 0.2,
        limit: int = 1000
    ) -> List[Dict]:
        """
        Fetch transactions for an account from the XRPL with pagination and retry logic
        
        Args:
            account_address: The XRPL account to fetch transactions for
            ledger_index_min: Minimum ledger index to fetch from
            ledger_index_max: Maximum ledger index to fetch to
            max_attempts: Maximum number of retry attempts per request
            retry_delay: Delay between retry attempts in seconds
            limit: Maximum number of transactions per request
            
        Returns:
            List of transaction dictionaries
        """
        all_transactions = []  # Store all transactions
        marker = None  # For pagination
        attempt = 0
        client = AsyncJsonRpcClient(self.network_url)

        while attempt < max_attempts:
            try:
                request = AccountTx(
                    account=account_address,
                    ledger_index_min=ledger_index_min,
                    ledger_index_max=ledger_index_max,
                    limit=limit,
                    marker=marker,
                    forward=True
                )
                response = await client.request(request)
                
                if response.is_successful():
                    transactions = response.result["transactions"]
                    all_transactions.extend(transactions)

                    # Check if there are more transactions to fetch
                    if "marker" not in response.result:
                        break
                    marker = response.result["marker"]
                else:
                    logger.error(f"XRPL request failed: {response.result}")
                    attempt += 1
                    
            except Exception as e:
                logger.error(f"Error fetching transactions (attempt {attempt + 1}): {str(e)}")
                attempt += 1
                if attempt < max_attempts:
                    logger.debug(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.warning("Max attempts reached. Transactions may be incomplete.")
                    break

        return all_transactions
    
    @property
    def payments(self) -> List[Dict]:
        """Get all payments for the user"""
        return self.sql_manager.get_account_payments(
            account_address=self.user_wallet.classic_address
        )

    @property
    def memo_transactions(self) -> List[Dict]:
        """Get all memo transactions for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address
        )
    
    @property
    def memos(self) -> List[Dict]:
        """Get all memos for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{MessageType.MEMO.value}%'
        )
    
    @property
    def handshakes(self) -> List[Dict]:
        """Get all handshakes for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{SystemMemoType.HANDSHAKE.value}%'
        )
    
    @property
    def initiation_rites(self) -> List[Dict]:
        """Get all initiation rites for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{SystemMemoType.INITIATION_RITE.value}%'
        )
    
    @property
    def google_context_doc_links(self) -> List[Dict]:
        """Get all google context doc links for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{SystemMemoType.GOOGLE_DOC_CONTEXT_LINK.value}%'
        )
    
    @property
    def tasks(self) -> List[Dict]:
        """Get all tasks for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{TaskType.TASK_REQUEST.value}%'
        )
    
    @property
    def proposals(self) -> List[Dict]:
        """Get all proposals for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{TaskType.PROPOSAL.value}%'
        )
    
    @property
    def verifications(self) -> List[Dict]:
        """Get all verifications for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{TaskType.VERIFICATION_PROMPT.value}%'
        )
    
    @property
    def rewards(self) -> List[Dict]:
        """Get all rewards for the user"""
        return self.sql_manager.get_account_memo_history(
            account_address=self.user_wallet.classic_address,
            memo_type_filter=f'%{TaskType.REWARD.value}%'
        )

    def _format_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """Format raw transactions for database storage"""
        formatted_transactions = []
        
        for tx in transactions:
            formatted_tx = {
                'hash': tx.get('hash'),
                'ledger_index': tx.get('ledger_index'),
                'close_time_iso': tx.get('close_time_iso'),
                'validated': tx.get('validated', False),
                'meta': tx.get('meta', {}),
                'tx_json': tx.get('tx_json', {})
            }
            formatted_transactions.append(formatted_tx)
            
        return formatted_transactions

    def _store_transactions(self, transactions: List[Dict]):
        """Store formatted transactions in database"""
        try:
            for tx in transactions:
                self.sql_manager.store_transaction(tx)
                
        except Exception as e:
            logger.error(f"Error storing transactions: {e}")
            raise

    def get_task(self, task_id):
        """ Returns the task dataframe for a given task ID """
        task_df = self.tasks[self.tasks['task_id'] == task_id]
        if task_df.empty or len(task_df) == 0:
            raise NoMatchingTaskException(f"No task found with task_id {task_id}")
        return task_df
    
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

    def spawn_user_wallet(self):
        """ This takes the credential manager and loads the wallet from the
        stored seed associated with the user name"""
        seed = self.credential_manager.get_credential('v1xrpsecret')
        live_wallet = xrpl.wallet.Wallet.from_seed(seed)
        return live_wallet

    @PerformanceMonitor.measure('send_pft')
    def send_pft(
        self, 
        amount: Decimal,
        destination: str,
        memo: Union[str, Memo, None] = None,
        destination_tag: Optional[int] = None,
        pft_distribution: PFTSendDistribution = PFTSendDistribution.LAST_CHUNK_ONLY
    ) -> Union[Response, List[Response]]:
        """Send PFT tokens with optional memo.
        
        Args:
            amount: Amount of PFT to send
            destination: Destination address
            memo: Optional memo content or Memo object
            destination_tag: Optional destination tag
            pft_distribution: How to distribute PFT across chunks
            
        Returns:
            Response or list of Responses for chunked memos
            
        Raises:
            ValueError: If memo is invalid type
        """
        try:
            # Handle different memo types
            if isinstance(memo, Memo):
                memo_data = memo.memo_data
                memo_type = memo.memo_type
            elif isinstance(memo, str):
                memo_data = memo
                memo_type = None
            elif memo is None:
                memo_data = ""
                memo_type = None
            else:
                raise ValueError("Memo must be either a string, Memo object, or None")

            # Use send_memo with PFT amount
            return self.send_memo(
                destination=destination,
                memo_data=memo_data,
                memo_type=memo_type,
                pft_amount=amount,
                destination_tag=destination_tag,
                pft_distribution=pft_distribution
            )

        except Exception as e:
            logger.error(f"Error sending PFT: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def handshake_sent(self):
        """Checks if the user has sent a handshake to the node"""
        logger.debug(f"Checking if user has sent handshake to the node. Wallet state: {self.wallet_state}")
        sent_key, _ = self.message_encryption.get_handshake_for_address(self.user_wallet.classic_address, self.default_node)
        return sent_key is not None
    
    def handshake_received(self):
        """Checks if the user has received a handshake from the node"""
        logger.debug(f"Checking if user has received handshake from the node. Wallet state: {self.wallet_state}")
        _, received_key = self.message_encryption.get_handshake_for_address(self.user_wallet.classic_address, self.default_node)
        return received_key is not None
    
    @PerformanceMonitor.measure('get_handshakes')
    def get_handshakes(self) -> List[Dict]:
        """ Returns a DataFrame of all handshake interactions with their current status"""
        handshakes = self.handshakes
        if len(handshakes) == 0:
            return []
        
        # Get unique counterparty addresses
        unique_addresses = {
            tx['user_account'] for tx in handshakes 
            if tx['user_account'] != self.user_wallet.classic_address
        }

        # Process each unique counterparty
        results = []
        for address in unique_addresses:
            # Get handshake keys for this address pair
            sent_key, received_key = self.message_encryption.get_handshake_for_address(
                self.user_wallet.classic_address,
                address
            )

            # Get timestamps for first incoming/outgoing handshakes
            incoming_handshake = next(
                (tx for tx in handshakes 
                    if tx['direction'] == 'INCOMING' and tx['user_account'] == address),
                None
            )
            outgoing_handshake = next(
                (tx for tx in handshakes 
                    if tx['direction'] == 'OUTGOING' and tx['user_account'] == address),
                None
            )

            result = {
                'address': address,
                'received_at': incoming_handshake['datetime'] if incoming_handshake else None,
                'sent_at': outgoing_handshake['datetime'] if outgoing_handshake else None,
                'encryption_ready': bool(sent_key and received_key)
            }

            # Add contact information if available
            contacts = self.credential_manager.get_contacts()
            contact_name = contacts.get(address)
            result['contact_name'] = contact_name
            result['display_address'] = (
                f"{contact_name} ({address})" 
                if contact_name 
                else address
            )

            results.append(result)

        # Sort by most recent handshake activity
        return sorted(
            results,
            key=lambda x: max(
                x['received_at'] or '0000-00-00',
                x['sent_at'] or '0000-00-00'
            ),
            reverse=True
        )

    def send_handshake(self, channel_counterparty: str) -> bool:
        """Send a handshake transaction containing the ECDH public key.
        
        Args:
            channel_counterparty: Address of the other end of the channel
            
        Returns:
            bool: True if handshake sent successfully
        """
        public_key = self.message_encryption.get_ecdh_public_key_from_seed(self.user_wallet.seed)
        response = self.send_memo(
            destination=channel_counterparty, 
            memo_data=public_key,
            memo_type=generate_custom_id() + "__" + SystemMemoType.HANDSHAKE.value
        )
        return response
    
    def send_xrp(
        self,
        amount: Union[Decimal, int, float], 
        destination: str, 
        memo_data: Optional[str] = None, 
        memo_type: Optional[str] = None,
        compress: bool = False,
        encrypt: bool = False,
        destination_tag: Optional[int] = None
    ) -> Union[Response, list[Response]]:
        """Send XRP with optional memo processing capabilities.
        
        Args:
            amount: Amount of XRP to send
            destination: XRPL destination address
            memo_data: Optional memo data to include
            memo_type: Optional memo type identifier
            compress: Whether to compress the memo data
            encrypt: Whether to encrypt the memo data
            destination_tag: Optional destination tag
            
        Returns:
            Single Response or list of Responses depending on number of memos
        """

        if not memo_data:
            return self._send_memo_single(
                destination=destination,
                memo=Memo(),  # Empty memo
                xrp_amount=Decimal(amount),
                destination_tag=destination_tag
            )
        
        params = MemoConstructionParameters.construct_standardized_memo(
            source=self.user_wallet.classic_address,
            destination=destination,
            memo_data=memo_data,
            memo_type=memo_type,
            should_encrypt=encrypt,
            should_compress=compress
        )

        memo_group = MemoProcessor.construct_group(
            memo_params=params,
            wallet=self.user_wallet,
            message_encryption=self.message_encryption
        )

        return self.send_memo_group(
            destination=destination,
            memo_group=memo_group,
            xrp_amount=Decimal(amount),
            destination_tag=destination_tag
        )

    @PerformanceMonitor.measure('send_memo')
    def send_memo(
        self,
        destination: str,
        memo_data: str,
        memo_type: Optional[str] = None,
        compress: bool = False,
        encrypt: bool = False,
        pft_amount: Optional[Decimal] = None,
        xrp_amount: Optional[Decimal] = None,
        destination_tag: Optional[int] = None,
        pft_distribution: PFTSendDistribution = PFTSendDistribution.LAST_CHUNK_ONLY
    ) -> Union[Response, list[Response]]:
        """Send a memo with optional compression and encryption"""
        
        # Construct parameters for memo processing
        params = MemoConstructionParameters.construct_standardized_memo(
            source=self.user_wallet.classic_address,
            destination=destination,
            memo_data=memo_data,
            memo_type=memo_type,
            should_encrypt=encrypt,
            should_compress=compress
        )

        # Generate memo group
        memo_group = MemoProcessor.construct_group(
            memo_params=params,
            wallet=self.user_wallet,
            message_encryption=self.message_encryption
        )

        return self.send_memo_group(
            destination=destination,
            memo_group=memo_group,
            pft_amount=pft_amount,
            xrp_amount=xrp_amount,
            destination_tag=destination_tag,
            pft_distribution=pft_distribution
        )
    
    def send_memo_group(
        self,
        destination: str,
        memo_group: MemoGroup,
        pft_amount: Optional[Decimal] = None,
        xrp_amount: Optional[Decimal] = None,
        destination_tag: Optional[int] = None,
        pft_distribution: PFTSendDistribution = PFTSendDistribution.LAST_CHUNK_ONLY
    ) -> Union[Response, list[Response]]:
        """Send a group of related memos"""
        
        responses = []
        num_memos = len(memo_group.memos)

        for idx, memo in enumerate(memo_group.memos):
            # Determine PFT amount for this chunk
            chunk_pft_amount = None
            if pft_amount:
                match pft_distribution:
                    case PFTSendDistribution.DISTRIBUTE_EVENLY:
                        chunk_pft_amount = pft_amount / num_memos
                    case PFTSendDistribution.LAST_CHUNK_ONLY:
                        chunk_pft_amount = pft_amount if idx == num_memos - 1 else None
                    case PFTSendDistribution.FULL_AMOUNT_EACH:
                        chunk_pft_amount = pft_amount

            # Only send XRP with last chunk
            chunk_xrp_amount = xrp_amount if idx == num_memos - 1 else None

            responses.append(self._send_memo_single(
                destination=destination,
                memo=memo,
                pft_amount=chunk_pft_amount,
                xrp_amount=chunk_xrp_amount,
                destination_tag=destination_tag
            ))

        return responses if len(responses) > 1 else responses[0]
    
    def _send_memo_single(
            self, 
            destination: str, 
            memo: Memo, 
            pft_amount: Optional[Decimal] = None,
            xrp_amount: Optional[Decimal] = None,
            destination_tag: Optional[int] = None
        ) -> Response:
        """ Sends a single memo to a destination. """
        client = xrpl.clients.JsonRpcClient(self.network_url)

        payment_args = {
            "account": self.user_wallet.address,
            "destination": destination,
            "memos": [memo]
        }

        if destination_tag is not None:
            payment_args["destination_tag"] = destination_tag
        
        if pft_amount and pft_amount > 0:
            payment_args["amount"] = xrpl.models.amounts.IssuedCurrencyAmount(
                currency="PFT",
                issuer=self.pft_issuer,
                value=str(pft_amount)
            )
        elif xrp_amount:
            payment_args["amount"] = xrpl.utils.xrp_to_drops(xrp_amount)
        else:
            # Send minimum XRP amount for memo-only transactions
            payment_args["amount"] = xrpl.utils.xrp_to_drops(Decimal(constants.MIN_XRP_PER_TRANSACTION))

        # Sign the transaction to get the hash
        # We need to derive the hash because the submit_and_wait function doesn't return a hash if transaction fails
        # TODO: tx_hash currently not used because it doesn't match the hash produced by xrpl.transaction.submit_and_wait
        # signed_tx = xrpl.transaction.sign(payment, wallet)
        # tx_hash = signed_tx.get_hash()

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
    
    def get_latest_outgoing_context_doc_link(self) -> Optional[str]:
        """Get the most recent Google Doc context link sent by this wallet.
        Handles both encrypted and unencrypted links using the MemoGroup system.
        
        Returns:
            Optional[str]: Most recent Google Doc link or None if not found
        """
        try:
            links = self.google_context_doc_links
            if not links:
                logger.debug("No context doc memos found")
                return None

            # Filter for outgoing messages to default node
            outgoing_memos = [
                memo for memo in links
                if (memo['direction'] == 'OUTGOING' and 
                    memo['destination'] == self.default_node)
            ]

            if not outgoing_memos:
                logger.debug("No outgoing context doc memos found")
                return None

            # Get the latest valid memo group
            memo_group = MemoGroupProcessor.get_latest_valid_memo_groups(
                memo_history=outgoing_memos
            )

            if not memo_group:
                logger.debug("No valid memo groups found")
                return None
            
            # Process the group
            result = MemoProcessor.parse_group(
                wallet=self.user_wallet,
                group=memo_group,
                message_encryption=self.message_encryption,
                credential_manager=self.credential_manager
            )

            if not result:
                logger.warning("Failed to parse memo group for context doc link")
                return None

            logger.debug(f"Found context doc link: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting latest context doc link: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def get_initiation_rite(self) -> Optional[InitiationRitePayload]:
        """Get the initiation rite payload for the user's wallet if it exists."""
        try:
            initiation_rites = self.initiation_rites
            if len(initiation_rites) == 0:
                return None
            
            # Convert to MemoGroup format
            memo_group = MemoGroupProcessor.get_latest_valid_memo_groups(memo_history=initiation_rites)
            if not memo_group:
                return None
            
            result = MemoProcessor.parse_group(
                wallet=self.user_wallet,
                group=memo_group
            )

            if not result:
                return None
            
            try:
                return InitiationRitePayload.from_json(result)
            except Exception as e:
                logger.error(f"Error parsing initiation rite payload: {e}")
                return None

        except Exception as e:
            logger.error(f"Error getting initiation rite: {e}")
            return None
    
    def initiation_rite_sent(self) -> bool:
        """Check if user has sent initiation rite"""
        logger.debug("Checking if initiation rite sent")
        initiation_rite = self.get_initiation_rite()
        return initiation_rite is not None

    def google_doc_sent(self):
        """Checks if the user has ever sent a google doc context link"""
        return self.get_latest_outgoing_context_doc_link() is not None

    def check_if_google_doc_is_valid(self, google_doc_link):
        """ Checks if the google doc is valid by """

        # Check 1: google doc is a valid url
        if not google_doc_link.startswith('https://docs.google.com/document/d/'):
            raise InvalidGoogleDocException(google_doc_link)
        
        google_doc_text = self.get_google_doc_text(google_doc_link)

        # Check 2: google doc exists
        if google_doc_text == "Failed to retrieve the document. Status code: 404":
            raise GoogleDocNotFoundException(google_doc_link)

        # Check 3: google doc is shared
        if google_doc_text == "Failed to retrieve the document. Status code: 401":
            raise GoogleDocIsNotSharedException(google_doc_link)
    
    def handle_google_doc(self, google_doc_link):
        """Validates, caches, and sends the Google Doc link"""
        logger.debug("Checking Google Doc link for validity and sending if valid...")
        self.check_if_google_doc_is_valid(google_doc_link)
        return self.send_google_doc(google_doc_link)
    
    def send_google_doc(self, google_doc_link: str) -> dict:
        """Send Google Doc context link to the node.
        
        Args:
            google_doc_link: Google Doc URL
            
        Returns:
            dict: Transaction response
            
        Raises:
            Exception: If there is an error sending the Google Doc link
        """
        try:
            logger.debug(
                f"Sending Google Doc link transaction to node {self.default_node}: "
                f"{google_doc_link}"
            )

            # Generate memo type with unique ID
            memo_type = f"{generate_custom_id()}__{SystemMemoType.GOOGLE_DOC_CONTEXT_LINK.value}"

            # Send encrypted memo
            response = self.send_memo(
                destination=self.default_node,
                memo_data=google_doc_link,
                memo_type=memo_type,
                encrypt=True  # Google Doc links are always encrypted
            )

            return response

        except Exception as e:
            logger.error(f"Error sending Google Doc link: {e}")
            logger.error(traceback.format_exc())
            raise

    @PerformanceMonitor.measure('get_task')
    def get_task(self, task_id: str) -> Task:
        """Get a task by its ID.
        
        Args:
            task_id: Task ID to retrieve
            
        Returns:
            Task: Task object with complete state history
            
        Raises:
            NoMatchingTaskException: If no task is found with the given ID
        """
        try:
            # Get all memos for this task ID
            task_memos = self.sql_manager.get_account_memo_history(
                account_address=self.user_wallet.classic_address,
                memo_type_filter=f'%{task_id}%'
            )

            if not task_memos:
                raise NoMatchingTaskException(f"No task found with task_id {task_id}")

            # Convert memos to memo groups
            memo_groups = MemoGroupProcessor.get_latest_valid_memo_groups(
                memo_history=task_memos,
                num_groups=0  # Get all groups
            )

            if not memo_groups:
                raise NoMatchingTaskException(f"No valid memo groups found for task_id {task_id}")

            # Create task from memo groups
            return Task.from_memo_groups(memo_groups)

        except NoMatchingTaskException:
            raise
        except Exception as e:
            logger.error(f"Error getting task {task_id}: {e}")
            logger.error(traceback.format_exc())
            raise

    def get_task_state_by_id(self, task_id: str) -> TaskType:
        """Get the current state of a task by its ID.
        
        Args:
            task_id: Task ID to check state of
            
        Returns:
            TaskType: Current state of the task
            
        Raises:
            NoMatchingTaskException: If no task is found with the given ID
        """
        task = self.get_task(task_id)
        return task.current_state
    
    @requires_wallet_state(WalletState.ACTIVE)
    def get_tasks(self) -> List[Task]:
        """Get all tasks for the user's wallet"""
        try:
            task_requests = self.tasks
            if not task_requests:
                return []

            task_requests = []
            # Process each task request
            for request in task_requests:
                try:
                    task_id = Task.extract_task_id(request['memo_type'])

                    # Get all state changes for this task
                    task_history = self.sql_manager.get_account_memo_history(
                        account_address=self.user_wallet.classic_address,
                        memo_type_filter=f'{task_id}__%'
                    )

                    if not task_history:
                        continue

                    # Get memo groups for this task
                    memo_groups = MemoGroupProcessor.get_latest_valid_memo_groups(
                        memo_history=task_history,
                        num_groups=0
                    )

                    if not memo_groups:
                        continue

                    # Create task from memo groups
                    task = Task.from_memo_groups(memo_groups)
                    task_requests.append(task)

                except Exception as e:
                    logger.warning(f"Error processing task {request['memo_type']}: {e}")
                    continue

            return task_requests

        except Exception as e:
            logger.error(f"Error getting tasks: {e}")
            return []

    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_proposals')
    def get_proposals(self, include_refused=False) -> List[Dict]:
        """Get processed proposals with their requests and responses"""
        try:
            tasks = self.get_tasks()
            if not tasks:
                return []

            # Filter tasks based on state
            filtered_tasks = []
            for task in tasks:
                if task.current_state in [TaskType.PROPOSAL, TaskType.ACCEPTANCE]:
                    filtered_tasks.append(task)
                elif include_refused and task.current_state == TaskType.REFUSAL:
                    filtered_tasks.append(task)

            # Convert tasks to display format and sort by datetime
            return sorted(
                [task.to_dict() for task in filtered_tasks],
                key=lambda x: x['datetime'],
                reverse=True
            )

        except Exception as e:
            logger.error(f"Error getting proposals: {e}")
            return []
    
    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_verifications')
    def get_verifications(self) -> List[Dict]:
        """Get tasks that are pending verification"""
        try:
            tasks = self.get_tasks()
            if not tasks:
                return []

            # Filter for tasks in verification state
            verification_tasks = [
                task for task in tasks 
                if task.current_state == TaskType.VERIFICATION_PROMPT
            ]

            # Convert to display format
            verifications = []
            for task in verification_tasks:
                verifications.append({
                    'task_id': task.task_id,
                    'proposal': task.proposal or '',
                    'verification': task.verification_prompt or '',
                    'datetime': task.verification_prompt_datetime.isoformat()
                })

            # Sort by datetime descending
            return sorted(
                verifications,
                key=lambda x: x['datetime'],
                reverse=True
            )

        except Exception as e:
            logger.error(f"Error getting verifications: {e}")
            return []

    @requires_wallet_state(WalletState.ACTIVE)
    @PerformanceMonitor.measure('get_rewards')
    def get_rewards(self) -> List[Dict]:
        """Get tasks that have been rewarded"""
        try:
            tasks = self.get_tasks()
            if not tasks:
                return []

            # Filter for rewarded tasks
            reward_tasks = [
                task for task in tasks 
                if task.current_state == TaskType.REWARD
            ]

            # Convert to display format
            rewards = []
            for task in reward_tasks:
                rewards.append({
                    'task_id': task.task_id,
                    'proposal': task.proposal or '',
                    'reward': task.reward or '',
                    'payout': float(task.pft_amount),  # Convert Decimal to float for display
                    'datetime': task.reward_datetime.isoformat()
                })

            # Sort by datetime descending
            return sorted(
                rewards,
                key=lambda x: x['datetime'],
                reverse=True
            )

        except Exception as e:
            logger.error(f"Error getting rewards: {e}")
            return []
    
    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_payments')
    def get_payments(self) -> List[Dict]:
        """ Returns a list of payment transaction details"""
        try:
            payment_transactions = self.payments
            if not payment_transactions:
                return []
            
            def get_payment_details(transaction: Dict) -> Dict:
                """Extract payment amount and token type from transaction metadata"""
                meta = transaction.get('meta', {})
                delivered = meta.get('delivered_amount')

                if not delivered:
                    return {'amount': None, 'token': None}

                if isinstance(delivered, dict):  # PFT payment
                    # Convert to float and format to prevent scientific notation
                    amount = float(delivered['value'])
                    amount_str = f"{amount:f}".rstrip('0').rstrip('.')
                    return {
                        'amount': amount_str,
                        'token': delivered['currency']
                    }
                else:  # XRP payment
                    amount = float(delivered) / 1000000
                    amount_str = f"{amount:f}".rstrip('0').rstrip('.')
                    return {
                        'amount': amount_str,
                        'token': 'XRP'
                    }

            # Process each transaction
            processed_payments = []
            contacts = self.credential_manager.get_contacts()

            for tx in payment_transactions:
                payment_details = get_payment_details(tx)
                
                # Skip if no payment details found
                if not payment_details['amount']:
                    continue

                counterparty = tx['user_account']
                contact_name = contacts.get(counterparty)
                display_address = (
                    f"{contact_name} ({counterparty})"
                    if contact_name
                    else counterparty
                )

                processed_payments.append({
                    'datetime': tx['datetime'],
                    'amount': payment_details['amount'],
                    'token': payment_details['token'],
                    'direction': 'From' if tx['direction'] == 'INCOMING' else 'To',
                    'display_address': display_address,
                    'tx_hash': tx['hash'],
                    'counterparty': counterparty
                })

            return processed_payments  # Already sorted by datetime DESC from SQL query
    
        except Exception as e:
            logger.error(f"Error processing payments: {e}")
            logger.error(traceback.format_exc())
            return []
    
    @requires_wallet_state(TRUSTLINED_STATES)
    @PerformanceMonitor.measure('get_memos')
    def get_memos(self, decrypt=True) -> List[Dict]:
        """Returns a dataframe containing only P2P messages (excluding handshakes)"""
        memo_history = self.memos
        if len(memo_history) == 0:
            logger.debug("No memos found")
            return []
        
        # Extract unique task IDs from memo_types
        task_ids = set()
        for memo in memo_history:
            memo_type = memo.get('memo_type', '')
            match = UNIQUE_ID_PATTERN_V1.search(memo_type)
            if match:
                task_ids.add(match.group(1))

        memos = []
        for task_id in task_ids:
            # Filter memos for this task ID
            task_memos = [
                memo for memo in memo_history 
                if task_id in memo.get('memo_type', '')
            ]
            
            if not task_memos:
                continue

            first_memo = task_memos[0]
            try:
                # Get memo group for this task
                memo_groups = MemoGroupProcessor.get_latest_valid_memo_groups(
                    memo_history=task_memos,
                    num_groups=0
                )
                
                if len(memo_groups) == 0:
                    logger.debug(f"No valid memo group found for task {task_id}")
                    continue

                # Process each group in the task
                for memo_group in memo_groups:
                    processed_content = MemoProcessor.parse_group(
                        wallet=self.user_wallet,
                        group=memo_group,
                        message_encryption=self.message_encryption if decrypt else None,
                        credential_manager=self.credential_manager,
                        decrypt=decrypt
                    )

                    if processed_content is None:
                        logger.warning(f"Failed to parse memo group for task {task_id}")
                        continue

                    # Build message metadata
                    message = {
                        'memo_id': task_id,
                        'content': processed_content,
                        'direction': 'From' if first_memo['direction'] == 'INCOMING' else 'To',
                        'counterparty': first_memo['user_account'],
                        'datetime': first_memo['datetime']
                    }

                    # Add contact information
                    contacts = self.credential_manager.get_contacts()
                    contact_name = contacts.get(message['counterparty'])
                    message['display_name'] = (
                        f"{contact_name} ({message['counterparty']})"
                        if contact_name
                        else message['counterparty']
                    )

                    memos.append(message)

            except Exception as e:
                logger.error(f"Error processing message {task_id}: {e}")
                logger.error(traceback.format_exc())
                continue

        return memos
    
    @PerformanceMonitor.measure('request_task')
    def request_task(self, request_message ):
        """Send a PostFiat task request.
        
        Args:
            request_message: The task request text
            
        Returns:
            dict: Transaction response
            
        Raises:
            Exception: If there is an error sending the request
        """
        task_id = generate_custom_id()
        logger.debug(
            f"Sending task request {task_id} to node {self.default_node}"
            f"{request_message}"
        )

        return self.send_memo(
            destination=self.default_node,
            memo_data=request_message,
            memo_type=f"{task_id}__{TaskType.TASK_REQUEST.value}",
            pft_amount=self.PFT_AMOUNT          
        )
    
    @PerformanceMonitor.measure('send_acceptance')
    def send_acceptance(self, task_id: str, acceptance_message: str) -> Dict:
        """Accept a proposed task.
        
        Args:
            task_id: Task ID to accept
            acceptance_message: Acceptance message
            
        Raises:
            WrongTaskStateException: If task is not in PROPOSAL state
        """
        # Verify task state
        task = self.get_task(task_id)
        if task.current_state != TaskType.PROPOSAL:
            raise WrongTaskStateException(
                expected=TaskType.PROPOSAL.name,
                actual=task.current_state.name
            )

        return self.send_memo(
            destination=self.default_node,
            memo_data=acceptance_message,
            memo_type=f"{task_id}__{TaskType.ACCEPTANCE.value}",
            pft_amount=self.PFT_AMOUNT
        )

    @PerformanceMonitor.measure('send_refusal_for_task')
    def send_refusal(self, task_id: str, refusal_reason: str) -> Dict:
        """Refuse a task.
        
        Args:
            task_id: Task ID to refuse
            refusal_reason: Reason for refusal
            
        Raises:
            WrongTaskStateException: If task is already rewarded
        """
        # Verify task state
        task = self.get_task(task_id)
        if task.current_state == TaskType.REWARD:
            raise WrongTaskStateException(
                expected=TaskType.REWARD.name,
                actual=task.current_state.name,
                restricted_flag=True
            )

        return self.send_memo(
            destination=self.default_node,
            memo_data=refusal_reason,
            memo_type=f"{task_id}__{TaskType.REFUSAL.value}",
            pft_amount=self.PFT_AMOUNT
        )

    @PerformanceMonitor.measure('submit_completion')
    def submit_completion(self, task_id: str, completion_message: str) -> Dict:
        """Submit initial task completion.
        
        Args:
            task_id: Task ID to submit completion for
            completion_message: Completion message/evidence
            
        Raises:
            WrongTaskStateException: If task is not in ACCEPTANCE state
        """
        # Verify task state
        task = self.get_task(task_id)
        if task.current_state != TaskType.ACCEPTANCE:
            raise WrongTaskStateException(
                expected=TaskType.ACCEPTANCE.name,
                actual=task.current_state.name
            )

        return self.send_memo(
            destination=self.default_node,
            memo_data=completion_message,
            memo_type=f"{task_id}__{TaskType.TASK_COMPLETION.value}",
            pft_amount=self.PFT_AMOUNT
        )
        
    @PerformanceMonitor.measure('send_verification_response')
    def send_verification_response(
        self, 
        task_id: str, 
        response_message: str
    ) -> Dict:
        """Submit verification response.
        
        Args:
            task_id: Task ID to submit verification for
            response_message: Verification response/evidence
            
        Raises:
            WrongTaskStateException: If task is not in VERIFICATION_PROMPT state
        """
        # Verify task state
        task = self.get_task(task_id)
        if task.current_state != TaskType.VERIFICATION_PROMPT:
            raise WrongTaskStateException(
                expected=TaskType.VERIFICATION_PROMPT.name,
                actual=task.current_state.name
            )

        return self.send_memo(
            destination=self.default_node,
            memo_data=response_message,
            memo_type=f"{task_id}__{TaskType.VERIFICATION_RESPONSE.value}",
            pft_amount=self.PFT_AMOUNT
        )

    @requires_wallet_state(FUNDED_STATES)
    @PerformanceMonitor.measure('process_account_info')
    def process_account_info(self):
        logger.debug(f"Processing account info for {self.user_wallet.classic_address}")

        account_info = {
            'Account Address': self.user_wallet.classic_address,
            'Default Node': self.default_node
        }

        try:
            # Get initiation rite information
            initiation_rite = self.get_initiation_rite()
            if initiation_rite:
                account_info['Initiated Username'] = initiation_rite.username

            # attempt to retrieve the decrypted google doc link
            google_doc_link = self.get_latest_outgoing_context_doc_link()
            if google_doc_link:
                account_info['Google Doc'] = google_doc_link

            def extract_latest_message(messages: List[Dict], direction: str, node: str) -> Optional[Dict]:
                """Extract the latest message of a given type for a specific node."""
                relevant_messages = [
                    msg for msg in messages
                    if msg['direction'] == direction and (
                        (direction == 'OUTGOING' and msg['destination'] == node) or
                        (direction == 'INCOMING' and msg['account'] == node)
                    )
                ]
                return max(relevant_messages, key=lambda x: x['datetime'], default=None)

            def format_message(message: Optional[Dict]) -> Optional[str]:
                """Format message details into a readable string."""
                if not message:
                    return None

                # Get memo group for this message
                memo_group = MemoGroupProcessor.get_latest_valid_memo_groups(
                    memo_history=[message]
                )
                
                if not memo_group:
                    return None

                content = memo_group.get_content()
                tx_hash = message.get('hash', 'N/A')
                tx_datetime = message.get('datetime', 'N/A')

                return (
                    f"Content: {content}\n"
                    f"Hash: {self.get_explorer_transaction_url(tx_hash)}\n"
                    f"Datetime: {tx_datetime}\n"
                )

            # Get all memo transactions
            memo_transactions = self.sql_manager.get_account_memo_history(
                account_address=self.user_wallet.classic_address,
                memo_type_filter='%MEMO%'
            )

            if memo_transactions:
                # Extract and format latest messages
                latest_outgoing = extract_latest_message(
                    memo_transactions, 'OUTGOING', self.default_node
                )
                latest_incoming = extract_latest_message(
                    memo_transactions, 'INCOMING', self.default_node
                )

                outgoing_formatted = format_message(latest_outgoing)
                if outgoing_formatted:
                    account_info['Outgoing Message'] = outgoing_formatted

                incoming_formatted = format_message(latest_incoming)
                if incoming_formatted:
                    account_info['Incoming Message'] = incoming_formatted

        except Exception as e:
            logger.error(f"Error processing account info: {e}")
            logger.error(traceback.format_exc())
        
        return account_info
    
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

    @staticmethod
    def fetch_xrp_balance(network_url, address):
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

    @staticmethod
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

    def estimate_memo_chunks(
        self,
        memo_data: str,
        encrypt: bool = False
    ) -> int:
        """Estimate number of chunks needed for a memo.
        
        Args:
            memo_data: Memo content
            encrypt: Whether memo will be encrypted
            
        Returns:
            int: Estimated number of chunks
            
        Raises:
            ValueError: If encryption is requested but handshake not complete
        """
        try:
            return MemoProcessor.estimate_chunks(
                memo_data=memo_data,
                encrypt=encrypt,
                message_encryption=self.message_encryption if encrypt else None
            )
        except Exception as e:
            logger.error(f"Error estimating memo chunks: {e}")
            logger.error(traceback.format_exc())
            raise