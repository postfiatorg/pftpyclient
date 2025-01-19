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
from typing import Union, Optional, Dict, List, Tuple
from decimal import Decimal
from pftpyclient.configuration.configuration import ConfigurationManager, get_network_config
import asyncio
from loguru import logger

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
        asset2_issuer: Optional[str] = None
    ) -> Dict:
        """
        Get information about an AMM instance.
        
        Args:
            asset1_currency: Currency code of the first asset (e.g., "XRP")
            asset2_currency: Currency code of the second asset (e.g., "PFT")
            asset2_issuer: Issuer address for the second asset (required if not XRP)
            
        Returns:
            dict: AMM information response
            
        Raises:
            Exception: If AMM doesn't exist ('actNotFound') or other errors
        """
        client = AsyncJsonRpcClient(self.network_url)
        # Create asset objects
        asset1 = {"currency": asset1_currency}
        asset2 = {
            "currency": asset2_currency,
            "issuer": asset2_issuer
        }

        # Create the AMM info request
        request = AMMInfo(
            asset=asset1,
            asset2=asset2
        )

        # Send request to the XRPL
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
        wallet: Wallet,
        taker_gets_currency: str,
        taker_pays_currency: str,
        taker_gets_issuer: Optional[str] = None,
        taker_pays_issuer: Optional[str] = None,
        limit: int = 10
    ) -> Dict:
        """
        Get the order book for a currency pair.
        
        Args:
            wallet: The wallet to use as taker
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
            taker=wallet.classic_address,
            taker_gets=taker_gets,
            taker_pays=taker_pays,
            limit=limit,
            ledger_index="validated"
        )
        
        # Send request
        response = await client.request(request)
        return response.result

    async def analyze_orderbook(
        self,
        wallet: Wallet,
        want_currency: str,
        want_amount: Decimal,
        spend_currency: str,
        spend_amount: Decimal,
        want_issuer: Optional[str] = None,
        spend_issuer: Optional[str] = None
    ) -> Tuple[Decimal, List[Dict]]:
        """
        Analyze order book to estimate if and how an offer would execute.
        
        Args:
            wallet: The wallet that would place the offer
            want_currency: Currency you want to receive
            want_amount: Amount you want to receive
            spend_currency: Currency you're willing to spend
            spend_amount: Amount you're willing to spend
            want_issuer: Issuer for want_currency (if not XRP)
            spend_issuer: Issuer for spend_currency (if not XRP)
            
        Returns:
            Tuple[Decimal, List[Dict]]: (matching_amount, matching_offers)
        """
        # Calculate the proposed quality (price)
        proposed_quality = spend_amount / want_amount
        
        # Get the order book
        orderbook = await self.get_orderbook(
            wallet=wallet,
            taker_gets_currency=want_currency,
            taker_pays_currency=spend_currency,
            taker_gets_issuer=want_issuer,
            taker_pays_issuer=spend_issuer
        )
        
        # Analyze matching offers
        offers = orderbook.get("offers", [])
        running_total = Decimal(0)
        matching_offers = []
        
        logger.debug(f"Analyzing order book for {want_amount} {want_currency}...")
        
        if not offers:
            logger.debug("No offers found in the matching book.")
            return Decimal(0), []
            
        for offer in offers:
            offer_quality = Decimal(offer["quality"])
            if offer_quality <= proposed_quality:
                owner_funds = Decimal(offer.get("owner_funds", "0"))
                matching_offers.append(offer)
                logger.debug(f"Found matching offer with {owner_funds} {want_currency}")
                
                running_total += owner_funds
                if running_total >= want_amount:
                    logger.debug("Full amount can be filled!")
                    break
            else:
                logger.debug("Remaining offers are too expensive.")
                break
        
        matched_amount = min(running_total, want_amount)
        logger.debug(f"Total matched: {matched_amount} {want_currency}")
        
        if 0 < matched_amount < want_amount:
            remaining = want_amount - matched_amount
            logger.debug(f"Remaining {remaining} {want_currency} would be placed as new offer")
            
        return matched_amount, matching_offers

    async def check_competing_offers(
        self,
        wallet: Wallet,
        want_currency: str,
        want_amount: Decimal,
        spend_currency: str,
        spend_amount: Decimal,
        want_issuer: Optional[str] = None,
        spend_issuer: Optional[str] = None
    ) -> Tuple[Decimal, List[Dict]]:
        """
        Check for competing offers at the same price point.
        
        Args:
            (same as analyze_orderbook)
            
        Returns:
            Tuple[Decimal, List[Dict]]: (competing_amount, competing_offers)
        """
        # Calculate the inverse quality for competing offers
        offered_quality = want_amount / spend_amount
        
        # Get the competing order book (reversed currencies)
        orderbook = await self.get_orderbook(
            wallet=wallet,
            taker_gets_currency=spend_currency,
            taker_pays_currency=want_currency,
            taker_gets_issuer=spend_issuer,
            taker_pays_issuer=want_issuer
        )
        
        offers = orderbook.get("offers", [])
        running_total = Decimal(0)
        competing_offers = []
        
        logger.debug("\nChecking for competing offers...")
        
        if not offers:
            logger.debug("No competing offers found. Would be first in book.")
            return Decimal(0), []
            
        for offer in offers:
            offer_quality = Decimal(offer["quality"])
            if offer_quality <= offered_quality:
                owner_funds = Decimal(offer.get("owner_funds", "0"))
                competing_offers.append(offer)
                logger.debug(f"Found competing offer with {owner_funds} {spend_currency}")
                running_total += owner_funds
            else:
                logger.debug("Remaining offers would be below our price point.")
                break
                
        logger.debug(f"Total competing liquidity: {running_total} {spend_currency}")
        return running_total, competing_offers
    
    async def calculate_swap_rate(
        self,
        wallet: Wallet,
        spend_currency: str,
        receive_currency: str,
        spend_amount: Optional[Decimal] = None,
        receive_amount: Optional[Decimal] = None,
        spend_issuer: Optional[str] = None,
        receive_issuer: Optional[str] = None,
    ) -> Dict:
        """
        Calculate expected swap rate and amounts based on current orderbook.
        
        Args:
            wallet: The wallet to use as taker
            spend_currency: Currency you're spending
            receive_currency: Currency you want to receive
            spend_amount: Amount you want to spend (optional)
            receive_amount: Amount you want to receive (optional)
            spend_issuer: Issuer for spend currency if not XRP
            receive_issuer: Issuer for receive currency if not XRP
            
        Returns:
            Dict containing:
                - expected_spend: How much will be spent
                - expected_receive: How much will be received
                - best_price: Best available price
                - worst_price: Worst price needed to fill order
                - sufficient_liquidity: Boolean indicating if enough liquidity exists
        
        Note: Either spend_amount or receive_amount must be provided, but not both
        """
        if (spend_amount is None and receive_amount is None) or (spend_amount is not None and receive_amount is not None):
            raise ValueError("Must provide either spend_amount or receive_amount, but not both")

        # Get orderbook with taker perspective
        orderbook = await self.get_orderbook(
            wallet=wallet,
            taker_gets_currency=receive_currency,  # What we want to receive
            taker_pays_currency=spend_currency,    # What we're willing to spend
            taker_gets_issuer=receive_issuer,
            taker_pays_issuer=spend_issuer
        )

        offers = orderbook.get("offers", [])
        if not offers:
            return {
                "expected_spend": Decimal("0"),
                "expected_receive": Decimal("0"),
                "best_price": None,
                "worst_price": None,
                "sufficient_liquidity": False
            }

        # Calculate based on available offers
        if spend_amount is not None:
            # We know how much we want to spend, calculate how much we'll receive
            remaining_spend = spend_amount
            total_receive = Decimal("0")
            prices = []

            for offer in offers:
                offer_quality = Decimal(offer["quality"])  # Price in terms of spend/receive
                prices.append(offer_quality)
                
                # Calculate how much we can get from this offer
                offer_funds = Decimal(offer.get("owner_funds", "0"))
                receivable = min(remaining_spend / offer_quality, offer_funds)
                
                total_receive += receivable
                remaining_spend -= receivable * offer_quality
                
                if remaining_spend <= 0:
                    break

            return {
                "expected_spend": spend_amount - remaining_spend,
                "expected_receive": total_receive,
                "best_price": min(prices) if prices else None,
                "worst_price": max(prices) if prices else None,
                "sufficient_liquidity": remaining_spend <= 0
            }

        else:
            # We know how much we want to receive, calculate how much we need to spend
            remaining_receive = receive_amount
            total_spend = Decimal("0")
            prices = []

            for offer in offers:
                offer_quality = Decimal(offer["quality"])
                prices.append(offer_quality)
                
                # Calculate how much we can get from this offer
                offer_funds = Decimal(offer.get("owner_funds", "0"))
                receivable = min(remaining_receive, offer_funds)
                
                total_spend += receivable * offer_quality
                remaining_receive -= receivable
                
                if remaining_receive <= 0:
                    break

            return {
                "expected_spend": total_spend,
                "expected_receive": receive_amount - remaining_receive,
                "best_price": min(prices) if prices else None,
                "worst_price": max(prices) if prices else None,
                "sufficient_liquidity": remaining_receive <= 0
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
        # Convert amounts to proper format
        client = AsyncJsonRpcClient(self.network_url)
        if taker_gets_currency == "XRP":
            taker_gets = xrp_to_drops(str(taker_gets_amount))
        else:
            taker_gets = IssuedCurrencyAmount(
                currency=taker_gets_currency,
                issuer=taker_gets_issuer,
                value=str(taker_gets_amount)
            )
            
        if taker_pays_currency == "XRP":
            taker_pays = xrp_to_drops(str(taker_pays_amount))
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
            taker_pays=taker_pays
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
            wallet=wallet,
            want_currency="PFT",
            want_amount=pft_amount,
            spend_currency="XRP",
            spend_amount=xrp_amount,
            want_issuer=pft_issuer
        )
        
        logger.debug(f"\nCreating offer to buy {pft_amount} PFT for {xrp_amount} XRP")
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
            logger.debug("\nOffer created successfully!")
            logger.debug(f"Transaction hash: {response.result.get('tx_json', {}).get('hash')}")
            
            # If there were matching offers, some or all might have been filled immediately
            if market_analysis[0] > 0:
                logger.debug(f"\nOffer may have filled immediately up to {market_analysis[0]} PFT")
                logger.debug("Check your balances to confirm the trade execution.")
        else:
            logger.debug(f"\nError creating offer: {response.result.get('engine_result_message')}")
        
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
            wallet=wallet,
            want_currency="XRP",
            want_amount=xrp_amount,
            spend_currency="PFT",
            spend_amount=pft_amount,
            spend_issuer=pft_issuer
        )
        
        logger.debug(f"\nCreating offer to sell {pft_amount} PFT for {xrp_amount} XRP")
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
            logger.debug("\nOffer created successfully!")
            logger.debug(f"Transaction hash: {response.result.get('tx_json', {}).get('hash')}")
            
            # If there were matching offers, some or all might have been filled immediately
            if market_analysis[0] > 0:
                logger.debug(f"\nOffer may have filled immediately up to {market_analysis[0]} XRP")
                logger.debug("Check your balances to confirm the trade execution.")
        else:
            logger.debug(f"\nError creating offer: {response.result.get('engine_result_message')}")
        
        return response