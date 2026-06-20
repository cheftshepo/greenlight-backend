"""
ENHANCED FAST RAG Scanner - Enterprise Intelligence in Fast Package
============================================================
Enterprise-level context awareness + RAG speed
"""

import logging
import os
import json
import time
import re
import math
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

# Global instances for reusability
_openai_client = None
_knowledge_base = None

@dataclass
class SimpleViolation:
    """Enhanced violation with Enterprise context"""
    violation_id: str
    matched_text: str
    severity: str
    regulation_ref: str
    reasoning: str
    confidence: float
    category: str = "fair_clear_not_misleading"
    context: str = ""
    page_number: int = 1
    remediation: str = ""
    before_after: Dict = None
    effective_date: str = ""        # ← NEW: from the regulation that triggered this
    
    def to_dict(self) -> Dict:
        return {
            "violation_id": self.violation_id,
            "matched_text": self.matched_text[:200],
            "severity": self.severity,
            "regulation": self.regulation_ref,
            "category": self.category,
            "section": self.regulation_ref,
            "rule": self.regulation_ref,
            "description": self.reasoning[:200],
            "confidence": round(self.confidence, 2),
            "ai_reasoning": self.reasoning,
            "text_snippet": self.matched_text[:200],
            "context": self.context[:300] if self.context else "",
            "page_number": self.page_number,
            "remediation": self.remediation,
            "before_after": self.before_after if self.before_after else {},
            "effective_date": self.effective_date,   # ← NEW
            "regulation_citation": {
                "section_reference": self.regulation_ref,
                "source_document": self.regulation_ref,
                "regulatory_text": self.reasoning[:200],
                "effective_date": self.effective_date,  # ← NEW
            }
        }

