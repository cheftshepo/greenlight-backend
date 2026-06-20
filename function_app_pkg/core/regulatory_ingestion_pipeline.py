"""
REGULATORY INGESTION PIPELINE
===============================
Populates Azure Search with structured, enriched regulatory chunks

Run this ONCE to populate your knowledge base, then schedule for updates.
"""

import logging
import os
import json
from typing import List, Dict
from datetime import datetime
import hashlib
from openai import AzureOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RegulatoryIngestionPipeline:
    """
    Intelligent pipeline that:
    1. Scrapes FCA Handbook (or loads from JSON)
    2. Chunks by semantic boundaries (individual rules)
    3. Enriches with GPT-4 (risk levels, keywords, examples)
    4. Generates embeddings
    5. Indexes in Azure Search
    """
    
    def __init__(self):
        self.openai_client = AzureOpenAI(
            api_key=os.getenv('AZURE_OPENAI_API_KEY'),
            api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
            azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
        )
        
        self.embed_model = os.getenv('AZURE_OPENAI_EMBEDDING_MODEL', 'text-embedding-ada-002')
        self.gpt_model = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4')
    
    def load_fca_regulations(self) -> List[Dict]:
        """
        Load FCA regulations from source
        
        OPTIONS:
        1. Scrape from https://www.handbook.fca.org.uk/handbook/COBS/4
        2. Load from prepared JSON file
        3. Use regulatory_scraper.py (if you have it)
        
        For now, returns SAMPLE DATA so you can test the pipeline.
        Replace this with real scraping.
        """
        
        # SAMPLE DATA - Replace with real FCA scraping
        sample_regulations = [
            {
                "section_reference": "FCA COBS 4.2.1R",
                "title": "The fair, clear and not misleading rule",
                "text": "A firm must ensure that a communication or a financial promotion is fair, clear and not misleading.",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "handbook": "COBS",
                "chapter": "4",
                "section": "4.2",
                "rule_number": "4.2.1",
                "rule_type": "R",
                "source_url": "https://www.handbook.fca.org.uk/handbook/COBS/4/2.html",
                "effective_date": "2007-11-01",
                "last_updated": "2023-07-31"
            },
            {
                "section_reference": "FCA COBS 4.6.2R",
                "title": "Past performance",
                "text": "A firm must ensure that information that contains an indication of past performance of relevant business, a relevant investment or a trading strategy satisfies certain conditions including appropriate warnings.",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "handbook": "COBS",
                "chapter": "4",
                "section": "4.6",
                "rule_number": "4.6.2",
                "rule_type": "R",
                "source_url": "https://www.handbook.fca.org.uk/handbook/COBS/4/6.html",
                "effective_date": "2007-11-01",
                "last_updated": "2023-07-31"
            },
            {
                "section_reference": "FCA COBS 4.5.2R",
                "title": "Balanced information",
                "text": "A firm must ensure that information in a financial promotion is presented in a way that is likely to be understood by the average member of the group to whom it is directed, and must not disguise, diminish or obscure important items, statements or warnings.",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "handbook": "COBS",
                "chapter": "4",
                "section": "4.5",
                "rule_number": "4.5.2",
                "rule_type": "R",
                "source_url": "https://www.handbook.fca.org.uk/handbook/COBS/4/5.html",
                "effective_date": "2007-11-01",
                "last_updated": "2023-07-31"
            },
            {
                "section_reference": "FCA PRIN 2.1.1R Principle 7",
                "title": "Communications with clients",
                "text": "A firm must pay due regard to the information needs of its clients, and communicate information to them in a way which is clear, fair and not misleading.",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "handbook": "PRIN",
                "chapter": "2",
                "section": "2.1",
                "rule_number": "2.1.1",
                "rule_type": "R",
                "source_url": "https://www.handbook.fca.org.uk/handbook/PRIN/2/1.html",
                "effective_date": "2001-12-01",
                "last_updated": "2023-07-31"
            }
        ]
        
        logger.info(f"📚 Loaded {len(sample_regulations)} sample FCA regulations")
        logger.warning("⚠️ USING SAMPLE DATA - Replace load_fca_regulations() with real scraping")
        
        return sample_regulations
    
    def enrich_with_ai(self, regulation: Dict) -> Dict:
        """
        Use GPT-4 to extract metadata that makes RAG work better:
        - Risk level (critical/high/medium/low)
        - Violation categories
        - Common violation examples
        - Keywords and synonyms
        - Plain English summary
        """
        
        prompt = f"""You are a UK FCA compliance expert. Analyze this regulation and extract metadata.

REGULATION:
Reference: {regulation['section_reference']}
Title: {regulation['title']}
Text: {regulation['text']}

Extract the following in JSON format:

{{
  "risk_level": "critical|high|medium|low",
  "risk_reasoning": "Why this severity?",
  "violation_categories": ["guarantees_promises", "risk_disclosure", "past_performance", "misleading_comparisons", "unsubstantiated_claims", "fair_clear_not_misleading"],
  "common_violations": [
    "Example 1 of text that violates this",
    "Example 2 of text that violates this",
    "Example 3 of text that violates this"
  ],
  "keywords": ["keyword1", "keyword2", ...],
  "synonyms": ["alternative terms that mean same thing"],
  "plain_english_summary": "One sentence for non-lawyers",
  "applies_to": ["retail_clients", "professional_clients", "all"],
  "penalty_range": "Typical enforcement action or fine range"
}}

Be specific and actionable."""

        try:
            response = self.openai_client.chat.completions.create(
                model=self.gpt_model,
                messages=[
                    {"role": "system", "content": "You are an FCA compliance expert. Output ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1500,
                response_format={"type": "json_object"}
            )
            
            enrichment = json.loads(response.choices[0].message.content)
            logger.info(f"✅ Enriched: {regulation['section_reference']}")
            return enrichment
            
        except Exception as e:
            logger.error(f"❌ Enrichment failed for {regulation['section_reference']}: {e}")
            # Return defaults
            return {
                "risk_level": "medium",
                "violation_categories": ["fair_clear_not_misleading"],
                "common_violations": [],
                "keywords": [],
                "synonyms": [],
                "plain_english_summary": regulation['title'],
                "applies_to": ["all"],
                "penalty_range": "Unknown"
            }
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for vector search"""
        try:
            response = self.openai_client.embeddings.create(
                model=self.embed_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"❌ Embedding failed: {e}")
            return None
    
    def create_enhanced_chunk(self, regulation: Dict, enrichment: Dict) -> Dict:
        """
        Create the PERFECT chunk format from Document 1
        """
        
        # Generate unique ID
        chunk_id = hashlib.md5(
            f"{regulation['jurisdiction']}-{regulation['section_reference']}".encode()
        ).hexdigest()[:16]
        
        # Build text for embedding (includes context for better matching)
        embedding_text = f"""
{regulation['section_reference']} - {regulation['title']}
{regulation['text']}

Categories: {', '.join(enrichment.get('violation_categories', []))}
Common violations: {', '.join(enrichment.get('common_violations', [])[:2])}
Keywords: {', '.join(enrichment.get('keywords', [])[:5])}
""".strip()
        
        # Generate embedding
        embedding = self.generate_embedding(embedding_text)
        
        # Build chunk
        chunk = {
            "id": f"uk-fca-{chunk_id}",
            "text": regulation['text'],
            "jurisdiction": regulation['jurisdiction'],
            "source_document": f"{regulation['regulator']} {regulation['handbook']}",
            "section_reference": regulation['section_reference'],
            "category": "general_marketing",  # Can be made more specific
            
            # From original regulation
            "effective_date": regulation.get('effective_date', ''),
            "last_updated": regulation.get('last_updated', ''),
            "source_url": regulation.get('source_url', ''),
            
            # From AI enrichment
            "risk_level": enrichment.get('risk_level', 'medium'),
            "penalty_info": enrichment.get('penalty_range', ''),
            "violation_categories": enrichment.get('violation_categories', []),
            "common_violations": enrichment.get('common_violations', []),
            "keywords": enrichment.get('keywords', []),
            "synonyms": enrichment.get('synonyms', []),
            "plain_english": enrichment.get('plain_english_summary', ''),
            "applies_to": enrichment.get('applies_to', ['all']),
            
            # Vector embedding
            "embedding": embedding,
            
            # Metadata
            "ingested_at": datetime.utcnow().isoformat() + 'Z',
            "ingestion_version": "1.0"
        }
        
        return chunk
    
    def run_pipeline(self, create_index: bool = True) -> Dict:
        """
        Main pipeline execution
        
        Args:
            create_index: If True, creates/updates Azure Search index
        
        Returns:
            Statistics about ingestion
        """
        
        logger.info("=" * 80)
        logger.info("🚀 REGULATORY INGESTION PIPELINE STARTING")
        logger.info("=" * 80)
        
        stats = {
            "regulations_loaded": 0,
            "chunks_created": 0,
            "chunks_indexed": 0,
            "enrichment_failures": 0,
            "embedding_failures": 0,
            "index_failures": 0,
            "start_time": datetime.utcnow().isoformat()
        }
        
        try:
            # Initialize knowledge base
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            kb = RegulatoryKnowledgeBase()
            
            # Create index if needed
            if create_index:
                logger.info("📊 Creating/updating Azure Search index...")
                kb.create_index()
            
            # Load regulations
            logger.info("📚 Loading FCA regulations...")
            regulations = self.load_fca_regulations()
            stats["regulations_loaded"] = len(regulations)
            
            # Process each regulation
            chunks = []
            for i, regulation in enumerate(regulations, 1):
                logger.info(f"Processing {i}/{len(regulations)}: {regulation['section_reference']}")
                
                try:
                    # Enrich with AI
                    enrichment = self.enrich_with_ai(regulation)
                    
                    # Create chunk
                    chunk = self.create_enhanced_chunk(regulation, enrichment)
                    
                    if chunk.get('embedding'):
                        chunks.append(chunk)
                        stats["chunks_created"] += 1
                    else:
                        stats["embedding_failures"] += 1
                        logger.warning(f"⚠️ No embedding for {regulation['section_reference']}")
                    
                except Exception as e:
                    stats["enrichment_failures"] += 1
                    logger.error(f"❌ Failed to process {regulation['section_reference']}: {e}")
            
            # Bulk index chunks
            if chunks:
                logger.info(f"📤 Indexing {len(chunks)} chunks to Azure Search...")
                result = kb.ingest_bulk_dicts(chunks, batch_size=50)
                
                stats["chunks_indexed"] = result.get("succeeded", 0)
                stats["index_failures"] = result.get("failed", 0)
                
                if result.get("errors"):
                    logger.error(f"Indexing errors: {result['errors'][:3]}")
            
            stats["end_time"] = datetime.utcnow().isoformat()
            
            # Print summary
            logger.info("=" * 80)
            logger.info("✅ INGESTION COMPLETE")
            logger.info(f"   Regulations loaded: {stats['regulations_loaded']}")
            logger.info(f"   Chunks created: {stats['chunks_created']}")
            logger.info(f"   Chunks indexed: {stats['chunks_indexed']}")
            logger.info(f"   Failures: {stats['enrichment_failures']} enrichment, {stats['embedding_failures']} embedding, {stats['index_failures']} indexing")
            logger.info("=" * 80)
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Pipeline failed: {e}")
            logger.exception(e)
            stats["error"] = str(e)
            return stats


if __name__ == "__main__":
    """
    Run the pipeline
    
    Usage:
        python regulatory_ingestion_pipeline.py
    """
    
    from dotenv import load_dotenv
    load_dotenv()
    
    pipeline = RegulatoryIngestionPipeline()
    stats = pipeline.run_pipeline(create_index=True)
    
    print("\n" + "=" * 80)
    print("INGESTION STATISTICS")
    print("=" * 80)
    print(json.dumps(stats, indent=2))
    print("=" * 80)
    
    if stats.get("chunks_indexed", 0) > 0:
        print("\n✅ SUCCESS! Your knowledge base now has regulations.")
        print("   Run a test scan to verify RAG is working.")
    else:
        print("\n❌ FAILED! No chunks were indexed.")
        print("   Check your Azure Search credentials.")