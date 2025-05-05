"""
Supabase database initialization and client.
"""
import os
from typing import Dict, Any, Optional, List
import logging
from functools import lru_cache
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

class SupabaseClient:
    """
    Supabase client for interacting with the Supabase database.
    
    Implements the Singleton pattern to ensure only one client is created.
    """
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Create a new instance if one doesn't exist."""
        if cls._instance is None:
            cls._instance = super(SupabaseClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the Supabase client if not already initialized."""
        if self._initialized:
            return
            
        self.settings = get_settings()
        
        try:
            # Initialize Supabase client with options
            self.client = create_client(
                self.settings.SUPABASE_URL, 
                self.settings.SUPABASE_KEY,
                options=ClientOptions(
                    schema="public",
                    headers={"x-application-name": "mcp-media-server"},
                    auto_refresh_token=True,
                    persist_session=True,
                )
            )
            self._initialized = True
            logger.info("Supabase client initialized successfully")
        except Exception as e:
            self._initialized = False
            logger.error(f"Failed to initialize Supabase client: {e}")
            raise
    
    async def init_schema(self):
        """Initialize database schema if it doesn't exist."""
        # Check if the videos table exists, if not create it
        try:
            # Define SQL for creating the necessary tables if they don't exist
            sql = """
            -- Create videos table
            CREATE TABLE IF NOT EXISTS videos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                tags JSONB DEFAULT '[]'::jsonb,
                duration FLOAT,
                size_bytes BIGINT,
                format TEXT,
                width INTEGER,
                height INTEGER,
                thumbnail_path TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
                metadata JSONB DEFAULT '{}'::jsonb,
                status TEXT DEFAULT 'processed'
            );

            -- Create video_analysis table
            CREATE TABLE IF NOT EXISTS video_analysis (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
                analysis_type TEXT NOT NULL,
                results JSONB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            
            -- Create processing_jobs table
            CREATE TABLE IF NOT EXISTS processing_jobs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                completed_at TIMESTAMP WITH TIME ZONE,
                params JSONB DEFAULT '{}'::jsonb,
                error TEXT,
                webhook_sent BOOLEAN DEFAULT FALSE
            );
            
            -- Create webhook_events table
            CREATE TABLE IF NOT EXISTS webhook_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID REFERENCES processing_jobs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                sent_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                endpoint TEXT NOT NULL
            );
            
            -- Create user_api_keys table
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
                api_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                permissions JSONB DEFAULT '[]'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                expires_at TIMESTAMP WITH TIME ZONE,
                last_used_at TIMESTAMP WITH TIME ZONE
            );
            
            -- Set up RLS (Row Level Security) policies
            ALTER TABLE videos ENABLE ROW LEVEL SECURITY;
            ALTER TABLE video_analysis ENABLE ROW LEVEL SECURITY;
            ALTER TABLE processing_jobs ENABLE ROW LEVEL SECURITY;
            
            -- Create policies
            CREATE POLICY "Users can view their own videos"
                ON videos FOR SELECT
                USING (auth.uid() = user_id);
                
            CREATE POLICY "Users can insert their own videos"
                ON videos FOR INSERT
                WITH CHECK (auth.uid() = user_id);
                
            CREATE POLICY "Users can update their own videos"
                ON videos FOR UPDATE
                USING (auth.uid() = user_id);
                
            CREATE POLICY "Users can delete their own videos"
                ON videos FOR DELETE
                USING (auth.uid() = user_id);
            
            -- Create functions
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            -- Create triggers
            DROP TRIGGER IF EXISTS update_videos_updated_at ON videos;
            CREATE TRIGGER update_videos_updated_at
                BEFORE UPDATE ON videos
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            
            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_videos_user_id ON videos(user_id);
            CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos(created_at);
            CREATE INDEX IF NOT EXISTS idx_processing_jobs_video_id ON processing_jobs(video_id);
            CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_video_analysis_video_id ON video_analysis(video_id);
            """
            
            # Execute the SQL
            result = await self.client.rpc("exec_sql", {"query": sql}).execute()
            
            if hasattr(result, 'error') and result.error:
                raise Exception(f"Error initializing schema: {result.error}")
                
            logger.info("Supabase schema initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Supabase schema: {e}")
            return False
    
    def table(self, table_name: str):
        """Get a Supabase table reference."""
        return self.client.table(table_name)
    
    def auth(self):
        """Get the Supabase auth client."""
        return self.client.auth
    
    def storage(self):
        """Get the Supabase storage client."""
        return self.client.storage
    
    def rpc(self, fn_name: str, params: Dict[str, Any]):
        """Call a Supabase RPC function."""
        return self.client.rpc(fn_name, params)


@lru_cache()
def get_supabase_client() -> SupabaseClient:
    """Get a cached Supabase client instance."""
    return SupabaseClient()


async def init_supabase():
    """Initialize Supabase database."""
    client = get_supabase_client()
    await client.init_schema()
    return client
