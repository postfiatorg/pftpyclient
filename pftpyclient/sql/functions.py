import sqlite3
import json
import binascii
from typing import Optional
from decimal import Decimal
from loguru import logger

def adapt_decimal(d: Decimal) -> str:
    """Convert Decimal to string for SQLite storage"""
    return str(d)

def convert_decimal(s: str) -> Decimal:
    """Convert string to Decimal when reading from SQLite"""
    return Decimal(s)

def register_functions(db: sqlite3.Connection):
    """Register custom SQLite functions"""
    # Register decimal handling
    sqlite3.register_adapter(Decimal, adapt_decimal)
    sqlite3.register_converter("DECIMAL", convert_decimal)
    
    # Register custom functions
    db.create_function("decode_hex_memo", 1, decode_hex_memo)

def decode_hex_memo(memo_text: Optional[str]) -> str:
    """Decode hex-encoded memo text to UTF-8 string"""
    if not memo_text:
        return ""
    
    try:
        # Strip '\x' prefix if present
        if memo_text.startswith('\\x'):
            memo_text = memo_text[2:]
        elif memo_text.startswith('0x'):
            memo_text = memo_text[2:]
            
        # Convert hex to bytes and decode as UTF-8
        return binascii.unhexlify(memo_text).decode('utf-8')
    except Exception as e:
        logger.error(f"Error decoding hex memo: {e}")
        return ""

def process_tx_memos(tx_json: dict, meta: dict, hash: str, close_time_iso: str) -> dict:
    """Process transaction data and prepare memo data for insertion"""
    try:
        # Extract memos if present
        memos = tx_json.get('Memos', [])
        if not memos:
            return None
            
        # Get first memo (currently we only process the first memo)
        memo = memos[0].get('Memo', {})
        
        # Calculate amounts
        delivered_amount = meta.get('delivered_amount', {})
        pft_amount = (
            Decimal(delivered_amount.get('value', '0'))
            if isinstance(delivered_amount, dict) and delivered_amount.get('currency') == 'PFT'
            else Decimal('0')
        )
        
        xrp_fee = Decimal(tx_json.get('Fee', '0')) / Decimal('1000000')
        
        return {
            'hash': hash,
            'account': tx_json.get('Account', ''),
            'destination': tx_json.get('Destination', ''),
            'pft_amount': pft_amount,
            'xrp_fee': xrp_fee,
            'memo_format': decode_hex_memo(memo.get('MemoFormat')),
            'memo_type': decode_hex_memo(memo.get('MemoType')),
            'memo_data': decode_hex_memo(memo.get('MemoData')),
            'datetime': close_time_iso,
            'transaction_result': meta.get('TransactionResult', '')
        }
        
    except Exception as e:
        logger.error(f"Error processing transaction memos: {e}")
        return None