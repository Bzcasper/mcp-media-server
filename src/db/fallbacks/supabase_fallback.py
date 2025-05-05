"""
Local fallback for Supabase when the cloud service is unavailable.
Implements a minimal subset of the Supabase API using SQLite.
"""
import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from datetime import datetime

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class LocalSupabaseFallback:
    """
    Provides local fallback functionality for essential Supabase operations.
    Uses SQLite to store and retrieve data when Supabase is unavailable.
    """
    
    def __init__(self):
        """Initialize the local fallback."""
        # Set up the database path
        self.db_dir = Path(settings.get_absolute_path("fallbacks"))
        self.db_dir.mkdir(exist_ok=True, parents=True)
        self.db_path = self.db_dir / "supabase_fallback.db"
        
        # Initialize the database
        self._initialize_database()
        
        # Create table references
        self._tables = {}
        
        logger.info("Local Supabase fallback initialized")
    
    def _initialize_database(self):
        """Initialize the SQLite database with required tables."""
        try:
            # Connect to SQLite
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Create tables if they don't exist
            
            # Videos table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                filename TEXT,
                file_path TEXT,
                title TEXT,
                description TEXT,
                tags TEXT,
                duration REAL,
                size_bytes INTEGER,
                format TEXT,
                width INTEGER,
                height INTEGER,
                thumbnail_path TEXT,
                created_at TEXT,
                updated_at TEXT,
                user_id TEXT,
                metadata TEXT,
                status TEXT DEFAULT 'processed'
            )
            ''')
            
            # Processing jobs table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS processing_jobs (
                id TEXT PRIMARY KEY,
                video_id TEXT,
                job_type TEXT,
                status TEXT,
                progress INTEGER DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                params TEXT,
                error TEXT,
                webhook_sent INTEGER DEFAULT 0
            )
            ''')
            
            # Webhook events table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS webhook_events (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                event_type TEXT,
                status TEXT,
                payload TEXT,
                sent_at TEXT,
                endpoint TEXT
            )
            ''')
            
            # Commit changes
            conn.commit()
            conn.close()
            
            logger.info("SQLite fallback database initialized")
        
        except Exception as e:
            logger.error(f"Error initializing fallback database: {e}")
            # If we can't initialize the fallback, we're in serious trouble
            # But we continue anyway to avoid crashing the application
    
    def _get_connection(self):
        """Get a database connection."""
        try:
            return sqlite3.connect(str(self.db_path))
        except Exception as e:
            logger.error(f"Error connecting to fallback database: {e}")
            raise
    
    def table(self, table_name: str):
        """
        Get a table query builder.
        
        Args:
            table_name: Name of the table
            
        Returns:
            Table query builder
        """
        if table_name not in self._tables:
            self._tables[table_name] = TableQueryBuilder(self, table_name)
        
        return self._tables[table_name]
    
    def _execute_query(self, query: str, params: tuple = ()):
        """
        Execute a SQL query.
        
        Args:
            query: SQL query
            params: Query parameters
            
        Returns:
            Query results
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            results = cursor.fetchall()
            conn.commit()
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            raise
    
    def _execute_insert(self, query: str, params: tuple = ()):
        """
        Execute an INSERT SQL query.
        
        Args:
            query: SQL query
            params: Query parameters
            
        Returns:
            Last inserted row ID
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Error executing insert: {e}")
            raise


class TableQueryBuilder:
    """
    Simplified query builder for SQLite tables.
    Mimics the basic functionality of Supabase's query builder.
    """
    
    def __init__(self, db: LocalSupabaseFallback, table_name: str):
        """
        Initialize the query builder.
        
        Args:
            db: Database connection
            table_name: Name of the table
        """
        self.db = db
        self.table_name = table_name
        self.reset_query()
    
    def reset_query(self):
        """Reset the query parameters."""
        self.select_columns = "*"
        self.where_clauses = []
        self.where_params = []
        self.order_by_clause = None
        self.limit_clause = None
        self.offset_clause = None
    
    def select(self, columns: str):
        """
        Set columns to select.
        
        Args:
            columns: Comma-separated list of columns
            
        Returns:
            Self for chaining
        """
        self.select_columns = columns
        return self
    
    def eq(self, column: str, value: Any):
        """
        Add an equality filter.
        
        Args:
            column: Column name
            value: Value to compare
            
        Returns:
            Self for chaining
        """
        self.where_clauses.append(f"{column} = ?")
        self.where_params.append(value)
        return self
    
    def in_(self, column: str, values: List[Any]):
        """
        Add an IN filter.
        
        Args:
            column: Column name
            values: List of values
            
        Returns:
            Self for chaining
        """
        placeholders = ", ".join(["?"] * len(values))
        self.where_clauses.append(f"{column} IN ({placeholders})")
        self.where_params.extend(values)
        return self
    
    def limit(self, limit: int):
        """
        Set limit clause.
        
        Args:
            limit: Maximum number of rows
            
        Returns:
            Self for chaining
        """
        self.limit_clause = limit
        return self
    
    def order(self, column: str, options: Dict[str, Any] = None):
        """
        Set order by clause.
        
        Args:
            column: Column to order by
            options: Ordering options
            
        Returns:
            Self for chaining
        """
        direction = "ASC"
        if options and options.get("ascending") is False:
            direction = "DESC"
        
        self.order_by_clause = f"{column} {direction}"
        return self
    
    def offset(self, offset: int):
        """
        Set offset clause.
        
        Args:
            offset: Number of rows to skip
            
        Returns:
            Self for chaining
        """
        self.offset_clause = offset
        return self
    
    def _build_query(self):
        """Build the SQL query."""
        query = f"SELECT {self.select_columns} FROM {self.table_name}"
        
        if self.where_clauses:
            query += " WHERE " + " AND ".join(self.where_clauses)
        
        if self.order_by_clause:
            query += f" ORDER BY {self.order_by_clause}"
        
        if self.limit_clause is not None:
            query += f" LIMIT {self.limit_clause}"
        
        if self.offset_clause is not None:
            query += f" OFFSET {self.offset_clause}"
        
        return query
    
    def execute(self):
        """
        Execute the query.
        
        Returns:
            Query results wrapped in a response object
        """
        try:
            query = self._build_query()
            results = self.db._execute_query(query, tuple(self.where_params))
            
            # Convert to list of dicts with column names
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({self.table_name})")
            columns = [info[1] for info in cursor.fetchall()]
            conn.close()
            
            # Create a Supabase-like result object
            data = []
            for row in results:
                # Convert special types to JSON if needed
                row_dict = {}
                for i, col_name in enumerate(columns):
                    if i < len(row):
                        value = row[i]
                        # Handle JSON fields
                        if col_name in ["tags", "metadata", "params", "payload"] and value:
                            try:
                                if isinstance(value, str):
                                    value = json.loads(value)
                            except:
                                pass
                        row_dict[col_name] = value
                
                data.append(row_dict)
            
            response = FallbackResponse(data)
            
            # Reset query for next use
            self.reset_query()
            
            return response
        
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            return FallbackResponse([])
    
    def insert(self, data: Dict[str, Any]):
        """
        Insert data into the table.
        
        Args:
            data: Dict mapping columns to values
            
        Returns:
            Self for chaining
        """
        try:
            # Handle JSON fields
            data_copy = data.copy()
            for key, value in data_copy.items():
                if isinstance(value, (dict, list)):
                    data_copy[key] = json.dumps(value)
            
            columns = ", ".join(data_copy.keys())
            placeholders = ", ".join(["?"] * len(data_copy))
            values = tuple(data_copy.values())
            
            query = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"
            
            self.db._execute_insert(query, values)
            
            # Return a response object similar to Supabase
            return FallbackResponse([data])
        
        except Exception as e:
            logger.error(f"Error inserting data: {e}")
            return FallbackResponse([])
    
    def update(self, data: Dict[str, Any]):
        """
        Set update parameters.
        
        Args:
            data: Dict mapping columns to values
            
        Returns:
            Self for chaining
        """
        try:
            # Handle JSON fields
            data_copy = data.copy()
            for key, value in data_copy.items():
                if isinstance(value, (dict, list)):
                    data_copy[key] = json.dumps(value)
            
            set_clause = ", ".join([f"{key} = ?" for key in data_copy.keys()])
            values = list(data_copy.values())
            
            if not self.where_clauses:
                logger.warning("Update without where clause, this will update all rows")
            
            query = f"UPDATE {self.table_name} SET {set_clause}"
            
            if self.where_clauses:
                query += " WHERE " + " AND ".join(self.where_clauses)
                values.extend(self.where_params)
            
            self.db._execute_query(query, tuple(values))
            
            # Return a response object similar to Supabase
            return FallbackResponse([])
        
        except Exception as e:
            logger.error(f"Error updating data: {e}")
            return FallbackResponse([])
    
    def delete(self):
        """
        Delete rows matching the query.
        
        Returns:
            Self for chaining
        """
        try:
            if not self.where_clauses:
                logger.warning("Delete without where clause, this will delete all rows")
            
            query = f"DELETE FROM {self.table_name}"
            
            if self.where_clauses:
                query += " WHERE " + " AND ".join(self.where_clauses)
            
            self.db._execute_query(query, tuple(self.where_params))
            
            # Return a response object similar to Supabase
            return FallbackResponse([])
        
        except Exception as e:
            logger.error(f"Error deleting data: {e}")
            return FallbackResponse([])


class FallbackResponse:
    """Mimics a Supabase response object."""
    
    def __init__(self, data: List[Dict[str, Any]], error: Optional[str] = None):
        """
        Initialize the response.
        
        Args:
            data: Response data
            error: Optional error message
        """
        self.data = data
        self.error = error
