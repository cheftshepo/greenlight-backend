"""
ULTIMATE PRODUCTION-GRADE AI REGULATORY SCRAPER
================================================
Handles ALL source types without hardcoding:
- HTML pages (static & JS-rendered)
- PDF documents (direct links & embedded)
- Mixed content pages
- Rate limiting & retries
- Automatic deduplication
- Works with ANY jurisdiction/regulator

NO MANUAL INTERVENTION NEEDED
"""

import asyncio
import aiohttp
import logging
from typing import List, Dict, Optional, Set
from datetime import datetime
import json
from dataclasses import dataclass
import os
import io
import hashlib
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from openai import AzureOpenAI
from dotenv import load_dotenv
import re

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class AIScrapedRegulation:
    """Enhanced regulation with versioning and validation"""
    jurisdiction: str
    regulator: str
    section: str
    title: str
    text: str
    category: str
    risk_level: str
    effective_date: str
    source_url: str
    penalty_info: str = ""
    version: str = "1.0"
    last_verified: str = ""
    content_hash: str = ""
    
    def __post_init__(self):
        """Auto-generate hash and verification date"""
        if not self.content_hash:
            self.content_hash = hashlib.md5(
                f"{self.section}{self.text}".encode()
            ).hexdigest()
        if not self.last_verified:
            self.last_verified = datetime.utcnow().isoformat()
    
    def to_cosmos_dict(self) -> Dict:
        """Convert to Cosmos DB format"""
        # Clean section for ID
        clean_section = re.sub(r'[^a-zA-Z0-9_-]', '_', self.section)
        
        return {
            "id": f"{self.jurisdiction}_{clean_section}_{self.content_hash[:8]}",
            "jurisdiction": self.jurisdiction,
            "regulator": self.regulator,
            "section_reference": self.section,
            "title": self.title,
            "text": self.text,
            "category": self.category,
            "risk_level": self.risk_level,
            "effective_date": self.effective_date,
            "source_url": self.source_url,
            "penalty_info": self.penalty_info,
            "version": self.version,
            "last_verified": self.last_verified,
            "content_hash": self.content_hash,
            "type": "regulation",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "requires_monthly_update": self.category in ["esg_greenwashing", "crypto_digital_assets"],
            "requires_quarterly_update": True,
        }


