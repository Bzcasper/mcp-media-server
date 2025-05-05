"""
Local fallback for Pinecone when the cloud service is unavailable.
Implements a minimal vector search using NumPy for emergency operations.
"""
import os
import json
import logging
import numpy as np
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Tuple
from datetime import datetime

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class LocalPineconeFallback:
    """
    Provides local fallback functionality for essential Pinecone operations.
    Uses SQLite to store vectors and NumPy for vector similarity search.
    """
    
    def __init__(self):
        """Initialize the local fallback."""
        # Set up the database path
        self.db_dir = Path(settings.get_absolute_path("fallbacks"))
        self.db_dir.mkdir(exist_ok=True, parents=True)
        self.db_path = self.db_dir / "pinecone_fallback.db"
        
        # Initialize the database
        self._initialize_database()
        
        # Cache for vectors to avoid repeated database lookups
        self.vector_cache = {}
        
        # Initialize client property for API compatibility
        self.client = self
        
        logger.info("Local Pinecone fallback initialized")
    
    def _initialize_database(self):
        """Initialize the SQLite database with required tables."""
        try:
            # Connect to SQLite
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Create tables if they don't exist
            
            # Vectors table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                vector TEXT,
                metadata TEXT,
                namespace TEXT DEFAULT '',
                created_at TEXT
            )
            ''')
            
            # Indexes table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS indexes (
                name TEXT PRIMARY KEY,
                dimension INTEGER,
                metric TEXT,
                created_at TEXT
            )
            ''')
            
            # Create default index
            cursor.execute('''
            INSERT OR IGNORE INTO indexes (name, dimension, metric, created_at)
            VALUES (?, ?, ?, ?)
            ''', ("video-search", 1536, "cosine", datetime.now().isoformat()))
            
            # Commit changes
            conn.commit()
            conn.close()
            
            logger.info("SQLite fallback database for vectors initialized")
        
        except Exception as e:
            logger.error(f"Error initializing fallback vector database: {e}")
    
    def _get_connection(self):
        """Get a database connection."""
        try:
            return sqlite3.connect(str(self.db_path))
        except Exception as e:
            logger.error(f"Error connecting to fallback vector database: {e}")
            raise
    
    def Index(self, name: str):
        """
        Get an index object.
        
        Args:
            name: Name of the index
            
        Returns:
            Index object
        """
        return LocalPineconeIndex(self, name)
    
    def list_indexes(self):
        """
        List all indexes.
        
        Returns:
            List of index information
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM indexes")
            indexes = cursor.fetchall()
            conn.close()
            
            result = []
            for index in indexes:
                result.append({
                    "name": index[0],
                    "dimension": index[1],
                    "metric": index[2],
                    "created_at": index[3]
                })
            
            return result
        except Exception as e:
            logger.error(f"Error listing indexes: {e}")
            return []
    
    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate a text embedding using a local model.
        This is a simplified fallback that creates a random embedding.
        
        Args:
            text: Text to generate embedding for
            
        Returns:
            Embedding vector
        """
        try:
            # In a real implementation, you would use a local embedding model
            # For fallback purposes, we're generating a random vector
            # This is NOT suitable for production but serves as an emergency fallback
            
            # Try to make the vector somewhat consistent for the same text
            import hashlib
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % 10000
            np.random.seed(seed)
            
            # Generate a random vector of the right dimension
            vector = np.random.rand(1536).tolist()
            
            logger.warning("Using random fallback embedding - NOT SUITABLE FOR PRODUCTION")
            return vector
        
        except Exception as e:
            logger.error(f"Error generating fallback embedding: {e}")
            # Return a zero vector as a last resort
            return [0.0] * 1536
    
    def _save_vector(self, id: str, vector: List[float], metadata: Dict[str, Any], namespace: str = ""):
        """
        Save a vector to the database.
        
        Args:
            id: Vector ID
            vector: Vector values
            metadata: Vector metadata
            namespace: Vector namespace
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Convert data to JSON
            vector_json = json.dumps(vector)
            metadata_json = json.dumps(metadata)
            
            # Insert or update the vector
            cursor.execute('''
            INSERT OR REPLACE INTO vectors (id, vector, metadata, namespace, created_at)
            VALUES (?, ?, ?, ?, ?)
            ''', (id, vector_json, metadata_json, namespace, datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
            
            # Update cache
            self.vector_cache[id] = (vector, metadata, namespace)
            
            return True
        
        except Exception as e:
            logger.error(f"Error saving vector: {e}")
            return False
    
    def _get_vector(self, id: str) -> Optional[Tuple[List[float], Dict[str, Any], str]]:
        """
        Get a vector from the database.
        
        Args:
            id: Vector ID
            
        Returns:
            Tuple of (vector, metadata, namespace) or None if not found
        """
        # Check cache first
        if id in self.vector_cache:
            return self.vector_cache[id]
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT vector, metadata, namespace FROM vectors WHERE id = ?
            ''', (id,))
            
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                return None
            
            # Parse JSON
            vector = json.loads(result[0])
            metadata = json.loads(result[1])
            namespace = result[2]
            
            # Update cache
            self.vector_cache[id] = (vector, metadata, namespace)
            
            return (vector, metadata, namespace)
        
        except Exception as e:
            logger.error(f"Error getting vector: {e}")
            return None
    
    def _get_all_vectors(self, namespace: str = "") -> Dict[str, Tuple[List[float], Dict[str, Any]]]:
        """
        Get all vectors from the database.
        
        Args:
            namespace: Vector namespace filter
            
        Returns:
            Dict mapping IDs to (vector, metadata) tuples
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if namespace:
                cursor.execute('''
                SELECT id, vector, metadata FROM vectors WHERE namespace = ?
                ''', (namespace,))
            else:
                cursor.execute('''
                SELECT id, vector, metadata FROM vectors
                ''')
            
            results = cursor.fetchall()
            conn.close()
            
            vectors = {}
            for row in results:
                id = row[0]
                vector = json.loads(row[1])
                metadata = json.loads(row[2])
                vectors[id] = (vector, metadata)
                
                # Update cache
                self.vector_cache[id] = (vector, metadata, namespace)
            
            return vectors
        
        except Exception as e:
            logger.error(f"Error getting all vectors: {e}")
            return {}
    
    def _delete_vector(self, id: str) -> bool:
        """
        Delete a vector from the database.
        
        Args:
            id: Vector ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            DELETE FROM vectors WHERE id = ?
            ''', (id,))
            
            conn.commit()
            conn.close()
            
            # Remove from cache
            if id in self.vector_cache:
                del self.vector_cache[id]
            
            return True
        
        except Exception as e:
            logger.error(f"Error deleting vector: {e}")
            return False
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        Args:
            a: First vector
            b: Second vector
            
        Returns:
            Cosine similarity
        """
        try:
            a_array = np.array(a)
            b_array = np.array(b)
            
            # Normalize for cosine similarity
            a_norm = np.linalg.norm(a_array)
            b_norm = np.linalg.norm(b_array)
            
            if a_norm == 0 or b_norm == 0:
                return 0.0
            
            return np.dot(a_array, b_array) / (a_norm * b_norm)
        
        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {e}")
            return 0.0
    
    def _euclidean_similarity(self, a: List[float], b: List[float]) -> float:
        """
        Calculate similarity based on Euclidean distance.
        
        Args:
            a: First vector
            b: Second vector
            
        Returns:
            Similarity score (1 / (1 + distance))
        """
        try:
            a_array = np.array(a)
            b_array = np.array(b)
            
            distance = np.linalg.norm(a_array - b_array)
            # Convert distance to similarity (closer means more similar)
            return 1.0 / (1.0 + distance)
        
        except Exception as e:
            logger.error(f"Error calculating euclidean similarity: {e}")
            return 0.0
    
    def _calculate_similarity(self, a: List[float], b: List[float], metric: str = "cosine") -> float:
        """
        Calculate similarity between two vectors.
        
        Args:
            a: First vector
            b: Second vector
            metric: Similarity metric (cosine, euclidean)
            
        Returns:
            Similarity score
        """
        if metric == "cosine":
            return self._cosine_similarity(a, b)
        elif metric == "euclidean":
            return self._euclidean_similarity(a, b)
        else:
            # Default to cosine
            return self._cosine_similarity(a, b)


