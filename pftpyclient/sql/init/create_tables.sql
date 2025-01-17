CREATE TABLE IF NOT EXISTS postfiat_tx_cache (
    hash TEXT PRIMARY KEY,
    ledger_index INTEGER,
    close_time_iso TEXT,
    meta TEXT,
    tx_json TEXT,
    validated INTEGER
);

CREATE TABLE IF NOT EXISTS transaction_memos (
    hash TEXT PRIMARY KEY,
    account TEXT,
    destination TEXT,
    pft_amount REAL,
    xrp_fee REAL,
    memo_format TEXT DEFAULT '',
    memo_type TEXT DEFAULT '',
    memo_data TEXT DEFAULT '',
    datetime TEXT,
    transaction_result TEXT,
    FOREIGN KEY (hash) REFERENCES postfiat_tx_cache(hash)
        ON DELETE CASCADE
);