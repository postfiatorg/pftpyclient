from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.models import (
    AMMCreate,
    AMMDeposit,
    AMMWithdraw,
    IssuedCurrencyAmount,
    AMMInfo,
    AccountLines,
    BookOffers,
    OfferCreate
)
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.utils import xrp_to_drops
from xrpl.wallet import Wallet
from typing import Union, Optional, Dict, List, Tuple, Any
from decimal import Decimal
from decimal import InvalidOperation
from pftpyclient.configuration.configuration import ConfigurationManager, get_network_config
from loguru import logger
from dataclasses import dataclass

class AMMUtilities:
    def __init__(self, config: ConfigurationManager):
        self.config = config
        self.network_config = get_network_config()
        self.network_url = self.config.get_current_endpoint()
        self.pft_issuer = self.network_config.issuer_address

    async def create_amm(
        self,
        wallet: Wallet,
        amount1: Union[str, IssuedCurrencyAmount],
        amount2: Union[str, IssuedCurrencyAmount],
        trading_fee: int = 500  # Default 0.5%
    ) -> dict:
        """
        Create a new AMM instance on XRPL.
        
        Args:
            wallet: The wallet that will create and fund the AMM
            amount1: First asset amount (can be XRP or issued currency)
            amount2: Second asset amount (can be XRP or issued currency)
            trading_fee: Trading fee in units of 0.001% (500 = 0.5%)
        
        Returns:
            dict: Transaction response
        """
        client = AsyncJsonRpcClient(self.network_url)
        # Construct the AMMCreate transaction
        create_tx = AMMCreate(
            account=wallet.classic_address,
            amount=amount1,
            amount2=amount2,
            trading_fee=trading_fee
        )
        
        # Sign and submit the transaction
        response = await submit_and_wait(create_tx, client, wallet)
        return response

    async def prepare_pft_xrp_amm_create(
        self,
        wallet: Wallet,
        pft_amount: str,
        xrp_amount: str,
        pft_issuer: str,
        trading_fee: int = 500
    ) -> dict:
        """
        Helper method specifically for creating a PFT/XRP AMM.
        
        Args:
            wallet: The wallet that will create and fund the AMM
            pft_amount: Amount of PFT tokens to provide
            xrp_amount: Amount of XRP to provide
            pft_issuer: The issuer address of the PFT token
            trading_fee: Trading fee in units of 0.001% (500 = 0.5%)
            
        Returns:
            dict: Transaction response
        """
        # Create the PFT amount object
        pft_currency_amount = IssuedCurrencyAmount(
            currency="PFT",
            issuer=pft_issuer,
            value=pft_amount
        )
        
        # Convert XRP amount to drops
        xrp_drops = xrp_to_drops(xrp_amount)
        
        # Create the AMM
        return await self.create_amm(
            wallet=wallet,
            amount1=pft_currency_amount,
            amount2=xrp_drops,
            trading_fee=trading_fee
        )
    
    async def get_amm_info(
        self,
        asset1_currency: str,
        asset2_currency: str,
        asset1_issuer: Optional[str] = None,
        asset2_issuer: Optional[str] = None
    ) -> Dict:
        """
        Sync information about an AMM instance with the local AMMInfo object.
        
        Args:
            asset1_currency: Currency code of the first asset (e.g., "XRP")
            asset2_currency: Currency code of the second asset (e.g., "PFT")
            asset1_issuer: Issuer address for the first asset (required if not XRP)
            asset2_issuer: Issuer address for the second asset (required if not XRP)
            
        Returns:
            dict: AMM information response
            
        Raises:
            Exception: If AMM doesn't exist ('actNotFound') or other errors
        """
        client = AsyncJsonRpcClient(self.network_url)
        
        # Construct asset objects
        asset1_obj = {"currency": asset1_currency}
        asset2_obj = {"currency": asset2_currency}
        
        if asset1_issuer:
            asset1_obj["issuer"] = asset1_issuer
        if asset2_issuer:
            asset2_obj["issuer"] = asset2_issuer

        # Create the AMM info request
        request = AMMInfo(
            asset=asset1_obj,
            asset2=asset2_obj
        )

        # Send request to the XRPL
        logger.debug(f"Requesting AMM info for {asset1_currency}/{asset2_currency}")
        response = await client.request(request)
        return response.result

    async def get_pft_xrp_amm_info(self, pft_issuer: str) -> Dict:
        """
        Helper method specifically for getting PFT/XRP AMM information.
        
        Args:
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: AMM information response
        """
        return await self.get_amm_info(
            asset1_currency="XRP",
            asset2_currency="PFT",
            asset2_issuer=pft_issuer
        )
    
    async def get_account_lines(
        self,
        account: str,
        peer: Optional[str] = None
    ) -> Dict:
        """
        Get trust lines for an account.
        
        Args:
            account: The account to check trust lines for
            peer: Optional issuer address to filter results
            
        Returns:
            dict: Account lines response
        """
        client = AsyncJsonRpcClient(self.network_url)
        request = AccountLines(
            account=account,
            ledger_index="validated",
            peer=peer
        )
        
        response = await client.request(request)
        return response.result

    async def deposit_both_assets(
        self,
        wallet: Wallet,
        pft_amount: Decimal,
        xrp_amount: Decimal,
        pft_issuer: str
    ) -> Dict:
        """
        Deposit both PFT and XRP into the AMM.
        
        Args:
            wallet: The wallet to deposit from
            pft_amount: Amount of PFT tokens to deposit
            xrp_amount: Amount of XRP to deposit
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        _ = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create the PFT amount object
        pft_currency_amount = IssuedCurrencyAmount(
            currency="PFT",
            issuer=pft_issuer,
            value=str(pft_amount)
        )
        
        # Convert XRP amount to drops
        xrp_drops = xrp_to_drops(str(xrp_amount))
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Create the deposit transaction
        deposit_tx = AMMDeposit(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            amount=xrp_drops,
            amount2=pft_currency_amount,
            flags=1048576  # tfTwoAsset flag for double-asset deposit
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(deposit_tx, client, wallet)
        return response

    async def deposit_single_asset(
        self,
        wallet: Wallet,
        amount: Decimal,
        is_xrp: bool,
        pft_issuer: str
    ) -> Dict:
        """
        Deposit a single asset (either PFT or XRP) into the AMM.
        
        Args:
            wallet: The wallet to deposit from
            amount: Amount to deposit
            is_xrp: True if depositing XRP, False if depositing PFT
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        _ = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Prepare the amount based on asset type
        if is_xrp:
            deposit_amount = xrp_to_drops(str(amount))
        else:
            deposit_amount = IssuedCurrencyAmount(
                currency="PFT",
                issuer=pft_issuer,
                value=str(amount)
            )
        
        # Create the deposit transaction
        deposit_tx = AMMDeposit(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            amount=deposit_amount,
            flags=524288  # tfSingleAsset flag for single-asset deposit
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(deposit_tx, client, wallet)
        return response
    
    async def withdraw_both_assets(
        self,
        wallet: Wallet,
        pft_amount: Decimal,
        xrp_amount: Decimal,
        pft_issuer: str
    ) -> Dict:
        """
        Withdraw both PFT and XRP from the AMM using tfTwoAsset flag.
        
        Args:
            wallet: The wallet to withdraw to
            pft_amount: Amount of PFT tokens to withdraw
            xrp_amount: Amount of XRP to withdraw
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        _ = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create the PFT amount object
        pft_currency_amount = IssuedCurrencyAmount(
            currency="PFT",
            issuer=pft_issuer,
            value=str(pft_amount)
        )
        
        # Convert XRP amount to drops
        xrp_drops = xrp_to_drops(str(xrp_amount))
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Create the withdraw transaction
        withdraw_tx = AMMWithdraw(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            amount=xrp_drops,
            amount2=pft_currency_amount,
            flags=1048576  # tfTwoAsset flag
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(withdraw_tx, client, wallet)
        return response

    async def withdraw_single_asset(
        self,
        wallet: Wallet,
        amount: Decimal,
        is_xrp: bool,
        pft_issuer: str
    ) -> Dict:
        """
        Withdraw a single asset (either PFT or XRP) from the AMM using tfSingleAsset flag.
        
        Args:
            wallet: The wallet to withdraw to
            amount: Amount to withdraw
            is_xrp: True if withdrawing XRP, False if withdrawing PFT
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        _ = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Prepare the amount based on asset type
        if is_xrp:
            withdraw_amount = xrp_to_drops(str(amount))
        else:
            withdraw_amount = IssuedCurrencyAmount(
                currency="PFT",
                issuer=pft_issuer,
                value=str(amount)
            )
        
        # Create the withdraw transaction
        withdraw_tx = AMMWithdraw(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            amount=withdraw_amount,
            flags=524288  # tfSingleAsset flag
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(withdraw_tx, client, wallet)
        return response

    async def withdraw_all(
        self,
        wallet: Wallet,
        pft_issuer: str
    ) -> Dict:
        """
        Withdraw all assets from the AMM using tfWithdrawAll flag.
        
        Args:
            wallet: The wallet to withdraw to
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        _ = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Create the withdraw transaction
        withdraw_tx = AMMWithdraw(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            flags=131072  # tfWithdrawAll flag
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(withdraw_tx, client, wallet)
        return response

    async def withdraw_with_lp_tokens(
        self,
        wallet: Wallet,
        lp_token_amount: Decimal,
        pft_issuer: str
    ) -> Dict:
        """
        Withdraw assets by specifying LP tokens to return using tfLPToken flag.
        
        Args:
            wallet: The wallet to withdraw to
            lp_token_amount: Amount of LP tokens to return
            pft_issuer: The issuer address of the PFT token
            
        Returns:
            dict: Transaction response
        """
        # Get AMM info first to verify it exists
        client = AsyncJsonRpcClient(self.network_url)
        amm_info = await self.get_pft_xrp_amm_info(pft_issuer)
        
        # Create asset definitions
        asset1 = {"currency": "XRP"}
        asset2 = {
            "currency": "PFT",
            "issuer": pft_issuer
        }
        
        # Create LP token amount object
        lp_token_currency = amm_info['amm']['lp_token']['currency']
        lp_token_issuer = amm_info['amm']['lp_token']['issuer']
        
        lp_tokens = IssuedCurrencyAmount(
            currency=lp_token_currency,
            issuer=lp_token_issuer,
            value=str(lp_token_amount)
        )
        
        # Create the withdraw transaction
        withdraw_tx = AMMWithdraw(
            account=wallet.classic_address,
            asset=asset1,
            asset2=asset2,
            lp_token_in=lp_tokens,
            flags=65536  # tfLPToken flag
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(withdraw_tx, client, wallet)
        return response
    
    async def get_orderbook(
        self,
        taker_gets_currency: str,
        taker_pays_currency: str,
        taker_gets_issuer: Optional[str] = None,
        taker_pays_issuer: Optional[str] = None,
        limit: int = 10
    ) -> Dict:
        """
        Get the order book for a currency pair.
        
        Args:
            taker_gets_currency: Currency the taker would receive
            taker_pays_currency: Currency the taker would pay
            taker_gets_issuer: Issuer for taker_gets (if not XRP)
            taker_pays_issuer: Issuer for taker_pays (if not XRP)
            limit: Maximum number of offers to return
            
        Returns:
            dict: Order book information
        """
        # Prepare currency objects
        client = AsyncJsonRpcClient(self.network_url)
        taker_gets = {"currency": taker_gets_currency}
        taker_pays = {"currency": taker_pays_currency}
        
        if taker_gets_issuer:
            taker_gets["issuer"] = taker_gets_issuer
        if taker_pays_issuer:
            taker_pays["issuer"] = taker_pays_issuer
            
        # Create the request
        request = BookOffers(
            taker_gets=taker_gets,
            taker_pays=taker_pays,
            limit=limit,
            ledger_index="validated"
        )
        
        # Send request
        response = await client.request(request)
        return response.result
    
    async def get_amm_pool_balances(
        self,
        base_currency: str,
        quote_currency: str,
        base_issuer: Optional[str] = None,
        quote_issuer: Optional[str] = None,
    ) -> Dict[str, Decimal]:
        """Get current pool balances for a currency pair"""
        response = await self.get_amm_info(base_currency, quote_currency, base_issuer, quote_issuer)

        if not response or "amm" not in response:
            return {base_currency: Decimal("0"), quote_currency: Decimal("0")}
            
        pool = response["amm"]
        amount1 = pool["amount"]
        amount2 = pool["amount2"]
        trading_fee = Decimal(pool["trading_fee"]) / Decimal("100000")  # Convert from 1/100000 units

        # Handle amounts which might be objects or direct values
        # Convert XRP from drops
        balance1 = Decimal(amount1.get("value", "0") if isinstance(amount1, dict) else amount1)
        if not isinstance(amount1, dict):  # This is XRP in drops
            balance1 = balance1 / Decimal("1000000")
            
        balance2 = Decimal(amount2.get("value", "0") if isinstance(amount2, dict) else amount2)
        if not isinstance(amount2, dict):  # This is XRP in drops
            balance2 = balance2 / Decimal("1000000")
        
        # Match balances to currencies based on amount2's currency
        if isinstance(amount2, dict) and amount2.get("currency") == base_currency:
            return {base_currency: balance2, quote_currency: balance1, "trading_fee": trading_fee}
        else:
            return {base_currency: balance1, quote_currency: balance2, "trading_fee": trading_fee}

    def _get_amount_value(self, amount: Union[Dict, str]) -> Decimal:
        """Helper to get decimal value from either XRP drops or issued currency amount"""
        if isinstance(amount, dict):
            return Decimal(amount.get("value", "0"))
        else:
            # Convert XRP drops to XRP
            return Decimal(amount) / Decimal("1000000")
        
    def convert_quality_to_price(self, offer: Dict[str, Any], side: str) -> float:
        """
        Convert the XRPL 'quality' field to a float price in terms of PFT/XRP.

        Args:
            offer: The offer dictionary returned from the XRPL orderbook.
            side: "asks" for offers selling PFT, "bids" for offers buying PFT.

        Returns:
            A float price in PFT/XRP.
        """
        raw_quality = float(offer.get("quality", 0))
        if side == "bids":
            return raw_quality * 1_000_000
        elif side == "asks":
            return (1 / raw_quality) * 1_000_000
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'asks' or 'bids'.")
    
    def _print_orderbook(
        self,
        asks: List[Dict],
        bids: List[Dict],
        base_currency: str,
        quote_currency: str
    ) -> Dict:
        """Get orderbook liquidity in a readable format.
        
        Args:
            asks: List of ask offers
            bids: List of bid offers
            base_currency: Base currency of the pair (e.g. PFT in PFT/XRP)
            quote_currency: Quote currency of the pair (e.g. XRP in PFT/XRP)
        """
        logger.debug(f"=== {base_currency}/{quote_currency} Orderbook ===")
        
        # Print asks (selling base currency)
        logger.debug(f"Asks (Selling {base_currency}):")
        logger.debug(f"Price (in {quote_currency}) | Amount {base_currency}    | Amount {quote_currency}")
        logger.debug("-" * 45)

        asks = asks.get('offers', [])
        bids = bids.get('offers', [])

        # Process asks (selling PFT)
        ask_base_amount = Decimal("0")
        ask_quote_amount = Decimal("0")
        best_ask = None
        
        for offer in asks:
            base_amount = self._get_amount_value(
                offer['TakerGets'] if isinstance(offer['TakerGets'], dict) 
                else {'value': offer['TakerGets']}
            )
            quote_amount = self._get_amount_value(
                offer['TakerPays'] if isinstance(offer['TakerPays'], dict)
                else {'value': offer['TakerPays']}
            )
            
            # Convert drops to XRP if needed
            if base_currency == "XRP":
                base_amount = base_amount / Decimal("1000000")
            if quote_currency == "XRP":
                quote_amount = quote_amount / Decimal("1000000")
            
            ask_base_amount += base_amount
            ask_quote_amount += quote_amount
            
            price = 1.0 / self.convert_quality_to_price(offer, side="asks")
            best_ask = min(price, best_ask) if best_ask is not None else price
            logger.debug(f"  {price:12.9f} | {base_amount:10.6f} | {quote_amount:10.6f}")
        
        logger.debug("")
        
        # Print bids (buying base currency)
        logger.debug(f"Bids (Buying {base_currency}):")
        logger.debug(f"Price (in {quote_currency}) | Amount {base_currency}    | Amount {quote_currency}")
        logger.debug("-" * 45)

        # Process bids
        bid_base_amount = Decimal("0")
        bid_quote_amount = Decimal("0")
        best_bid = None
        
        for offer in bids:
            base_amount = self._get_amount_value(
                offer['TakerPays'] if isinstance(offer['TakerPays'], dict)
                else {'value': offer['TakerPays']}
            )
            quote_amount = self._get_amount_value(
                offer['TakerGets'] if isinstance(offer['TakerGets'], dict)
                else {'value': offer['TakerGets']}
            )
            
            # Convert drops to XRP if needed
            if base_currency == "XRP":
                base_amount = base_amount / Decimal("1000000")
            if quote_currency == "XRP":
                quote_amount = quote_amount / Decimal("1000000")

            bid_base_amount += base_amount
            bid_quote_amount += quote_amount
            
            price = 1.0 / self.convert_quality_to_price(offer, side="bids")
            best_bid = max(price, best_bid) if best_bid is not None else price
            logger.debug(f"  {price:12.9f} | {base_amount:10.6f} | {quote_amount:10.6f}")
        
        logger.debug("")

        if asks:
            best_ask = 1.0 / self.convert_quality_to_price(asks[0], side="asks")
            logger.debug(f"Best ask: {best_ask:.9f} PFT/XRP")
        else:
            logger.debug("No asks (sell orders) in the book")
        
        if bids:
            best_bid = 1.0 / self.convert_quality_to_price(bids[0], side="bids")
            logger.debug(f"Best bid: {best_bid:.9f} PFT/XRP")
        else:
            logger.debug("No bids (buy orders) in the book")
        
    async def print_orderbook(
        self,
        base_currency: str,  # PFT in PFT/XRP
        quote_currency: str,  # XRP in PFT/XRP
        base_issuer: Optional[str] = None,
        quote_issuer: Optional[str] = None,
    ) -> None:
        """Get orderbook liquidity in a readable format.
        
        Args:
            asks: List of ask offers
            bids: List of bid offers
            base_currency: Base currency of the pair (e.g. PFT in PFT/XRP)
            quote_currency: Quote currency of the pair (e.g. XRP in PFT/XRP)
        """
        # Get asks (i.e. offers selling PFT for XRP)
        asks = await self.get_orderbook(
            taker_gets_currency=base_currency,
            taker_pays_currency=quote_currency,
            taker_gets_issuer=base_issuer if base_currency != "XRP" else None,
            taker_pays_issuer=quote_issuer if quote_currency != "XRP" else None
        )

        # Get bids (i.e. offers buying PFT with XRP)
        bids = await self.get_orderbook(
            taker_gets_currency=quote_currency,
            taker_pays_currency=base_currency,
            taker_gets_issuer=quote_issuer if quote_currency != "XRP" else None,
            taker_pays_issuer=base_issuer if base_currency != "XRP" else None
        )

        # Print orderbook for debugging
        return self._print_orderbook(asks, bids, base_currency, quote_currency)
    
    async def calculate_estimated_receive(
        self,
        spend_currency: str,
        receive_currency: str,
        spend_amount: Decimal,
        spend_issuer: Optional[str] = None,
        receive_issuer: Optional[str] = None
    ) -> Dict:
        """
        Calculate expected receive amount for a market order by walking AMM and orderbook.
        
        Args:
            spend_currency: Currency being spent
            receive_currency: Currency to receive
            spend_amount: Amount to spend
            spend_issuer: Issuer for spend currency if not XRP
            receive_issuer: Issuer for receive currency if not XRP
            
        Returns:
            Dict containing:
                - expected_receive: Total amount expected to receive
                - routing: Dict showing amount routed to each source
                - steps: List of execution steps for UI
                - sufficient_liquidity: Boolean indicating if full amount can be filled
        """
        # Determine the trade direction
        if spend_currency == "XRP":
            # Spending XRP to receive PFT (look at asks)
            base_currency = receive_currency
            quote_currency = spend_currency
            base_issuer = receive_issuer
            quote_issuer = spend_issuer
            orderbook_side = "asks"
            logger.debug(f"Trade direction: Buying {base_currency} with {quote_currency}")
            logger.debug(f"Looking at {orderbook_side} side of orderbook")
        else:
            # Spending PFT to receive XRP (look at bids)
            base_currency = spend_currency
            quote_currency = receive_currency
            base_issuer = spend_issuer
            quote_issuer = receive_issuer
            orderbook_side = "bids"
            logger.debug(f"Trade direction: Selling {base_currency} for {quote_currency}")
            logger.debug(f"Looking at {orderbook_side} side of orderbook")
    
        # Get current liquidity state
        amm_info = await self.get_amm_pool_balances(
            base_currency=base_currency,
            quote_currency=quote_currency,
            base_issuer=base_issuer,
            quote_issuer=quote_issuer
        )
        logger.debug(f"AMM Pool State:")
        logger.debug(f"Base ({base_currency}): {amm_info[base_currency]}")
        logger.debug(f"Quote ({quote_currency}): {amm_info[quote_currency]}")
        logger.debug(f"Fee: {amm_info['trading_fee']}")

        # Fetch the correct orderbook side
        if orderbook_side == "asks":
            # Fetch asks (selling base_currency for quote_currency)
            logger.debug("Fetching asks (selling base for quote)")
            orderbook_offers = await self.get_orderbook(
                taker_gets_currency=quote_currency,
                taker_pays_currency=base_currency,
                taker_gets_issuer=quote_issuer if quote_currency != "XRP" else None,
                taker_pays_issuer=base_issuer if base_currency != "XRP" else None
            )
        else:
            # Fetch bids (buying base_currency with quote_currency)
            logger.debug("Fetching bids (buying base with quote)")
            orderbook_offers = await self.get_orderbook(
                taker_gets_currency=base_currency,
                taker_pays_currency=quote_currency,
                taker_gets_issuer=base_issuer if base_currency != "XRP" else None,
                taker_pays_issuer=quote_issuer if quote_currency != "XRP" else None
            )

        # Debugging
        await self.print_orderbook(base_currency, quote_currency, base_issuer, quote_issuer)

        # Initialize state
        remaining_spend = spend_amount
        total_receive = Decimal("0")
        routing = {"amm": Decimal("0"), "orderbook": Decimal("0")}
        steps = []

        # Working copy of AMM state
        pool_state = amm_info.copy()
        orderbook_offers = orderbook_offers.get("offers", [])

        logger.debug(f"Initial state:")
        logger.debug(f"Spend amount: {spend_amount} {quote_currency}")
        logger.debug(f"Remaining spend: {remaining_spend}")

        while remaining_spend > 0:
            # Calculate AMM output and price
            amm_fee_adjusted = remaining_spend * (1 - pool_state["trading_fee"])
            amm_output = (pool_state[quote_currency] * amm_fee_adjusted) / (
                pool_state[base_currency] + amm_fee_adjusted
            )
            amm_price = remaining_spend / amm_output if amm_output > 0 else Decimal("0")

            logger.debug(f"AMM calculation:")
            logger.debug(f"Fee adjusted spend: {amm_fee_adjusted}")
            logger.debug(f"AMM output: {amm_output:.9f} {quote_currency}")
            logger.debug(f"AMM price: {amm_price:.9f} {base_currency} per {quote_currency}")

            # Get best orderbook price and liquidity
            if orderbook_offers:
                best_offer = orderbook_offers[0]
                offer_spend_amount = self._get_amount_value(best_offer["TakerPays"])
                offer_receive_amount = self._get_amount_value(best_offer["TakerGets"])
                ob_price = offer_receive_amount / offer_spend_amount if offer_spend_amount > 0 else Decimal("0")
                logger.debug(f"Best orderbook offer:")
                logger.debug(f"Spend amount: {offer_spend_amount} {base_currency}")
                logger.debug(f"Receive amount: {offer_receive_amount} {quote_currency}")
                logger.debug(f"Price: {ob_price} {base_currency} per {quote_currency}")
            else:
                ob_price = Decimal("0")
                logger.debug("No orderbook offers available")

            # Determine which source to route to
            if amm_output and (not ob_price or amm_price <= ob_price):
                # Route to AMM (better price or no orderbook liquidity)
                logger.debug(f"Routing to AMM (better price or no orderbook liquidity)")
                routed_amount = remaining_spend
                received_amount = amm_output
                source = "amm"

                # Update pool state
                pool_state[base_currency] += amm_fee_adjusted
                pool_state[quote_currency] -= received_amount
            else:
                # Route to orderbook
                logger.debug(f"Routing to orderbook")
                offer_spend_amount = self._get_amount_value(best_offer["TakerPays"])
                offer_receive_amount = self._get_amount_value(best_offer["TakerGets"])

                # Calculate how much of this offer can be filled
                fillable_spend = min(remaining_spend, offer_spend_amount)
                fillable_receive = (fillable_spend / offer_spend_amount) * offer_receive_amount
                logger.debug(f"Fillable spend: {fillable_spend} {base_currency}")
                logger.debug(f"Fillable receive: {fillable_receive} {quote_currency}")

                routed_amount = fillable_spend
                received_amount = fillable_receive
                source = "orderbook"

                # Update orderbook state
                if fillable_spend == offer_spend_amount:
                    # Remove the filled offer
                    logger.debug("Removing filled offer")
                    orderbook_offers.pop(0)
                else:
                    # Partially fill the offer
                    logger.debug("Partially filling offer")
                    best_offer["TakerPays"] = self._get_amount_value(best_offer["TakerPays"]) - fillable_spend
                    best_offer["TakerGets"] = self._get_amount_value(best_offer["TakerGets"]) - fillable_receive

            logger.debug(f"Routed amount: {routed_amount} {base_currency}")
            logger.debug(f"Received amount: {received_amount} {quote_currency}")
            logger.debug(f"Updated remaining spend: {remaining_spend} {base_currency}")
            logger.debug(f"Updated total receive: {total_receive} {quote_currency}")

            # Update totals
            remaining_spend -= routed_amount
            total_receive += received_amount
            routing[source] += routed_amount

            # Log step
            steps.append({
                "source": source,
                "spend": routed_amount,
                "receive": received_amount,
                "price": received_amount / routed_amount if routed_amount > 0 else Decimal("0")
            })

            # Break if no more liquidity
            if routed_amount == 0:
                break

        logger.debug(f"Final results:")
        logger.debug(f"Total received: {total_receive} {quote_currency}")
        logger.debug(f"Routing: AMM={routing['amm']}, Orderbook={routing['orderbook']}")
        logger.debug(f"Sufficient liquidity: {remaining_spend == 0}")

        return {
            "expected_receive": total_receive,
            "routing": routing,
            "steps": steps,
            "sufficient_liquidity": remaining_spend == 0,
            "effective_price": total_receive / spend_amount if spend_amount > 0 else Decimal("0")
        }

    async def create_offer(
        self,
        wallet: Wallet,
        taker_gets_amount: Union[str, Decimal],
        taker_pays_amount: Union[str, Decimal],
        taker_gets_currency: str = "XRP",
        taker_pays_currency: str = "PFT",
        taker_gets_issuer: Optional[str] = None,
        taker_pays_issuer: Optional[str] = None
    ) -> Dict:
        """
        Create a new offer on the XRPL.
        
        Args:
            wallet: The wallet creating the offer
            taker_gets_amount: Amount you're offering to give
            taker_pays_amount: Amount you want to receive
            taker_gets_currency: Currency you're offering (default: "XRP")
            taker_pays_currency: Currency you want (default: "PFT")
            taker_gets_issuer: Issuer for taker_gets (if not XRP)
            taker_pays_issuer: Issuer for taker_pays (if not XRP)
            
        Returns:
            dict: Transaction response
        """
        if isinstance(taker_gets_amount, str):
            taker_gets_amount = Decimal(taker_gets_amount)
        if isinstance(taker_pays_amount, str):
            taker_pays_amount = Decimal(taker_pays_amount)

        client = AsyncJsonRpcClient(self.network_url)

        if taker_gets_currency == "XRP":
            taker_gets = xrp_to_drops(taker_gets_amount)
        else:
            taker_gets = IssuedCurrencyAmount(
                currency=taker_gets_currency,
                issuer=taker_gets_issuer,
                value=str(taker_gets_amount)
            )
            
        if taker_pays_currency == "XRP":
            taker_pays = xrp_to_drops(taker_pays_amount)
        else:
            taker_pays = IssuedCurrencyAmount(
                currency=taker_pays_currency,
                issuer=taker_pays_issuer,
                value=str(taker_pays_amount)
            )
        
        # Create the offer transaction
        offer_tx = OfferCreate(
            account=wallet.classic_address,
            taker_gets=taker_gets,
            taker_pays=taker_pays,
            flags=131072  # Immediate or cancel
        )
        
        # Submit and wait for validation
        response = await submit_and_wait(offer_tx, client, wallet)
        return response

    async def buy_pft_with_xrp(
        self,
        wallet: Wallet,
        pft_amount: Decimal,
        xrp_amount: Decimal,
        pft_issuer: str
    ) -> Dict:
        """
        Create an offer to buy PFT using XRP.
        
        Args:
            wallet: The wallet creating the offer
            pft_amount: Amount of PFT to buy
            xrp_amount: Amount of XRP to spend
            pft_issuer: The PFT token issuer address
            
        Returns:
            dict: Transaction response
        """
        # First analyze the market
        market_analysis = await self.analyze_orderbook(
            want_currency="PFT",
            want_amount=pft_amount,
            spend_currency="XRP",
            spend_amount=xrp_amount,
            want_issuer=pft_issuer
        )
        
        logger.debug(f"Creating offer to buy {pft_amount} PFT for {xrp_amount} XRP")
        logger.debug(f"Price: {float(xrp_amount/pft_amount):.6f} XRP per PFT")
        
        # Create the offer
        response = await self.create_offer(
            wallet=wallet,
            taker_gets_currency="PFT",
            taker_gets_amount=pft_amount,
            taker_pays_currency="XRP",
            taker_pays_amount=xrp_amount,
            taker_gets_issuer=pft_issuer
        )
        
        # Check if transaction was successful
        if response.result.get("engine_result") == "tesSUCCESS":
            logger.debug("Offer created successfully!")
            logger.debug(f"Transaction hash: {response.result.get('tx_json', {}).get('hash')}")
            
            # If there were matching offers, some or all might have been filled immediately
            if market_analysis[0] > 0:
                logger.debug(f"Offer may have filled immediately up to {market_analysis[0]} PFT")
                logger.debug("Check your balances to confirm the trade execution.")
        else:
            logger.debug(f"Error creating offer: {response.result.get('engine_result_message')}")
        
        return response

    async def sell_pft_for_xrp(
        self,
        wallet: Wallet,
        pft_amount: Decimal,
        xrp_amount: Decimal,
        pft_issuer: str
    ) -> Dict:
        """
        Create an offer to sell PFT for XRP.
        
        Args:
            wallet: The wallet creating the offer
            pft_amount: Amount of PFT to sell
            xrp_amount: Amount of XRP to receive
            pft_issuer: The PFT token issuer address
            
        Returns:
            dict: Transaction response
        """
        # First analyze the market
        market_analysis = await self.analyze_orderbook(
            want_currency="XRP",
            want_amount=xrp_amount,
            spend_currency="PFT",
            spend_amount=pft_amount,
            spend_issuer=pft_issuer
        )
        
        logger.debug(f"Creating offer to sell {pft_amount} PFT for {xrp_amount} XRP")
        logger.debug(f"Price: {float(xrp_amount/pft_amount):.6f} XRP per PFT")
        
        # Create the offer
        response = await self.create_offer(
            wallet=wallet,
            taker_gets_currency="XRP",
            taker_gets_amount=xrp_amount,
            taker_pays_currency="PFT",
            taker_pays_amount=pft_amount,
            taker_pays_issuer=pft_issuer
        )
        
        # Check if transaction was successful
        if response.result.get("engine_result") == "tesSUCCESS":
            logger.debug("Offer created successfully!")
            logger.debug(f"Transaction hash: {response.result.get('tx_json', {}).get('hash')}")
            
            # If there were matching offers, some or all might have been filled immediately
            if market_analysis[0] > 0:
                logger.debug(f"Offer may have filled immediately up to {market_analysis[0]} XRP")
                logger.debug("Check your balances to confirm the trade execution.")
        else:
            logger.debug(f"Error creating offer: {response.result.get('engine_result_message')}")
        
        return response