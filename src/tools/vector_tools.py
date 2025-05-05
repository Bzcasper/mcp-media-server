"""
Vector search tools for semantic search using Pinecone.
"""
import os
import logging
import json
import uuid
from typing import Dict, Any, List, Optional, Union

from src.core.server import mcp_server
from src.config.settings import get_settings
from src.utils.cache import Cache
from src.db.pinecone_init import get_pinecone_client
from src.db.supabase_init import get_supabase_client

logger = logging.getLogger(__name__)
settings = get_settings()
cache = Cache()

@mcp_server.register_tool
async def generate_video_embedding(
    video_id: str,
    include_audio_transcription: bool = False
) -> Dict[str, Any]:
    """
    Generate embedding for a video and store it in Pinecone.
    
    Args:
        video_id: ID of the video in Supabase
        include_audio_transcription: Whether to include audio transcription in the embedding
        
    Returns:
        Dict containing the status of the operation
    """
    try:
        # Get the video data from Supabase
        supabase = get_supabase_client()
        response = supabase.table("videos") \
            .select("*") \
            .eq("id", video_id) \
            .limit(1) \
            .execute()
        
        if not response.data:
            raise ValueError(f"Video with ID {video_id} not found")
        
        video_data = response.data[0]
        
        # Extract text for embedding
        title = video_data.get("title", "")
        description = video_data.get("description", "")
        tags = video_data.get("tags", [])
        
        # Combine text for embedding
        embedding_text = f"Title: {title}\nDescription: {description}\nTags: {', '.join(tags)}"
        
        # Add audio transcription if requested and available
        if include_audio_transcription:
            # Get transcription from video_analysis table
            transcription_response = supabase.table("video_analysis") \
                .select("*") \
                .eq("video_id", video_id) \
                .eq("analysis_type", "transcription") \
                .limit(1) \
                .execute()
            
            if transcription_response.data:
                transcription = transcription_response.data[0].get("results", {}).get("text", "")
                if transcription:
                    embedding_text += f"\nTranscription: {transcription}"
        
        # Generate embedding
        pinecone_client = get_pinecone_client()
        embedding = await pinecone_client.generate_embedding(embedding_text)
        
        # Store in Pinecone
        metadata = {
            "title": title,
            "description": description,
            "tags": tags,
            "file_path": video_data.get("file_path"),
            "thumbnail_path": video_data.get("thumbnail_path"),
            "duration": video_data.get("duration"),
            "format": video_data.get("format")
        }
        
        success = await pinecone_client.insert_vector(
            id=video_id,
            vector=embedding,
            metadata=metadata
        )
        
        if success:
            # Update the video record to indicate that the embedding has been generated
            supabase.table("videos") \
                .update({"has_embedding": True}) \
                .eq("id", video_id) \
                .execute()
            
            return {
                "status": "success",
                "video_id": video_id,
                "message": "Embedding generated and stored successfully"
            }
        else:
            return {
                "status": "error",
                "video_id": video_id,
                "message": "Failed to store embedding in Pinecone"
            }
    
    except Exception as e:
        logger.error(f"Error generating video embedding: {e}")
        return {
            "status": "error",
            "video_id": video_id,
            "message": f"Failed to generate embedding: {str(e)}"
        }


@mcp_server.register_tool
async def search_videos_by_text(
    query: str,
    limit: int = 10,
    filter: Optional[Dict[str, Any]] = None,
    namespace: str = ""
) -> Dict[str, Any]:
    """
    Search for videos using natural language.
    
    Args:
        query: Text query to search for
        limit: Maximum number of results to return
        filter: Optional filter to apply to the search
        namespace: Optional namespace to search in
        
    Returns:
        Dict containing search results
    """
    try:
        # Generate cache key
        cache_key = f"vector_search_{query}_{limit}_{namespace}_{json.dumps(filter or {})}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            logger.info(f"Using cached vector search results for '{query}'")
            return cached_result
        
        # Perform the search
        pinecone_client = get_pinecone_client()
        search_results = await pinecone_client.search_by_text(
            query_text=query,
            namespace=namespace,
            top_k=limit,
            filter=filter,
            include_metadata=True
        )
        
        # Process the results
        videos = []
        
        if "matches" in search_results:
            # Get all video IDs from the results
            video_ids = [match["id"] for match in search_results["matches"]]
            
            if video_ids:
                # Get the full video data from Supabase
                supabase = get_supabase_client()
                response = supabase.table("videos") \
                    .select("*") \
                    .in_("id", video_ids) \
                    .execute()
                
                # Create a mapping of ID to full data
                video_map = {video["id"]: video for video in response.data}
                
                # Merge the search results with the full data
                for match in search_results["matches"]:
                    video_id = match["id"]
                    score = match["score"]
                    metadata = match.get("metadata", {})
                    
                    # Get full data if available, otherwise use metadata
                    video_data = video_map.get(video_id, metadata)
                    
                    # Add the score from Pinecone
                    video_data["score"] = score
                    
                    videos.append(video_data)
        
        # Create the result
        result = {
            "query": query,
            "total_results": len(videos),
            "videos": videos
        }
        
        # Cache the result
        cache.set(cache_key, result, expire_in=3600)  # Cache for 1 hour
        
        return result
    
    except Exception as e:
        logger.error(f"Error searching videos by text: {e}")
        return {
            "status": "error",
            "message": f"Failed to search videos: {str(e)}",
            "query": query,
            "total_results": 0,
            "videos": []
        }


