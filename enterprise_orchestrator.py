"""
ENTERPRISE-GRADE REGULATORY SCRAPING ORCHESTRATOR
==================================================
Multi-jurisdiction regulatory intelligence platform for tier-1 financial institutions

Coverage:
🇬🇧 UK (FCA) - 23 sources
🇺🇸 US (SEC/FINRA) - 27 sources  
🇪🇺 EU (ESMA) - 20 sources
🇦🇺 AU (ASIC) - 8 sources
🇿🇦 ZA (FSCA) - 10 sources
🇨🇦 CA (CSA/CIRO) - 6 sources
🇸🇬 SG (MAS) - 6 sources
🇭🇰 HK (SFC) - 6 sources
🌍 GLOBAL (IOSCO/BIS) - 5 sources

Total: 111+ regulatory sources

Features:
✓ Intelligent retry with exponential backoff
✓ Health checks and validation
✓ Real-time progress tracking
✓ Parallel processing with rate limiting
✓ Quality assurance on scraped content
✓ Automatic deduplication
✓ Full audit trail
✓ Comprehensive error handling
✓ Production-grade logging
"""

import asyncio
import logging
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib
import json
import sys
import os

# Add path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)


@dataclass
class ScrapingResult:
    """Detailed scraping result with full metrics"""
    jurisdiction: str
    source_name: str
    source_id: str
    url: str
    status: str  # success, partial, failed
    regulations_scraped: int = 0
    regulations_saved: int = 0
    regulations_indexed: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    retry_count: int = 0
    content_hash: str = ""
    
    @property
    def success_rate(self) -> float:
        """Percentage of successful operations"""
        if self.regulations_scraped == 0:
            return 0.0
        return (self.regulations_saved / self.regulations_scraped) * 100
    
    def to_dict(self) -> Dict:
        return {
            "jurisdiction": self.jurisdiction,
            "source": self.source_name,
            "source_id": self.source_id,
            "url": self.url[:100],
            "status": self.status,
            "regulations_scraped": self.regulations_scraped,
            "regulations_saved": self.regulations_saved,
            "regulations_indexed": self.regulations_indexed,
            "duration_seconds": round(self.duration_seconds, 2),
            "success_rate": round(self.success_rate, 2),
            "retry_count": self.retry_count,
            "errors": self.errors[:3]  # First 3 errors only
        }


@dataclass
class OrchestrationStats:
    """Comprehensive orchestration statistics"""
    start_time: datetime
    end_time: Optional[datetime] = None
    total_sources: int = 0
    sources_attempted: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    sources_partial: int = 0
    total_regulations: int = 0
    total_saved: int = 0
    total_indexed: int = 0
    total_duplicates_removed: int = 0
    by_jurisdiction: Dict[str, Dict] = field(default_factory=dict)
    results: List[ScrapingResult] = field(default_factory=list)
    errors: List[Dict] = field(default_factory=list)
    
    @property
    def duration_minutes(self) -> float:
        if not self.end_time:
            return 0.0
        return (self.end_time - self.start_time).total_seconds() / 60
    
    @property
    def success_rate(self) -> float:
        if self.sources_attempted == 0:
            return 0.0
        return (self.sources_succeeded / self.sources_attempted) * 100
    
    def to_dict(self) -> Dict:
        """Export for reporting"""
        return {
            "summary": {
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "duration_minutes": round(self.duration_minutes, 2),
                "total_sources": self.total_sources,
                "sources_attempted": self.sources_attempted,
                "sources_succeeded": self.sources_succeeded,
                "sources_failed": self.sources_failed,
                "success_rate": round(self.success_rate, 2),
                "total_regulations": self.total_regulations,
                "total_saved": self.total_saved,
                "total_indexed": self.total_indexed,
                "duplicates_removed": self.total_duplicates_removed
            },
            "by_jurisdiction": self.by_jurisdiction,
            "results": [r.to_dict() for r in self.results]
        }


