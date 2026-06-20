#!/usr/bin/env python3
"""
ENTERPRISE REGULATORY INTELLIGENCE PLATFORM
===========================================
Setup & Management CLI for Tier-1 Financial Institutions

Global Coverage:
🇬🇧 UK (FCA) - 23 priority sources
🇺🇸 US (SEC/FINRA) - 27 priority sources
🇪🇺 EU (ESMA/EC) - 20 priority sources
🇦🇺 AU (ASIC) - 8 priority sources
🇿🇦 ZA (FSCA) - 10 priority sources
🇨🇦 CA (CSA/CIRO) - 6 priority sources
🇸🇬 SG (MAS) - 6 priority sources
🇭🇰 HK (SFC) - 6 priority sources
🌍 GLOBAL (IOSCO/BIS) - 5 priority sources

Total: 111+ regulatory sources

Usage:
    python setup_rag.py --all                          # Scrape ALL jurisdictions
    python setup_rag.py --jurisdictions ZA UK US       # Specific jurisdictions
    python setup_rag.py --check                        # Health check
    python setup_rag.py --stats                        # Detailed statistics
    python setup_rag.py --list                         # List available sources
"""

import asyncio
import argparse
import logging
import sys
import os
from datetime import datetime
from typing import List, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f'scraping_{datetime.now().strftime("%Y%m%d")}.log')
    ]
)
logger = logging.getLogger(__name__)