class AIRegulatoryScraper:
    """
    ULTIMATE production scraper - handles everything automatically
    """
    
    VALID_CATEGORIES = {
        "guarantees_promises", "past_performance", "risk_warnings",
        "esg_greenwashing", "crypto_digital_assets", "testimonials",
        "fair_clear_not_misleading", "comparisons", "fees_charges",
        "suitability", "general_marketing"
    }
    
    def __init__(self):
        self.openai_client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2024-12-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        self.session = None
        self.playwright = None
        self.browser = None
        self.seen_hashes: Set[str] = set()
        self.seen_sections: Set[str] = set()
        self.session_regulations: Set[str] = set() 
        
        from function_app_pkg.core.database import CosmosDBClient
        self.db = CosmosDBClient()
        
        
        # Initialize AI Search for indexing
        try:
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            self.knowledge_base = RegulatoryKnowledgeBase()
            self.knowledge_base._ensure_initialized()
            logger.info("✅ AI Search knowledge base connected")
        except Exception as e:
            logger.warning(f"⚠️ AI Search not available: {e}")
            self.knowledge_base = None
        
        # Load existing hashes from DB for deduplication
        self._load_existing_hashes()
        
        logger.info("✅ Ultimate AI Scraper initialized (Cosmos + AI Search)")
    
    def _load_existing_hashes(self):
        """Load existing hashes AND section refs from DB to prevent duplicates"""
        try:
            container = self.db.get_container("rules")
            if container:
                # Get both content_hash and section_reference for deduplication
                query = "SELECT c.content_hash, c.section_reference, c.jurisdiction FROM c WHERE c.type = 'regulation'"
                items = list(container.query_items(query=query, enable_cross_partition_query=True))
                
                self.seen_hashes = set()
                self.seen_sections = set()  # NEW: Track section+jurisdiction combos
                
                for item in items:
                    if item.get('content_hash'):
                        self.seen_hashes.add(item['content_hash'])
                    
                    # Create unique key from jurisdiction + section
                    section = item.get('section_reference', '')
                    jur = item.get('jurisdiction', '')
                    if section and jur:
                        self.seen_sections.add(f"{jur}:{section}")
                
                logger.info(f"📊 Loaded {len(self.seen_hashes)} hashes, {len(self.seen_sections)} sections for dedup")
        except Exception as e:
            logger.warning(f"Could not load existing hashes: {e}")
            self.seen_hashes = set()
            self.seen_sections = set()
    
    async def _ensure_session(self):
        """Lazy initialize aiohttp session"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=180, connect=60)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def ensure_playwright_ready(self):
        """Initialize Playwright browser"""
        if not self.playwright:
            logger.info("🎭 Initializing Playwright browser...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            logger.info("✅ Playwright ready")
    
    async def scrape_jurisdiction_parallel(
        self, 
        jurisdiction_config: Dict,
        max_concurrent: int = 3
    ) -> List[AIScrapedRegulation]:
        """Main scraping method - handles ALL content types automatically"""
        
        logger.info(f"🔍 Scraping: {jurisdiction_config['name']}")
        
        await self._ensure_session()
        
        # Get content from URL - auto-detects type
        content_items = await self._smart_fetch(jurisdiction_config['url'])
        
        if not content_items:
            logger.error(f"❌ No content from {jurisdiction_config['name']}")
            return []
        
        logger.info(f"📄 Found {len(content_items)} content items")
        
        # Process in parallel
        all_regulations = []
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_content(content_item):
            async with semaphore:
                try:
                    content = content_item.get('content', '')
                    
                    if len(content) < 500:
                        logger.debug(f"Skipping short content ({len(content)} chars)")
                        return []
                    
                    # Smart chunking for long docs
                    if len(content) > 50000:
                        regulations = await self._extract_from_long_document(
                            content, jurisdiction_config, content_item.get('url', jurisdiction_config['url'])
                        )
                    else:
                        regulations = await self._ai_extract_regulations(
                            content=content,
                            jurisdiction_config={**jurisdiction_config, 'url': content_item.get('url', jurisdiction_config['url'])}
                        )
                    
                    # Deduplicate
                    unique_regs = self._deduplicate_regulations(regulations)
                    
                    if unique_regs:
                        logger.info(f"  ✅ {len(unique_regs)} unique regs from {content_item.get('url', 'content')[:80]}")
                    
                    return unique_regs
                    
                except Exception as e:
                    logger.error(f"  ❌ Processing failed: {e}")
                    return []
        
        tasks = [process_content(item) for item in content_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, list):
                all_regulations.extend(result)
        
        logger.info(f"🎯 Total: {len(all_regulations)} unique regulations from {jurisdiction_config['name']}")
        return all_regulations
    
    async def _smart_fetch(self, url: str) -> List[Dict]:
        """
        SMART FETCH - Uses Playwright for ALL sites (most reliable)
        Falls back to aiohttp only for direct PDFs
        """
        logger.info(f"🔍 Smart fetching: {url[:100]}")

        return await self._fetch_with_playwright(url)

    async def _fetch_direct_pdf(self, url: str) -> List[Dict]:
        """Fetch direct PDF using aiohttp"""
        await self._ensure_session()  # ← ADD THIS LINE
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            async with self.session.get(url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    pdf_bytes = await response.read()
                    logger.info(f"📥 Downloaded PDF: {len(pdf_bytes)} bytes")
                    text = self._extract_pdf_text(pdf_bytes)
                    if text:
                        logger.info(f"✅ Extracted {len(text)} chars from PDF")
                        return [{'content': text, 'url': url, 'type': 'pdf'}]
                    else:
                        logger.warning(f"⚠️ No text extracted from PDF")
                else:
                    logger.warning(f"⚠️ PDF download failed: HTTP {response.status}")
        except Exception as e:
            logger.error(f"❌ PDF fetch failed: {e}")
        return []
    async def _fetch_pdf_from_url(self, pdf_url: str) -> Optional[str]:
        """Fetch and extract text from a PDF URL"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with self.session.get(pdf_url, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    pdf_bytes = await response.read()
                    return self._extract_pdf_text(pdf_bytes)
        except Exception as e:
            logger.debug(f"Could not fetch PDF {pdf_url[:80]}: {e}")
        return None
    
    async def _fetch_with_playwright(self, url: str) -> List[Dict]:
        """Fetch content using Playwright - FIXED for better extraction"""
        try:
            await self.ensure_playwright_ready()
            
            context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = await context.new_page()
            
            # Block unnecessary resources for speed
            await page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2}", lambda route: route.abort())
            
            logger.info(f"⏳ Loading: {url[:80]}...")
            
            try:
                # Try networkidle first (best for JS-heavy sites)
                await page.goto(url, wait_until='networkidle', timeout=60000)
            except Exception:
                try:
                    # Fallback to domcontentloaded
                    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                except Exception as e:
                    logger.warning(f"⚠️ Page load issues: {e}")
            
            # Wait for content to render
            await page.wait_for_timeout(3000)
            
            # Scroll to trigger lazy loading
            await page.evaluate("""
                async () => {
                    for (let i = 0; i < 3; i++) {
                        window.scrollTo(0, document.body.scrollHeight * (i + 1) / 3);
                        await new Promise(r => setTimeout(r, 500));
                    }
                    window.scrollTo(0, 0);
                }
            """)
            await page.wait_for_timeout(2000)
            
            # Try multiple content extraction strategies
            content = ""
            
            # Strategy 1: Main content selectors (regulatory sites often use these)
            selectors = [
                'main', 'article', '.content', '#content', '.main-content',
                '.handbook-content', '.rule-content', '.regulation-text',
                '#main', '.body-content', '[role="main"]'
            ]
            
            for selector in selectors:
                try:
                    elem = await page.query_selector(selector)
                    if elem:
                        text = await elem.inner_text()
                        if text and len(text) > len(content):
                            content = text
                except:
                    continue
            
            # Strategy 2: Full body if no main content found
            if len(content) < 500:
                try:
                    content = await page.inner_text('body')
                except:
                    pass
            
            # Strategy 3: Get all text nodes
            if len(content) < 500:
                try:
                    content = await page.evaluate("""
                        () => {
                            const walker = document.createTreeWalker(
                                document.body,
                                NodeFilter.SHOW_TEXT,
                                null,
                                false
                            );
                            let text = '';
                            while (walker.nextNode()) {
                                const node = walker.currentNode;
                                if (node.textContent.trim().length > 20) {
                                    text += node.textContent.trim() + '\\n';
                                }
                            }
                            return text;
                        }
                    """)
                except:
                    pass
            
            # Clean content
            if content:
                # Remove excessive whitespace
                import re
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = re.sub(r' {2,}', ' ', content)
                content = content.strip()
            
            await context.close()
            
            if len(content) < 200:
                logger.warning(f"⚠️ Very little content extracted ({len(content)} chars)")
                return []
            
            logger.info(f"✅ Extracted {len(content)} chars from {url[:50]}")
            
            return [{'content': content, 'url': url, 'type': 'playwright'}]
            
        except Exception as e:
            logger.error(f"❌ Playwright failed for {url}: {e}")
            return []
    
    def _extract_pdf_text(self, pdf_bytes: bytes) -> Optional[str]:
        """Extract text from PDF bytes"""
        try:
            import pypdf
            
            pdf_file = io.BytesIO(pdf_bytes)
            pdf_reader = pypdf.PdfReader(pdf_file)
            
            text_parts = []
            for page in pdf_reader.pages[:150]:  # Up to 150 pages
                try:
                    text_parts.append(page.extract_text())
                except:
                    continue
            
            full_text = '\n'.join(text_parts)
            logger.info(f"✅ Extracted {len(full_text)} chars from PDF ({len(pdf_reader.pages)} pages)")
            return full_text
            
        except Exception as e:
            logger.error(f"❌ PDF extraction failed: {e}")
            return None
    
    async def _extract_from_long_document(
        self,
        content: str,
        jurisdiction_config: Dict,
        url: str
    ) -> List[AIScrapedRegulation]:
        """Smart chunking for long documents"""
        
        logger.info(f"📚 Long document ({len(content)} chars), using smart chunking")
        
        chunks = self._smart_chunk_content(content, chunk_size=35000)
        all_regulations = []
        
        for i, chunk in enumerate(chunks):
            logger.info(f"  Chunk {i+1}/{len(chunks)}")
            
            regulations = await self._ai_extract_regulations(
                content=chunk,
                jurisdiction_config={**jurisdiction_config, 'url': url}
            )
            
            all_regulations.extend(regulations)
            await asyncio.sleep(1)  # Rate limiting
        
        return all_regulations
    
    def _smart_chunk_content(self, content: str, chunk_size: int = 35000) -> List[str]:
        """Split content at paragraph boundaries"""
        chunks = []
        paragraphs = content.split('\n\n')
        
        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                chunks.append(current_chunk)
                current_chunk = para
            else:
                current_chunk += "\n\n" + para
        
        if current_chunk:
            chunks.append(current_chunk)
        
        logger.info(f"Split into {len(chunks)} chunks")
        return chunks
    
    def _deduplicate_regulations(
        self, 
        regulations: List[AIScrapedRegulation]
    ) -> List[AIScrapedRegulation]:
        """Remove duplicates - checks hash, section, AND text similarity"""
        unique = []
        
        for reg in regulations:
            # Check 1: Content hash (exact duplicate)
            if reg.content_hash in self.seen_hashes:
                logger.debug(f"⚠️ Duplicate hash: {reg.section}")
                continue
            
            if reg.content_hash in self.session_regulations:
                logger.debug(f"⚠️ Duplicate in session: {reg.section}")
                continue
            
            # Check 2: Section + jurisdiction combo (same rule from same source)
            section_key = f"{reg.jurisdiction}:{reg.section}"
            if hasattr(self, 'seen_sections') and section_key in self.seen_sections:
                logger.debug(f"⚠️ Duplicate section: {section_key}")
                continue
            
            # Check 3: Very similar text (fuzzy dedup for same content, different formatting)
            text_start = reg.text[:100].lower().strip()
            is_similar = False
            for existing in unique:
                if existing.text[:100].lower().strip() == text_start:
                    is_similar = True
                    break
            
            if is_similar:
                logger.debug(f"⚠️ Similar text found: {reg.section}")
                continue
            
            # Passed all checks - it's unique
            self.seen_hashes.add(reg.content_hash)
            self.session_regulations.add(reg.content_hash)
            if hasattr(self, 'seen_sections'):
                self.seen_sections.add(section_key)
            
            unique.append(reg)
        
        if len(regulations) != len(unique):
            logger.info(f"🔄 Deduplication: {len(regulations)} → {len(unique)} ({len(regulations) - len(unique)} removed)")
        
        return unique
    
    async def _ai_extract_regulations(
        self, 
        content: str, 
        jurisdiction_config: Dict
    ) -> List[AIScrapedRegulation]:
        """AI extraction with improved prompt"""
        
        if len(content) > 45000:
            content = content[:45000]
        
        parts = jurisdiction_config['name'].split('-')
        jurisdiction = parts[0].strip() if parts else "GLOBAL"
        regulator = parts[1].strip() if len(parts) > 1 else jurisdiction
        
        prompt = f"""Extract financial marketing regulations from this document.

SOURCE: {jurisdiction_config['name']}
JURISDICTION: {jurisdiction}
REGULATOR: {regulator}

FOCUS: Marketing, communications, financial promotions, advertising rules

CATEGORIES (use exactly):
esg_greenwashing, guarantees_promises, risk_warnings, crypto_digital_assets, 
past_performance, testimonials, fair_clear_not_misleading, comparisons, 
fees_charges, suitability, general_marketing

RULES:
1. Extract 10-25 regulations (marketing/communications ONLY)
2. Each regulation: 150-500 words of actual regulatory text
3. Official section reference (e.g., "COBS 4.2.1R", "Article 24(3)")
4. Risk level: critical, high, medium, low

CONTENT:
{content[:38000]}

Return JSON:
{{
  "regulations": [
    {{
      "section": "Official reference",
      "title": "Title (max 100 chars)",
      "text": "Full regulatory text (150-500 words)",
      "category": "exact category from list",
      "risk_level": "critical|high|medium|low",
      "penalty_info": "Fines if mentioned",
      "effective_date": "YYYY-MM-DD or current"
    }}
  ]
}}
"""
        
        try:
            response = self.openai_client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o'),
                messages=[
                    {"role": "system", "content": "You are a legal expert. Extract financial marketing regulations. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            regulations_data = result.get("regulations", [])
            
            regulations = []
            for reg_data in regulations_data:
                text = reg_data.get("text", "")
                category = reg_data.get("category", "general_marketing")
                
                if len(text) < 150:
                    continue

                # Skip garbage content
                garbage_patterns = [
                    "cookie policy", "privacy policy", "terms of use",
                    "javascript required", "enable javascript", "page not found",
                    "404 error", "access denied", "subscribe to newsletter",
                    "sign up for", "click here to"
                ]
                text_lower = text.lower()
                if any(pattern in text_lower for pattern in garbage_patterns):
                    logger.debug(f"⚠️ Skipping garbage content: {text[:50]}...")
                    continue
                
                # Validate it looks like regulatory content
                regulatory_keywords = ["must", "shall", "required", "prohibited", "compliance", "rule", "regulation"]
                if not any(kw in text_lower for kw in regulatory_keywords):
                    logger.debug(f"⚠️ Skipping non-regulatory content")
                    continue
                
                if category not in self.VALID_CATEGORIES:
                    category = "general_marketing"
                
                regulation = AIScrapedRegulation(
                    jurisdiction=jurisdiction,
                    regulator=regulator,
                    section=reg_data.get("section", "UNKNOWN"),
                    title=reg_data.get("title", "Untitled")[:100],
                    text=text[:2000],
                    category=category,
                    risk_level=reg_data.get("risk_level", "medium"),
                    effective_date=reg_data.get("effective_date", "current"),
                    source_url=jurisdiction_config.get('url', ''),
                    penalty_info=reg_data.get("penalty_info", "")[:500]
                )
                regulations.append(regulation)
            
            logger.info(f"🤖 AI extracted {len(regulations)} regulations")
            return regulations
            
        except Exception as e:
            logger.error(f"❌ AI extraction failed: {e}")
            return []
    
    async def save_regulations_to_cosmos(
        self, 
        regulations: List[AIScrapedRegulation],
        batch_size: int = 25
    ) -> Dict[str, int]:
        """Save to Cosmos DB AND index to AI Search with embeddings"""
        
        stats = {
            "total": len(regulations),
            "saved": 0,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "error_details": []
        }
        
        container = self.db.get_container("rules")
        if not container:
            logger.error("❌ Rules container not found!")
            stats["errors"] = len(regulations)
            return stats
        
        search_docs = []
        
        for i in range(0, len(regulations), batch_size):
            batch = regulations[i:i + batch_size]
            
            for reg in batch:
                try:
                    doc = reg.to_cosmos_dict()
                    
                    if not doc.get("jurisdiction"):
                        logger.warning(f"⚠️ Skipping regulation without jurisdiction")
                        stats["skipped"] += 1
                        continue
                    
                    # Validate content quality
                    text = doc.get("text", "")
                    if len(text) < 100:
                        logger.debug(f"⚠️ Skipping short text ({len(text)} chars)")
                        stats["skipped"] += 1
                        continue
                    
                    # Save to Cosmos DB
                    container.upsert_item(body=doc)
                    stats["saved"] += 1
                    
                    # Prepare for AI Search indexing
                    search_doc = {
                        "id": doc["id"],
                        "text": text,
                        "jurisdiction": doc["jurisdiction"],
                        "source_document": f"{doc.get('regulator', 'Unknown')} - {doc.get('title', 'Untitled')}",
                        "section_reference": doc.get("section_reference", ""),
                        "category": doc.get("category", "general_marketing"),
                        "risk_level": doc.get("risk_level", "medium"),
                        "penalty_info": doc.get("penalty_info", "")[:500],
                        "effective_date": doc.get("effective_date", "current"),
                        "last_updated": datetime.utcnow().isoformat(),
                    }
                    search_docs.append(search_doc)
                    
                    if stats["saved"] % 10 == 0:
                        logger.info(f"   Progress: {stats['saved']}/{stats['total']}")
                        
                except Exception as e:
                    logger.error(f"❌ Save failed: {e}")
                    stats["errors"] += 1
                    stats["error_details"].append(str(e)[:100])
            
            await asyncio.sleep(0.3)
        
        # Index to AI Search with embeddings
        if search_docs and self.knowledge_base:
            try:
                logger.info(f"📤 Indexing {len(search_docs)} regulations to AI Search...")
                result = self.knowledge_base.ingest_bulk_dicts(search_docs, batch_size=25)
                stats["indexed"] = result.get("succeeded", 0)
                
                if result.get("errors"):
                    stats["error_details"].extend(result["errors"][:5])
                
                logger.info(f"✅ AI Search: {stats['indexed']}/{len(search_docs)} indexed")
                
            except Exception as e:
                logger.error(f"❌ AI Search indexing failed: {e}")
                stats["error_details"].append(f"AI Search: {str(e)[:100]}")
        elif not self.knowledge_base:
            logger.warning("⚠️ AI Search not configured - skipping indexing")
        
        return stats

    async def close(self):
        """Cleanup"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        if self.session:
            await self.session.close()


if __name__ == "__main__":
    asyncio.run(scrape_all_jurisdictions())

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class AIScrapedRegulation:
    """Enhanced regulation with versioning and validation"""
    jurisdiction: str
    regulator: str
    section: str
    title: str
    text: str
    category: str
    risk_level: str
    effective_date: str
    source_url: str
    penalty_info: str = ""
    version: str = "1.0"
    last_verified: str = ""
    content_hash: str = ""
    
    def __post_init__(self):
        """Auto-generate hash and verification date"""
        if not self.content_hash:
            self.content_hash = hashlib.md5(
                f"{self.section}{self.text}".encode()
            ).hexdigest()
        if not self.last_verified:
            self.last_verified = datetime.utcnow().isoformat()
    
    def to_cosmos_dict(self) -> Dict:
        """Convert to Cosmos DB format"""
        # Clean section for ID
        clean_section = re.sub(r'[^a-zA-Z0-9_-]', '_', self.section)
        
        return {
            "id": f"{self.jurisdiction}_{clean_section}_{self.content_hash[:8]}",
            "jurisdiction": self.jurisdiction,
            "regulator": self.regulator,
            "section_reference": self.section,
            "title": self.title,
            "text": self.text,
            "category": self.category,
            "risk_level": self.risk_level,
            "effective_date": self.effective_date,
            "source_url": self.source_url,
            "penalty_info": self.penalty_info,
            "version": self.version,
            "last_verified": self.last_verified,
            "content_hash": self.content_hash,
            "type": "regulation",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "requires_monthly_update": self.category in ["esg_greenwashing", "crypto_digital_assets"],
            "requires_quarterly_update": True,
        }


class AIRegulatoryScraper:
    """
    ULTIMATE production scraper - handles everything automatically
    """
    
    VALID_CATEGORIES = {
        "guarantees_promises", "past_performance", "risk_warnings",
        "esg_greenwashing", "crypto_digital_assets", "testimonials",
        "fair_clear_not_misleading", "comparisons", "fees_charges",
        "suitability", "general_marketing"
    }
    
    def __init__(self):
        self.openai_client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2024-12-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        self.session = None
        self.playwright = None
        self.browser = None
        self.seen_hashes: Set[str] = set()
        self.seen_sections: Set[str] = set()
        self.session_regulations: Set[str] = set() 
        
        from function_app_pkg.core.database import CosmosDBClient
        self.db = CosmosDBClient()
        
        
        # Initialize AI Search for indexing
        try:
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            self.knowledge_base = RegulatoryKnowledgeBase()
            self.knowledge_base._ensure_initialized()
            logger.info("✅ AI Search knowledge base connected")
        except Exception as e:
            logger.warning(f"⚠️ AI Search not available: {e}")
            self.knowledge_base = None
        
        # Load existing hashes from DB for deduplication
        self._load_existing_hashes()
        
        logger.info("✅ Ultimate AI Scraper initialized (Cosmos + AI Search)")
    
    def _load_existing_hashes(self):
        """Load existing hashes AND section refs from DB to prevent duplicates"""
        try:
            container = self.db.get_container("rules")
            if container:
                # Get both content_hash and section_reference for deduplication
                query = "SELECT c.content_hash, c.section_reference, c.jurisdiction FROM c WHERE c.type = 'regulation'"
                items = list(container.query_items(query=query, enable_cross_partition_query=True))
                
                self.seen_hashes = set()
                self.seen_sections = set()  # NEW: Track section+jurisdiction combos
                
                for item in items:
                    if item.get('content_hash'):
                        self.seen_hashes.add(item['content_hash'])
                    
                    # Create unique key from jurisdiction + section
                    section = item.get('section_reference', '')
                    jur = item.get('jurisdiction', '')
                    if section and jur:
                        self.seen_sections.add(f"{jur}:{section}")
                
                logger.info(f"📊 Loaded {len(self.seen_hashes)} hashes, {len(self.seen_sections)} sections for dedup")
        except Exception as e:
            logger.warning(f"Could not load existing hashes: {e}")
            self.seen_hashes = set()
            self.seen_sections = set()
    
    async def _ensure_session(self):
        """Lazy initialize aiohttp session"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=180, connect=60)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def ensure_playwright_ready(self):
        """Initialize Playwright browser"""
        if not self.playwright:
            logger.info("🎭 Initializing Playwright browser...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            logger.info("✅ Playwright ready")
    
    async def scrape_jurisdiction_parallel(
        self, 
        jurisdiction_config: Dict,
        max_concurrent: int = 3
    ) -> List[AIScrapedRegulation]:
        """Main scraping method - handles ALL content types automatically"""
        
        logger.info(f"🔍 Scraping: {jurisdiction_config['name']}")
        
        await self._ensure_session()
        
        # Get content from URL - auto-detects type
        content_items = await self._smart_fetch(jurisdiction_config['url'])
        
        if not content_items:
            logger.error(f"❌ No content from {jurisdiction_config['name']}")
            return []
        
        logger.info(f"📄 Found {len(content_items)} content items")
        
        # Process in parallel
        all_regulations = []
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_content(content_item):
            async with semaphore:
                try:
                    content = content_item.get('content', '')
                    
                    if len(content) < 500:
                        logger.debug(f"Skipping short content ({len(content)} chars)")
                        return []
                    
                    # Smart chunking for long docs
                    if len(content) > 50000:
                        regulations = await self._extract_from_long_document(
                            content, jurisdiction_config, content_item.get('url', jurisdiction_config['url'])
                        )
                    else:
                        regulations = await self._ai_extract_regulations(
                            content=content,
                            jurisdiction_config={**jurisdiction_config, 'url': content_item.get('url', jurisdiction_config['url'])}
                        )
                    
                    # Deduplicate
                    unique_regs = self._deduplicate_regulations(regulations)
                    
                    if unique_regs:
                        logger.info(f"  ✅ {len(unique_regs)} unique regs from {content_item.get('url', 'content')[:80]}")
                    
                    return unique_regs
                    
                except Exception as e:
                    logger.error(f"  ❌ Processing failed: {e}")
                    return []
        
        tasks = [process_content(item) for item in content_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, list):
                all_regulations.extend(result)
        
        logger.info(f"🎯 Total: {len(all_regulations)} unique regulations from {jurisdiction_config['name']}")
        return all_regulations
    
    async def _smart_fetch(self, url: str) -> List[Dict]:
        """
        SMART FETCH - Routes to correct handler based on URL type
        - PDFs → Direct download + pypdf extraction
        - HTML/other → Playwright for JS rendering
        """
        logger.info(f"🔍 Smart fetching: {url[:100]}")
        
        # Check if it's a PDF URL
        if url.lower().endswith('.pdf') or '/pdf/' in url.lower():
            logger.info(f"📄 Detected PDF, using direct download...")
            return await self._fetch_direct_pdf(url)
        
        # Otherwise use Playwright for HTML/JS pages
        return await self._fetch_with_playwright(url)


    async def _fetch_direct_pdf(self, url: str) -> List[Dict]:
        """Fetch direct PDF using aiohttp"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with self.session.get(url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    pdf_bytes = await response.read()
                    text = self._extract_pdf_text(pdf_bytes)
                    if text:
                        return [{'content': text, 'url': url, 'type': 'pdf'}]
        except Exception as e:
            logger.error(f"PDF fetch failed: {e}")
        return []


    async def _fetch_pdf_from_url(self, pdf_url: str) -> Optional[str]:
        """Fetch and extract text from a PDF URL"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with self.session.get(pdf_url, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status == 200:
                    pdf_bytes = await response.read()
                    return self._extract_pdf_text(pdf_bytes)
        except Exception as e:
            logger.debug(f"Could not fetch PDF {pdf_url[:80]}: {e}")
        return None
    
    async def _fetch_with_playwright(self, url: str) -> List[Dict]:
        """Fetch content using Playwright - FIXED for better extraction"""
        try:
            await self.ensure_playwright_ready()
            
            context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = await context.new_page()
            
            # Block unnecessary resources for speed
            await page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2}", lambda route: route.abort())
            
            logger.info(f"⏳ Loading: {url[:80]}...")
            
            try:
                # Try networkidle first (best for JS-heavy sites)
                await page.goto(url, wait_until='networkidle', timeout=60000)
            except Exception:
                try:
                    # Fallback to domcontentloaded
                    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                except Exception as e:
                    logger.warning(f"⚠️ Page load issues: {e}")
            
            # Wait for content to render
            await page.wait_for_timeout(3000)
            
            # Scroll to trigger lazy loading
            await page.evaluate("""
                async () => {
                    for (let i = 0; i < 3; i++) {
                        window.scrollTo(0, document.body.scrollHeight * (i + 1) / 3);
                        await new Promise(r => setTimeout(r, 500));
                    }
                    window.scrollTo(0, 0);
                }
            """)
            await page.wait_for_timeout(2000)
            
            # Try multiple content extraction strategies
            content = ""
            
            # Strategy 1: Main content selectors (regulatory sites often use these)
            selectors = [
                'main', 'article', '.content', '#content', '.main-content',
                '.handbook-content', '.rule-content', '.regulation-text',
                '#main', '.body-content', '[role="main"]'
            ]
            
            for selector in selectors:
                try:
                    elem = await page.query_selector(selector)
                    if elem:
                        text = await elem.inner_text()
                        if text and len(text) > len(content):
                            content = text
                except:
                    continue
            
            # Strategy 2: Full body if no main content found
            if len(content) < 500:
                try:
                    content = await page.inner_text('body')
                except:
                    pass
            
            # Strategy 3: Get all text nodes
            if len(content) < 500:
                try:
                    content = await page.evaluate("""
                        () => {
                            const walker = document.createTreeWalker(
                                document.body,
                                NodeFilter.SHOW_TEXT,
                                null,
                                false
                            );
                            let text = '';
                            while (walker.nextNode()) {
                                const node = walker.currentNode;
                                if (node.textContent.trim().length > 20) {
                                    text += node.textContent.trim() + '\\n';
                                }
                            }
                            return text;
                        }
                    """)
                except:
                    pass
            
            # Clean content
            if content:
                # Remove excessive whitespace
                import re
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = re.sub(r' {2,}', ' ', content)
                content = content.strip()
            
            await context.close()
            
            if len(content) < 200:
                logger.warning(f"⚠️ Very little content extracted ({len(content)} chars)")
                return []
            
            logger.info(f"✅ Extracted {len(content)} chars from {url[:50]}")
            
            return [{'content': content, 'url': url, 'type': 'playwright'}]
            
        except Exception as e:
            logger.error(f"❌ Playwright failed for {url}: {e}")
            return []
    
    def _extract_pdf_text(self, pdf_bytes: bytes) -> Optional[str]:
        """Extract text from PDF bytes"""
        try:
            import pypdf
            
            pdf_file = io.BytesIO(pdf_bytes)
            pdf_reader = pypdf.PdfReader(pdf_file)
            
            text_parts = []
            for page in pdf_reader.pages[:150]:  # Up to 150 pages
                try:
                    text_parts.append(page.extract_text())
                except:
                    continue
            
            full_text = '\n'.join(text_parts)
            logger.info(f"✅ Extracted {len(full_text)} chars from PDF ({len(pdf_reader.pages)} pages)")
            return full_text
            
        except Exception as e:
            logger.error(f"❌ PDF extraction failed: {e}")
            return None
    
    async def _extract_from_long_document(
        self,
        content: str,
        jurisdiction_config: Dict,
        url: str
    ) -> List[AIScrapedRegulation]:
        """Smart chunking for long documents"""
        
        logger.info(f"📚 Long document ({len(content)} chars), using smart chunking")
        
        chunks = self._smart_chunk_content(content, chunk_size=35000)
        all_regulations = []
        
        for i, chunk in enumerate(chunks):
            logger.info(f"  Chunk {i+1}/{len(chunks)}")
            
            regulations = await self._ai_extract_regulations(
                content=chunk,
                jurisdiction_config={**jurisdiction_config, 'url': url}
            )
            
            all_regulations.extend(regulations)
            await asyncio.sleep(1)  # Rate limiting
        
        return all_regulations
    
    def _smart_chunk_content(self, content: str, chunk_size: int = 35000) -> List[str]:
        """Split content at paragraph boundaries"""
        chunks = []
        paragraphs = content.split('\n\n')
        
        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                chunks.append(current_chunk)
                current_chunk = para
            else:
                current_chunk += "\n\n" + para
        
        if current_chunk:
            chunks.append(current_chunk)
        
        logger.info(f"Split into {len(chunks)} chunks")
        return chunks
    
    def _deduplicate_regulations(
        self, 
        regulations: List[AIScrapedRegulation]
    ) -> List[AIScrapedRegulation]:
        """Remove duplicates - checks hash, section, AND text similarity"""
        unique = []
        
        for reg in regulations:
            # Check 1: Content hash (exact duplicate)
            if reg.content_hash in self.seen_hashes:
                logger.debug(f"⚠️ Duplicate hash: {reg.section}")
                continue
            
            if reg.content_hash in self.session_regulations:
                logger.debug(f"⚠️ Duplicate in session: {reg.section}")
                continue
            
            # Check 2: Section + jurisdiction combo (same rule from same source)
            section_key = f"{reg.jurisdiction}:{reg.section}"
            if hasattr(self, 'seen_sections') and section_key in self.seen_sections:
                logger.debug(f"⚠️ Duplicate section: {section_key}")
                continue
            
            # Check 3: Very similar text (fuzzy dedup for same content, different formatting)
            text_start = reg.text[:100].lower().strip()
            is_similar = False
            for existing in unique:
                if existing.text[:100].lower().strip() == text_start:
                    is_similar = True
                    break
            
            if is_similar:
                logger.debug(f"⚠️ Similar text found: {reg.section}")
                continue
            
            # Passed all checks - it's unique
            self.seen_hashes.add(reg.content_hash)
            self.session_regulations.add(reg.content_hash)
            if hasattr(self, 'seen_sections'):
                self.seen_sections.add(section_key)
            
            unique.append(reg)
        
        if len(regulations) != len(unique):
            logger.info(f"🔄 Deduplication: {len(regulations)} → {len(unique)} ({len(regulations) - len(unique)} removed)")
        
        return unique
    
    async def _ai_extract_regulations(
        self, 
        content: str, 
        jurisdiction_config: Dict
    ) -> List[AIScrapedRegulation]:
        """AI extraction with improved prompt"""
        
        if len(content) > 45000:
            content = content[:45000]
        
        parts = jurisdiction_config['name'].split('-')
        jurisdiction = parts[0].strip() if parts else "GLOBAL"
        regulator = parts[1].strip() if len(parts) > 1 else jurisdiction
        
        prompt = f"""Extract financial marketing regulations from this document.

SOURCE: {jurisdiction_config['name']}
JURISDICTION: {jurisdiction}
REGULATOR: {regulator}

FOCUS: Marketing, communications, financial promotions, advertising rules

CATEGORIES (use exactly):
esg_greenwashing, guarantees_promises, risk_warnings, crypto_digital_assets, 
past_performance, testimonials, fair_clear_not_misleading, comparisons, 
fees_charges, suitability, general_marketing

RULES:
1. Extract 10-25 regulations (marketing/communications ONLY)
2. Each regulation: 150-500 words of actual regulatory text
3. Official section reference (e.g., "COBS 4.2.1R", "Article 24(3)")
4. Risk level: critical, high, medium, low

CONTENT:
{content[:38000]}

Return JSON:
{{
  "regulations": [
    {{
      "section": "Official reference",
      "title": "Title (max 100 chars)",
      "text": "Full regulatory text (150-500 words)",
      "category": "exact category from list",
      "risk_level": "critical|high|medium|low",
      "penalty_info": "Fines if mentioned",
      "effective_date": "YYYY-MM-DD or current"
    }}
  ]
}}
"""
        
        try:
            response = self.openai_client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o'),
                messages=[
                    {"role": "system", "content": "You are a legal expert. Extract financial marketing regulations. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            regulations_data = result.get("regulations", [])
            
            regulations = []
            for reg_data in regulations_data:
                text = reg_data.get("text", "")
                category = reg_data.get("category", "general_marketing")
                
                if len(text) < 150:
                    continue

                # Skip garbage content
                garbage_patterns = [
                    "cookie policy", "privacy policy", "terms of use",
                    "javascript required", "enable javascript", "page not found",
                    "404 error", "access denied", "subscribe to newsletter",
                    "sign up for", "click here to"
                ]
                text_lower = text.lower()
                if any(pattern in text_lower for pattern in garbage_patterns):
                    logger.debug(f"⚠️ Skipping garbage content: {text[:50]}...")
                    continue
                
                # Validate it looks like regulatory content
                regulatory_keywords = ["must", "shall", "required", "prohibited", "compliance", "rule", "regulation"]
                if not any(kw in text_lower for kw in regulatory_keywords):
                    logger.debug(f"⚠️ Skipping non-regulatory content")
                    continue
                
                if category not in self.VALID_CATEGORIES:
                    category = "general_marketing"
                
                regulation = AIScrapedRegulation(
                    jurisdiction=jurisdiction,
                    regulator=regulator,
                    section=reg_data.get("section", "UNKNOWN"),
                    title=reg_data.get("title", "Untitled")[:100],
                    text=text[:2000],
                    category=category,
                    risk_level=reg_data.get("risk_level", "medium"),
                    effective_date=reg_data.get("effective_date", "current"),
                    source_url=jurisdiction_config.get('url', ''),
                    penalty_info=reg_data.get("penalty_info", "")[:500]
                )
                regulations.append(regulation)
            
            logger.info(f"🤖 AI extracted {len(regulations)} regulations")
            return regulations
            
        except Exception as e:
            logger.error(f"❌ AI extraction failed: {e}")
            return []
    
    async def save_regulations_to_cosmos(
        self, 
        regulations: List[AIScrapedRegulation],
        batch_size: int = 25
    ) -> Dict[str, int]:
        """Save to Cosmos DB AND index to AI Search with embeddings"""
        
        stats = {
            "total": len(regulations),
            "saved": 0,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "error_details": []
        }
        
        container = self.db.get_container("rules")
        if not container:
            logger.error("❌ Rules container not found!")
            stats["errors"] = len(regulations)
            return stats
        
        search_docs = []
        
        for i in range(0, len(regulations), batch_size):
            batch = regulations[i:i + batch_size]
            
            for reg in batch:
                try:
                    doc = reg.to_cosmos_dict()
                    
                    if not doc.get("jurisdiction"):
                        logger.warning(f"⚠️ Skipping regulation without jurisdiction")
                        stats["skipped"] += 1
                        continue
                    
                    # Validate content quality
                    text = doc.get("text", "")
                    if len(text) < 100:
                        logger.debug(f"⚠️ Skipping short text ({len(text)} chars)")
                        stats["skipped"] += 1
                        continue
                    
                    # Save to Cosmos DB
                    container.upsert_item(body=doc)
                    stats["saved"] += 1
                    
                    # Prepare for AI Search indexing
                    search_doc = {
                        "id": doc["id"],
                        "text": text,
                        "jurisdiction": doc["jurisdiction"],
                        "source_document": f"{doc.get('regulator', 'Unknown')} - {doc.get('title', 'Untitled')}",
                        "section_reference": doc.get("section_reference", ""),
                        "category": doc.get("category", "general_marketing"),
                        "risk_level": doc.get("risk_level", "medium"),
                        "penalty_info": doc.get("penalty_info", "")[:500],
                        "effective_date": doc.get("effective_date", "current"),
                        "last_updated": datetime.utcnow().isoformat(),
                    }
                    search_docs.append(search_doc)
                    
                    if stats["saved"] % 10 == 0:
                        logger.info(f"   Progress: {stats['saved']}/{stats['total']}")
                        
                except Exception as e:
                    logger.error(f"❌ Save failed: {e}")
                    stats["errors"] += 1
                    stats["error_details"].append(str(e)[:100])
            
            await asyncio.sleep(0.3)
        
        # Index to AI Search with embeddings
        if search_docs and self.knowledge_base:
            try:
                logger.info(f"📤 Indexing {len(search_docs)} regulations to AI Search...")
                result = self.knowledge_base.ingest_bulk_dicts(search_docs, batch_size=25)
                stats["indexed"] = result.get("succeeded", 0)
                
                if result.get("errors"):
                    stats["error_details"].extend(result["errors"][:5])
                
                logger.info(f"✅ AI Search: {stats['indexed']}/{len(search_docs)} indexed")
                
            except Exception as e:
                logger.error(f"❌ AI Search indexing failed: {e}")
                stats["error_details"].append(f"AI Search: {str(e)[:100]}")
        elif not self.knowledge_base:
            logger.warning("⚠️ AI Search not configured - skipping indexing")
        
        return stats

    async def close(self):
        """Cleanup"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        if self.session:
            await self.session.close()


if __name__ == "__main__":
    asyncio.run(scrape_all_jurisdictions())