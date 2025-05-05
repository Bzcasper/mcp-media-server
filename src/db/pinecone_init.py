"""
Pinecone vector database initialization and client.
"""
import os
import logging
from functools import lru_cache
from typing import Dict, Any, List, Optional, Union
import time

from pinecone import Pinecone, ServerlessSpec, PodSpec
import openai
import numpy as np

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

class PineconeClient:
    """
    Pinecone client for vector search operations.
    
    Implements the Singleton pattern to ensure only one client is created.
    """
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Create a new instance if one doesn't exist."""
        if cls._instance is None:
            cls._instance = super(PineconeClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the Pinecone client if not already initialized."""
        if self._initialized:
            return
            
        self.settings = get_settings()
        
        try:
            # Initialize Pinecone client
            self.client = Pinecone(api_key=self.settings.PINECONE_API_KEY)
            
            # Set OpenAI API key for embeddings
            openai.api_key = self.settings.OPENAI_API_KEY
            
            self._initialized = True
            logger.info("Pinecone client initialized successfully")
        except Exception as e:
            self._initialized = False
            logger.error(f"Failed to initialize Pinecone client: {e}")
            raise
    
    async def init_indexes(self):
        """Initialize Pinecone indexes if they don't exist."""
        try:
            # Check if video-search index exists
            indexes = self.client.list_indexes()
            index_names = [index.name for index in indexes]
            
            if "video-search" not in index_names:
                logger.info("Creating 'video-search' index in Pinecone")
                
                # Create a serverless index for video search
                self.client.create_index(
                    name="video-search",
                    dimension=1536,  # OpenAI embedding dimension
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud="aws", 
                        region="us-west-2"
                    )
                )
                
                # Wait for index to be ready
                time.sleep(10)
                logger.info("'video-search' index created successfully")
            else:
                logger.info("'video-search' index already exists")
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to initialize Pinecone indexes: {e}")
            return False
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate an embedding for the given text using OpenAI."""
        try:
            response = await openai.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            
            embedding = response.data[0].embedding
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise
    
    async def insert_vector(
        self,
        id: str,
        vector: List[float],
        metadata: Dict[str, Any],
        namespace: str = ""
    ) -> bool:
        """
        Insert a vector into the Pinecone index.
        
        Args:
            id: Unique identifier for the vector
            vector: The embedding vector
            metadata: Metadata to store with the vector
            namespace: Namespace for the vector
            
        Returns:
            bool: True if the insertion was successful
        """
        try:
            index = self.client.Index("video-search")
            
            # Upsert the vector
            upsert_response = index.upsert(
                vectors=[
                    {
                        "id": id,
                        "values": vector,
                        "metadata": metadata
                    }
                ],
                namespace=namespace
            )
            
            logger.info(f"Vector inserted successfully: {id}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to insert vector: {e}")
            return False
    
    async def search(
        self,
        query_vector: List[float],
        namespace: str = "",
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> Dict[str, Any]:
        """
        Search for vectors similar to the query vector.
        
        Args:
            query_vector: The query embedding vector
            namespace: Namespace to search in
            top_k: Number of results to return
            filter: Filter to apply to the search
            include_metadata: Whether to include metadata in the results
            
        Returns:
            Dict containing search results
        """
        try:
            index = self.client.Index("video-search")
            
            # Perform the search
            search_response = index.query(
                vector=query_vector,
                namespace=namespace,
                top_k=top_k,
                include_metadata=include_metadata,
                filter=filter
            )
            
            return search_response
        
        except Exception as e:
            logger.error(f"Failed to search vectors: {e}")
            raise
    
    async def search_by_text(
        self,
        query_text: str,
        namespace: str = "",
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> Dict[str, Any]:
        """
        Search for vectors similar to the query text.
        
        Args:
            query_text: The text to generate an embedding for and search with
            namespace: Namespace to search in
            top_k: Number of results to return
            filter: Filter to apply to the search
            include_metadata: Whether to include metadata in the results
            
        Returns:
            Dict containing search results
        """
        try:
            # Generate embedding for the query text
            query_vector = await self.generate_embedding(query_text)
            
            # Perform the search
            return await self.search(
                query_vector=query_vector,
                namespace=namespace,
                top_k=top_k,
                filter=filter,
                include_metadata=include_metadata
            )
        
        except Exception as e:
            logger.error(f"Failed to search by text: {e}")
            raise
    
    async def delete_vector(self, id: str, namespace: str = "") -> bool:
        """
        Delete a vector from the Pinecone index.
        
        Args:
            id: Unique identifier for the vector
            namespace: Namespace for the vector
            
        Returns:
            bool: True if the deletion was successful
        """
        try:
            index = self.client.Index("video-search")
            
            # Delete the vector
            delete_response = index.delete(
                ids=[id],
                namespace=namespace
            )
            
            logger.info(f"Vector deleted successfully: {id}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete vector: {e}")
            return False


@lru_cache()
def get_pinecone_client() -> PineconeClient:
    """Get a cached Pinecone client instance."""
    return PineconeClient()


async def init_pinecone():
    """Initialize Pinecone database."""
    client = get_pinecone_client()
    await client.init_indexes()
    return client
