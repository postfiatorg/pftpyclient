SELECT 
    hash,
    datetime,
    memo_data,
    CASE 
        WHEN account = ? THEN 'OUTGOING'
        ELSE 'INCOMING'
    END as direction
FROM transaction_memos
WHERE 
    ((account = ? AND destination = ?) OR (account = ? AND destination = ?))
    AND memo_type LIKE '%HANDSHAKE%'
ORDER BY datetime DESC;