"""
SEMANTIC SEARCH ENGINE
======================
Vector embeddings for intelligent document & violation search

REVENUE IMPACT: Enterprise feature (+$500/month)

File: function_app_pkg/core/semantic_search.py
"""

import logging
import os
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class SemanticSearchEngine:
    """
    Semantic search using Azure OpenAI embeddings
    
    Features:
    - Find similar documents by content
    - Semantic violation search
    - Auto-cluster documents by topic
    - Smart recommendations
    """
    
    def __init__(self):
        self.openai_client = None
        self.embedding_model = "text-embedding-3-small"  # 1536 dimensions, fast
        self.embedding_dim = 1536
        self._init_openai()
    
    def _init_openai(self):
        """Initialize Azure OpenAI for embeddings"""
        try:
            from openai import AzureOpenAI
            
            key = os.getenv('AZURE_OPENAI_API_KEY')
            endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
            api_version = os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview')
            
            if not key or not endpoint:
                logger.warning("⚠️ Azure OpenAI not configured - semantic search disabled")
                return
            
            self.openai_client = AzureOpenAI(
                api_key=key,
                api_version=api_version,
                azure_endpoint=endpoint
            )
            
            logger.info(f"✅ Semantic search ready (model: {self.embedding_model})")
            
        except Exception as e:
            logger.error(f"❌ Semantic search init failed: {e}")
    
    def embed_text(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding vector for text
        
        Args:
            text: Text to embed (max 8191 tokens)
        
        Returns:
            1536-dimensional embedding vector or None
        """
        if not self.openai_client:
            return None
        
        try:
            # Truncate if too long
            if len(text) > 30000:  # ~8k tokens
                text = text[:30000]
            
            response = self.openai_client.embeddings.create(
                input=text,
                model=self.embedding_model
            )
            
            embedding = response.data[0].embedding
            
            logger.debug(f"✅ Generated embedding: {len(embedding)} dimensions")
            return embedding
            
        except Exception as e:
            logger.error(f"❌ Embedding generation failed: {e}")
            return None
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            
            dot_product = np.dot(v1, v2)
            norm_v1 = np.linalg.norm(v1)
            norm_v2 = np.linalg.norm(v2)
            
            if norm_v1 == 0 or norm_v2 == 0:
                return 0.0
            
            similarity = dot_product / (norm_v1 * norm_v2)
            return float(similarity)
            
        except Exception as e:
            logger.error(f"❌ Similarity calculation failed: {e}")
            return 0.0
    
    def find_similar_documents(
        self,
        query_text: str,
        organization_id: str,
        top_k: int = 10,
        min_similarity: float = 0.7
    ) -> List[Dict]:
        """
        Find documents similar to query text
        
        Args:
            query_text: Search query (semantic, not keyword)
            organization_id: Filter to organization
            top_k: Number of results
            min_similarity: Minimum cosine similarity threshold
        
        Returns:
            List of documents with similarity scores
        """
        if not self.openai_client:
            logger.warning("Semantic search not available")
            return []
        
        try:
            # Generate query embedding
            query_embedding = self.embed_text(query_text)
            if not query_embedding:
                return []
            
            # Get all documents (in production, use vector DB)
            from .database import get_db
            db = get_db()
            container = db.get_container('documents')
            
            query = "SELECT c.id, c.filename, c.text_content, c.extracted_text, c.risk_score, c.violations_count FROM c WHERE c.organization_id = @org_id AND c.type = 'document'"
            docs = list(container.query_items(
                query=query,
                parameters=[{"name": "@org_id", "value": organization_id}],
                partition_key=organization_id
            ))
            
            logger.info(f"🔍 Comparing query against {len(docs)} documents")
            
            # Calculate similarities
            results = []
            
            for doc in docs:
                # Get document text
                doc_text = doc.get('text_content') or doc.get('extracted_text') or ''
                if not doc_text or len(doc_text) < 50:
                    continue
                
                # Generate document embedding
                doc_embedding = self.embed_text(doc_text[:5000])  # First 5k chars for speed
                if not doc_embedding:
                    continue
                
                # Calculate similarity
                similarity = self.cosine_similarity(query_embedding, doc_embedding)
                
                if similarity >= min_similarity:
                    results.append({
                        'document_id': doc.get('id'),
                        'filename': doc.get('filename'),
                        'similarity_score': round(similarity, 3),
                        'risk_score': doc.get('risk_score', 0),
                        'violations_count': doc.get('violations_count', 0),
                        'match_type': 'semantic'
                    })
            
            # Sort by similarity
            results.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            logger.info(f"✅ Found {len(results)} similar documents")
            
            return results[:top_k]
            
        except Exception as e:
            logger.error(f"❌ Similar document search failed: {e}")
            return []
    
    def find_similar_violations(
        self,
        violation_text: str,
        organization_id: str,
        top_k: int = 5
    ) -> List[Dict]:
        """
        Find documents with similar violations
        
        Args:
            violation_text: Text of violation to match
            organization_id: Filter to organization
            top_k: Number of results
        
        Returns:
            List of documents with similar violations
        """
        if not self.openai_client:
            return []
        
        try:
            # Generate violation embedding
            violation_embedding = self.embed_text(violation_text)
            if not violation_embedding:
                return []
            
            # Get all documents with violations
            from .database import get_db
            db = get_db()
            container = db.get_container('documents')
            
            query = """
            SELECT c.id, c.filename, c.violations, c.risk_score 
            FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'document'
            AND ARRAY_LENGTH(c.violations) > 0
            """
            
            docs = list(container.query_items(
                query=query,
                parameters=[{"name": "@org_id", "value": organization_id}],
                partition_key=organization_id
            ))
            
            results = []
            
            for doc in docs:
                violations = doc.get('violations', [])
                
                for v in violations:
                    v_text = v.get('matched_text', '') or v.get('ai_reasoning', '')
                    if not v_text or len(v_text) < 10:
                        continue
                    
                    # Embed violation
                    v_embedding = self.embed_text(v_text)
                    if not v_embedding:
                        continue
                    
                    # Calculate similarity
                    similarity = self.cosine_similarity(violation_embedding, v_embedding)
                    
                    if similarity >= 0.75:  # High threshold for violations
                        results.append({
                            'document_id': doc.get('id'),
                            'filename': doc.get('filename'),
                            'violation_text': v_text[:200],
                            'violation_category': v.get('category'),
                            'similarity_score': round(similarity, 3),
                            'risk_score': doc.get('risk_score', 0)
                        })
            
            # Sort by similarity
            results.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            logger.info(f"✅ Found {len(results)} similar violations")
            
            return results[:top_k]
            
        except Exception as e:
            logger.error(f"❌ Similar violation search failed: {e}")
            return []
    
    def cluster_documents(
        self,
        organization_id: str,
        num_clusters: int = 5
    ) -> Dict[str, List[str]]:
        """
        Auto-cluster documents by semantic similarity
        
        Args:
            organization_id: Filter to organization
            num_clusters: Number of clusters to create
        
        Returns:
            Dict mapping cluster_id to list of document_ids
        """
        if not self.openai_client:
            return {}
        
        try:
            # Get all documents
            from .database import get_db
            db = get_db()
            container = db.get_container('documents')
            
            query = "SELECT c.id, c.text_content, c.extracted_text FROM c WHERE c.organization_id = @org_id AND c.type = 'document'"
            docs = list(container.query_items(
                query=query,
                parameters=[{"name": "@org_id", "value": organization_id}],
                partition_key=organization_id
            ))
            
            if len(docs) < num_clusters:
                logger.warning(f"Not enough documents ({len(docs)}) for {num_clusters} clusters")
                return {}
            
            # Generate embeddings
            doc_embeddings = []
            doc_ids = []
            
            for doc in docs:
                text = (doc.get('text_content') or doc.get('extracted_text') or '')[:5000]
                if len(text) < 50:
                    continue
                
                embedding = self.embed_text(text)
                if embedding:
                    doc_embeddings.append(embedding)
                    doc_ids.append(doc.get('id'))
            
            if len(doc_embeddings) < num_clusters:
                logger.warning(f"Not enough valid embeddings for clustering")
                return {}
            
            # Simple K-means clustering
            from sklearn.cluster import KMeans
            
            X = np.array(doc_embeddings)
            kmeans = KMeans(n_clusters=num_clusters, random_state=42)
            cluster_labels = kmeans.fit_predict(X)
            
            # Group documents by cluster
            clusters = {}
            for doc_id, cluster_id in zip(doc_ids, cluster_labels):
                cluster_key = f"cluster_{cluster_id}"
                if cluster_key not in clusters:
                    clusters[cluster_key] = []
                clusters[cluster_key].append(doc_id)
            
            logger.info(f"✅ Clustered {len(doc_ids)} documents into {num_clusters} groups")
            
            return clusters
            
        except ImportError:
            logger.error("scikit-learn not installed - clustering unavailable")
            return {}
        except Exception as e:
            logger.error(f"❌ Document clustering failed: {e}")
            return {}
    
    def recommend_similar_approved_docs(
        self,
        document_id: str,
        organization_id: str,
        top_k: int = 5
    ) -> List[Dict]:
        """
        Recommend similar approved documents (for guidance)
        
        Args:
            document_id: Current document
            organization_id: Organization filter
            top_k: Number of recommendations
        
        Returns:
            List of similar approved documents
        """
        try:
            # Get current document
            from .database import get_document
            doc = get_document(document_id, organization_id)
            
            if not doc:
                return []
            
            doc_text = doc.get('text_content') or doc.get('extracted_text') or ''
            if not doc_text:
                return []
            
            # Embed current document
            doc_embedding = self.embed_text(doc_text[:5000])
            if not doc_embedding:
                return []
            
            # Get approved documents
            from .database import get_db
            db = get_db()
            container = db.get_container('documents')
            
            query = """
            SELECT c.id, c.filename, c.text_content, c.extracted_text, c.jurisdiction 
            FROM c 
            WHERE c.organization_id = @org_id 
            AND c.type = 'document'
            AND c.status = 'approved'
            AND c.id != @current_id
            """
            
            approved_docs = list(container.query_items(
                query=query,
                parameters=[
                    {"name": "@org_id", "value": organization_id},
                    {"name": "@current_id", "value": document_id}
                ],
                partition_key=organization_id
            ))
            
            results = []
            
            for approved_doc in approved_docs:
                text = (approved_doc.get('text_content') or approved_doc.get('extracted_text') or '')[:5000]
                if not text:
                    continue
                
                embedding = self.embed_text(text)
                if not embedding:
                    continue
                
                similarity = self.cosine_similarity(doc_embedding, embedding)
                
                if similarity >= 0.7:
                    results.append({
                        'document_id': approved_doc.get('id'),
                        'filename': approved_doc.get('filename'),
                        'similarity_score': round(similarity, 3),
                        'jurisdiction': approved_doc.get('jurisdiction'),
                        'recommendation_reason': f'{int(similarity * 100)}% similar to your document - use as reference'
                    })
            
            results.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            return results[:top_k]
            
        except Exception as e:
            logger.error(f"❌ Recommendation failed: {e}")
            return []


# Global instance
semantic_search = SemanticSearchEngine()


# =============================================================================
# API ENDPOINT
# =============================================================================

def handle_semantic_search(req, user) -> dict:
    """
    POST /search/semantic
    Semantic document search endpoint
    
    Body:
    {
        "query": "documents about guaranteed returns",
        "top_k": 10,
        "min_similarity": 0.7
    }
    """
    try:
        from ..shared.http_utils import json_response
        
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        body = req.get_json()
        query = body.get('query', '').strip()
        top_k = int(body.get('top_k', 10))
        min_similarity = float(body.get('min_similarity', 0.7))
        
        if not query:
            return json_response(400, error="Search query required")
        
        results = semantic_search.find_similar_documents(
            query_text=query,
            organization_id=org_id,
            top_k=top_k,
            min_similarity=min_similarity
        )
        
        return json_response(200, data={
            'query': query,
            'results': results,
            'total_found': len(results),
            'search_type': 'semantic'
        })
        
    except Exception as e:
        logger.error(f"❌ Semantic search endpoint error: {e}")
        return json_response(500, error=str(e))