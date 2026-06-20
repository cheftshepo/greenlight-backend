"""
Enhanced Azure Language Service with AI-Powered PII Analysis
"""
import os
import logging
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

class EnhancedLanguageAnalyzer:
    """Enterprise-grade language analysis with AI context"""
    
    def __init__(self):
        self.endpoint = os.getenv('LANGUAGE_SERVICE_ENDPOINT')
        self.key = os.getenv('LANGUAGE_SERVICE_KEY')
        self.location = os.getenv('LANGUAGE_SERVICE_LOCATION', 'uksouth')
        
        self.client = None
        self.openai_client = None
        self.enabled = False
        
        # Initialize Azure Language Service
        if self.endpoint and self.key:
            try:
                from azure.ai.textanalytics import TextAnalyticsClient
                from azure.core.credentials import AzureKeyCredential
                
                self.client = TextAnalyticsClient(
                    endpoint=self.endpoint,
                    credential=AzureKeyCredential(self.key)
                )
                self.enabled = True
                logger.info(f"✅ Language Service ready: {self.endpoint[:50]}")
            except Exception as e:
                logger.error(f"❌ Language Service init failed: {e}")
        else:
            logger.warning("⚠️ Language Service disabled - missing credentials")
        
        # Initialize OpenAI for enhanced analysis
        try:
            from openai import AzureOpenAI
            self.openai_client = AzureOpenAI(
                api_key=os.getenv('AZURE_OPENAI_API_KEY'),
                api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview'),
                azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
            )
            logger.info("✅ OpenAI client ready for enhanced analysis")
        except Exception as e:
            logger.warning(f"⚠️ OpenAI client not available: {e}")
    
    def analyze_with_ai_context(self, text: str, jurisdiction: str, document_type: str = "marketing_material") -> Dict:
        """Full analysis with AI-powered context understanding"""
        
        if not self.enabled or not text:
            return self._empty_analysis()
        
        logger.info(f"🔬 AI-Enhanced analysis of {len(text)} chars")
        
        try:
            # Basic language service analysis
            basic_analysis = self._basic_analysis(text)
            
            # AI-powered context analysis
            ai_context = self._analyze_context_with_ai(text, jurisdiction, document_type)
            
            # Enhanced PII analysis
            enhanced_pii = self._enhance_pii_with_context(
                basic_analysis.get('pii_detected', []),
                text,
                jurisdiction,
                document_type
            )
            
            # Merge results
            result = {
                **basic_analysis,
                'ai_context': ai_context,
                'enhanced_pii': enhanced_pii['items'],
                'pii_summary': enhanced_pii['summary'],
                'compliance_risks': self._generate_compliance_risks(
                    enhanced_pii['items'],
                    basic_analysis.get('entities', []),
                    basic_analysis.get('key_phrases', []),
                    jurisdiction,
                    document_type
                ),
                'analyzed_at': datetime.utcnow().isoformat()
            }
            
            logger.info(f"✅ AI-Enhanced analysis complete")
            return result
            
        except Exception as e:
            logger.error(f"❌ AI-enhanced analysis failed: {e}")
            return self._basic_analysis(text) if self.enabled else self._empty_analysis()
    
    def _basic_analysis(self, text: str) -> Dict:
        """Basic language service analysis"""
        if not self.enabled:
            return self._empty_analysis()
        
        try:
            # Truncate if too long
            text_sample = text[:125000] if len(text) > 125000 else text
            
            # Run analyses
            pii_items = self._detect_pii(text_sample)
            entities = self._extract_entities(text_sample)
            key_phrases = self._extract_key_phrases(text_sample)
            sentiment = self._analyze_sentiment(text_sample)
            
            # Calculate promotional score
            promotional_score = self._calculate_promotional_score(
                text_sample, entities, key_phrases
            )
            
            return {
                'pii_detected': pii_items,
                'pii_count': len(pii_items),
                'entities': entities,
                'entity_count': len(entities),
                'key_phrases': key_phrases,
                'sentiment': sentiment,
                'promotional_score': promotional_score,
                'basic_analysis': True
            }
            
        except Exception as e:
            logger.error(f"❌ Basic analysis failed: {e}")
            return self._empty_analysis()
    
    def _analyze_context_with_ai(self, text: str, jurisdiction: str, document_type: str) -> Dict:
        """Use AI to understand document context and intent"""
        if not self.openai_client:
            return {'ai_available': False}
        
        try:
            # Sample text for context analysis
            sample = text[:2000] + ("..." if len(text) > 2000 else "")
            
            prompt = f"""Analyze this document for compliance context:

JURISDICTION: {jurisdiction}
DOCUMENT TYPE: {document_type}
SAMPLE TEXT: {sample[:1000]}...

Provide analysis of:
1. Document purpose/intent
2. Target audience (retail/institutional)
3. Key compliance themes
4. Likely regulatory focus areas

Output JSON format:"""
            
            response = self.openai_client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4'),
                messages=[
                    {
                        "role": "system",
                        "content": "You are a compliance analyst. Provide concise, accurate context analysis."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )
            
            ai_result = json.loads(response.choices[0].message.content)
            return {
                'ai_available': True,
                **ai_result
            }
            
        except Exception as e:
            logger.warning(f"⚠️ AI context analysis failed: {e}")
            return {'ai_available': False, 'error': str(e)}
    
    def _enhance_pii_with_context(self, pii_items: List[Dict], text: str, jurisdiction: str, document_type: str) -> Dict:
        """Enhance PII detection with context awareness"""
        if not pii_items:
            return {
                'items': [],
                'summary': self._get_empty_pii_summary()
            }
        
        # Get document context for PII analysis
        document_context = {
            'type': document_type,
            'jurisdiction': jurisdiction,
            'is_public_facing': document_type in ['website', 'social_media', 'email']
        }
        
        enhanced_items = []
        for item in pii_items:
            pii_type = item.get('type', 'unknown')
            pii_text = item.get('text', '')
            
            # Analyze context around PII
            context = self._get_pii_context(text, item.get('offset', 0), item.get('length', 0))
            
            # Determine risk based on context
            risk_level = self._assess_pii_risk(pii_type, pii_text, context, document_context)
            
            # Determine if relevant
            relevant = self._is_pii_relevant(pii_type, pii_text, context, document_context)
            
            # Generate smart suggestion
            suggestion = self._generate_pii_suggestion(pii_type, pii_text, risk_level, document_context)
            
            enhanced_item = {
                **item,
                'risk_level': risk_level,
                'relevant': relevant,
                'context': context,
                'suggestion': suggestion,
                'jurisdiction': jurisdiction,
                'document_type': document_type
            }
            
            enhanced_items.append(enhanced_item)
        
        # Create enhanced summary
        summary = self._create_enhanced_pii_summary(enhanced_items, document_context)
        
        return {
            'items': enhanced_items,
            'summary': summary
        }
    
    def _get_pii_context(self, text: str, offset: int, length: int) -> str:
        """Get context around PII (50 chars before and after)"""
        start = max(0, offset - 50)
        end = min(len(text), offset + length + 50)
        return text[start:end]
    
    def _assess_pii_risk(self, pii_type: str, pii_text: str, context: str, document_context: Dict) -> str:
        """Assess PII risk level based on type, context, AND document type"""
        
        # CRITICAL: Check document type first
        doc_type = document_context.get('type', 'unknown')
        
        # CVs/Resumes are SUPPOSED to have personal info
        if doc_type in ['cv', 'resume', 'curriculum_vitae', 'application']:
            # In CVs, PII is expected - only flag if truly sensitive
            if pii_type.lower() in ['socialsecuritynumber', 'creditcardnumber', 'bankaccountnumber', 'password']:
                return 'high'  # These shouldn't be in CVs
            else:
                return 'low'  # Names, emails, phones are normal in CVs
        
        # Critical PII types (always high risk in marketing)
        critical_types = [
            'socialsecuritynumber', 'creditcardnumber', 'bankaccountnumber',
            'password', 'pin', 'drivinglicensenumber', 'passportnumber'
        ]
        
        # High risk types (context-dependent)
        high_risk_types = ['email', 'phonenumber', 'idnumber']
        
        # Medium risk types
        medium_risk_types = ['person', 'address', 'dateofbirth']
        
        pii_type_lower = pii_type.lower()
        
        # For marketing materials
        if document_context.get('is_public_facing', False):
            if any(critical in pii_type_lower for critical in critical_types):
                return 'critical'
            elif any(high in pii_type_lower for high in high_risk_types):
                # Check if it's company contact info
                if 'contact' in context.lower() or 'info@' in pii_text.lower() or 'support@' in pii_text.lower():
                    return 'low'  # Company contact info is OK
                return 'high'
            elif any(medium in pii_type_lower for medium in medium_risk_types):
                return 'medium'
            else:
                return 'low'
        else:
            # Internal documents have different risk profile
            if any(critical in pii_type_lower for critical in critical_types):
                return 'critical'
            elif any(high in pii_type_lower for high in high_risk_types):
                return 'medium'
            else:
                return 'low'
    def _is_pii_relevant(self, pii_type: str, pii_text: str, context: str, document_context: Dict) -> bool:
        """Determine if PII is actually relevant (not example/test data)"""
        
        # Check for example/test patterns
        example_patterns = [
            'example.com', 'test.com', 'sample.com',
            'john.doe', 'jane.doe', 'test@test',
            '123-45-6789', '000-00-0000',  # Example SSN
            '4111-1111-1111-1111'  # Example credit card
        ]
        
        pii_lower = pii_text.lower()
        context_lower = context.lower()
        
        # Check if it's example data
        if any(pattern in pii_lower for pattern in example_patterns):
            return False
        
        # Check if context suggests it's example/test
        if 'example' in context_lower or 'test' in context_lower or 'sample' in context_lower:
            return False
        
        # Company contact info in public docs might be acceptable
        if document_context.get('is_public_facing', False):
            if pii_type.lower() in ['email', 'phonenumber']:
                if 'contact' in context_lower or 'info' in context_lower:
                    return True  # Public contact info is often acceptable
        
        return True
    
    def _generate_pii_suggestion(self, pii_type: str, pii_text: str, risk_level: str, document_context: Dict) -> str:
        """Generate smart suggestion for handling PII"""
        
        suggestions = {
            'critical': f"Remove {pii_type} immediately. This is highly sensitive data.",
            'high': f"Review {pii_type}. Consider anonymizing or removing from public document.",
            'medium': f"Consider if {pii_type} is necessary for document purpose.",
            'low': f"{pii_type} may be acceptable depending on context."
        }
        
        base_suggestion = suggestions.get(risk_level, "Review for compliance.")
        
        # Add context-specific advice
        if document_context.get('is_public_facing', False):
            if pii_type.lower() in ['email', 'phonenumber']:
                base_suggestion += " For public documents, use generic contact methods."
        
        return base_suggestion
    
    def _create_enhanced_pii_summary(self, pii_items: List[Dict], document_context: Dict) -> Dict:
        """Create comprehensive PII summary"""
        if not pii_items:
            return self._get_empty_pii_summary()
        
        by_type = {}
        samples = {}
        risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        relevant_count = 0
        
        for item in pii_items:
            pii_type = item.get('type', 'unknown')
            
            # Count by type
            by_type[pii_type] = by_type.get(pii_type, 0) + 1
            
            # Collect samples
            if pii_type not in samples:
                samples[pii_type] = []
            if len(samples[pii_type]) < 2:
                samples[pii_type].append(item.get('text', '')[:50])
            
            # Count risk levels
            risk = item.get('risk_level', 'medium')
            if risk in risk_counts:
                risk_counts[risk] += 1
            
            # Count relevant
            if item.get('relevant', True):
                relevant_count += 1
        
        # Smart suggestions based on context
        smart_suggestions = []
        if document_context.get('is_public_facing', False):
            smart_suggestions.append("Use generic contact methods instead of personal emails/phones")
            smart_suggestions.append("Anonymize client names in case studies")
        
        if risk_counts['critical'] > 0:
            smart_suggestions.append("CRITICAL: Remove all sensitive financial/ID numbers immediately")
        
        return {
            "count": len(pii_items),
            "relevant_count": relevant_count,
            "by_type": by_type,
            "samples": samples,
            "critical_risk_count": risk_counts["critical"],
            "high_risk_count": risk_counts["high"],
            "medium_risk_count": risk_counts["medium"],
            "low_risk_count": risk_counts["low"],
            "smart_suggestions": smart_suggestions[:5],
            "context_analysis": {
                "is_public_facing": document_context.get('is_public_facing', False),
                "document_type": document_context.get('type', 'unknown'),
                "jurisdiction": document_context.get('jurisdiction', 'UK')
            }
        }
    
    def _generate_compliance_risks(self, pii_items: List[Dict], entities: List[Dict], 
                                 key_phrases: List[str], jurisdiction: str, document_type: str) -> List[Dict]:
        """Generate compliance risks based on analysis"""
        risks = []
        
        # PII risks
        if pii_items:
            high_risk_pii = [p for p in pii_items if p.get('risk_level') in ['critical', 'high']]
            risk_level = 'CRITICAL' if high_risk_pii else 'HIGH'
            
            risks.append({
                'type': 'pii_detected',
                'severity': risk_level,
                'message': f'Document contains {len(pii_items)} PII items ({len(high_risk_pii)} high-risk)',
                'regulation': f'{jurisdiction} Data Protection Regulations',
                'recommendation': 'Review all PII items and remove/anonymize as appropriate'
            })
        
        # Missing disclaimers check
        disclaimer_keywords = ['risk', 'warning', 'disclaimer', 'past performance', 'not guaranteed']
        has_disclaimer = any(keyword in ' '.join(key_phrases).lower() for keyword in disclaimer_keywords)
        
        if not has_disclaimer and document_type == 'financial_marketing':
            risks.append({
                'type': 'missing_disclaimer',
                'severity': 'HIGH',
                'message': 'No risk disclaimers detected in financial marketing document',
                'regulation': f'{jurisdiction} Financial Promotions Rules',
                'recommendation': 'Add required risk warnings and performance disclaimers'
            })
        
        # Promotional language check
        promotional_words = ['guaranteed', 'risk-free', 'certain', 'assured', '100%', 'always']
        promotional_count = sum(1 for phrase in key_phrases 
                              if any(word in phrase.lower() for word in promotional_words))
        
        if promotional_count > 2:
            risks.append({
                'type': 'promotional_language',
                'severity': 'MEDIUM',
                'message': f'Document contains {promotional_count} promotional claims',
                'regulation': f'{jurisdiction} Fair Marketing Regulations',
                'recommendation': 'Review and tone down promotional language'
            })
        
        return risks
    
    # Basic analysis methods (keep from your original)
    def _detect_pii(self, text: str) -> List[Dict]:
        """Detect PII entities"""
        if not self.enabled:
            return []
        
        try:
            response = self.client.recognize_pii_entities([text], language='en')
            
            pii_items = []
            for doc in response:
                if not doc.is_error:
                    for entity in doc.entities:
                        pii_items.append({
                            'type': entity.category,
                            'subtype': entity.subcategory or entity.category,
                            'text': entity.text,
                            'confidence': entity.confidence_score,
                            'offset': entity.offset,
                            'length': entity.length
                        })
            
            return pii_items
            
        except Exception as e:
            logger.error(f"❌ PII detection failed: {e}")
            return []
    
    def _extract_entities(self, text: str) -> List[Dict]:
        """Extract named entities"""
        if not self.enabled:
            return []
        
        try:
            response = self.client.recognize_entities([text], language='en')
            
            entities = []
            for doc in response:
                if not doc.is_error:
                    for entity in doc.entities:
                        entities.append({
                            'text': entity.text,
                            'category': entity.category,
                            'subcategory': entity.subcategory,
                            'confidence': entity.confidence_score
                        })
            
            return entities
            
        except Exception as e:
            logger.error(f"❌ Entity extraction failed: {e}")
            return []
    
    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract key phrases"""
        if not self.enabled:
            return []
        
        try:
            response = self.client.extract_key_phrases([text], language='en')
            
            for doc in response:
                if not doc.is_error:
                    return doc.key_phrases[:20]
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Key phrase extraction failed: {e}")
            return []
    
    def _analyze_sentiment(self, text: str) -> str:
        """Analyze sentiment"""
        if not self.enabled:
            return 'neutral'
        
        try:
            response = self.client.analyze_sentiment([text], language='en')
            
            for doc in response:
                if not doc.is_error:
                    return doc.sentiment
            
            return 'neutral'
            
        except Exception as e:
            logger.error(f"❌ Sentiment analysis failed: {e}")
            return 'neutral'
    
    def _calculate_promotional_score(self, text: str, entities: List[Dict], key_phrases: List[str]) -> int:
        """Calculate promotional score (0-100)"""
        score = 0
        text_lower = text.lower()
        
        # Prohibited words
        prohibited = [
            'guarantee', 'guaranteed', 'risk-free', 'no risk', 'zero risk',
            'certain', 'assured', 'promise', '100%', 'always win'
        ]
        
        for word in prohibited:
            count = text_lower.count(word)
            score += count * 15
        
        # Performance claims
        performance_words = [
            'outperform', 'beat the market', 'superior returns',
            'exceptional', 'unique opportunity', 'limited time'
        ]
        
        for word in performance_words:
            if word in text_lower:
                score += 10
        
        # Check for percentage claims
        import re
        percentages = re.findall(r'\d+%', text)
        score += len(percentages) * 5
        
        return min(100, score)
    
    def _get_empty_pii_summary(self) -> Dict:
        """Return empty PII summary structure"""
        return {
            "count": 0,
            "relevant_count": 0,
            "by_type": {},
            "samples": {},
            "critical_risk_count": 0,
            "high_risk_count": 0,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "smart_suggestions": [],
            "context_analysis": {
                "is_public_facing": False,
                "document_type": "unknown",
                "jurisdiction": "UK"
            }
        }
    
    def _empty_analysis(self) -> Dict:
        """Return empty analysis structure"""
        return {
            'pii_detected': [],
            'pii_count': 0,
            'entities': [],
            'entity_count': 0,
            'key_phrases': [],
            'sentiment': 'neutral',
            'promotional_score': 0,
            'ai_context': {'ai_available': False},
            'enhanced_pii': [],
            'pii_summary': self._get_empty_pii_summary(),
            'compliance_risks': [],
            'analyzed_at': datetime.utcnow().isoformat(),
            'basic_analysis': False
        }


# Singleton
enhanced_language_analyzer = EnhancedLanguageAnalyzer()


def enhance_scan_with_language_analysis(
    text: str, 
    jurisdiction: str, 
    existing_violations: List[Dict],
    document_type: str = "marketing_material"
) -> Dict:
    """
    Enhanced language analysis wrapper
    """
    try:
        # Use enhanced analyzer
        analysis = enhanced_language_analyzer.analyze_with_ai_context(
            text, jurisdiction, document_type
        )
        
        # Convert compliance risks to violations
        additional_violations = []
        
        for risk in analysis.get('compliance_risks', []):
            violation = {
                'violation_id': f"lang_{risk['type']}_{int(datetime.utcnow().timestamp())}",
                'category': risk['type'],
                'severity': risk['severity'].upper(),
                'matched_text': risk.get('message', '')[:200],
                'ai_reasoning': risk['message'],
                'regulatory_reference': risk['regulation'],
                'regulation': risk['regulation'],
                'section': risk['regulation'],
                'rule': risk['regulation'],
                'description': risk['message'],
                'remediation': risk['recommendation'],
                'source': 'language_service',
                'confidence': 0.85
            }
            
            # Add PII details for PII violations
            if risk['type'] == 'pii_detected':
                violation['pii_details'] = analysis.get('pii_summary', {})
                violation['pii_items'] = analysis.get('enhanced_pii', [])[:5]
            
            additional_violations.append(violation)
        
        return {
            'language_analysis': analysis,
            'additional_violations': additional_violations,
            'pii_detected': analysis.get('enhanced_pii', []),
            'pii_count': len(analysis.get('enhanced_pii', [])),
            'pii_summary': analysis.get('pii_summary', {}),
            'entity_count': analysis.get('entity_count', 0),
            'promotional_score': analysis.get('promotional_score', 0),
            'ai_context': analysis.get('ai_context', {})
        }
        
    except Exception as e:
        logger.error(f"❌ Enhanced language analysis failed: {e}")
        return {
            'language_analysis': enhanced_language_analyzer._empty_analysis(),
            'additional_violations': [],
            'pii_detected': [],
            'pii_count': 0,
            'pii_summary': {},
            'entity_count': 0,
            'promotional_score': 0,
            'ai_context': {'ai_available': False, 'error': str(e)}
        }