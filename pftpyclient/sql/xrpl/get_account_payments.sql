WITH base_query AS (
    SELECT 
        ptc.hash,
        ptc.ledger_index,
        ptc.close_time_iso as datetime,
        ptc.meta,
        ptc.tx_json,
        CASE
            WHEN tx_json->>'Destination' = ? THEN 'INCOMING'
            ELSE 'OUTGOING'
        END as direction,
        CASE
            WHEN tx_json->>'Destination' = ? THEN tx_json->>'Account'
            ELSE tx_json->>'Destination'
        END as user_account
    FROM postfiat_tx_cache ptc
    WHERE (tx_json->>'Account' = ? OR tx_json->>'Destination' = ?)
    AND tx_json->>'TransactionType' = 'Payment'
    AND validated = 1
)
SELECT * FROM base_query 
ORDER BY datetime DESC;