class FastRAGScanner:
    """Fast RAG Scanner with ENTERPRISE Intelligence & Context Awareness"""
    
    def __init__(self):
        # Configuration - Enhanced for better context
        self.max_chunks = 10  # Increased for better coverage
        self.max_text_length = 50000  # Increased limit
        self.chunk_size = 4000  # Larger chunks for better context
        self.chunk_overlap = 800  # More overlap for context continuity
        
        # Severity mapping for risk scoring
        self.severity_scores = {
            "CRITICAL": 25,
            "HIGH": 18,
            "MEDIUM": 10,
            "LOW": 3
        }
        
        logger.info("✅ ENHANCED FAST RAG Scanner initialized - Enterprise Intelligence")
    
    def _init_openai(self) -> bool:
        """Initialize OpenAI client with Enterprise capabilities"""
        global _openai_client
        if _openai_client:
            return True
        
        try:
            from openai import AzureOpenAI
            _openai_client = AzureOpenAI(
                api_key=os.getenv('AZURE_OPENAI_API_KEY'),
                api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
                azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
                timeout=60.0,  # Increased for complex analysis
                max_retries=3  # More retries for reliability
            )
            logger.info("✅ OpenAI client initialized - Enterprise Mode")
            return True
        except Exception as e:
            logger.error(f"❌ OpenAI init failed: {e}")
            return False
    
    def _smart_chunk_text(self, text: str, page_map: Optional[List[Dict]] = None) -> List[Dict]:
        """Enhanced chunking with semantic boundaries and page awareness"""
        chunks = []
        
        if not text or len(text.strip()) == 0:
            logger.warning("❌ Empty text provided to scanner")
            return chunks
        
        text_length = len(text)
        start = 0
        
        # Handle short text
        if text_length <= self.chunk_size:
            chunk_pages = self._get_pages_for_chunk(start, text_length, page_map) if page_map else []
            return [{
                "id": 0,
                "text": text,
                "start_pos": 0,
                "end_pos": text_length,
                "pages": chunk_pages
            }]
        
        # Smart chunking with ENTERPRISE intelligence
        while start < text_length and len(chunks) < self.max_chunks:
            end = min(start + self.chunk_size, text_length)
            
            # Find natural boundaries for better context preservation
            if end < text_length:
                # Look for semantic breaks in priority order
                boundaries = [
                    ('\n\n', 2),           # Paragraph break
                    ('. \n', 3),           # Sentence end with newline
                    ('.\n', 2),            # Sentence end at line end
                    ('!\n', 2),            # Exclamation at line end
                    ('?\n', 2),            # Question at line end
                    ('\n', 1),             # Simple newline
                    ('. ', 2),             # Sentence end
                    ('! ', 2),             # Exclamation
                    ('? ', 2),             # Question
                    ('; ', 2),             # Semicolon
                    (', ', 2),             # Comma
                ]
                
                for boundary, length in boundaries:
                    idx = text.rfind(boundary, start, end)
                    if idx > start and (end - idx) < 200:  # Only adjust if close
                        end = idx + length
                        break
            
            chunk_text = text[start:end].strip()
            
            # Only add meaningful chunks
            if len(chunk_text) > 100:  # Increased minimum for better context
                chunk_pages = self._get_pages_for_chunk(start, end, page_map) if page_map else []
                chunks.append({
                    "id": len(chunks),
                    "text": chunk_text,
                    "start_pos": start,
                    "end_pos": end,
                    "pages": chunk_pages
                })
            
            # Move start with overlap (dynamic based on content)
            next_start = end - self.chunk_overlap
            if next_start > start:
                start = next_start
            else:
                start = end  # Fallback
            
            # Ensure progress
            if start >= text_length:
                break
        
        logger.info(f"📝 Created {len(chunks)} ENHANCED chunks from {text_length} chars")
        return chunks
    
    def _get_pages_for_chunk(self, start: int, end: int, page_map: List[Dict]) -> List[Dict]:
        """Determine which pages a chunk covers"""
        if not page_map:
            return []
        
        chunk_pages = []
        for page in page_map:
            page_start = page.get('start_char', 0)
            page_end = page.get('end_char', 0)
            
            # Check if chunk overlaps with page
            if not (end < page_start or start > page_end):
                chunk_pages.append(page)
        
        return chunk_pages
    
    def _get_relevant_regulations_fast(self, text: str, jurisdiction: str) -> List[Dict]:
        """Get relevant regulations WITH FULL METADATA using ENHANCED vector search"""
        
        try:
            from function_app_pkg.core.knowledge_base import RegulatoryKnowledgeBase
            
            if not hasattr(self, '_kb'):
                self._kb = RegulatoryKnowledgeBase()
            
            # ENHANCED: Create smarter queries based on content analysis
            content_summary = self._extract_key_themes(text[:500])
            
            # Multiple queries for better coverage
            queries = [
                f"{jurisdiction} financial marketing regulations for: {content_summary}",
                f"{jurisdiction} compliance requirements for investment communications",
                f"{jurisdiction} FCA COBS rules for financial promotions"
            ]
            
            all_regulations = {}  # Dict to store full regulation objects by ID
            for query in queries[:2]:  # Use top 2 queries
                try:
                    results = self._kb.search(
                        query_text=query,
                        jurisdiction=jurisdiction,
                        top_k=4  # Get more results
                    )
                    
                    for result in results:
                        # Extract the chunk with full metadata
                        if isinstance(result, dict) and 'chunk' in result:
                            chunk = result['chunk']
                            chunk_id = chunk.id if hasattr(chunk, 'id') else chunk.get('id')
                            
                            # Store by ID to avoid duplicates
                            if chunk_id not in all_regulations:
                                # Convert to dict if it's an object
                                if hasattr(chunk, 'to_dict'):
                                    all_regulations[chunk_id] = chunk.to_dict()
                                elif hasattr(chunk, '__dict__'):
                                    all_regulations[chunk_id] = vars(chunk)
                                else:
                                    all_regulations[chunk_id] = (chunk)
                        
                except Exception as e:
                    logger.warning(f"⚠️ Query failed: {e}")
                    continue
            
            regulations_list = list(all_regulations.values())
            
            if regulations_list:
                logger.info(f"📚 Found {len(regulations_list)} relevant regulations via ENHANCED RAG")
                logger.info(f"   With metadata: risk_level, common_violations, keywords, etc.")
                return regulations_list[:6]  # Return top 6 with full metadata
            
        except Exception as e:
            logger.warning(f"⚠️ Knowledge base failed: {e}")
        
        # FALLBACK - return empty to indicate RAG unavailable
        logger.error("❌ RAG unavailable - no regulations found")
        return []

    def _extract_key_themes(self, text: str) -> str:
        """Extract key themes from text for better regulation matching"""
        # Simple theme extraction (could be enhanced with NLP)
        keywords = ["guarantee", "return", "profit", "risk", "investment", 
                   "fund", "performance", "safe", "secure", "growth"]
        
        themes = []
        for keyword in keywords:
            if keyword.lower() in text.lower():
                themes.append(keyword)
        
        if themes:
            return f"content about {', '.join(themes[:3])}"
        return "general financial content"
    
    def _analyze_chunk_fast(self, chunk: Dict, regulations: List[Dict], jurisdiction: str, 
                       briefing: Optional[Dict] = None, image_context: Optional[str] = None) -> List[SimpleViolation]:
        """ENTERPRISE-LEVEL ANALYSIS with HUMAN CONTEXT AWARENESS + ENRICHED REGULATIONS"""
        
        # Build comprehensive context
        context_parts = []
        
        if briefing:
            context_parts.append(f"""
    DOCUMENT CONTEXT & PURPOSE:
    • Marketing Type: {briefing.get('marketing_type', 'General Promotion')}
    • Distribution: {briefing.get('distribution_media', 'Multiple Channels')}
    • Target Audience: {briefing.get('target_audience', 'General Public')}
    • Document Intent: {briefing.get('content_type', 'Marketing Material')}
            """)
        
        if image_context:
            context_parts.append(f"""
    VISUAL CONTEXT (from image analysis):
    {image_context[:500]}
            """)
        
        # Page context
        pages = chunk.get('pages', [])
        page_numbers = [p.get('page_number', 1) for p in pages] if pages else [1]
        page_context = f"Pages {min(page_numbers)}-{max(page_numbers)}" if len(page_numbers) > 1 else f"Page {page_numbers[0]}"
        
        briefing_context = "\n".join(context_parts) if context_parts else ""
        
        # ✅ BUILD ENRICHED REGULATIONS CONTEXT - FIXED VERSION
        if regulations and len(regulations) > 0:
            regs_text_parts = []
            for reg in regulations:
                # Extract metadata SAFELY
                section_ref = reg.get('section_reference', 'Unknown')
                text = reg.get('text', reg.get('regulatory_text', ''))  # ✅ Fallback
                risk_level = reg.get('risk_level', 'medium')
                common_violations = reg.get('common_violations', [])
                keywords = reg.get('keywords', [])
                plain_english = reg.get('plain_english', text[:200])  # ✅ Fallback
                
                reg_block = f"""
    ═══════════════════════════════════════════════════════════════════
    {section_ref} ({risk_level.upper()} risk)
    ═══════════════════════════════════════════════════════════════════
    RULE TEXT: {text[:300] if text else 'N/A'}

    PLAIN ENGLISH: {plain_english[:200] if plain_english else 'Review for general compliance'}

    COMMON VIOLATIONS (watch for these):
    {chr(10).join([f'• {v}' for v in (common_violations[:3] if common_violations else [])])}

    KEY INDICATORS: {', '.join(keywords[:8]) if keywords else 'N/A'}
    ═══════════════════════════════════════════════════════════════════
    """
                regs_text_parts.append(reg_block)
            
            regs_text = '\n'.join(regs_text_parts)
        else:
            # ✅ PROVIDE FALLBACK GUIDANCE
            regs_text = f"""
⚠️ Regulatory database unavailable for {jurisdiction}

Apply general {jurisdiction} financial marketing principles:
• Clear and not misleading
• Fair balance of risks and benefits  
• No guarantees of future performance
• Appropriate risk warnings
• No unsubstantiated claims
"""
        
        # ENTERPRISE-LEVEL CONTEXT-AWARE PROMPT
        jurisdiction_display = jurisdiction if jurisdiction else "Unknown"

        prompt = f"""You are a SENIOR COMPLIANCE OFFICER analyzing documents for {jurisdiction_display} jurisdiction.

JURISDICTION: {jurisdiction_display}
DOCUMENT SECTION: {page_context}

{briefing_context}

APPLICABLE REGULATIONS (for {jurisdiction_display}):
{regs_text}

═══════════════════════════════════════════════════════════════════

🎯 YOUR MISSION: JURISDICTION-SPECIFIC ANALYSIS FOR {jurisdiction_display}

You are analyzing this document ONLY for {jurisdiction_display} compliance.
DO NOT apply UK/FCA rules unless jurisdiction is UK.
DO NOT apply US/SEC rules unless jurisdiction is US.
DO NOT apply general rules - use {jurisdiction_display}-specific regulations.

CRITICAL THINKING FRAMEWORK:

1. **JURISDICTION VERIFICATION**
- This is a {jurisdiction_display} document
- Apply {jurisdiction_display} regulations ONLY
- Reference {jurisdiction_display}-specific laws and guidelines
- Consider {jurisdiction_display} cultural and legal context

2. **DOCUMENT TYPE ASSESSMENT**
- Is this actually marketing/promotional material?
- Could it be a CV/resume, research paper, or other non-marketing document?
- If NOT marketing: Flag as "document_type_mismatch" and skip marketing violations

3. **UNDERSTAND THE WHOLE PICTURE**
- What message is this document trying to convey?
- Who is the intended audience?
- What is the overall tone and impression?
- Is this appropriate for {jurisdiction_display} market?

4. **ANALYZE MEANING, NOT JUST WORDS**
✅ CORRECT: Apply {jurisdiction_display} standards
❌ WRONG: Apply UK/US/other jurisdiction rules

5. **MATCH AGAINST {jurisdiction_display} COMMON VIOLATIONS**
- Each regulation above lists {jurisdiction_display}-specific violation patterns
- Check if document contains similar language under {jurisdiction_display} law
- Consider {jurisdiction_display} cultural and legal context

═══════════════════════════════════════════════════════════════════

📄 TEXT TO ANALYZE:
\"\"\"
{chunk["text"]}
\"\"\"

═══════════════════════════════════════════════════════════════════

🎯 OUTPUT REQUIREMENTS (STRICT JSON):

{{
  "violations": [
    {{
      "text": "exact problematic phrase (150 chars max)",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "rule": "specific {jurisdiction_display} regulation reference",
      "reason": "Why this violates {jurisdiction_display} law specifically",
      "category": "guarantees_promises|risk_disclosure|past_performance|misleading_comparisons|unsubstantiated_claims|fair_clear_not_misleading|document_type_mismatch",
      "context": "100-200 chars before/after for context",
      "page": {page_numbers[0] if page_numbers else 1},
      "remediation": "How to fix for {jurisdiction_display} compliance",
      "before_after": {{
        "before": "problematic text exactly as shown",
        "after": "compliant alternative for {jurisdiction_display}"
      }},
      "conf": 0.75-1.00 (only if ≥75% confident this violates {jurisdiction_display} law)
    }}
  ]
}}

═══════════════════════════════════════════════════════════════════

🔑 KEY PRINCIPLES:
1. ONLY flag violations of {jurisdiction_display} regulations
2. If document is not marketing material, flag "document_type_mismatch" with LOW severity
3. Consider {jurisdiction_display} cultural and legal context
4. Be a HUMAN analyst, not a keyword bot
5. Only flag if ≥75% confident it violates {jurisdiction_display} law specifically
6. Provide {jurisdiction_display}-SPECIFIC fixes
7. DO NOT apply UK rules to non-UK documents
8. DO NOT apply US rules to non-US documents

BEGIN {jurisdiction_display} COMPLIANCE ANALYSIS:"""
        
        if not _openai_client:
            logger.error("❌ OpenAI client not initialized")
            return []
        
        try:
            response = _openai_client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are a senior {jurisdiction} compliance officer with human-level judgment. 
    You understand nuance, context, and real-world investor perception. 
    You analyze MEANING, not just words. You provide actionable, specific fixes.
    You have access to enriched regulations with common violation patterns.
    Output ONLY valid JSON with no additional text."""
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # Low for consistency
                max_tokens=3000,  # Increased for detailed analysis
                response_format={"type": "json_object"}
            )
            
            raw_response = response.choices[0].message.content
            violations_list = self._extract_json_safe(raw_response)
            
            # Convert to SimpleViolation objects
            violations = []
            for i, v in enumerate(violations_list):
                conf = float(v.get("conf", v.get("confidence", 0.8)))
                if conf < 0.75:
                    continue

                page_num = v.get("page", page_numbers[0] if page_numbers else 1)

                # ── Pull effective_date from the matching regulation ──────────
                rule_ref = v.get("rule", "")
                effective_date = ""
                if regulations:
                    # Try to match by section_reference first
                    for reg in regulations:
                        if reg.get("section_reference", "") in rule_ref or rule_ref in reg.get("section_reference", ""):
                            effective_date = reg.get("effective_date", "")
                            break
                    # Fall back to first regulation's date
                    if not effective_date:
                        effective_date = regulations[0].get("effective_date", "")
                # ─────────────────────────────────────────────────────────────

                violation = SimpleViolation(
                    violation_id=f"ent_rag_{chunk['id']}_{i}_{int(time.time())}",
                    matched_text=v.get("text", "")[:200],
                    severity=v.get("severity", "MEDIUM").upper(),
                    regulation_ref=v.get("rule", regulations[0].get('section_reference') if regulations else "General Compliance"),
                    reasoning=v.get("reason", "")[:500],
                    confidence=conf,
                    category=v.get("category", "fair_clear_not_misleading"),
                    context=v.get("context", "")[:300],
                    page_number=page_num,
                    remediation=v.get("remediation", ""),
                    before_after=v.get("before_after", {}),
                    effective_date=effective_date,   # ← NEW
                )

                violations.append(violation)
            return violations
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse failed in analysis")
            logger.error(f"   Error: {e}")
            if 'raw_response' in locals():
                logger.error(f"   Raw response preview: {raw_response[:500]}")
            return []
        except Exception as e:
            logger.error(f"❌ ENTERPRISE analysis failed: {e}")
            logger.error(f"   Chunk ID: {chunk.get('id', 'unknown')}")
            logger.error(f"   Text length: {len(chunk.get('text', ''))}")
            logger.exception(e)
            return []

    def _extract_json_safe(self, text: str) -> List[Dict]:
        """BULLETPROOF JSON extraction with multiple fallbacks"""
        
        # Method 1: Direct JSON parse
        try:
            result = json.loads(text)
            return result.get("violations", [])
        except:
            pass
        
        # Method 2: Strip ALL markdown variations
        try:
            clean = text.strip()
            
            # Remove markdown code blocks
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0]
            elif "```" in clean:
                parts = clean.split("```")
                if len(parts) >= 2:
                    clean = parts[1]
            
            clean = clean.strip()
            
            # Remove any leading/trailing text
            start = clean.find('{')
            end = clean.rfind('}')
            if start != -1 and end != -1:
                clean = clean[start:end+1]
            
            result = json.loads(clean)
            return result.get("violations", [])
        except Exception as e:
            logger.warning(f"Method 2 failed: {e}")
            pass
        
        # Method 3: Regex extraction
        try:
            # Find the first complete JSON object
            pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                json_str = match.group(0)
                result = json.loads(json_str)
                return result.get("violations", [])
        except:
            pass
        
        # Method 4: Character-by-character brace matching
        try:
            start = text.find('{')
            if start == -1:
                logger.error("❌ No JSON found")
                return []
            
            brace_count = 0
            in_string = False
            escape_next = False
            
            for i in range(start, len(text)):
                char = text[i]
                
                if escape_next:
                    escape_next = False
                    continue
                
                if char == '\\':
                    escape_next = True
                    continue
                
                if char == '"':
                    in_string = not in_string
                    continue
                
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_str = text[start:i+1]
                            result = json.loads(json_str)
                            return result.get("violations", [])
        except Exception as e:
            logger.error(f"❌ Brace matching failed: {e}")
        
        logger.error("❌ ALL JSON extraction methods failed")
        logger.error(f"Raw text preview: {text[:300]}")
        return []
    
    def scan(
        self,
        text: str,
        document_id: str,
        jurisdiction: str = "UK",
        metadata: Optional[Dict] = None,
        briefing: Optional[Dict] = None,
        image_analysis: Optional[str] = None,
        organization_id: Optional[str] = None
    ) -> Tuple[List[Dict], Dict]:
        """Main scan method - ENTERPRISE intelligence with RAG speed"""
        
        logger.info("=" * 80)
        logger.info(f"🚀 ENTERPRISE RAG SCAN STARTING")
        logger.info(f"📄 Document: {document_id}")
        logger.info(f"🌍 Jurisdiction: {jurisdiction}")
        logger.info(f"📊 Size: {len(text):,} characters")
        if image_analysis:
            logger.info(f"🖼️ Image analysis: {len(image_analysis):,} chars")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        # Initialize stats
        stats = {
            "scan_started": datetime.utcnow().isoformat(),
            "method": "ENTERPRISE_RAG",
            "text_length": len(text),
            "jurisdiction": jurisdiction,
            "chunks_analyzed": 0,
            "violations_found": 0,
            "critical_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "overall_risk_score": 0,
            "has_image_context": bool(image_analysis),
            "has_briefing": bool(briefing),
            "errors": []
        }
        
        try:
            # Validate input
            if not text or len(text.strip()) < 20:
                stats["errors"].append("Text too short for meaningful analysis")
                logger.warning("⚠️ Text too short for analysis")
                return [], stats
            
            # Initialize OpenAI
            if not self._init_openai():
                stats["errors"].append("OpenAI initialization failed")
                return [], stats
            
            # Get page map from metadata
            page_map = metadata.get('page_map', []) if metadata else []
            
            # Get regulations via RAG (no hardcoded fallbacks)
            regulations = self._get_relevant_regulations_fast(text, jurisdiction)
            logger.info(f"📚 Regulations from RAG: {len(regulations)} found")
            
            # Enhanced chunking with page awareness
            chunks = self._smart_chunk_text(text, page_map)
            stats["chunks_analyzed"] = len(chunks)
            
            if not chunks:
                stats["errors"].append("No text chunks created")
                return [], stats
            
            # ENTERPRISE analysis of each chunk
            all_violations = []
            for chunk in chunks:
                chunk_violations = self._analyze_chunk_fast(
                    chunk, 
                    regulations, 
                    jurisdiction,
                    briefing,
                    image_analysis
                )
                all_violations.extend(chunk_violations)
                
                # Timeout check (3 minutes max for speed)
                if (time.time() - start_time) > 180:
                    logger.warning("⚠️ 3-min timeout - stopping early")
                    stats["warnings"] = ["Scan stopped early due to timeout"]
                    break
            
            # Convert to dicts
            violations_dicts = [v.to_dict() for v in all_violations]
            stats["violations_found"] = len(violations_dicts)
            
            # Calculate statistics
            severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            for v in violations_dicts:
                severity = v.get("severity", "MEDIUM").upper()
                if severity in severity_counts:
                    severity_counts[severity] += 1
            
            stats.update({
                "critical_count": severity_counts["CRITICAL"],
                "high_count": severity_counts["HIGH"],
                "medium_count": severity_counts["MEDIUM"],
                "low_count": severity_counts["LOW"]
            })
            
            # ═══════════════════════════════════════════════════════════════════
            # ✅ REALISTIC RISK SCORING ALGORITHM
            # ═══════════════════════════════════════════════════════════════════
            
            import math

            # Base score: 0 for clean documents.
            # We only add points for actual violations — a document
            # with zero violations should score 0, not 15.
            base_score = 0

            # Weighted violation score with diminishing returns
            violation_score = 0
            for severity, count in severity_counts.items():
                if count > 0:
                    base_points = self.severity_scores.get(severity, 10)
                    diminishing_factor = 1 / (1 + math.log(count + 1))
                    weighted_points = base_points * count * diminishing_factor
                    violation_score += weighted_points

            # Length factor (longer docs penalised less per violation)
            text_length_factor = 1.0
            if stats["text_length"] > 20000:
                text_length_factor = 0.8
            elif stats["text_length"] < 5000:
                text_length_factor = 1.2

            # Violation density
            if stats["text_length"] > 0:
                violation_density = (stats["violations_found"] / stats["text_length"]) * 1000
                density_multiplier = min(1.5, 1.0 + (violation_density * 0.1))
            else:
                violation_density = 0.0
                density_multiplier = 1.0

            raw_score = base_score + (violation_score * text_length_factor * density_multiplier)

            # Non-linear scaling: spread scores across 0–90
            if raw_score == 0:
                final_score = 0
            elif raw_score <= 20:
                final_score = 5 + (raw_score * 1.5)
            elif raw_score <= 50:
                final_score = 35 + ((raw_score - 20) * 0.8)
            elif raw_score <= 80:
                final_score = 59 + ((raw_score - 50) * 0.6)
            else:
                final_score = 77 + min(13, (raw_score - 80) * 0.3)

            # Cap at 90 from the compliance scan alone.
            # PII risk is added separately in scan_api.py.
            compliance_score = min(90, max(0, int(final_score)))
            stats["overall_risk_score"] = compliance_score

            # ── Human-readable score explanation ─────────────────────────────
            # Build a plain-English breakdown that goes straight to the UI.
            # scan_api.py will append the PII contribution before sending.
            explanation_parts = []

            if severity_counts["CRITICAL"] > 0:
                explanation_parts.append(
                    f"{severity_counts['CRITICAL']} critical violation(s) "
                    f"(+{round(severity_counts['CRITICAL'] * self.severity_scores['CRITICAL'] * (1 / (1 + math.log(severity_counts['CRITICAL'] + 1))), 1)} pts)"
                )
            if severity_counts["HIGH"] > 0:
                explanation_parts.append(
                    f"{severity_counts['HIGH']} high violation(s) "
                    f"(+{round(severity_counts['HIGH'] * self.severity_scores['HIGH'] * (1 / (1 + math.log(severity_counts['HIGH'] + 1))), 1)} pts)"
                )
            if severity_counts["MEDIUM"] > 0:
                explanation_parts.append(
                    f"{severity_counts['MEDIUM']} medium violation(s) "
                    f"(+{round(severity_counts['MEDIUM'] * self.severity_scores['MEDIUM'] * (1 / (1 + math.log(severity_counts['MEDIUM'] + 1))), 1)} pts)"
                )
            if severity_counts["LOW"] > 0:
                explanation_parts.append(
                    f"{severity_counts['LOW']} low violation(s) "
                    f"(+{round(severity_counts['LOW'] * self.severity_scores['LOW'] * (1 / (1 + math.log(severity_counts['LOW'] + 1))), 1)} pts)"
                )

            if not explanation_parts:
                compliance_summary = "No regulatory violations detected."
            else:
                compliance_summary = "Violations found: " + "; ".join(explanation_parts) + "."

            if text_length_factor != 1.0:
                doc_length_note = (
                    "Score reduced slightly — long documents naturally contain more content to review."
                    if text_length_factor < 1.0 else
                    "Score weighted slightly higher — short documents with violations are higher risk."
                )
            else:
                doc_length_note = ""

            stats["score_explanation"] = {
                # For the score badge tooltip
                "summary": compliance_summary,
                "doc_length_note": doc_length_note,

                # Full breakdown for the detail page
                "components": {
                    "compliance_violations": {
                        "score": compliance_score,
                        "label": "Regulatory compliance",
                        "detail": compliance_summary,
                    },
                    # pii component is filled in by scan_api.py
                    "pii_risk": {
                        "score": 0,
                        "label": "Data protection (PII)",
                        "detail": "Calculated separately",
                    }
                },

                # Raw numbers for nerds / debugging
                "calculation": {
                    "base_score": base_score,
                    "violation_score": round(violation_score, 1),
                    "text_length_factor": round(text_length_factor, 2),
                    "violation_density_per_1k": round(violation_density, 4),
                    "density_multiplier": round(density_multiplier, 2),
                    "raw_score": round(raw_score, 1),
                    "compliance_score": compliance_score,
                    "severity_breakdown": {
                        "critical": severity_counts["CRITICAL"],
                        "high":     severity_counts["HIGH"],
                        "medium":   severity_counts["MEDIUM"],
                        "low":      severity_counts["LOW"],
                    }
                }
            }

            # Keep risk_breakdown for backward compat
            stats["risk_breakdown"] = stats["score_explanation"]["calculation"]
            
            # ═══════════════════════════════════════════════════════════════════
            # END RISK SCORING
            # ═══════════════════════════════════════════════════════════════════
            
            # Final logging
            total_time = time.time() - start_time
            stats["total_time_seconds"] = round(total_time, 2)
            stats["scan_completed"] = datetime.utcnow().isoformat()
            
            logger.info("=" * 80)
            logger.info(f"✅ ENTERPRISE RAG SCAN COMPLETE")
            logger.info(f"   Violations: {stats['violations_found']}")
            logger.info(f"   Critical: {stats['critical_count']}, High: {stats['high_count']}, Medium: {stats['medium_count']}, Low: {stats['low_count']}")
            logger.info(f"   Risk Score: {stats['overall_risk_score']}/100 (from raw: {stats['risk_breakdown']['raw_score']})")
            logger.info(f"   Time: {total_time:.1f}s")
            logger.info(f"   Method: ENTERPRISE_RAG with context-awareness")
            logger.info(f"   Violation Density: {stats['risk_breakdown']['violation_density_per_1k']:.4f} per 1K chars")
            logger.info("=" * 80)
            
            return violations_dicts, stats
            
        except Exception as e:
            error_msg = f"Scan crashed: {str(e)[:200]}"
            logger.error(f"❌ {error_msg}")
            logger.exception(e)
            stats["errors"].append(error_msg)
            stats["total_time_seconds"] = round(time.time() - start_time, 2)
            stats["overall_risk_score"] = 50  # Default to medium risk on error
            return [], stats

# Singleton instance
rag_scanner = FastRAGScanner()
logger.info("✅ ENHANCED FAST RAG scanner singleton created - Enterprise Intelligence")