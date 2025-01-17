WITH base_query AS (
    SELECT 
        hash,
        account,
        destination,
        pft_amount,
        xrp_fee,
        memo_format,
        memo_type,
        memo_data,
        datetime,
        transaction_result,
        CASE
            WHEN destination = ? THEN 'INCOMING'
            ELSE 'OUTGOING'
        END as direction,
        CASE
            WHEN destination = ? THEN pft_amount
            ELSE -pft_amount
        END as directional_pft,
        CASE
            WHEN account = ? THEN destination
            ELSE account
        END as user_account
    FROM transaction_memos
    WHERE (account = ? OR destination = ?)
)
SELECT * FROM base_query 
WHERE 1=1
    AND CASE WHEN ? THEN pft_amount IS NOT NULL ELSE TRUE END
    AND CASE WHEN ? IS NOT NULL THEN memo_type LIKE ? ELSE TRUE END
ORDER BY datetime DESC;