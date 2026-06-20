# function_app_pkg/core/regulatory_data.py - ENHANCED VERSION
"""
Dynamic Regulatory Data Management
==================================
All regulatory data stored in and loaded from Cosmos DB
Enhanced version with caching, fallbacks, and RAG integration
"""

import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import json
from functools import lru_cache

from .database import get_container, query_items

logger = logging.getLogger(__name__)


class RegulatorySourceType(str, Enum):
    STATUTE = "statute"
    REGULATION = "regulation"
    GUIDANCE = "guidance"
    ENFORCEMENT = "enforcement"
    INTERNAL_POLICY = "internal_policy"


class UpdateFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    BIYEARLY = "bi-yearly"
    YEARLY = "yearly"
    NONE = "none"


@dataclass
class RegulatorySource:
    """Regulatory source metadata"""
    id: str
    name: str
    jurisdiction: str
    regulator: str
    source_type: RegulatorySourceType
    url: str = ""
    description: str = ""
    version: str = "1.0"
    effective_date: str = ""
    last_updated: str = ""
    update_frequency: str = UpdateFrequency.MONTHLY.value
    scrape_config: Dict = None
    is_active: bool = True
    type: str = "regulatory_source"
    partition_key: str = ""
    
    def __post_init__(self):
        if not self.partition_key:
            self.partition_key = self.jurisdiction


@dataclass
class RegulatoryProvision:
    """A single regulatory provision/rule"""
    id: str
    source_id: str
    jurisdiction: str
    regulator: str
    section_reference: str
    title: str
    text: str
    category: str
    risk_level: str
    effective_date: str
    last_updated: str
    metadata: Dict = None
    embedding_generated: bool = False
    vector_embedding: List[float] = None
    type: str = "regulatory_provision"
    partition_key: str = ""
    is_active: bool = True
    
    def __post_init__(self):
        if not self.partition_key:
            self.partition_key = self.jurisdiction
    
    def to_knowledge_base_chunk(self) -> Dict:
        """Convert to knowledge base chunk format"""
        return {
            "id": self.id,
            "text": self.text,
            "jurisdiction": self.jurisdiction,
            "source_document": self.regulator,
            "section_reference": self.section_reference,
            "category": self.category,
            "effective_date": self.effective_date,
            "last_updated": self.last_updated,
            "risk_level": self.risk_level,
            "penalty_info": self.metadata.get("penalty_info", "") if self.metadata else ""
        }


@dataclass
class RegulatoryCategory:
    """Dynamic category definition"""
    id: str
    name: str
    description: str
    jurisdictions: List[str]
    keywords: List[str]
    priority: int = 1
    risk_weight: float = 1.0
    type: str = "regulatory_category"
    partition_key: str = "global"
    
    def __post_init__(self):
        if not self.partition_key:
            self.partition_key = "global"


@dataclass
class ScraperConfig:
    """Dynamic scraper configuration"""
    id: str
    name: str
    jurisdiction: str
    regulator: str
    url: str
    source_type: str
    selectors: Dict
    schedule: str = "daily"
    is_active: bool = True
    last_run: str = ""
    last_success: str = ""
    error_count: int = 0
    type: str = "scraper_config"
    partition_key: str = ""
    
    def __post_init__(self):
        if not self.partition_key:
            self.partition_key = self.jurisdiction


@dataclass
class QueryTemplate:
    """Dynamic query templates for RAG scanner"""
    id: str
    name: str
    jurisdiction: str
    category: str
    template: str
    priority: int = 1
    is_active: bool = True
    type: str = "query_template"
    partition_key: str = ""
    
    def __post_init__(self):
        if not self.partition_key:
            self.partition_key = self.jurisdiction
    
    def format_query(self, **kwargs) -> str:
        """Format the template with context"""
        try:
            return self.template.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing key in query template: {e}")
            return self.template