class EnterpriseRegulatorySetup:
    """
    Enterprise regulatory intelligence setup and management
    """
    
    SUPPORTED_JURISDICTIONS = {
        "UK": "🇬🇧 United Kingdom (FCA)",
        "US": "🇺🇸 United States (SEC/FINRA)",
        "EU": "🇪🇺 European Union (ESMA)",
        "AU": "🇦🇺 Australia (ASIC)",
        "ZA": "🇿🇦 South Africa (FSCA)",
        "CA": "🇨🇦 Canada (CSA/CIRO)",
        "SG": "🇸🇬 Singapore (MAS)",
        "HK": "🇭🇰 Hong Kong (SFC)",
        "GLOBAL": "🌍 Global (IOSCO/BIS/FSB)"
    }
    
    async def run_scrape(self, 
                        jurisdictions: Optional[List[str]] = None,
                        max_retries: int = 3,
                        max_concurrent: int = 2,
                        priority_only: bool = True,
                        save_report: bool = True) -> dict:
        """
        Execute regulatory scraping
        
        Args:
            jurisdictions: List of jurisdiction codes or None for all
            max_retries: Retry attempts per source
            max_concurrent: Concurrent scrapes
            priority_only: Only priority 1 sources
            save_report: Save JSON report
        
        Returns:
            Statistics dictionary
        """
        try:
            from enterprise_orchestrator import run_enterprise_scrape
            
            logger.info("="*80)
            logger.info("🚀 ENTERPRISE REGULATORY INTELLIGENCE PLATFORM")
            logger.info("="*80)
            
            if jurisdictions:
                logger.info(f"\n🎯 Target Jurisdictions:")
                for jur in jurisdictions:
                    logger.info(f"   {self.SUPPORTED_JURISDICTIONS.get(jur.upper(), jur)}")
            else:
                logger.info(f"\n🌍 Scraping ALL Jurisdictions:")
                for jur, name in self.SUPPORTED_JURISDICTIONS.items():
                    logger.info(f"   {name}")
            
            logger.info(f"\n⚙️  Configuration:")
            logger.info(f"   Max retries: {max_retries}")
            logger.info(f"   Concurrent sources: {max_concurrent}")
            logger.info(f"   Priority sources only: {priority_only}")
            logger.info(f"   Save report: {save_report}")
            logger.info("")
            
            # Run the scrape
            stats = await run_enterprise_scrape(
                jurisdictions=jurisdictions,
                max_retries=max_retries,
                max_concurrent=max_concurrent,
                priority_only=priority_only,
                save_report=save_report
            )
            
            # Return summary
            return stats.to_dict()
            
        except Exception as e:
            logger.error(f"❌ Scraping failed: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
    
    def check_database(self):
        """Check Cosmos DB contents"""
        try:
            from function_app_pkg.core.database import CosmosDBClient
            
            logger.info("\n" + "="*80)
            logger.info("💾 COSMOS DB HEALTH CHECK")
            logger.info("="*80)
            
            db = CosmosDBClient()
            container = db.get_container("rules")
            
            if not container:
                logger.error("❌ Rules container not found!")
                return
            
            # Count by jurisdiction
            query = """
            SELECT c.jurisdiction, COUNT(1) as count
            FROM c
            WHERE c.type = 'regulation'
            GROUP BY c.jurisdiction
            """
            
            results = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            if not results:
                logger.warning("⚠️  Database is empty - no regulations found")
                logger.info("\n💡 Run: python setup_rag.py --all")
                return
            
            total = 0
            logger.info(f"\n📊 Regulations by Jurisdiction:")
            for item in sorted(results, key=lambda x: x['count'], reverse=True):
                jur = item['jurisdiction']
                count = item['count']
                total += count
                jur_name = self.SUPPORTED_JURISDICTIONS.get(jur, jur)
                logger.info(f"   {jur_name}: {count:,} regulations")
            
            logger.info(f"\n✅ Total: {total:,} regulations")
            
            # Check for missing jurisdictions
            found_jurs = {item['jurisdiction'] for item in results}
            missing_jurs = set(self.SUPPORTED_JURISDICTIONS.keys()) - found_jurs
            
            if missing_jurs:
                logger.warning(f"\n⚠️  Missing Jurisdictions:")
                for jur in sorted(missing_jurs):
                    logger.warning(f"   {self.SUPPORTED_JURISDICTIONS[jur]}")
                logger.info(f"\n💡 Run: python setup_rag.py --jurisdictions {' '.join(sorted(missing_jurs))}")
            
            logger.info("="*80)
            
        except Exception as e:
            logger.error(f"❌ Database check failed: {e}")
            import traceback
            traceback.print_exc()
    
    def check_knowledge_base(self):
        """Check Azure AI Search index"""
        try:
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            
            logger.info("\n" + "="*80)
            logger.info("🔍 AZURE AI SEARCH HEALTH CHECK")
            logger.info("="*80)
            
            kb = RegulatoryKnowledgeBase()
            kb._ensure_initialized()
            
            # Get health status
            health = kb.health_check()
            
            logger.info(f"\n📊 Status: {health['status'].upper()}")
            logger.info(f"   Search client: {'✅' if health['search_client'] else '❌'}")
            logger.info(f"   Embedding client: {'✅' if health['embed_client'] else '❌'}")
            logger.info(f"   Index exists: {'✅' if health['index_exists'] else '❌'}")
            logger.info(f"   Document count: {health['document_count']:,}")
            
            if health['errors']:
                logger.warning(f"\n⚠️  Errors:")
                for error in health['errors']:
                    logger.warning(f"   {error}")
            
            # Get detailed stats
            if health['search_client']:
                stats = kb.get_stats()
                
                if stats.get('jurisdictions'):
                    logger.info(f"\n📊 Regulations by Jurisdiction:")
                    for jur, count in sorted(stats['jurisdictions'].items(), key=lambda x: x[1], reverse=True):
                        jur_name = self.SUPPORTED_JURISDICTIONS.get(jur, jur)
                        logger.info(f"   {jur_name}: {count:,} chunks")
                
                if stats.get('categories'):
                    logger.info(f"\n📂 Top Categories:")
                    top_cats = sorted(stats['categories'].items(), key=lambda x: x[1], reverse=True)[:5]
                    for cat, count in top_cats:
                        logger.info(f"   {cat}: {count:,}")
            
            logger.info("="*80)
            
        except Exception as e:
            logger.error(f"❌ Knowledge base check failed: {e}")
            import traceback
            traceback.print_exc()
    
    def list_sources(self, jurisdiction: Optional[str] = None):
        """List available regulatory sources"""
        try:
            from function_app_pkg.core.jurisdiction_config import (
                SOURCES_BY_JURISDICTION,
                PRIORITY_SOURCES,
                ALL_SOURCES
            )
            
            logger.info("\n" + "="*80)
            logger.info("📚 AVAILABLE REGULATORY SOURCES")
            logger.info("="*80)
            
            if jurisdiction:
                # Show sources for specific jurisdiction
                jur = jurisdiction.upper()
                sources = SOURCES_BY_JURISDICTION.get(jur, [])
                
                if not sources:
                    logger.error(f"❌ No sources found for jurisdiction: {jur}")
                    return
                
                logger.info(f"\n{self.SUPPORTED_JURISDICTIONS.get(jur, jur)}")
                logger.info(f"Total sources: {len(sources)}")
                logger.info(f"Priority 1 sources: {len([s for s in sources if s.priority == 1])}")
                
                logger.info(f"\n📋 Priority 1 Sources:")
                for source in sources:
                    if source.priority == 1:
                        logger.info(f"\n   • {source.name}")
                        logger.info(f"     ID: {source.id}")
                        logger.info(f"     Type: {source.source_type.value}")
                        logger.info(f"     URL: {source.url[:80]}...")
                        if source.notes:
                            logger.info(f"     Notes: {source.notes}")
            else:
                # Show all jurisdictions
                logger.info(f"\n📊 Summary:")
                logger.info(f"   Total sources: {len(ALL_SOURCES)}")
                logger.info(f"   Priority sources: {len(PRIORITY_SOURCES)}")
                
                logger.info(f"\n🌍 By Jurisdiction:")
                for jur in sorted(SOURCES_BY_JURISDICTION.keys()):
                    sources = SOURCES_BY_JURISDICTION[jur]
                    priority_count = len([s for s in sources if s.priority == 1])
                    jur_name = self.SUPPORTED_JURISDICTIONS.get(jur, jur)
                    
                    logger.info(f"\n   {jur_name}")
                    logger.info(f"      Total: {len(sources)} sources")
                    logger.info(f"      Priority: {priority_count} sources")
            
            logger.info("\n" + "="*80)
            
        except Exception as e:
            logger.error(f"❌ Failed to list sources: {e}")
            import traceback
            traceback.print_exc()
    
    def get_statistics(self):
        """Get comprehensive statistics"""
        try:
            logger.info("\n" + "="*80)
            logger.info("📈 COMPREHENSIVE STATISTICS")
            logger.info("="*80)
            
            # Database stats
            logger.info("\n💾 Cosmos DB:")
            self.check_database()
            
            # Knowledge base stats  
            logger.info("\n🔍 Azure AI Search:")
            self.check_knowledge_base()
            
        except Exception as e:
            logger.error(f"❌ Failed to get statistics: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Enterprise Regulatory Intelligence Platform - Setup & Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape all jurisdictions (priority sources)
  python setup_rag.py --all
  
  # Scrape specific jurisdictions
  python setup_rag.py --jurisdictions ZA UK US
  
  # Scrape with custom settings
  python setup_rag.py --all --max-retries 5 --max-concurrent 3
  
  # Scrape ALL sources (not just priority)
  python setup_rag.py --all --all-sources
  
  # Health check
  python setup_rag.py --check
  
  # List available sources
  python setup_rag.py --list
  python setup_rag.py --list --jurisdiction UK
  
  # Get statistics
  python setup_rag.py --stats
        """
    )
    
    # Scraping options
    scrape_group = parser.add_argument_group('Scraping')
    scrape_group.add_argument(
        '--all',
        action='store_true',
        help='Scrape all jurisdictions'
    )
    scrape_group.add_argument(
        '--jurisdictions',
        nargs='+',
        metavar='JUR',
        help='Specific jurisdictions to scrape (e.g., ZA UK US)'
    )
    scrape_group.add_argument(
        '--max-retries',
        type=int,
        default=3,
        help='Maximum retry attempts per source (default: 3)'
    )
    scrape_group.add_argument(
        '--max-concurrent',
        type=int,
        default=2,
        help='Maximum concurrent scrapes (default: 2)'
    )
    scrape_group.add_argument(
        '--all-sources',
        action='store_true',
        help='Scrape ALL sources, not just priority 1'
    )
    scrape_group.add_argument(
        '--no-report',
        action='store_true',
        help='Do not save JSON report'
    )
    
    # Information options
    info_group = parser.add_argument_group('Information')
    info_group.add_argument(
        '--check',
        action='store_true',
        help='Health check (database + knowledge base)'
    )
    info_group.add_argument(
        '--check-db',
        action='store_true',
        help='Check Cosmos DB only'
    )
    info_group.add_argument(
        '--check-kb',
        action='store_true',
        help='Check Azure AI Search only'
    )
    info_group.add_argument(
        '--list',
        action='store_true',
        help='List available regulatory sources'
    )
    info_group.add_argument(
        '--jurisdiction',
        type=str,
        help='Jurisdiction for --list command'
    )
    info_group.add_argument(
        '--stats',
        action='store_true',
        help='Show comprehensive statistics'
    )
    
    args = parser.parse_args()
    
    # Create setup instance
    setup = EnterpriseRegulatorySetup()
    
    # Show help if no arguments
    if len(sys.argv) == 1:
        parser.print_help()
        return
    
    # Execute commands
    try:
        # Information commands
        if args.list:
            setup.list_sources(args.jurisdiction)
        
        if args.check or args.check_db:
            setup.check_database()
        
        if args.check or args.check_kb:
            setup.check_knowledge_base()
        
        if args.stats:
            setup.get_statistics()
        
        # Scraping commands
        if args.all or args.jurisdictions:
            await setup.run_scrape(
                jurisdictions=args.jurisdictions,
                max_retries=args.max_retries,
                max_concurrent=args.max_concurrent,
                priority_only=not args.all_sources,
                save_report=not args.no_report
            )
    
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())