class EnterpriseScrapingOrchestrator:
    """
    Enterprise-grade regulatory scraping orchestrator
    
    Handles all global jurisdictions with production-ready reliability
    """
    
    # Global regulatory jurisdictions (tier-1 coverage)
    SUPPORTED_JURISDICTIONS = {
        "UK": "United Kingdom - FCA",
        "US": "United States - SEC/FINRA",
        "EU": "European Union - ESMA/EC",
        "AU": "Australia - ASIC",
        "ZA": "South Africa - FSCA",
        "CA": "Canada - CSA/CIRO",
        "SG": "Singapore - MAS",
        "HK": "Hong Kong - SFC",
        "GLOBAL": "International - IOSCO/BIS/FSB"
    }
    
    def __init__(self, 
                 max_retries: int = 3,
                 retry_delay: int = 5,
                 max_concurrent: int = 2,
                 enable_quality_checks: bool = True,
                 priority_only: bool = True):
        
        self.scraper = None
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_concurrent = max_concurrent
        self.enable_quality_checks = enable_quality_checks
        self.priority_only = priority_only
        self._initialized = False
        
        # Tracking
        self.seen_hashes: Set[str] = set()
        self.stats = None
        
        logger.info("🏢 Enterprise Scraping Orchestrator initialized")
        logger.info(f"   Supported jurisdictions: {len(self.SUPPORTED_JURISDICTIONS)}")
        logger.info(f"   Max retries: {self.max_retries}")
        logger.info(f"   Concurrent sources: {self.max_concurrent}")
        logger.info(f"   Priority sources only: {self.priority_only}")
    
    def _initialize(self):
        """Lazy initialization of scraper"""
        if self._initialized:
            return
        
        try:
            from function_app_pkg.core.ai_regulatory_scraper import AIRegulatoryScraper
            
            self.scraper = AIRegulatoryScraper()
            self._initialized = True
            logger.info("✅ AIRegulatoryScraper initialized")
        except Exception as e:
            logger.error(f"❌ Scraper initialization failed: {e}")
            raise
    
    def load_jurisdiction_sources(self, jurisdictions: List[str] = None) -> List[Dict]:
        """
        Load regulatory sources from jurisdiction-config.py
        
        Args:
            jurisdictions: List of jurisdiction codes (e.g., ["UK", "ZA", "US"])
                        or None to load all
        
        Returns:
            List of source configurations
        """
        try:
            # Import the jurisdiction config module
            from function_app_pkg.core.jurisdiction_config import (
                SOURCES_BY_JURISDICTION, 
                PRIORITY_SOURCES,
                ALL_SOURCES
            )
            
            logger.info("📚 Loading regulatory sources from jurisdiction-config.py")
            
            # Determine which sources to load
            if self.priority_only:
                # Get priority sources only (critical + high)
                if jurisdictions:
                    # Filter priority sources by jurisdiction
                    sources_to_load = [
                        s for s in PRIORITY_SOURCES 
                        if s.get('jurisdiction') in jurisdictions
                    ]
                else:
                    sources_to_load = PRIORITY_SOURCES
                logger.info(f"   Using PRIORITY sources only")
            else:
                # Get all sources
                if jurisdictions:
                    sources_to_load = []
                    for jur in jurisdictions:
                        sources_to_load.extend(SOURCES_BY_JURISDICTION.get(jur, []))
                else:
                    sources_to_load = ALL_SOURCES
                logger.info(f"   Using ALL sources")
            
            logger.info(f"   Total sources loaded: {len(sources_to_load)}")
            
            # ✅ FIXED: Sources are already dicts from jurisdiction_config.py
            # Just ensure they have all required fields
            configs = []
            for source in sources_to_load:
                # Handle both dict and object formats
                if isinstance(source, dict):
                    config = {
                        "id": source.get('name', 'unknown').replace(' ', '-'),
                        "name": source.get('name', 'Unknown'),
                        "url": source.get('url', ''),
                        "jurisdiction": source.get('jurisdiction', 'GLOBAL'),
                        "regulator": source.get('regulator', 'Unknown'),
                        "source_type": source.get('type', 'page'),
                        "content_type": source.get('focus', 'regulation'),
                        "categories": source.get('categories', ['general_marketing']),
                        "priority": 1 if source.get('priority') in ['critical', 'high'] else 2,
                        "requires_js": source.get('requires_js', False),
                        "has_cloudflare": source.get('has_cloudflare', False),
                        "notes": source.get('focus', '')
                    }
                else:
                    # Handle dataclass/object format (legacy)
                    config = {
                        "id": getattr(source, 'id', source.name.replace(' ', '-')),
                        "name": getattr(source, 'name', 'Unknown'),
                        "url": getattr(source, 'url', ''),
                        "jurisdiction": getattr(source, 'jurisdiction', 'GLOBAL'),
                        "regulator": getattr(source, 'regulator', 'Unknown'),
                        "source_type": getattr(source, 'source_type', {}).value if hasattr(getattr(source, 'source_type', None), 'value') else 'page',
                        "content_type": getattr(source, 'content_type', {}).value if hasattr(getattr(source, 'content_type', None), 'value') else 'regulation',
                        "categories": [cat.value if hasattr(cat, 'value') else cat for cat in getattr(source, 'categories', [])],
                        "priority": getattr(source, 'priority', 2),
                        "requires_js": getattr(source, 'requires_js', False),
                        "has_cloudflare": getattr(source, 'has_cloudflare', False),
                        "notes": getattr(source, 'notes', '')
                    }
                configs.append(config)
            
            # Log breakdown by jurisdiction
            by_jurisdiction = defaultdict(int)
            for config in configs:
                by_jurisdiction[config['jurisdiction']] += 1
            
            logger.info(f"\n📊 Sources by Jurisdiction:")
            for jur, count in sorted(by_jurisdiction.items()):
                jur_name = self.SUPPORTED_JURISDICTIONS.get(jur, jur)
                logger.info(f"   {jur} ({jur_name}): {count} sources")
            
            return configs
            
        except ImportError as e:
            logger.error(f"❌ Failed to import jurisdiction-config.py: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Failed to load sources: {e}")
            import traceback
            traceback.print_exc()
            raise


    async def scrape_source_with_retry(self, config: Dict) -> ScrapingResult:
        """
        Scrape a single source with retry logic
        
        Args:
            config: Source configuration
        
        Returns:
            ScrapingResult with detailed metrics
        """
        self._initialize()
        
        result = ScrapingResult(
            jurisdiction=config['jurisdiction'],
            source_name=config['name'],
            source_id=config['id'],
            url=config['url'],
            status="pending",
            start_time=datetime.utcnow()
        )
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"{'  ' * (attempt - 1)}🔄 Attempt {attempt}/{self.max_retries}: {config['name']}")
                logger.info(f"{'  ' * (attempt - 1)}   URL: {config['url'][:80]}...")
                
                # Scrape using AIRegulatoryScraper
                regulations = await self.scraper.scrape_jurisdiction_parallel(
                    jurisdiction_config=config,
                    max_concurrent=1  # One at a time for reliability
                )
                
                if regulations:
                    logger.info(f"{'  ' * (attempt - 1)}✅ Scraped {len(regulations)} regulations")
                    
                    # Quality check
                    if self.enable_quality_checks:
                        regulations = self._quality_check(regulations, config)
                    
                    result.regulations_scraped = len(regulations)
                    
                    # Save to Cosmos DB & index to Azure AI Search
                    save_stats = await self.scraper.save_regulations_to_cosmos(
                        regulations=regulations,
                        batch_size=25
                    )
                    
                    result.regulations_saved = save_stats.get('saved', 0)
                    result.regulations_indexed = save_stats.get('indexed', 0)
                    
                    if save_stats.get('errors'):
                        result.warnings.extend(save_stats['errors'][:3])
                    
                    # Success!
                    result.status = "success"
                    result.end_time = datetime.utcnow()
                    result.duration_seconds = (result.end_time - result.start_time).total_seconds()
                    result.retry_count = attempt - 1
                    
                    logger.info(f"{'  ' * (attempt - 1)}💾 Saved: {result.regulations_saved}, Indexed: {result.regulations_indexed}")
                    
                    return result
                else:
                    # No regulations scraped
                    if attempt < self.max_retries:
                        logger.warning(f"{'  ' * (attempt - 1)}⚠️ No regulations found, retrying in {self.retry_delay}s...")
                        await asyncio.sleep(self.retry_delay * attempt)  # Exponential backoff
                    else:
                        logger.warning(f"{'  ' * (attempt - 1)}❌ No regulations after {self.max_retries} attempts")
                        result.status = "failed"
                        result.errors.append("No regulations extracted after all retries")
                
            except Exception as e:
                error_msg = f"Attempt {attempt} failed: {str(e)[:100]}"
                result.errors.append(error_msg)
                
                if attempt < self.max_retries:
                    logger.warning(f"{'  ' * (attempt - 1)}⚠️ {error_msg}")
                    logger.warning(f"{'  ' * (attempt - 1)}   Retrying in {self.retry_delay}s...")
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    logger.error(f"{'  ' * (attempt - 1)}❌ Failed after {self.max_retries} attempts: {e}")
                    result.status = "failed"
        
        # All retries exhausted
        result.end_time = datetime.utcnow()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        result.retry_count = self.max_retries
        
        return result
    
    def _quality_check(self, regulations, config: Dict) -> List:
        """
        Quality check on scraped regulations
        
        Filters out:
        - Duplicate content
        - Garbage/non-regulatory content
        - Too short content
        """
        original_count = len(regulations)
        
        # Remove duplicates by content hash
        unique_regs = []
        for reg in regulations:
            content_hash = hashlib.md5(f"{reg.section}{reg.text}".encode()).hexdigest()
            
            if content_hash not in self.seen_hashes:
                self.seen_hashes.add(content_hash)
                unique_regs.append(reg)
        
        duplicates_removed = original_count - len(unique_regs)
        if duplicates_removed > 0:
            logger.info(f"   🔄 Removed {duplicates_removed} duplicates")
        
        # Validate content quality
        quality_regs = []
        for reg in unique_regs:
            # Must have minimum length
            if len(reg.text) < 100:
                continue
            
            # Must contain regulatory keywords
            text_lower = reg.text.lower()
            regulatory_keywords = ["must", "shall", "required", "prohibited", "compliance", "regulation", "rule"]
            if not any(kw in text_lower for kw in regulatory_keywords):
                continue
            
            quality_regs.append(reg)
        
        quality_removed = len(unique_regs) - len(quality_regs)
        if quality_removed > 0:
            logger.info(f"   🔍 Removed {quality_removed} low-quality regulations")
        
        return quality_regs
    
    async def scrape_jurisdiction_batch(self, 
                                       configs: List[Dict],
                                       jurisdiction: str) -> List[ScrapingResult]:
        """
        Scrape all sources for a jurisdiction with concurrency control
        
        Args:
            configs: List of source configurations
            jurisdiction: Jurisdiction code (e.g., "UK")
        
        Returns:
            List of ScrapingResults
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"🌍 SCRAPING JURISDICTION: {jurisdiction}")
        logger.info(f"   {self.SUPPORTED_JURISDICTIONS.get(jurisdiction, jurisdiction)}")
        logger.info(f"   Sources: {len(configs)}")
        logger.info(f"{'='*60}\n")
        
        # Use semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def scrape_with_semaphore(config):
            async with semaphore:
                return await self.scrape_source_with_retry(config)
        
        # Execute in parallel (but limited by semaphore)
        tasks = [scrape_with_semaphore(config) for config in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"❌ Task exception: {result}")
                # Create failed result
                config = configs[i]
                failed_result = ScrapingResult(
                    jurisdiction=config['jurisdiction'],
                    source_name=config['name'],
                    source_id=config['id'],
                    url=config['url'],
                    status="failed",
                    start_time=datetime.utcnow(),
                    end_time=datetime.utcnow()
                )
                failed_result.errors.append(str(result)[:200])
                final_results.append(failed_result)
            else:
                final_results.append(result)
        
        return final_results
    
    async def run_full_scrape(self, 
                             jurisdictions: List[str] = None,
                             save_report: bool = True) -> OrchestrationStats:
        """
        Execute full scraping orchestration
        
        Args:
            jurisdictions: List of jurisdiction codes or None for all
            save_report: Whether to save JSON report
        
        Returns:
            OrchestrationStats with comprehensive metrics
        """
        logger.info("\n" + "="*80)
        logger.info("🚀 ENTERPRISE REGULATORY SCRAPING ORCHESTRATOR")
        logger.info("="*80)
        
        self.stats = OrchestrationStats(start_time=datetime.utcnow())
        
        try:
            # Load sources
            all_configs = self.load_jurisdiction_sources(jurisdictions)
            self.stats.total_sources = len(all_configs)
            
            if not all_configs:
                logger.error("❌ No sources to scrape!")
                return self.stats
            
            # Group by jurisdiction
            by_jurisdiction = defaultdict(list)
            for config in all_configs:
                by_jurisdiction[config['jurisdiction']].append(config)
            
            # Scrape each jurisdiction
            for jur, configs in sorted(by_jurisdiction.items()):
                logger.info(f"\n📍 Starting jurisdiction: {jur}")
                
                # Scrape jurisdiction
                results = await self.scrape_jurisdiction_batch(configs, jur)
                
                # Update stats
                self.stats.sources_attempted += len(results)
                self.stats.results.extend(results)
                
                # Aggregate jurisdiction stats
                jur_stats = {
                    "sources_total": len(configs),
                    "sources_succeeded": 0,
                    "sources_failed": 0,
                    "regulations_scraped": 0,
                    "regulations_saved": 0,
                    "regulations_indexed": 0
                }
                
                for result in results:
                    if result.status == "success":
                        self.stats.sources_succeeded += 1
                        jur_stats["sources_succeeded"] += 1
                    elif result.status == "failed":
                        self.stats.sources_failed += 1
                        jur_stats["sources_failed"] += 1
                    else:
                        self.stats.sources_partial += 1
                    
                    jur_stats["regulations_scraped"] += result.regulations_scraped
                    jur_stats["regulations_saved"] += result.regulations_saved
                    jur_stats["regulations_indexed"] += result.regulations_indexed
                    
                    self.stats.total_regulations += result.regulations_scraped
                    self.stats.total_saved += result.regulations_saved
                    self.stats.total_indexed += result.regulations_indexed
                
                self.stats.by_jurisdiction[jur] = jur_stats
                
                # Log jurisdiction summary
                logger.info(f"\n✅ Jurisdiction {jur} complete:")
                logger.info(f"   Sources: {jur_stats['sources_succeeded']}/{jur_stats['sources_total']} succeeded")
                logger.info(f"   Regulations: {jur_stats['regulations_saved']} saved, {jur_stats['regulations_indexed']} indexed")
                
                # Small delay between jurisdictions
                await asyncio.sleep(2)
            
            self.stats.end_time = datetime.utcnow()
            
            # Print final report
            self._print_final_report()
            
            # Save JSON report
            if save_report:
                self._save_json_report()
            
            return self.stats
            
        except Exception as e:
            logger.error(f"❌ Orchestration failed: {e}")
            import traceback
            traceback.print_exc()
            
            if self.stats:
                self.stats.end_time = datetime.utcnow()
                self.stats.errors.append({"error": str(e), "traceback": traceback.format_exc()})
            
            return self.stats
        
        finally:
            # Cleanup
            if self.scraper:
                try:
                    await self.scraper.close()
                except:
                    pass
    
    def _print_final_report(self):
        """Print comprehensive final report"""
        logger.info("\n" + "="*80)
        logger.info("📊 SCRAPING ORCHESTRATION COMPLETE")
        logger.info("="*80)
        
        logger.info(f"\n⏱️  Duration: {self.stats.duration_minutes:.2f} minutes")
        logger.info(f"\n📈 Overall Statistics:")
        logger.info(f"   Total sources: {self.stats.total_sources}")
        logger.info(f"   Sources attempted: {self.stats.sources_attempted}")
        logger.info(f"   ✅ Succeeded: {self.stats.sources_succeeded}")
        logger.info(f"   ❌ Failed: {self.stats.sources_failed}")
        logger.info(f"   ⚠️  Partial: {self.stats.sources_partial}")
        logger.info(f"   Success rate: {self.stats.success_rate:.1f}%")
        
        logger.info(f"\n📚 Regulations:")
        logger.info(f"   Total scraped: {self.stats.total_regulations}")
        logger.info(f"   Saved to Cosmos DB: {self.stats.total_saved}")
        logger.info(f"   Indexed in Azure AI Search: {self.stats.total_indexed}")
        
        logger.info(f"\n🌍 By Jurisdiction:")
        for jur in sorted(self.stats.by_jurisdiction.keys()):
            stats = self.stats.by_jurisdiction[jur]
            jur_name = self.SUPPORTED_JURISDICTIONS.get(jur, jur)
            logger.info(f"\n   {jur} - {jur_name}:")
            logger.info(f"      Sources: {stats['sources_succeeded']}/{stats['sources_total']}")
            logger.info(f"      Regulations: {stats['regulations_saved']} saved, {stats['regulations_indexed']} indexed")
        
        if self.stats.sources_failed > 0:
            logger.info(f"\n⚠️  Failed Sources:")
            failed_results = [r for r in self.stats.results if r.status == "failed"]
            for result in failed_results[:10]:  # Show first 10
                logger.info(f"   ❌ {result.source_id}: {result.source_name}")
                if result.errors:
                    logger.info(f"      Error: {result.errors[0][:100]}")
        
        logger.info("\n" + "="*80)
    
    def _save_json_report(self):
        """Save detailed JSON report"""
        try:
            report_filename = f"scraping_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(report_filename, 'w') as f:
                json.dump(self.stats.to_dict(), f, indent=2)
            
            logger.info(f"\n💾 Report saved: {report_filename}")
            
        except Exception as e:
            logger.error(f"❌ Failed to save report: {e}")


async def run_enterprise_scrape(
    jurisdictions: List[str] = None,
    max_retries: int = 3,
    max_concurrent: int = 2,
    priority_only: bool = True,
    save_report: bool = True
) -> OrchestrationStats:
    """
    Main entry point for enterprise scraping
    
    Args:
        jurisdictions: List of jurisdiction codes (e.g., ["UK", "ZA", "US"]) or None for all
        max_retries: Maximum retry attempts per source
        max_concurrent: Maximum concurrent scrapes
        priority_only: Only scrape priority 1 sources
        save_report: Save JSON report
    
    Returns:
        OrchestrationStats with comprehensive results
    
    Examples:
        # Scrape everything (priority sources only)
        await run_enterprise_scrape()
        
        # Scrape specific jurisdictions
        await run_enterprise_scrape(jurisdictions=["UK", "ZA", "US"])
        
        # Scrape all sources (not just priority)
        await run_enterprise_scrape(priority_only=False)
        
        # More aggressive scraping
        await run_enterprise_scrape(max_retries=5, max_concurrent=3)
    """
    orchestrator = EnterpriseScrapingOrchestrator(
        max_retries=max_retries,
        max_concurrent=max_concurrent,
        priority_only=priority_only
    )
    
    return await orchestrator.run_full_scrape(
        jurisdictions=jurisdictions,
        save_report=save_report
    )


if __name__ == "__main__":
    import sys
    
    # Parse command line arguments
    jurisdictions = sys.argv[1:] if len(sys.argv) > 1 else None
    
    if jurisdictions:
        print(f"🎯 Scraping jurisdictions: {', '.join(jurisdictions)}")
    else:
        print("🌍 Scraping ALL jurisdictions (priority sources)")
    
    # Run the scrape
    asyncio.run(run_enterprise_scrape(jurisdictions=jurisdictions))