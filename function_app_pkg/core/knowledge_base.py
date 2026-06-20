# function_app_pkg/core/knowledge_base.py - FIXED VERSION

import logging
import os
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass, asdict, field
import hashlib
from dotenv import load_dotenv

load_dotenv()   
logger = logging.getLogger(__name__)


@dataclass
class RegulatoryChunk:
    """A chunk of regulatory text with metadata"""
    id: str
    text: str
    jurisdiction: str
    source_document: str
    section_reference: str
    category: str
    effective_date: str
    last_updated: str
    risk_level: str = "medium"
    penalty_info: str = ""
    embedding: List[float] = field(default=None, repr=False)
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        if d.get('embedding') is None:
            d.pop('embedding', None)
        return d


class RegulatoryKnowledgeBase:
    """
    Vector-based regulatory knowledge base using Azure AI Search
    
    Now works with live data from Cosmos DB 'rules' container
    """
    
    def __init__(self):
        self.search_client = None
        self.index_client = None
        self.embed_client = None
        self.index_name = os.getenv('AZURE_SEARCH_INDEX', 'regulatory-knowledge')
        self._initialized = False
        
        logger.info(f"📚 Knowledge base initialized (will use Azure Search: {self.index_name})")
    
    def _ensure_initialized(self):
        """Lazy initialization of clients"""
        if self._initialized:
            return
        
        try:
            from azure.search.documents import SearchClient
            from azure.search.documents.indexes import SearchIndexClient
            from azure.core.credentials import AzureKeyCredential
            from openai import AzureOpenAI
            
            # Azure AI Search
            search_endpoint = os.getenv('AZURE_SEARCH_ENDPOINT')
            search_key = os.getenv('AZURE_SEARCH_KEY')
            
            if not search_endpoint or not search_key:
                logger.warning("❌ Missing Azure Search credentials - knowledge base will be read-only")
                logger.warning("   Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY environment variables")
                self.search_client = None
                return
            
            self.search_client = SearchClient(
                endpoint=search_endpoint,
                index_name=self.index_name,
                credential=AzureKeyCredential(search_key)
            )
            
            self.index_client = SearchIndexClient(
                endpoint=search_endpoint,
                credential=AzureKeyCredential(search_key)
            )
            
            # OpenAI for embeddings
            api_key = os.getenv('AZURE_OPENAI_API_KEY')
            endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
            
            if api_key and endpoint:
                self.embed_client = AzureOpenAI(
                    api_key=api_key,
                    api_version="2024-02-01",
                    azure_endpoint=endpoint
                )
                self.embed_model = os.getenv('AZURE_OPENAI_EMBEDDING_MODEL', 'text-embedding-ada-002')
            else:
                logger.warning("⚠️ Missing OpenAI credentials - embeddings disabled")
                self.embed_client = None
            
            self._initialized = True
            logger.info(f"✅ Azure Search knowledge base initialized: {self.index_name}")
            
        except ImportError as e:
            logger.error(f"❌ Azure Search SDK not installed: {e}")
            logger.error("   Run: pip install azure-search-documents openai")
        except Exception as e:
            logger.error(f"❌ Knowledge base init failed: {e}")
    
    def create_index(self):
        """Create the search index with vector field"""
        self._ensure_initialized()
        
        if not self.index_client:
            logger.error("❌ Cannot create index: Azure Search not configured")
            return None
        
        from azure.search.documents.indexes.models import (
            SearchIndex, SearchField, SearchFieldDataType,
            VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
            SearchableField, SimpleField, SemanticConfiguration,
            SemanticField, SemanticPrioritizedFields, SemanticSearch
        )
        
        try:
            fields = [
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SearchableField(name="text", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
                SimpleField(name="jurisdiction", type=SearchFieldDataType.String, filterable=True, facetable=True),
                SearchableField(name="source_document", type=SearchFieldDataType.String, filterable=True),
                SearchableField(name="section_reference", type=SearchFieldDataType.String),
                SimpleField(name="category", type=SearchFieldDataType.String, filterable=True, facetable=True),
                SimpleField(name="risk_level", type=SearchFieldDataType.String, filterable=True),
                SearchableField(name="penalty_info", type=SearchFieldDataType.String),
                SimpleField(name="effective_date", type=SearchFieldDataType.String, sortable=True),
                SimpleField(name="last_updated", type=SearchFieldDataType.String, sortable=True),
                SearchField(
                    name="embedding",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True,
                    vector_search_dimensions=1536,  # ada-002 dimension
                    vector_search_profile_name="vector-profile"
                )
            ]
            
            # Vector search configuration
            vector_search = VectorSearch(
                algorithms=[
                    HnswAlgorithmConfiguration(
                        name="hnsw-config",
                        parameters={
                            "m": 4,
                            "efConstruction": 400,
                            "efSearch": 500,
                            "metric": "cosine"
                        }
                    )
                ],
                profiles=[
                    VectorSearchProfile(
                        name="vector-profile",
                        algorithm_configuration_name="hnsw-config"
                    )
                ]
            )
            
            # Semantic search for better ranking
            semantic_config = SemanticConfiguration(
                name="semantic-config",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="text")],
                    keywords_fields=[SemanticField(field_name="section_reference")],
                    title_field=SemanticField(field_name="source_document")
                )
            )
            
            semantic_search = SemanticSearch(configurations=[semantic_config])
            
            index = SearchIndex(
                name=self.index_name,
                fields=fields,
                vector_search=vector_search,
                semantic_search=semantic_search
            )
            
            result = self.index_client.create_or_update_index(index)
            logger.info(f"✅ Created/updated search index: {result.name}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to create index: {e}")
            return None
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using Azure OpenAI"""
        self._ensure_initialized()
        
        if not self.embed_client:
            logger.warning("⚠️ Embedding generation disabled - no OpenAI client")
            return None
        
        # Truncate if too long (ada-002 max is 8191 tokens)
        if len(text) > 30000:
            text = text[:30000]
        
        try:
            response = self.embed_client.embeddings.create(
                model=self.embed_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"❌ Embedding generation failed: {e}")
            return None
    
    def get_regulations_from_cosmos(self, jurisdiction: str, category: str = None, limit: int = 50) -> List[RegulatoryChunk]:
        """Get regulations from Cosmos DB 'rules' container as fallback"""
        try:
            from .database import query_items
            
            query = """
            SELECT * FROM c 
            WHERE c.jurisdiction = @jurisdiction
            AND c.type = 'regulation'
            """
            params = [{"name": "@jurisdiction", "value": jurisdiction}]
            
            if category:
                query += " AND c.category = @category"
                params.append({"name": "@category", "value": category})
            
            query += f" ORDER BY c.effective_date DESC OFFSET 0 LIMIT {limit}"
            
            items = query_items(
                container_name="rules",
                query=query,
                parameters=params,
                partition_key=jurisdiction
            )
            
            chunks = []
            for item in items:
                chunk = RegulatoryChunk(
                    id=item.get('id', ''),
                    text=item.get('text', ''),
                    jurisdiction=item.get('jurisdiction', ''),
                    source_document=item.get('source_document', item.get('regulator', '')),
                    section_reference=item.get('section_reference', item.get('section', '')),
                    category=item.get('category', 'general_marketing'),
                    risk_level=item.get('risk_level', 'medium'),
                    penalty_info=item.get('penalty_info', ''),
                    effective_date=item.get('effective_date', ''),
                    last_updated=item.get('updated_at', datetime.utcnow().isoformat())
                )
                chunks.append(chunk)
            
            logger.info(f"📚 Retrieved {len(chunks)} regulations from Cosmos DB for {jurisdiction}")
            return chunks
            
        except Exception as e:
            logger.error(f"❌ Failed to get regulations from Cosmos DB: {e}")
            return []
    

    def search(
        self,
        query_text: str,
        jurisdiction: str = None,
        categories: List[str] = None,
        top_k: int = 10,
        min_score: float = 0.0
    ) -> List[Dict]:
        """Enhanced search with both keyword and vector search"""
        self._ensure_initialized()
        
        if not self.search_client:
            logger.warning("⚠️ No search client, using Cosmos fallback")
            return self._search_cosmos_fallback(query_text, jurisdiction, categories, top_k)
        
        try:
            # Build filter
            filters = []
            if jurisdiction:
                filters.append(f"jurisdiction eq '{jurisdiction}'")
            if categories:
                cat_filters = " or ".join([f"category eq '{c}'" for c in categories])
                filters.append(f"({cat_filters})")
            
            filter_str = " and ".join(filters) if filters else None
            
            all_results = []
            
            # 1. Try vector search first (if we have embedding capability)
            if self.embed_client and query_text.strip():
                try:
                    # Generate embedding for query
                    embedding = self.generate_embedding(query_text)
                    if embedding:
                        from azure.search.documents.models import VectorizedQuery
                        
                        vector_query = VectorizedQuery(
                            vector=embedding,
                            k_nearest_neighbors=top_k,
                            fields="embedding",
                            exhaustive=True
                        )
                        
                        # Vector search
                        vector_results = self.search_client.search(
                            search_text=None,  # No keyword search
                            vector_queries=[vector_query],
                            filter=filter_str,
                            top=top_k,
                            select=["id", "text", "jurisdiction", "source_document", 
                                    "section_reference", "category", "risk_level", 
                                    "penalty_info", "effective_date", "last_updated"]
                        )
                        
                        # FIXED: Convert iterator to list before counting
                        vector_results_list = list(vector_results)
                        for result in vector_results_list:
                            score = result.get("@search.score", 0)
                            all_results.append({
                                "chunk": self._create_chunk_from_result(result),
                                "similarity_score": score,
                                "search_type": "vector"
                            })
                            
                        logger.info(f"🔍 Vector search found {len(vector_results_list)} regulations")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Vector search failed: {e}")
            
            # 2. Keyword search (fallback or supplement)
            try:
                keyword_results = self.search_client.search(
                    search_text=query_text,
                    filter=filter_str,
                    top=top_k,
                    select=["id", "text", "jurisdiction", "source_document", 
                            "section_reference", "category", "risk_level", 
                            "penalty_info", "effective_date", "last_updated"]
                )
                
                # FIXED: Convert iterator to list before counting
                keyword_results_list = list(keyword_results)
                keyword_count = 0
                for result in keyword_results_list:
                    score = result.get("@search.score", 0)
                    all_results.append({
                        "chunk": self._create_chunk_from_result(result),
                        "similarity_score": score,
                        "search_type": "keyword"
                    })
                    keyword_count += 1
                    
                logger.info(f"🔍 Keyword search found {keyword_count} regulations")
                
            except Exception as e:
                logger.warning(f"⚠️ Keyword search failed: {e}")
            
            # 3. Deduplicate and sort results
            unique_results = {}
            for result in all_results:
                chunk_id = result["chunk"].id
                if chunk_id not in unique_results or result["similarity_score"] > unique_results[chunk_id]["similarity_score"]:
                    unique_results[chunk_id] = result
            
            final_results = list(unique_results.values())
            final_results.sort(key=lambda x: x["similarity_score"], reverse=True)
            
            # Filter by min_score if specified
            if min_score > 0:
                final_results = [r for r in final_results if r["similarity_score"] >= min_score]
            
            logger.info(f"🔍 Total unique regulations found: {len(final_results)}")
            return final_results
            
        except Exception as e:
            logger.error(f"❌ Search failed: {e}")
            return self._search_cosmos_fallback(query_text, jurisdiction, categories, top_k)

    def _create_chunk_from_result(self, result: Dict) -> RegulatoryChunk:
        """Helper to create RegulatoryChunk from search result"""
        return RegulatoryChunk(
            id=result["id"],
            text=result["text"],
            jurisdiction=result["jurisdiction"],
            source_document=result["source_document"],
            section_reference=result["section_reference"],
            category=result["category"],
            risk_level=result.get("risk_level", "medium"),
            penalty_info=result.get("penalty_info", ""),
            effective_date=result.get("effective_date", ""),
            last_updated=result.get("last_updated", "")
        )

    def _search_cosmos_fallback(self, query_text: str, jurisdiction: str = None, 
                               categories: List[str] = None, top_k: int = 10) -> List[Dict]:
        """Fallback search using Cosmos DB when Azure Search is unavailable"""
        try:
            # Get regulations from Cosmos DB
            if jurisdiction:
                regulations = self.get_regulations_from_cosmos(jurisdiction, categories and categories[0], top_k * 2)
            else:
                # Get from all jurisdictions if none specified
                regulations = []
                for jur in ["UK", "EU", "US"]:
                    regs = self.get_regulations_from_cosmos(jur, categories and categories[0], top_k)
                    regulations.extend(regs)
            
            # Simple keyword matching
            query_lower = query_text.lower()
            scored_regulations = []
            
            for reg in regulations:
                score = 0
                text_lower = reg.text.lower()
                
                # Basic keyword matching
                for word in query_lower.split():
                    if len(word) > 3 and word in text_lower:
                        score += 1
                
                # Boost for exact phrase matches
                if query_lower in text_lower:
                    score += 5
                
                # Boost for category matches if specified
                if categories and reg.category in categories:
                    score += 3
                
                if score > 0:
                    # Normalize score to 0-1 range
                    normalized_score = min(1.0, score / 10.0)
                    scored_regulations.append({
                        "chunk": reg,
                        "similarity_score": normalized_score
                    })
            
            # Sort by score and limit
            scored_regulations.sort(key=lambda x: x["similarity_score"], reverse=True)
            
            logger.info(f"📚 Cosmos DB fallback found {len(scored_regulations)} regulations")
            return scored_regulations[:top_k]
            
        except Exception as e:
            logger.error(f"❌ Cosmos DB fallback also failed: {e}")
            return []
    
    def ingest_bulk(self, chunks: List[RegulatoryChunk], batch_size: int = 50) -> Dict:
        """Bulk ingest regulatory chunks into Azure Search"""
        self._ensure_initialized()
        
        if not self.search_client:
            logger.error("❌ Cannot ingest: Azure Search not configured")
            return {"succeeded": 0, "failed": len(chunks), "total": len(chunks), "errors": ["Azure Search not configured"]}
        
        stats = {
            "total": len(chunks),
            "succeeded": 0,
            "failed": 0,
            "errors": []
        }
        
        docs = []
        
        for i, chunk in enumerate(chunks):
            try:
                # Generate embedding
                if not chunk.embedding:
                    chunk.embedding = self.generate_embedding(chunk.text)
                
                doc = {
                    "id": chunk.id,
                    "text": chunk.text,
                    "jurisdiction": chunk.jurisdiction,
                    "source_document": chunk.source_document,
                    "section_reference": chunk.section_reference,
                    "category": chunk.category,
                    "risk_level": chunk.risk_level,
                    "penalty_info": chunk.penalty_info,
                    "effective_date": chunk.effective_date,
                    "last_updated": chunk.last_updated,
                }
                
                # Only add embedding if we have it
                if chunk.embedding:
                    doc["embedding"] = chunk.embedding
                
                docs.append(doc)
                
                # Upload in batches
                if len(docs) >= batch_size:
                    results = self.search_client.upload_documents(docs)
                    for r in results:
                        if r.succeeded:
                            stats["succeeded"] += 1
                        else:
                            stats["failed"] += 1
                            stats["errors"].append(str(r.error_message))
                    
                    logger.info(f"📤 Batch uploaded: {i+1}/{len(chunks)}")
                    docs = []
                    
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{chunk.id}: {str(e)}")
        
        # Upload remaining
        if docs:
            results = self.search_client.upload_documents(docs)
            for r in results:
                if r.succeeded:
                    stats["succeeded"] += 1
                else:
                    stats["failed"] += 1
        
        logger.info(f"✅ Bulk ingestion complete: {stats['succeeded']}/{stats['total']} succeeded")
        return stats

    def ingest_bulk_dicts(self, chunk_dicts: List[Dict], batch_size: int = 50) -> Dict:
        """Bulk ingest regulatory chunk dictionaries into Azure Search"""
        self._ensure_initialized()
        
        if not self.search_client:
            logger.error("❌ Cannot ingest: Azure Search not configured")
            return {"succeeded": 0, "failed": len(chunk_dicts), "total": len(chunk_dicts), "errors": ["Azure Search not configured"]}
        
        stats = {
            "total": len(chunk_dicts),
            "succeeded": 0,
            "failed": 0,
            "errors": []
        }
        
        docs = []
        
        for i, chunk_dict in enumerate(chunk_dicts):
            try:
                # Generate embedding if not present
                if not chunk_dict.get("embedding"):
                    text = chunk_dict.get("text", "")
                    embedding = self.generate_embedding(text)
                    if embedding:
                        chunk_dict["embedding"] = embedding
                
                # Ensure required fields
                if "id" not in chunk_dict:
                    chunk_dict["id"] = f"chunk_{i}"
                
                docs.append(chunk_dict)
                
                # Upload in batches
                if len(docs) >= batch_size:
                    results = self.search_client.upload_documents(docs)
                    for r in results:
                        if r.succeeded:
                            stats["succeeded"] += 1
                        else:
                            stats["failed"] += 1
                            stats["errors"].append(str(r.error_message))
                    
                    logger.info(f"📤 Batch uploaded: {i+1}/{len(chunk_dicts)}")
                    docs = []
                    
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append(f"{chunk_dict.get('id', str(i))}: {str(e)}")
        
        # Upload remaining
        if docs:
            try:
                results = self.search_client.upload_documents(docs)
                for r in results:
                    if r.succeeded:
                        stats["succeeded"] += 1
                    else:
                        stats["failed"] += 1
                        stats["errors"].append(str(r.error_message))
            except Exception as e:
                stats["failed"] += len(docs)
                stats["errors"].append(f"Final batch: {str(e)}")
        
        logger.info(f"✅ Bulk ingestion complete: {stats['succeeded']}/{stats['total']} succeeded")
        return stats

    # =========================================================================
    # MISSING METHODS - ADDED
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """
        Get knowledge base statistics
        
        Returns:
            Dict with index_name, total_chunks, and jurisdictions breakdown
        """
        self._ensure_initialized()
        
        stats = {
            "index_name": self.index_name,
            "total_chunks": 0,
            "jurisdictions": {},
            "categories": {},
            "risk_levels": {},
            "initialized": self._initialized,
            "search_client_available": self.search_client is not None,
            "embed_client_available": self.embed_client is not None
        }
        
        if not self.search_client:
            logger.warning("⚠️ Cannot get stats: Azure Search not configured")
            return stats
        
        try:
            # Get total count by searching for everything
            all_results = self.search_client.search(
                search_text="*",
                include_total_count=True,
                top=0  # Don't need actual results, just count
            )
            
            # Get the count
            stats["total_chunks"] = all_results.get_count() or 0
            
            # Get jurisdiction breakdown using facets
            facet_results = self.search_client.search(
                search_text="*",
                facets=["jurisdiction", "category", "risk_level"],
                top=0
            )
            
            # Extract facet counts
            facets = facet_results.get_facets() or {}
            
            if "jurisdiction" in facets:
                for facet in facets["jurisdiction"]:
                    stats["jurisdictions"][facet["value"]] = facet["count"]
            
            if "category" in facets:
                for facet in facets["category"]:
                    stats["categories"][facet["value"]] = facet["count"]
            
            if "risk_level" in facets:
                for facet in facets["risk_level"]:
                    stats["risk_levels"][facet["value"]] = facet["count"]
            
            logger.info(f"📊 Knowledge base stats: {stats['total_chunks']} total chunks")
            
        except Exception as e:
            logger.error(f"❌ Failed to get stats: {e}")
            # Try alternative method - count via query
            try:
                results = list(self.search_client.search(
                    search_text="*",
                    top=1000,
                    select=["id", "jurisdiction"]
                ))
                stats["total_chunks"] = len(results)
                
                # Count jurisdictions manually
                for r in results:
                    jur = r.get("jurisdiction", "unknown")
                    stats["jurisdictions"][jur] = stats["jurisdictions"].get(jur, 0) + 1
                    
            except Exception as e2:
                logger.error(f"❌ Alternative stats method also failed: {e2}")
        
        return stats
    
    def delete_all(self) -> int:
        """
        Delete all documents from the index
        
        Returns:
            Number of documents deleted
        """
        self._ensure_initialized()
        
        if not self.search_client:
            logger.error("❌ Cannot delete: Azure Search not configured")
            return 0
        
        deleted_count = 0
        
        try:
            # Get all document IDs
            all_docs = list(self.search_client.search(
                search_text="*",
                top=1000,
                select=["id"]
            ))
            
            if not all_docs:
                logger.info("📭 Index is already empty")
                return 0
            
            # Delete in batches
            batch_size = 100
            for i in range(0, len(all_docs), batch_size):
                batch = all_docs[i:i + batch_size]
                doc_ids = [{"id": doc["id"]} for doc in batch]
                
                try:
                    results = self.search_client.delete_documents(doc_ids)
                    for r in results:
                        if r.succeeded:
                            deleted_count += 1
                except Exception as e:
                    logger.error(f"❌ Batch delete failed: {e}")
                
                logger.info(f"🗑️ Deleted batch {i // batch_size + 1}: {deleted_count} total")
            
            logger.info(f"✅ Deleted {deleted_count} documents from index")
            
        except Exception as e:
            logger.error(f"❌ Delete all failed: {e}")
        
        return deleted_count
    
    def get_document_count(self) -> int:
        """Get total document count in index"""
        stats = self.get_stats()
        return stats.get("total_chunks", 0)
    
    def health_check(self) -> Dict:
        """Check health of knowledge base connections"""
        self._ensure_initialized()
        
        health = {
            "status": "unknown",
            "search_client": False,
            "embed_client": False,
            "index_exists": False,
            "document_count": 0,
            "errors": []
        }
        
        # Check search client
        if self.search_client:
            try:
                # Try a simple search
                list(self.search_client.search(search_text="test", top=1))
                health["search_client"] = True
                health["index_exists"] = True
            except Exception as e:
                health["errors"].append(f"Search client: {str(e)[:100]}")
        else:
            health["errors"].append("Search client not initialized")
        
        # Check embed client
        if self.embed_client:
            try:
                # Try generating an embedding
                self.generate_embedding("test")
                health["embed_client"] = True
            except Exception as e:
                health["errors"].append(f"Embed client: {str(e)[:100]}")
        else:
            health["errors"].append("Embed client not initialized")
        
        # Get document count
        try:
            stats = self.get_stats()
            health["document_count"] = stats.get("total_chunks", 0)
        except Exception as e:
            health["errors"].append(f"Stats: {str(e)[:100]}")
        
        # Determine overall status
        if health["search_client"] and health["embed_client"]:
            health["status"] = "healthy"
        elif health["search_client"]:
            health["status"] = "degraded"
        else:
            health["status"] = "unhealthy"
        
        return health


# Global instance
knowledge_base = RegulatoryKnowledgeBase()