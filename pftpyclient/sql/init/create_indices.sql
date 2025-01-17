-- Index for transaction lookup by account
CREATE INDEX IF NOT EXISTS idx_tx_memos_account 
ON transaction_memos(account);

-- Index for transaction lookup by destination
CREATE INDEX IF NOT EXISTS idx_tx_memos_destination 
ON transaction_memos(destination);

-- Index for transaction lookup by datetime
CREATE INDEX IF NOT EXISTS idx_tx_memos_datetime 
ON transaction_memos(datetime);

-- Index for transaction lookup by memo_type
CREATE INDEX IF NOT EXISTS idx_tx_memos_memo_type 
ON transaction_memos(memo_type);

-- Index for postfiat_tx_cache ledger lookup
CREATE INDEX IF NOT EXISTS idx_tx_cache_ledger 
ON postfiat_tx_cache(ledger_index);