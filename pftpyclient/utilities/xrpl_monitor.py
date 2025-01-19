from pftpyclient.configuration.configuration import ConfigurationManager
from pftpyclient.utilities.wallet_state import WalletUIState
import wx
import xrpl
from loguru import logger
from xrpl.asyncio.clients import AsyncWebsocketClient
import asyncio
import random
import time
import traceback
from threading import Event, Thread

from pftpyclient.protocols.prod_wallet import WalletApp

class XRPLMonitorThread(Thread):
    def __init__(
            self,
            gui: WalletApp
        ):
        Thread.__init__(self, daemon=True)
        self.gui: WalletApp = gui
        self.config = ConfigurationManager()
        self.ws_urls = self.config.get_ws_endpoints()
        self.ws_url_index = 0
        self.url = self.ws_urls[self.ws_url_index]
        logger.debug(f"Starting XRPL monitor thread with endpoint: {self.url}")
        self.loop = asyncio.new_event_loop()
        self.context = None
        self._stop_event = Event()

        # Error handling parameters
        self.reconnect_delay = 1  # Initial delay in seconds
        self.max_reconnect_delay = 30  # Maximum delay
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5  # Per node

        # Ledger monitoring
        self.last_ledger_time = None
        self.LEDGER_TIMEOUT = 30  # seconds
        self.CHECK_INTERVAL = 4  # match XRPL block time
        self.PING_INTERVAL = 60  # Send ping every 60 seconds
        self.PING_TIMEOUT = 10   # Wait up to 10 seconds for pong

    def run(self):
        """Thread entry point"""
        asyncio.set_event_loop(self.loop)
        try:
            self.context = self.loop.run_until_complete(self.monitor())
        except Exception as e:
            if not self.stopped():
                logger.error(f"Unexpected error in XRPLMonitorThread: {e}")
        finally:
            self.loop.close()

    def stop(self):
        """Signal the thread to stop"""
        self._stop_event.set()

        # Close websocket connection if it exists
        if hasattr(self, 'client') and self.client:
            # Use the worker's existing loop to close
            future = asyncio.run_coroutine_threadsafe(
                self.client.close(),
                self.loop
            )
            try:
                # Wait for the websocket to close with a timeout
                future.result(timeout=2)
            except Exception:
                pass  # Ignore timeout or other errors during close

        # Cancel any pending tasks
        pending_tasks = asyncio.all_tasks(self.loop)
        for task in pending_tasks:
            task.cancel()

        # Stop the event loop
        try:
            self.loop.call_soon_threadsafe(
                lambda: self.loop.stop()
            )
        except Exception as e:
            pass  # Ignore any errors during loop stop

    def stopped(self):
        """Check if the thread has been signaled to stop"""
        return self._stop_event.is_set()

    async def ping_server(self):
        """Send ping and wait for response"""
        try:
            # Use server_info as a lightweight ping
            response = await self.client.request(xrpl.models.requests.ServerInfo())
            return response.is_successful()
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False

    async def check_timeouts(self):
        """Check for ledger timeouts"""
        last_ping_time = time.time()

        while True:
            await asyncio.sleep(self.CHECK_INTERVAL)

            current_time = time.time()

            # Check ledger updates
            if self.last_ledger_time is not None:
                time_since_last_ledger = time.time() - self.last_ledger_time
                if time_since_last_ledger > self.LEDGER_TIMEOUT:
                    logger.warning(f"No ledger updates for {time_since_last_ledger:.1f} seconds")
                    raise Exception(f"No ledger updates received for {time_since_last_ledger:.1f} seconds")

            # Check if it's time for a ping
            time_since_last_ping = current_time - last_ping_time
            if time_since_last_ping >= self.PING_INTERVAL:
                try:
                    async with asyncio.timeout(self.PING_TIMEOUT):
                        is_alive = await self.ping_server()
                        if is_alive:
                            logger.debug(f"Pinged websocket...")
                        else:
                            raise Exception("Ping failed - no valid response")
                    last_ping_time = current_time
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"Connection check failed: {e}")
                    raise Exception(f"Connection check failed: {e}")

    def set_ui_state(self, state: WalletUIState, message: str = None):
        """Helper method to safely update UI state from thread"""
        wx.CallAfter(self.gui.set_wallet_ui_state, state, message)

    async def handle_connection_error(self, error_msg: str) -> bool:
        """
        Connection error handling with exponential backoff
        Returns True if should retry, False if should switch nodes
        """
        logger.error(error_msg)
        self.set_ui_state(WalletUIState.ERROR, error_msg)

        self.reconnect_attempts += 1
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.warning(f"Max reconnection attempts reached for node {self.url}. Switching to next node.")
            self.switch_node()
            self.reconnect_attempts = 0
            self.reconnect_delay = 1
            return False

        # Exponential backoff with jitter
        jitter = random.uniform(0, 0.1) * self.reconnect_delay
        delay = min(self.reconnect_delay + jitter, self.max_reconnect_delay)
        logger.info(f"Reconnecting in {delay:.1f} seconds...")
        await asyncio.sleep(delay)
        self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
        return True

    async def monitor(self):
        """Main monitoring coroutine with error handling and reconnection logic"""
        while not self.stopped():
            try:
                await self.watch_xrpl_account(self.gui.wallet.classic_address, self.gui.wallet)
                # Reset reconnection parameters on successful connection
                self.reconnect_delay = 1
                self.reconnect_attempts = 0

            except asyncio.CancelledError:
                logger.debug("Monitor task cancelled")
                break

            except Exception as e:
                if self.stopped():
                    break

                await self.handle_connection_error(f"Error in monitor: {e}")

    def switch_node(self):
        self.ws_url_index = (self.ws_url_index + 1) % len(self.ws_urls)
        self.url = self.ws_urls[self.ws_url_index]
        logger.info(f"Switching to next node: {self.url}")

    async def watch_xrpl_account(self, address, wallet=None):
        self.account = address
        self.wallet = wallet
        self.last_ledger_time = time.time()

        async with AsyncWebsocketClient(self.url) as self.client:
            self.set_ui_state(WalletUIState.SYNCING, "Connecting to XRPL websocket...")

            # Subcribe to streams
            response = await self.client.request(xrpl.models.requests.Subscribe(
                streams=["ledger"],
                accounts=[self.account]
            ))

            if not response.is_successful():
                self.set_ui_state(WalletUIState.IDLE, "Failed to connect to XRPL websocket.")
                raise Exception(f"Subscription failed: {response.result}")

            self.set_ui_state(WalletUIState.IDLE)
            logger.info(f"Successfully subscribed to account {self.account} updates on node {self.url}")

            # Create task for timeout checking     
            timeout_task = asyncio.create_task(self.check_timeouts())

            try:
                async for message in self.client:
                    if self.stopped():
                        break

                    try:
                        mtype = message.get("type")

                        if mtype == "ledgerClosed":
                            self.last_ledger_time = time.time()
                        elif mtype == "transaction":
                            await self.process_transaction(message)

                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        self.set_ui_state(WalletUIState.ERROR, f"Error processing update: {str(e)}")
                        continue

            finally:
                timeout_task.cancel()
                try:
                    await timeout_task
                except asyncio.CancelledError:
                    pass

    async def process_transaction(self, tx_message):
        """Process a single transaction update from websocket"""
        try:
            self.set_ui_state(WalletUIState.BUSY, "Processing new transaction...")
            logger.debug(f"Full websocket transaction message: {tx_message}")

            wx.CallAfter(self.gui.task_manager.store_transaction, tx_message)

            # Update account info
            response = await self.client.request(xrpl.models.requests.AccountInfo(
                account=self.account,
                ledger_index="validated"
            ))

            if response.is_successful():
                async def update_all():
                    await self.gui.update_account(response.result["account_data"])
                    await self.gui.update_tokens()
                    self.gui.refresh_grids()
                await update_all()
            else:
                logger.error(f"Failed to get account info: {response.result}")

            self.set_ui_state(WalletUIState.IDLE)

        except Exception as e:
            logger.error(f"Error processing transaction update: {e}")
            logger.error(traceback.format_exc())
            self.set_ui_state(WalletUIState.IDLE, f"Error: {str(e)}")