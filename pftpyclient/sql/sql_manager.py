import sqlite3
from importlib import resources
import pathlib
from typing import Optional, List, Dict
from loguru import logger
import traceback
import json
from contextlib import contextmanager
from pftpyclient.sql.functions import register_functions


class SQLManager:
    """Manages SQLite database operations, initialization, and queries"""
    
    def __init__(self, db_path: Optional[str] = None, sql_path: Optional[str] = None):
        """
        Initialize SQLManager
        
        Args:
            db_path: Path to SQLite database file. Defaults to user data directory
            sql_path: Path to SQL files directory. If None, uses package resources
        """
        if db_path is None:
            data_dir = pathlib.Path.home() / '.pftpyclient'
            try:
                data_dir.mkdir(exist_ok=True)
                logger.info(f"Using data directory: {data_dir}")
            except Exception as e:
                logger.error(f"Failed to create data directory {data_dir}: {e}")
                raise

            self.db_path = data_dir / 'transactions.db'
        else:
            self.db_path = pathlib.Path(db_path)

        logger.debug(f"SQLManager: Transaction database path: {self.db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sql_path = pathlib.Path(sql_path) if sql_path else None

        # Check if database needs initialization
        if not self.db_path.exists():
            logger.info("Database does not exist, initializing new database")
            self.initialize_database()
        elif not self.verify_database():
            logger.warning("Existing database verification failed, reinitializing")
            self.initialize_database()
            if not self.verify_database():
                raise RuntimeError("Database initialization failed verification")
        else:
            logger.info("Using existing verified database")

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        conn.row_factory = sqlite3.Row
        
        try:
            # Register custom functions
            register_functions(conn)
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            logger.error(traceback.format_exc())
            raise
        finally:
            conn.close()

    def load_query(self, category: str, name: str) -> str:
        """Load SQL query from file"""
        try:
            if self.sql_path:
                # Use direct file system path if provided
                file_path = self.sql_path / category / f"{name}.sql"
                return file_path.read_text()
            else:
                # Use package resources
                package_path = f"pftpyclient.sql.{category}"
                with resources.files(package_path).joinpath(f"{name}.sql").open('r') as f:
                    return f.read()
        except Exception as e:
            logger.error(f"Failed to load SQL file: {name}.sql")
            logger.error(traceback.format_exc())
            raise

    def initialize_database(self):
        """Initialize the database with required tables and functions"""
        try:
            with self.get_connection() as conn:
                # Create tables
                conn.executescript(self.load_query('init', 'create_tables'))
                
                # Create indices
                conn.executescript(self.load_query('init', 'create_indices'))
                
                # Create triggers
                conn.executescript(self.load_query('init', 'create_triggers'))
                
                logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            logger.error(traceback.format_exc())
            raise

    def verify_database(self) -> bool:
        """Verify database structure and integrity"""
        try:
            with self.get_connection() as conn:
                # Check tables exist
                tables = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' 
                    AND name IN ('postfiat_tx_cache', 'transaction_memos')
                """).fetchall()
                
                if len(tables) != 2:
                    logger.error("Missing required tables")
                    return False

                # Check triggers exist
                triggers = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='trigger' 
                    AND name IN ('process_tx_memos_insert', 'process_tx_memos_update')
                """).fetchall()
                
                if len(triggers) != 2:
                    logger.error("Missing required triggers")
                    return False

                # Test custom functions
                try:
                    conn.execute("SELECT decode_hex_memo('68656C6C6F')").fetchone()
                except sqlite3.OperationalError:
                    logger.error("Custom functions not properly registered")
                    return False

                return True

        except Exception as e:
            logger.error(f"Database verification failed: {e}")
            logger.error(traceback.format_exc())
            return False

    def store_transaction(self, tx_data: dict):
        """Store a transaction in the database"""
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO postfiat_tx_cache 
                    (hash, ledger_index, close_time_iso, meta, tx_json, validated)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    tx_data['hash'],
                    tx_data['ledger_index'],
                    tx_data['close_time_iso'],
                    json.dumps(tx_data['meta']),
                    json.dumps(tx_data['tx_json']),
                    1 if tx_data.get('validated', False) else 0
                ))
        except Exception as e:
            logger.error(f"Failed to store transaction: {e}")
            logger.error(traceback.format_exc())
            raise

    def get_account_memo_history(
        self, 
        account_address: str, 
        pft_only: bool = False, 
        memo_type_filter: Optional[str] = None
    ) -> List[Dict]:
        """Get transaction history with memos for an account.
        
        Args:
            account_address: XRPL account address to get history for
            pft_only: If True, only return transactions with PFT included
            memo_type_filter: Optional string to filter memo_types using LIKE
            
        Returns:
            List of dictionaries containing memo transactions
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    self.load_query('xrpl', 'get_account_memo_history'),
                    (
                        account_address,  # for direction CASE
                        account_address,  # for directional_pft CASE
                        account_address,  # for user_account CASE
                        account_address,  # for WHERE clause
                        account_address,  # for WHERE clause
                        1 if pft_only else 0,  # SQLite doesn't have true boolean
                        memo_type_filter,  # for LIKE clause
                        memo_type_filter   # for LIKE clause
                    )
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting account memo history: {e}")
            logger.error(traceback.format_exc())
            return []
        
    def get_account_payments(self, account_address: str) -> List[Dict]:
        """Get payment transactions for an account from the transaction cache.
        
        Args:
            account_address: XRPL account address to get payments for
            
        Returns:
            List of dictionaries containing payment transactions with full metadata
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    self.load_query('xrpl', 'get_account_payments'),
                    (
                        account_address,  # for direction CASE
                        account_address,  # for user_account CASE
                        account_address,  # for WHERE Account
                        account_address   # for WHERE Destination
                    )
                )
                
                payments = []
                
                for row in cursor.fetchall():
                    payment = dict(row)
                    # Parse JSON fields
                    payment['meta'] = json.loads(payment['meta'])
                    payment['tx_json'] = json.loads(payment['tx_json'])
                    payments.append(payment)
                    
                return payments

        except Exception as e:
            logger.error(f"Error getting account payments: {e}")
            return []