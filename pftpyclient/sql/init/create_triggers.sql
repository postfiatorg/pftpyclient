-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS process_tx_memos_insert;
DROP TRIGGER IF EXISTS process_tx_memos_update;

-- Trigger for INSERT operations
CREATE TRIGGER IF NOT EXISTS process_tx_memos_insert
AFTER INSERT ON postfiat_tx_cache
FOR EACH ROW
WHEN json_extract(NEW.tx_json, '$.Memos') IS NOT NULL
BEGIN
    INSERT INTO transaction_memos (
        hash,
        account,
        destination,
        pft_amount,
        xrp_fee,
        memo_format,
        memo_type,
        memo_data,
        datetime,
        transaction_result
    )
    SELECT
        NEW.hash,
        json_extract(NEW.tx_json, '$.Account'),
        json_extract(NEW.tx_json, '$.Destination'),
        CASE 
            WHEN json_extract(NEW.meta, '$.delivered_amount.currency') = 'PFT'
            THEN CAST(json_extract(NEW.meta, '$.delivered_amount.value') AS REAL)
            ELSE 0
        END,
        CAST(json_extract(NEW.tx_json, '$.Fee') AS REAL) / 1000000.0,
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoFormat')),
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoType')),
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoData')),
        NEW.close_time_iso,
        json_extract(NEW.meta, '$.TransactionResult');
END;

-- Trigger for UPDATE operations
CREATE TRIGGER IF NOT EXISTS process_tx_memos_update
AFTER UPDATE ON postfiat_tx_cache
FOR EACH ROW
WHEN json_extract(NEW.tx_json, '$.Memos') IS NOT NULL
BEGIN
    INSERT OR REPLACE INTO transaction_memos (
        hash,
        account,
        destination,
        pft_amount,
        xrp_fee,
        memo_format,
        memo_type,
        memo_data,
        datetime,
        transaction_result
    )
    SELECT
        NEW.hash,
        json_extract(NEW.tx_json, '$.Account'),
        json_extract(NEW.tx_json, '$.Destination'),
        CASE 
            WHEN json_extract(NEW.meta, '$.delivered_amount.currency') = 'PFT'
            THEN CAST(json_extract(NEW.meta, '$.delivered_amount.value') AS REAL)
            ELSE 0
        END,
        CAST(json_extract(NEW.tx_json, '$.Fee') AS REAL) / 1000000.0,
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoFormat')),
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoType')),
        decode_hex_memo(json_extract(NEW.tx_json, '$.Memos[0].Memo.MemoData')),
        NEW.close_time_iso,
        json_extract(NEW.meta, '$.TransactionResult');
END;