@mcp_server.register_tool
async def batch_generate_embeddings(
    video_ids: List[str] = None,
    limit: int = 100,
    include_audio_transcription: bool = False
) -> Dict[str, Any]:
    """
    Generate embeddings for multiple videos.
    
    Args:
        video_ids: List of video IDs to process. If None, processes videos without embeddings.
        limit: Maximum number of videos to process if video_ids is None
        include_audio_transcription: Whether to include audio transcription in the embeddings
        
    Returns:
        Dict containing the status of the operation
    """
    try:
        batch_id = str(uuid.uuid4())
        supabase = get_supabase_client()
        
        # If no video IDs provided, get videos without embeddings
        if not video_ids:
            response = supabase.table("videos") \
                .select("id") \
                .eq("has_embedding", False) \
                .limit(limit) \
                .execute()
            
            video_ids = [video["id"] for video in response.data]
        
        if not video_ids:
            return {
                "status": "success",
                "batch_id": batch_id,
                "message": "No videos found to process",
                "total_processed": 0,
                "successful": 0,
                "failed": 0,
                "results": []
            }
        
        # Process each video
        results = []
        successful = 0
        failed = 0
        
        for video_id in video_ids:
            try:
                # Generate embedding
                result = await generate_video_embedding(
                    video_id=video_id,
                    include_audio_transcription=include_audio_transcription
                )
                
                results.append(result)
                
                if result["status"] == "success":
                    successful += 1
                else:
                    failed += 1
                
            except Exception as e:
                logger.error(f"Error generating embedding for video {video_id}: {e}")
                results.append({
                    "status": "error",
                    "video_id": video_id,
                    "message": f"Failed to generate embedding: {str(e)}"
                })
                failed += 1
        
        return {
            "status": "complete",
            "batch_id": batch_id,
            "message": f"Processed {len(video_ids)} videos",
            "total_processed": len(video_ids),
            "successful": successful,
            "failed": failed,
            "results": results
        }
    
    except Exception as e:
        logger.error(f"Error in batch_generate_embeddings: {e}")
        return {
            "status": "error",
            "batch_id": batch_id if 'batch_id' in locals() else str(uuid.uuid4()),
            "message": f"Failed to process batch: {str(e)}",
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "results": []
        }


@mcp_server.register_tool
async def similar_videos(
    video_id: str,
    limit: int = 10,
    namespace: str = ""
) -> Dict[str, Any]:
    """
    Find videos similar to a given video.
    
    Args:
        video_id: ID of the video to find similar videos for
        limit: Maximum number of results to return
        namespace: Optional namespace to search in
        
    Returns:
        Dict containing search results
    """
    try:
        # Generate cache key
        cache_key = f"similar_videos_{video_id}_{limit}_{namespace}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            logger.info(f"Using cached similar videos results for '{video_id}'")
            return cached_result
        
        # Get the video's embedding from Pinecone
        pinecone_client = get_pinecone_client()
        index = pinecone_client.client.Index("video-search")
        
        fetch_response = index.fetch(ids=[video_id], namespace=namespace)
        
        if not fetch_response.get("vectors") or video_id not in fetch_response["vectors"]:
            raise ValueError(f"Video with ID {video_id} not found in Pinecone")
        
        # Get the embedding vector
        vector = fetch_response["vectors"][video_id]["values"]
        
        # Search for similar videos
        search_response = index.query(
            vector=vector,
            namespace=namespace,
            top_k=limit + 1,  # Add 1 to account for the query video itself
            include_metadata=True
        )
        
        # Filter out the query video itself
        matches = [match for match in search_response["matches"] if match["id"] != video_id]
        
        # Get the full video data from Supabase
        if matches:
            similar_video_ids = [match["id"] for match in matches]
            
            # Get the full video data from Supabase
            supabase = get_supabase_client()
            response = supabase.table("videos") \
                .select("*") \
                .in_("id", similar_video_ids) \
                .execute()
            
            # Create a mapping of ID to full data
            video_map = {video["id"]: video for video in response.data}
            
            # Prepare the results
            videos = []
            for match in matches:
                video_id = match["id"]
                score = match["score"]
                metadata = match.get("metadata", {})
                
                # Get full data if available, otherwise use metadata
                video_data = video_map.get(video_id, metadata)
                
                # Add the score from Pinecone
                video_data["score"] = score
                
                videos.append(video_data)
        else:
            videos = []
        
        # Create the result
        result = {
            "video_id": video_id,
            "total_results": len(videos),
            "videos": videos
        }
        
        # Cache the result
        cache.set(cache_key, result, expire_in=3600)  # Cache for 1 hour
        
        return result
    
    except Exception as e:
        logger.error(f"Error finding similar videos: {e}")
        return {
            "status": "error",
            "video_id": video_id,
            "message": f"Failed to find similar videos: {str(e)}",
            "total_results": 0,
            "videos": []
        }
