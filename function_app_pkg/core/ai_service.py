"""
AI Service - OpenAI client and utilities
"""

import os
from openai import OpenAI
from dotenv import load_dotenv  
load_dotenv()  # Load environment variables from .env file
_client = None

def get_openai_client() -> OpenAI:
    """
    Get or create OpenAI client instance (singleton pattern)
    """
    global _client
    
    if _client is None:
        api_key = os.getenv('AZURE_OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        
        _client = OpenAI(api_key=api_key)
    
    return _client