class LocalPineconeIndex:
    """
    Local implementation of a Pinecone index.
    """
    
    def __init__(self, client: LocalPineconeFallback, name: str):
        """
        Initialize the index.
        
        Args:
            client: LocalPineconeFallback instance
            name: Name of the index
        """
        self.client = client
        self.name = name
        
        # Get index configuration
        self.config = self._get_config()
    
    def _get_config(self) -> Dict[str, Any]:
        """
        Get index configuration.
        
        Returns:
            Dict containing index configuration
        """
        try:
            conn = self.client._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT dimension, metric FROM indexes WHERE name = ?
            ''', (self.name,))
            
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                # Create default configuration
                return {
                    "dimension": 1536,
                    "metric": "cosine"
                }
            
            return {
                "dimension": result[0],
                "metric": result[1]
            }
        
        except Exception as e:
            logger.error(f"Error getting index configuration: {e}")
            # Return default configuration
            return {
                "dimension": 1536,
                "metric": "cosine"
            }
    
    def upsert(self, vectors: List[Dict[str, Any]], namespace: str = "") -> Dict[str, Any]:
        """
        Insert or update vectors.
        
        Args:
            vectors: List of vector objects
            namespace: Namespace for the vectors
            
        Returns:
            Dict containing operation status
        """
        try:
            upserted_count = 0
            
            for vector in vectors:
                id = vector.get("id")
                values = vector.get("values")
                metadata = vector.get("metadata", {})
                
                if not id or not values:
                    logger.warning("Invalid vector object, skipping")
                    continue
                
                # Save the vector
                if self.client._save_vector(id, values, metadata, namespace):
                    upserted_count += 1
            
            return {
                "upsertedCount": upserted_count
            }
        
        except Exception as e:
            logger.error(f"Error upserting vectors: {e}")
            return {
                "upsertedCount": 0,
                "error": str(e)
            }
    
    def fetch(self, ids: List[str], namespace: str = "") -> Dict[str, Any]:
        """
        Fetch vectors by IDs.
        
        Args:
            ids: List of vector IDs
            namespace: Namespace for the vectors
            
        Returns:
            Dict containing fetched vectors
        """
        try:
            vectors = {}
            
            for id in ids:
                result = self.client._get_vector(id)
                
                if result:
                    vector, metadata, vector_namespace = result
                    
                    # Check namespace if specified
                    if namespace and vector_namespace != namespace:
                        continue
                    
                    vectors[id] = {
                        "id": id,
                        "values": vector,
                        "metadata": metadata
                    }
            
            return {
                "vectors": vectors,
                "namespace": namespace
            }
        
        except Exception as e:
            logger.error(f"Error fetching vectors: {e}")
            return {
                "vectors": {},
                "namespace": namespace,
                "error": str(e)
            }
    
    def delete(self, ids: List[str] = None, namespace: str = "", delete_all: bool = False) -> Dict[str, Any]:
        """
        Delete vectors.
        
        Args:
            ids: List of vector IDs to delete
            namespace: Namespace for the vectors
            delete_all: Whether to delete all vectors in the namespace
            
        Returns:
            Dict containing operation status
        """
        try:
            deleted_count = 0
            
            if delete_all:
                # Delete all vectors in the namespace
                conn = self.client._get_connection()
                cursor = conn.cursor()
                
                if namespace:
                    cursor.execute('''
                    DELETE FROM vectors WHERE namespace = ?
                    ''', (namespace,))
                else:
                    cursor.execute('''
                    DELETE FROM vectors
                    ''')
                
                deleted_count = cursor.rowcount
                conn.commit()
                conn.close()
                
                # Clear cache
                self.client.vector_cache.clear()
            
            elif ids:
                # Delete specific vectors
                for id in ids:
                    if self.client._delete_vector(id):
                        deleted_count += 1
            
            return {
                "deletedCount": deleted_count
            }
        
        except Exception as e:
            logger.error(f"Error deleting vectors: {e}")
            return {
                "deletedCount": 0,
                "error": str(e)
            }
    
    def query(
        self,
        vector: List[float],
        top_k: int = 10,
        namespace: str = "",
        include_metadata: bool = True,
        filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Query for similar vectors.
        
        Args:
            vector: Query vector
            top_k: Number of results to return
            namespace: Namespace for the vectors
            include_metadata: Whether to include metadata in results
            filter: Metadata filter
            
        Returns:
            Dict containing query results
        """
        try:
            # Get all vectors in the namespace
            all_vectors = self.client._get_all_vectors(namespace)
            
            if not all_vectors:
                return {
                    "matches": [],
                    "namespace": namespace
                }
            
            # Calculate similarities
            similarities = []
            for id, (vec, metadata) in all_vectors.items():
                # Apply filter if provided
                if filter and not self._match_filter(metadata, filter):
                    continue
                
                similarity = self.client._calculate_similarity(vector, vec, self.config.get("metric", "cosine"))
                similarities.append((id, similarity, metadata))
            
            # Sort by similarity (highest first)
            similarities.sort(key=lambda x: x[1], reverse=True)
            
            # Return top_k results
            matches = []
            for id, score, metadata in similarities[:top_k]:
                match = {
                    "id": id,
                    "score": score
                }
                
                if include_metadata:
                    match["metadata"] = metadata
                
                matches.append(match)
            
            return {
                "matches": matches,
                "namespace": namespace
            }
        
        except Exception as e:
            logger.error(f"Error querying vectors: {e}")
            return {
                "matches": [],
                "namespace": namespace,
                "error": str(e)
            }
    
    def _match_filter(self, metadata: Dict[str, Any], filter: Dict[str, Any]) -> bool:
        """
        Check if metadata matches a filter.
        
        Args:
            metadata: Vector metadata
            filter: Filter to apply
            
        Returns:
            True if metadata matches filter, False otherwise
        """
        # This is a simplified implementation that only handles basic equality filters
        for key, value in filter.items():
            if key not in metadata or metadata[key] != value:
                return False
        
        return True