class RegulatoryDataService:
    """
    Complete regulatory data management with caching and fallbacks
    
    Single source of truth for ALL regulatory data:
    - Provisions/rules
    - Categories 
    - Scraper configs
    - Query templates
    """
    
    def __init__(self, fallback_enabled: bool = True):
        self.container = get_container("regulatory_data")
        self.cache = {}
        self.fallback_enabled = fallback_enabled
        self._init_cache()
        logger.info("✅ RegulatoryDataService initialized")
    
    def _init_cache(self):
        """Initialize in-memory cache"""
        self.cache = {
            "provisions": {},
            "categories": {},
            "sources": {},
            "scrapers": {},
            "queries": {}
        }
    
    # =========================================================================
    # REGULATORY PROVISIONS
    # =========================================================================
    
    @lru_cache(maxsize=100)
    def get_provisions(self, jurisdiction: str, category: str = None, 
                      limit: int = 100) -> List[RegulatoryProvision]:
        """Get provisions with caching"""
        cache_key = f"provisions_{jurisdiction}_{category}"
        
        if cache_key in self.cache["provisions"]:
            return self.cache["provisions"][cache_key]
        
        query = """
        SELECT * FROM c 
        WHERE c.type = 'regulatory_provision' 
        AND c.jurisdiction = @jurisdiction
        AND c.is_active = true
        """
        params = [{"name": "@jurisdiction", "value": jurisdiction}]
        
        if category:
            query += " AND c.category = @category"
            params.append({"name": "@category", "value": category})
        
        query += " ORDER BY c.effective_date DESC OFFSET 0 LIMIT @limit"
        params.append({"name": "@limit", "value": limit})
        
        try:
            items = list(query_items(
                query=query,
                parameters=params,
                container=self.container,
                enable_cross_partition_query=True
            ))
            
            provisions = []
            for item in items:
                provision = RegulatoryProvision(
                    id=item['id'],
                    source_id=item['source_id'],
                    jurisdiction=item['jurisdiction'],
                    regulator=item['regulator'],
                    section_reference=item['section_reference'],
                    title=item['title'],
                    text=item['text'],
                    category=item['category'],
                    risk_level=item.get('risk_level', 'medium'),
                    effective_date=item.get('effective_date', ''),
                    last_updated=item.get('last_updated', ''),
                    metadata=item.get('metadata', {}),
                    embedding_generated=item.get('embedding_generated', False),
                    is_active=item.get('is_active', True)
                )
                provisions.append(provision)
            
            self.cache["provisions"][cache_key] = provisions
            logger.info(f"📚 Loaded {len(provisions)} provisions for {jurisdiction}")
            return provisions
            
        except Exception as e:
            logger.error(f"❌ Failed to load provisions: {e}")
            if self.fallback_enabled:
                return self._get_fallback_provisions(jurisdiction, category)
            return []
    
    def _get_fallback_provisions(self, jurisdiction: str, category: str = None) -> List[RegulatoryProvision]:
        """Fallback to hardcoded data during transition"""
        from .regulatory_scraper import (
            ESMAScraper, SECScraper, FCAHandbookScraper,
            SFDRScraper, PRIIPsScraper, ConsumerDutyScraper,
            CryptoAssetScraper, AntiGreenwashingScraper
        )
        
        scraper_map = {
            "EU": ESMAScraper,
            "US": SECScraper,
            "UK": FCAHandbookScraper,
            "SFDR": SFDRScraper,
            "PRIIPs": PRIIPsScraper,
            "ConsumerDuty": ConsumerDutyScraper,
            "Crypto": CryptoAssetScraper,
            "Greenwashing": AntiGreenwashingScraper
        }
        
        provisions = []
        
        # Load from appropriate scraper based on jurisdiction
        for scraper_name, scraper_class in scraper_map.items():
            if scraper_name.lower() in jurisdiction.lower() or jurisdiction.lower() in scraper_name.lower():
                try:
                    scraper = scraper_class()
                    scraped = scraper.scrape()
                    
                    for reg in scraped:
                        provision = RegulatoryProvision(
                            id=str(uuid.uuid4()),
                            source_id=f"fallback_{scraper_name.lower()}",
                            jurisdiction=reg.jurisdiction,
                            regulator=reg.regulator,
                            section_reference=reg.section_reference,
                            title=reg.title,
                            text=reg.text,
                            category=reg.category,
                            risk_level=reg.risk_level,
                            effective_date=reg.effective_date,
                            last_updated=datetime.utcnow().isoformat(),
                            metadata={
                                "source_url": reg.source_url,
                                "parent_document": reg.parent_document,
                                "penalty_info": reg.penalty_info,
                                "scrape_timestamp": reg.scrape_timestamp
                            },
                            is_active=True
                        )
                        provisions.append(provision)
                    
                    logger.info(f"⚠️ Using fallback provisions from {scraper_name}")
                    break
                except Exception as e:
                    logger.error(f"❌ Fallback scraper failed: {e}")
        
        # Filter by category if specified
        if category:
            provisions = [p for p in provisions if p.category == category]
        
        return provisions
    
    # =========================================================================
    # CATEGORIES
    # =========================================================================
    
    @lru_cache(maxsize=10)
    def get_categories(self, jurisdiction: str = None) -> List[RegulatoryCategory]:
        """Get categories with caching"""
        cache_key = f"categories_{jurisdiction or 'all'}"
        
        if cache_key in self.cache["categories"]:
            return self.cache["categories"][cache_key]
        
        query = "SELECT * FROM c WHERE c.type = 'regulatory_category'"
        params = []
        
        if jurisdiction:
            query += " AND ARRAY_CONTAINS(c.jurisdictions, @jurisdiction)"
            params.append({"name": "@jurisdiction", "value": jurisdiction})
        
        try:
            items = list(query_items(
                query=query,
                parameters=params if params else None,
                container=self.container,
                enable_cross_partition_query=True
            ))
            
            categories = []
            for item in items:
                category = RegulatoryCategory(
                    id=item['id'],
                    name=item['name'],
                    description=item['description'],
                    jurisdictions=item['jurisdictions'],
                    keywords=item.get('keywords', []),
                    priority=item.get('priority', 1),
                    risk_weight=item.get('risk_weight', 1.0)
                )
                categories.append(category)
            
            self.cache["categories"][cache_key] = categories
            return categories
            
        except Exception as e:
            logger.error(f"❌ Failed to load categories: {e}")
            if self.fallback_enabled:
                return self._get_fallback_categories(jurisdiction)
            return []
    
    def _get_fallback_categories(self, jurisdiction: str = None) -> List[RegulatoryCategory]:
        """Fallback to hardcoded categories"""
        from .knowledge_base import REGULATORY_CATEGORIES
        
        categories = []
        for cat_id, cat_info in REGULATORY_CATEGORIES.items():
            category = RegulatoryCategory(
                id=cat_id,
                name=cat_id.replace("_", " ").title(),
                description=cat_info["description"],
                jurisdictions=["UK", "EU", "US", "GLOBAL"],
                keywords=cat_info.get("keywords", []),
                priority=1,
                risk_weight=1.0
            )
            categories.append(category)
        
        # Filter by jurisdiction if specified
        if jurisdiction:
            categories = [c for c in categories if jurisdiction in c.jurisdictions]
        
        logger.warning(f"⚠️ Using fallback categories: {len(categories)} categories")
        return categories
    
    def categorize_text(self, text: str, jurisdiction: str = "UK") -> str:
        """Auto-categorize text using dynamic categories"""
        categories = self.get_categories(jurisdiction)
        text_lower = text.lower()
        
        scores = {}
        for category in categories:
            if jurisdiction in category.jurisdictions:
                # Score based on keyword matches
                score = sum(1 for kw in category.keywords if kw.lower() in text_lower)
                if score > 0:
                    scores[category.name] = score * category.risk_weight * category.priority
        
        if scores:
            return max(scores, key=scores.get)
        return "general_marketing"
    
    # =========================================================================
    # SCRAPER CONFIGURATIONS
    # =========================================================================
    
    def get_scraper_configs(self, jurisdiction: str = None, 
                           active_only: bool = True) -> List[ScraperConfig]:
        """Get scraper configurations"""
        cache_key = f"scrapers_{jurisdiction or 'all'}_{active_only}"
        
        if cache_key in self.cache["scrapers"]:
            return self.cache["scrapers"][cache_key]
        
        query = "SELECT * FROM c WHERE c.type = 'scraper_config'"
        params = []
        
        if active_only:
            query += " AND c.is_active = true"
        
        if jurisdiction:
            query += " AND c.jurisdiction = @jurisdiction"
            params.append({"name": "@jurisdiction", "value": jurisdiction})
        
        try:
            items = list(query_items(
                query=query,
                parameters=params if params else None,
                container=self.container,
                enable_cross_partition_query=True
            ))
            
            configs = []
            for item in items:
                config = ScraperConfig(
                    id=item['id'],
                    name=item['name'],
                    jurisdiction=item['jurisdiction'],
                    regulator=item['regulator'],
                    url=item['url'],
                    source_type=item['source_type'],
                    selectors=item['selectors'],
                    schedule=item.get('schedule', 'daily'),
                    is_active=item.get('is_active', True),
                    last_run=item.get('last_run', ''),
                    last_success=item.get('last_success', ''),
                    error_count=item.get('error_count', 0)
                )
                configs.append(config)
            
            self.cache["scrapers"][cache_key] = configs
            return configs
            
        except Exception as e:
            logger.error(f"❌ Failed to load scraper configs: {e}")
            return []
    
    # =========================================================================
    # QUERY TEMPLATES (For RAG scanner)
    # =========================================================================
    
    @lru_cache(maxsize=20)
    def get_query_templates(self, jurisdiction: str, category: str = None) -> List[QueryTemplate]:
        """Get query templates for RAG scanner"""
        cache_key = f"queries_{jurisdiction}_{category or 'all'}"
        
        if cache_key in self.cache["queries"]:
            return self.cache["queries"][cache_key]
        
        query = """
        SELECT * FROM c 
        WHERE c.type = 'query_template' 
        AND c.jurisdiction = @jurisdiction
        AND c.is_active = true
        """
        params = [{"name": "@jurisdiction", "value": jurisdiction}]
        
        if category:
            query += " AND c.category = @category"
            params.append({"name": "@category", "value": category})
        
        query += " ORDER BY c.priority DESC"
        
        try:
            items = list(query_items(
                query=query,
                parameters=params,
                container=self.container,
                enable_cross_partition_query=True
            ))
            
            templates = []
            for item in items:
                template = QueryTemplate(
                    id=item['id'],
                    name=item['name'],
                    jurisdiction=item['jurisdiction'],
                    category=item['category'],
                    template=item['template'],
                    priority=item.get('priority', 1),
                    is_active=item.get('is_active', True)
                )
                templates.append(template)
            
            self.cache["queries"][cache_key] = templates
            return templates
            
        except Exception as e:
            logger.error(f"❌ Failed to load query templates: {e}")
            if self.fallback_enabled:
                return self._get_fallback_query_templates(jurisdiction, category)
            return []
    
    def _get_fallback_query_templates(self, jurisdiction: str, category: str = None) -> List[QueryTemplate]:
        """Fallback query templates from hardcoded rag_scanner.py logic"""
        templates = []
        
        # Core queries for all jurisdictions
        base_queries = [
            "fair clear not misleading communications",
            "financial promotions requirements",
            "risk warnings capital at risk"
        ]
        
        # Jurisdiction-specific additions
        if jurisdiction == "UK":
            base_queries.extend([
                "FCA COBS 4 marketing communications",
                "Consumer Duty PRIN 2A requirements",
                "Past performance warnings required"
            ])
        elif jurisdiction == "EU":
            base_queries.extend([
                "MiFID II Article 24 marketing requirements",
                "ESMA guidelines on marketing communications",
                "PRIIPs KID requirements"
            ])
        elif jurisdiction == "US":
            base_queries.extend([
                "SEC Rule 206(4)-1 marketing rule",
                "Investment Advisers Act advertising requirements",
                "Testimonial endorsement rules"
            ])
        
        # Add category-specific queries
        if category:
            category_map = {
                "past_performance": ["past performance disclosures", "historical returns warnings"],
                "risk_warnings": ["capital at risk warnings", "investment risk disclosures"],
                "esg_greenwashing": ["ESG sustainability claims", "greenwashing prohibitions"],
                "crypto_digital_assets": ["cryptoasset promotion rules", "digital asset warnings"]
            }
            base_queries.extend(category_map.get(category, []))
        
        # Convert to QueryTemplate objects
        for i, query_text in enumerate(base_queries):
            template = QueryTemplate(
                id=f"fallback_query_{i}_{uuid.uuid4().hex[:8]}",
                name=f"Fallback {category or 'general'} query {i}",
                jurisdiction=jurisdiction,
                category=category or "general",
                template=query_text,
                priority=10 - i,  # First queries have higher priority
                is_active=True
            )
            templates.append(template)
        
        logger.warning(f"⚠️ Using fallback query templates: {len(templates)} templates")
        return templates
    
    def generate_rag_queries(self, text: str, jurisdiction: str) -> List[str]:
        """Generate RAG queries for document text using dynamic templates"""
        # First, categorize the text
        category = self.categorize_text(text, jurisdiction)
        
        # Get query templates for jurisdiction and category
        templates = self.get_query_templates(jurisdiction, category)
        
        # Also get general templates
        general_templates = self.get_query_templates(jurisdiction)
        
        # Combine and deduplicate
        all_templates = {}
        for template in templates + general_templates:
            if template.id not in all_templates:
                all_templates[template.id] = template
        
        # Generate queries from templates
        queries = []
        text_lower = text.lower()
        
        for template in all_templates.values():
            # Check if template keywords match text
            if template.category != "general":
                # For category-specific templates, check if text contains category keywords
                categories = self.get_categories(jurisdiction)
                category_keywords = []
                for cat in categories:
                    if cat.name == template.category:
                        category_keywords = cat.keywords
                        break
                
                if not any(kw.lower() in text_lower for kw in category_keywords):
                    continue
            
            queries.append(template.template)
            
            # Limit number of queries
            if len(queries) >= 10:
                break
        
        # Add some dynamic queries based on text content
        if "guarantee" in text_lower or "promise" in text_lower:
            queries.append("guaranteed returns prohibitions regulations")
        
        if "past" in text_lower and "performance" in text_lower:
            queries.append("past performance disclosure requirements")
        
        if "crypto" in text_lower or "bitcoin" in text_lower:
            queries.append("cryptoasset promotion rules warnings")
        
        return list(set(queries))[:8]  # Deduplicate and limit
    
    # =========================================================================
    # SYNC WITH AZURE AI SEARCH
    # =========================================================================
    
    def sync_to_knowledge_base(self, jurisdiction: str = None, 
                              batch_size: int = 50) -> Dict:
        """Sync regulatory provisions to Azure AI Search knowledge base"""
        try:
            from .knowledge_base import RegulatoryKnowledgeBase, RegulatoryChunk
            
            kb = RegulatoryKnowledgeBase()
            
            # Get provisions for specified jurisdiction(s)
            jurisdictions = [jurisdiction] if jurisdiction else ["UK", "EU", "US", "GLOBAL"]
            
            all_chunks = []
            for jur in jurisdictions:
                provisions = self.get_provisions(jur, limit=1000)
                
                for prov in provisions:
                    chunk = RegulatoryChunk(
                        id=prov.id,
                        text=prov.text,
                        jurisdiction=prov.jurisdiction,
                        source_document=prov.regulator,
                        section_reference=prov.section_reference,
                        category=prov.category,
                        effective_date=prov.effective_date,
                        last_updated=prov.last_updated,
                        risk_level=prov.risk_level,
                        penalty_info=prov.metadata.get("penalty_info", "") if prov.metadata else ""
                    )
                    all_chunks.append(chunk)
            
            # Ingest into knowledge base
            stats = kb.ingest_bulk(all_chunks, batch_size=batch_size)
            
            logger.info(f"✅ Synced {stats['succeeded']} provisions to knowledge base")
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to sync to knowledge base: {e}")
            return {"error": str(e), "succeeded": 0, "failed": 0}
    
    # =========================================================================
    # MIGRATION HELPERS
    # =========================================================================
    
    def migrate_hardcoded_data(self):
        """Migrate all hardcoded data to Cosmos DB"""
        logger.info("🚀 Starting migration of hardcoded regulatory data...")
        
        try:
            # 1. Migrate categories from knowledge_base.py
            self._migrate_categories()
            
            # 2. Migrate provisions from regulatory_scraper.py
            self._migrate_provisions()
            
            # 3. Migrate query templates (extract from rag_scanner.py logic)
            self._migrate_query_templates()
            
            # 4. Create scraper configurations
            self._create_scraper_configs()
            
            logger.info("✅ Migration complete!")
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            raise
    
    def _migrate_categories(self):
        """Migrate categories from knowledge_base.py"""
        from .knowledge_base import REGULATORY_CATEGORIES
        
        for cat_id, cat_info in REGULATORY_CATEGORIES.items():
            category = RegulatoryCategory(
                id=cat_id,
                name=cat_id.replace("_", " ").title(),
                description=cat_info["description"],
                jurisdictions=["UK", "EU", "US", "GLOBAL"],  # Default to all
                keywords=cat_info.get("keywords", []),
                priority=1,
                risk_weight=1.0
            )
            
            self._save_category(category)
        
        logger.info(f"✅ Migrated {len(REGULATORY_CATEGORIES)} categories")
    
    def _migrate_provisions(self):
        """Migrate provisions from regulatory_scraper.py"""
        from .regulatory_scraper import RegulatoryScraperOrchestrator
        
        orchestrator = RegulatoryScraperOrchestrator()
        regulations = orchestrator.scrape_all()
        
        for reg in regulations:
            provision = RegulatoryProvision(
                id=str(uuid.uuid4()),
                source_id=f"migrated_{reg.regulator.lower()}",
                jurisdiction=reg.jurisdiction,
                regulator=reg.regulator,
                section_reference=reg.section_reference,
                title=reg.title,
                text=reg.text,
                category=reg.category,
                risk_level=reg.risk_level,
                effective_date=reg.effective_date,
                last_updated=datetime.utcnow().isoformat(),
                metadata={
                    "source_url": reg.source_url,
                    "parent_document": reg.parent_document,
                    "penalty_info": reg.penalty_info,
                    "scrape_timestamp": reg.scrape_timestamp,
                    "migrated": True
                },
                is_active=True
            )
            
            self._save_provision(provision)
        
        logger.info(f"✅ Migrated {len(regulations)} provisions")
    
    def _migrate_query_templates(self):
        """Extract query templates from rag_scanner.py logic"""
        # These are extracted from _extract_search_queries method
        query_definitions = [
            {
                "name": "Guaranteed returns prohibition",
                "jurisdiction": "GLOBAL",
                "category": "guarantees_promises",
                "template": "guaranteed returns prohibited promise assurance",
                "priority": 10
            },
            {
                "name": "Past performance warnings",
                "jurisdiction": "GLOBAL",
                "category": "past_performance",
                "template": "past performance warnings required historical returns disclosure",
                "priority": 9
            },
            {
                "name": "Risk warnings",
                "jurisdiction": "GLOBAL",
                "category": "risk_warnings",
                "template": "risk warnings capital at risk loss disclosure",
                "priority": 9
            },
            {
                "name": "ESG greenwashing",
                "jurisdiction": "GLOBAL",
                "category": "esg_greenwashing",
                "template": "ESG sustainability greenwashing environmental claims",
                "priority": 8
            },
            {
                "name": "Cryptoasset promotions",
                "jurisdiction": "UK",
                "category": "crypto_digital_assets",
                "template": "cryptoasset digital asset promotion warnings FCA COBS 4.12A",
                "priority": 8
            }
        ]
        
        for query_def in query_definitions:
            template = QueryTemplate(
                id=str(uuid.uuid4()),
                name=query_def["name"],
                jurisdiction=query_def["jurisdiction"],
                category=query_def["category"],
                template=query_def["template"],
                priority=query_def["priority"],
                is_active=True
            )
            
            self._save_query_template(template)
        
        logger.info(f"✅ Migrated {len(query_definitions)} query templates")
    
    def _create_scraper_configs(self):
        """Create scraper configurations for dynamic scraping"""
        scraper_configs = [
            {
                "name": "FCA Handbook Scraper",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "url": "https://www.handbook.fca.org.uk",
                "source_type": "handbook",
                "selectors": {
                    "sections": {
                        "COBS/4/2": "fair_clear_not_misleading",
                        "COBS/4/6": "past_performance",
                        "PRIN/2A": "consumer_duty"
                    }
                }
            },
            {
                "name": "ESMA MiFID II Scraper",
                "jurisdiction": "EU",
                "regulator": "ESMA",
                "url": "https://www.esma.europa.eu",
                "source_type": "regulation",
                "selectors": {
                    "articles": ["24", "25"]
                }
            }
        ]
        
        for config_def in scraper_configs:
            config = ScraperConfig(
                id=str(uuid.uuid4()),
                name=config_def["name"],
                jurisdiction=config_def["jurisdiction"],
                regulator=config_def["regulator"],
                url=config_def["url"],
                source_type=config_def["source_type"],
                selectors=config_def["selectors"],
                schedule="weekly",
                is_active=True
            )
            
            self._save_scraper_config(config)
        
        logger.info(f"✅ Created {len(scraper_configs)} scraper configurations")
    
    def _save_provision(self, provision: RegulatoryProvision):
        """Save provision to Cosmos DB"""
        self.container.create_item(provision.__dict__)
    
    def _save_category(self, category: RegulatoryCategory):
        """Save category to Cosmos DB"""
        self.container.create_item(category.__dict__)
    
    def _save_query_template(self, template: QueryTemplate):
        """Save query template to Cosmos DB"""
        self.container.create_item(template.__dict__)
    
    def _save_scraper_config(self, config: ScraperConfig):
        """Save scraper config to Cosmos DB"""
        self.container.create_item(config.__dict__)
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def clear_cache(self):
        """Clear all cached data"""
        self._init_cache()
        self.get_provisions.cache_clear()
        self.get_categories.cache_clear()
        self.get_query_templates.cache_clear()
        logger.info("✅ Cache cleared")
    
    def get_stats(self) -> Dict:
        """Get regulatory data statistics"""
        stats = {
            "provisions": {},
            "categories": 0,
            "scrapers": 0,
            "queries": 0
        }
        
        # Count provisions by jurisdiction
        for jurisdiction in ["UK", "EU", "US", "GLOBAL"]:
            provisions = self.get_provisions(jurisdiction, limit=10000)
            stats["provisions"][jurisdiction] = len(provisions)
        
        # Count other items
        stats["categories"] = len(self.get_categories())
        stats["scrapers"] = len(self.get_scraper_configs(active_only=False))
        stats["queries"] = len(self.get_query_templates("GLOBAL"))
        
        return stats


# Global instance with fallback enabled (for transition period)
regulatory_service = RegulatoryDataService(fallback_enabled=True)


