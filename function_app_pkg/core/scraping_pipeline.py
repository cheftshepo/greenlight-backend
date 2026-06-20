"""
Complete Regulatory Scraping Pipeline 
Now uses AIRegulatoryScraper and scrapes ALL jurisdiction
"""

import asyncio
import logging
from typing import List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class RegulatoryScrapingPipeline:
    """
    Full pipeline: Scrape → Store → Index
    Now properly configured for ALL jurisdictions
    """
    
    def __init__(self):
        self.scraper = None
        self.kb = None
        self._initialized = False
    
    def _initialize(self):
        """Lazy initialization"""
        if self._initialized:
            return
        
        try:
            # Use the ADVANCED AI scraper
            from function_app_pkg.core.ai_regulatory_scraper import AIRegulatoryScraper
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            
            self.scraper = AIRegulatoryScraper()
            self.kb = RegulatoryKnowledgeBase()
            self._initialized = True
            logger.info("✅ Scraping pipeline initialized with AIRegulatoryScraper")
        except Exception as e:
            logger.error(f"❌ Pipeline initialization failed: {e}")
            raise
    
    def get_all_jurisdiction_configs(self) -> List[Dict]:
        """
        Get ALL jurisdiction configurations from jurisdiction-config.py
        This includes UK, US, EU, AU, ZA (South Africa), CA, SG, HK, and GLOBAL
        """
        try:
            # Import from your jurisdiction config file
            import sys
            import os
            
            # Add the core directory to path
            core_path = os.path.join(os.path.dirname(__file__), 'function_app_pkg', 'core')
            if core_path not in sys.path:
                sys.path.insert(0, core_path)
            
            # Try different import methods
            try:
                from function_app_pkg.core import jurisdiction_config
                sources_module = jurisdiction_config
            except ImportError:
                # Fallback: direct import
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "jurisdiction_config", 
                    os.path.join(core_path, "jurisdiction-config.py")
                )
                sources_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sources_module)
            
            # Get all source lists
            all_sources = []
            
            # Priority 1 sources for each jurisdiction
            source_lists = {
                "UK": getattr(sources_module, "UK_SOURCES", []),
                "US": getattr(sources_module, "US_SOURCES", []),
                "EU": getattr(sources_module, "EU_SOURCES", []),
                "AU": getattr(sources_module, "AU_SOURCES", []),
                "ZA": getattr(sources_module, "ZA_SOURCES", []),  # South Africa
                "CA": getattr(sources_module, "CA_SOURCES", []),
                "SG": getattr(sources_module, "SG_SOURCES", []),
                "HK": getattr(sources_module, "HK_SOURCES", []),
                "GLOBAL": getattr(sources_module, "GLOBAL_SOURCES", [])
            }
            
            logger.info(f"📚 Found {len(source_lists)} jurisdiction source lists")
            
            # Convert to scraper configs (only priority 1 sources)
            for jurisdiction, sources in source_lists.items():
                priority_sources = [s for s in sources if s.priority == 1]
                
                logger.info(f"  {jurisdiction}: {len(priority_sources)} priority sources")
                
                for source in priority_sources:
                    config = {
                        "name": f"{jurisdiction} - {source.regulator}",
                        "url": source.url,
                        "jurisdiction": jurisdiction,
                        "regulator": source.regulator,
                        "source_type": source.source_type.value,
                        "categories": [cat.value for cat in source.categories],
                        "description": source.notes or f"{source.name}"
                    }
                    all_sources.append(config)
            
            logger.info(f"✅ Total sources to scrape: {len(all_sources)}")
            return all_sources
            
        except Exception as e:
            logger.error(f"❌ Failed to load jurisdiction configs: {e}")
            # Fallback to minimal config
            return self._get_fallback_configs()
    
    def _get_fallback_configs(self) -> List[Dict]:
        """Fallback configs if jurisdiction-config.py can't be loaded"""
        logger.warning("⚠️ Using fallback jurisdiction configs")
        
        return [
            # UK
            {
                "name": "UK - FCA",
                "url": "https://www.handbook.fca.org.uk/handbook/COBS/4.html",
                "jurisdiction": "UK",
                "regulator": "FCA",
                "description": "FCA COBS 4 - Financial Promotions"
            },
            # EU
            {
                "name": "EU - ESMA",
                "url": "https://www.esma.europa.eu/sites/default/files/library/esma34-45-1272_guidelines_on_marketing_communications.pdf",
                "jurisdiction": "EU",
                "regulator": "ESMA",
                "description": "ESMA Marketing Communications Guidelines"
            },
            # US
            {
                "name": "US - SEC",
                "url": "https://www.sec.gov/rules/final/2020/ia-5653.pdf",
                "jurisdiction": "US",
                "regulator": "SEC",
                "description": "SEC Investment Adviser Marketing Rule"
            },
            # SOUTH AFRICA - This was missing!
            {
                "name": "ZA - FSCA",
                "url": "https://www.fsca.co.za/Regulatory%20Frameworks/Temp/FAIS%20General%20Code%20of%20Conduct.pdf",
                "jurisdiction": "ZA",
                "regulator": "FSCA",
                "description": "FAIS General Code of Conduct"
            },
            # Australia
            {
                "name": "AU - ASIC",
                "url": "https://download.asic.gov.au/media/3278289/rg234-published-9-november-2015.pdf",
                "jurisdiction": "AU",
                "regulator": "ASIC",
                "description": "ASIC RG 234 - Advertising Financial Products"
            }
        ]
    
    async def scrape_jurisdiction(self, config: Dict) -> List:
        """Scrape a single jurisdiction using AIRegulatoryScraper"""
        self._initialize()
        
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"📚 Scraping: {config['name']}")
            logger.info(f"   URL: {config['url'][:80]}...")
            logger.info(f"{'='*60}")
            
            # Use the AI scraper's parallel method
            regulations = await self.scraper.scrape_jurisdiction_parallel(
                jurisdiction_config=config,
                max_concurrent=2  # Conservative to avoid rate limits
            )
            
            if regulations:
                logger.info(f"✅ Scraped {len(regulations)} regulations from {config['name']}")
                
                # Save to Cosmos DB immediately
                save_stats = await self.scraper.save_regulations_to_cosmos(
                    regulations=regulations,
                    batch_size=25
                )
                
                logger.info(f"💾 Saved {save_stats['saved']}/{save_stats['total']} to Cosmos DB")
                logger.info(f"🔍 Indexed {save_stats['indexed']}/{save_stats['total']} to AI Search")
                
                return regulations
            else:
                logger.warning(f"⚠️ No regulations scraped from {config['name']}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Failed to scrape {config['name']}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    async def run_full_scrape(self, jurisdictions: List[str] = None) -> Dict:
        """
        Scrape all jurisdictions or specific ones
        
        Args:
            jurisdictions: List of jurisdiction codes (e.g., ["UK", "ZA", "US"]) 
                         or None to scrape all
        """
        logger.info("🚀 Starting full regulatory scrape...")
        
        # Get all available configs
        all_configs = self.get_all_jurisdiction_configs()
        
        # Filter by requested jurisdictions if specified
        if jurisdictions:
            jurisdictions_upper = [j.upper() for j in jurisdictions]
            configs_to_scrape = [
                c for c in all_configs 
                if c['jurisdiction'].upper() in jurisdictions_upper
            ]
            logger.info(f"🎯 Scraping {len(configs_to_scrape)} sources from jurisdictions: {jurisdictions}")
        else:
            configs_to_scrape = all_configs
            logger.info(f"🌍 Scraping ALL {len(configs_to_scrape)} sources")
        
        if not configs_to_scrape:
            logger.error("❌ No sources to scrape!")
            return {"error": "No sources configured"}
        
        stats = {
            "start_time": datetime.utcnow().isoformat(),
            "total_sources": len(configs_to_scrape),
            "total_scraped": 0,
            "total_saved": 0,
            "total_indexed": 0,
            "by_jurisdiction": {},
            "errors": [],
            "successful_sources": [],
            "failed_sources": []
        }
        
        # Scrape each source
        for i, config in enumerate(configs_to_scrape, 1):
            jurisdiction = config['jurisdiction']
            
            logger.info(f"\n{'#'*60}")
            logger.info(f"Progress: {i}/{len(configs_to_scrape)}")
            logger.info(f"{'#'*60}")
            
            try:
                regulations = await self.scrape_jurisdiction(config)
                
                if regulations:
                    # Update stats
                    stats["total_scraped"] += len(regulations)
                    stats["successful_sources"].append(config['name'])
                    
                    # Track by jurisdiction
                    if jurisdiction not in stats["by_jurisdiction"]:
                        stats["by_jurisdiction"][jurisdiction] = {
                            "scraped": 0,
                            "sources": []
                        }
                    
                    stats["by_jurisdiction"][jurisdiction]["scraped"] += len(regulations)
                    stats["by_jurisdiction"][jurisdiction]["sources"].append(config['name'])
                else:
                    stats["failed_sources"].append(config['name'])
                    
            except Exception as e:
                logger.error(f"❌ Source failed: {config['name']}: {e}")
                stats["errors"].append(f"{config['name']}: {str(e)[:100]}")
                stats["failed_sources"].append(config['name'])
            
            # Small delay between sources to be nice to servers
            await asyncio.sleep(2)
        
        # Cleanup
        if self.scraper:
            try:
                await self.scraper.close()
            except:
                pass
        
        stats["end_time"] = datetime.utcnow().isoformat()
        
        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("✅ SCRAPING PIPELINE COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Sources processed: {stats['total_sources']}")
        logger.info(f"Total regulations: {stats['total_scraped']}")
        logger.info(f"Successful sources: {len(stats['successful_sources'])}")
        logger.info(f"Failed sources: {len(stats['failed_sources'])}")
        logger.info(f"\nBy Jurisdiction:")
        for jur, data in stats["by_jurisdiction"].items():
            logger.info(f"  {jur}: {data['scraped']} regulations from {len(data['sources'])} sources")
        
        if stats["errors"]:
            logger.warning(f"\n⚠️ Errors encountered: {len(stats['errors'])}")
            for error in stats["errors"][:5]:  # Show first 5
                logger.warning(f"  - {error}")
        
        logger.info("=" * 80)
        
        return stats


async def run_scrape(jurisdictions: List[str] = None):
    """
    Run the complete pipeline
    
    Examples:
        await run_scrape()  # Scrape everything
        await run_scrape(["ZA"])  # Just South Africa
        await run_scrape(["UK", "ZA", "US"])  # Multiple jurisdictions
    """
    pipeline = RegulatoryScrapingPipeline()
    return await pipeline.run_full_scrape(jurisdictions=jurisdictions)


# CLI entry point
if __name__ == "__main__":
    import sys
    
    # Allow specifying jurisdictions via command line
    # python scraping_pipeline_fixed.py ZA UK US
    jurisdictions = sys.argv[1:] if len(sys.argv) > 1 else None
    
    if jurisdictions:
        print(f"🎯 Scraping jurisdictions: {', '.join(jurisdictions)}")
    else:
        print("🌍 Scraping ALL jurisdictions")
    
    asyncio.run(run_scrape(jurisdictions))