"""
CONTEXT-AWARE QUESTION GENERATOR
Generates document-specific compliance questions based on ACTUAL content
"""

import logging
import os
import json
import re
from typing import List, Dict, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

class ContextAwareQuestionGenerator:
    """Generate document-specific compliance questions"""
    
    def __init__(self):
        self.openai_client = None
        self.deployment = None
        self._init_openai()
    
    def _init_openai(self):
        """Initialize Azure OpenAI"""
        try:
            from openai import AzureOpenAI
            
            key = os.getenv('AZURE_OPENAI_API_KEY')
            endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
            deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4.1')
            api_version = os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview')
            
            if not key or not endpoint:
                raise ValueError("OpenAI credentials required")
            
            self.openai_client = AzureOpenAI(
                api_key=key,
                api_version=api_version,
                azure_endpoint=endpoint
            )
            self.deployment = deployment
            logger.info(f"✅ CONTEXT-AWARE Question generator ready")
                
        except Exception as e:
            logger.error(f"❌ Question generator init failed: {e}")
            raise
    
    def analyze_document_content(self, text: str, filename: str) -> Dict:
        """Analyze document to understand what it actually is"""
        logger.info(f"🔍 Analyzing document: {filename}")
        
        # Quick content analysis
        text_lower = text.lower()
        
        analysis = {
            "likely_document_type": "unknown",
            "key_topics": [],
            "contains_financial_terms": False,
            "contains_legal_terms": False,
            "contains_ethical_terms": False,
            "contains_pii_indicators": False,
            "document_length_category": self._categorize_length(len(text)),
            "structure_indicators": {}
        }
        
        # Check for document type indicators
        financial_terms = ['invest', 'return', 'fund', 'portfolio', 'risk', 'fee', 'commission']
        legal_terms = ['compliance', 'regulation', 'law', 'act', 'section', 'clause']
        ethical_terms = ['ethics', 'integrity', 'moral', 'values', 'principles', 'conduct']
        
        for term in financial_terms:
            if term in text_lower:
                analysis["contains_financial_terms"] = True
                break
        
        for term in legal_terms:
            if term in text_lower:
                analysis["contains_legal_terms"] = True
                break
        
        for term in ethical_terms:
            if term in text_lower:
                analysis["contains_ethical_terms"] = True
                break
        
        # PII indicators
        pii_patterns = [
            r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # Names
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # Phone
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'  # Email
        ]
        
        for pattern in pii_patterns:
            if re.search(pattern, text):
                analysis["contains_pii_indicators"] = True
                break
        
        # Determine likely document type
        if 'ethics' in filename.lower() or analysis["contains_ethical_terms"]:
            analysis["likely_document_type"] = "ethics_manifesto"
            analysis["key_topics"] = ["ethics", "conduct", "values", "principles"]
        elif analysis["contains_financial_terms"]:
            analysis["likely_document_type"] = "financial_marketing"
            analysis["key_topics"] = ["investment", "risk", "returns", "fees"]
        elif analysis["contains_legal_terms"]:
            analysis["likely_document_type"] = "legal_document"
            analysis["key_topics"] = ["compliance", "regulation", "legal"]
        else:
            analysis["likely_document_type"] = "general_document"
        
        # Extract key phrases for context
        sentences = text.split('.')
        key_sentences = [s.strip() for s in sentences[:5] if len(s.strip()) > 20]
        analysis["opening_context"] = key_sentences[:3]
        
        logger.info(f"📊 Document analysis: {analysis['likely_document_type']}")
        return analysis
    
    def generate_document_specific_questions(
        self, 
        text: str,
        filename: str,
        jurisdiction: str,
        violations: List[Dict],
        document_analysis: Dict,
        risk_score: int,
        briefing_info: Dict = None
    ) -> List[Dict]:
        """
        Generate context-aware compliance questions
        """
        logger.info(f"🎯 Generating context-aware questions for {filename} ({jurisdiction})")
        
        # Analyze violations for question focus
        violation_summary = self._analyze_violations(violations)
        
        # Get document-specific prompt
        prompt = self._build_context_aware_prompt(
            text=text,
            filename=filename,
            jurisdiction=jurisdiction,
            document_analysis=document_analysis,
            violation_summary=violation_summary,
            risk_score=risk_score,
            briefing_info=briefing_info
        )
        
        try:
            response = self.openai_client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_context_aware_system_prompt(jurisdiction)
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content.strip()
            result = json.loads(result_text)
            
            raw_questions = result.get('questions', [])
            
            # Transform to structured questions
            structured_questions = []
            for i, q in enumerate(raw_questions, 1):
                structured_q = self._create_context_aware_question(
                    q, 
                    i, 
                    jurisdiction,
                    document_analysis
                )
                if structured_q:
                    structured_questions.append(structured_q)
            
            logger.info(f"✅ Generated {len(structured_questions)} context-aware questions")
            return structured_questions
            
        except Exception as e:
            logger.error(f"❌ Context-aware question generation failed: {e}", exc_info=True)
            return []
    
    def _analyze_violations(self, violations: List[Dict]) -> Dict:
        """Analyze violations to understand what needs attention"""
        summary = {
            "total_violations": len(violations),
            "by_category": {},
            "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "pii_found": False,
            "specific_issues": []
        }
        
        for violation in violations:
            # Count by category
            category = violation.get('category', 'unknown')
            summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
            
            # Count by severity
            severity = violation.get('severity', 'MEDIUM')
            summary["by_severity"][severity] = summary["by_severity"].get(severity, 0) + 1
            
            # Check for PII
            if 'pii' in category.lower() or 'data_protection' in category.lower():
                summary["pii_found"] = True
            
            # Extract specific issues
            if violation.get('matched_text'):
                summary["specific_issues"].append({
                    "category": category,
                    "severity": severity,
                    "text_snippet": violation['matched_text'][:100] + "..."
                })
        
        return summary
    
    def _build_context_aware_prompt(
        self,
        text: str,
        filename: str,
        jurisdiction: str,
        document_analysis: Dict,
        violation_summary: Dict,
        risk_score: int,
        briefing_info: Dict = None
    ) -> str:
        """Build prompt for generating ACTIONABLE compliance questions"""
        
        # Build violation context
        violation_context = ""
        if violation_summary["total_violations"] > 0:
            violation_context = f"""
VIOLATION BREAKDOWN:
- Critical: {violation_summary['by_severity']['CRITICAL']}
- High: {violation_summary['by_severity']['HIGH']}
- Medium: {violation_summary['by_severity']['MEDIUM']}
- Low: {violation_summary['by_severity']['LOW']}

Categories: {', '.join(violation_summary['by_category'].keys())}
"""
        
        # Build briefing context
        briefing_context = ""
        if briefing_info:
            briefing_context = f"""
BRIEFING INFORMATION:
- Marketing Type: {briefing_info.get('marketing_type', 'N/A')}
- Distribution: {briefing_info.get('distribution_media', 'N/A')}
- Target Audience: {briefing_info.get('target_audience', 'N/A')}
"""
        
        # Get document excerpt
        context_text = text[:2000] if len(text) > 2000 else text
        
        prompt = f"""
===== GENERATE ACTIONABLE COMPLIANCE QUESTIONS =====

DOCUMENT: {filename}
JURISDICTION: {jurisdiction}
RISK SCORE: {risk_score}/100

DOCUMENT TYPE: {document_analysis.get('likely_document_type')}
KEY TOPICS: {', '.join(document_analysis.get('key_topics', []))}

VIOLATIONS FOUND: {violation_summary["total_violations"]}
{violation_context if violation_summary["total_violations"] > 0 else "NO VIOLATIONS DETECTED"}

{briefing_context}

DOCUMENT EXCERPT:   {context_text}---

🎯 YOUR TASK: Generate 5-8 SPECIFIC, ACTIONABLE compliance questions

EACH QUESTION MUST:
1. **Be answerable by reading the document** (not require external knowledge)
2. **Have clear Yes/No/Uncertain answer**
3. **Include helpful guidance** on what to look for
4. **Provide exact fix** if answer is "No"
5. **Cite specific regulation** that applies

---

📋 QUESTION QUALITY STANDARDS:

✅ GOOD QUESTION:
{{
  "verification_question": "Does the document include a risk warning within the first paragraph before any performance claims?",
  "context": "FCA requires risk warnings BEFORE performance data. Your document shows '15% returns' in paragraph 1 but risk warning is in paragraph 5.",
  "severity": "high",
  "category": "risk_disclosure",
  "regulatory_reference": "FCA COBS 4.2.1R",
  "help_text": "Check the first 3 paragraphs. Look for phrases like 'capital at risk', 'past performance', or 'investments can fall'. These must appear BEFORE any return percentages.",
  "exact_fix": "Add this sentence at the start of paragraph 1: 'Investments can fall as well as rise and you may get back less than you invest. Past performance is not a reliable indicator of future results.'"
}}

❌ BAD QUESTION:
{{
  "verification_question": "Is the document compliant with GDPR?",  // Too vague
  "help_text": "Check for GDPR compliance"  // Not helpful
}}

---

🎯 FOCUS AREAS FOR THIS DOCUMENT:

{self._get_focus_areas(document_analysis, violation_summary, briefing_info)}

---

OUTPUT JSON FORMAT:
{{
  "questions": [
    {{
      "verification_question": "Clear Yes/No/Uncertain question",
      "context": "Why this matters for THIS specific document",
      "severity": "critical|high|medium|low",
      "category": "pii_detection|risk_disclosure|guarantees_promises|past_performance|data_protection|costs_charges|suitability|fair_clear_not_misleading",
      "regulatory_reference": "Specific {jurisdiction} regulation (e.g., 'FCA COBS 4.2.1R')",
      "help_text": "Step-by-step guidance: 'Look in paragraph X for Y. Check if Z is present.'",
      "exact_fix": "If answer is NO: 'Add this text: [specific wording]. Remove this phrase: [specific text].'",
      "required": true/false
    }}
  ]
}}

---

GENERATE QUESTIONS NOW (5-8 questions, prioritized by severity)
"""
        
        return prompt

    def _get_focus_areas(self, doc_analysis: Dict, violations: Dict, briefing: Dict = None) -> str:
        """Generate focus areas based on actual document content"""
        
        focus = []
        
        if violations.get("pii_found"):
            focus.append("- **PII DETECTED**: Ask about data anonymization and consent")
        
        if violations.get("by_severity", {}).get("CRITICAL", 0) > 0:
            focus.append("- **CRITICAL VIOLATIONS**: Focus on guarantee language and risk-free claims")
        
        if doc_analysis.get("contains_financial_terms"):
            focus.append("- **FINANCIAL MARKETING**: Verify risk warnings and past performance disclaimers")
        
        if briefing and briefing.get("distribution_media") == "social_media":
            focus.append("- **SOCIAL MEDIA**: Check character limits force brevity in disclaimers")
        
        if not focus:
            focus.append("- **GENERAL COMPLIANCE**: Standard marketing compliance checks")
        
        return "\n".join(focus)

    def _get_context_aware_system_prompt(self, jurisdiction: str) -> str:
        return f"""You are a compliance expert specializing in {jurisdiction} regulations.

Your task: Generate document-specific compliance questions based on ACTUAL document content.

CRITICAL RULES:
1. Questions MUST be relevant to the ACTUAL document content provided
2. If the document is an ethics manifesto, ask about ethics compliance, NOT financial risk warnings
3. If PII was detected, ask SPECIFIC questions about the PII found
4. Tailor questions to the document type and content
5. Reference ACTUAL sections/content from the document when possible
6. Make questions actionable and verifiable

Do NOT generate generic boilerplate questions. Every question must be justified by the document content.
"""
    
    def _create_context_aware_question(
        self, 
        q: Dict, 
        idx: int, 
        jurisdiction: str,
        document_analysis: Dict
    ) -> Dict:
        """Create structured question with context"""
        
        if not isinstance(q, dict):
            return None
        
        # Generate unique ID
        question_id = f"q{idx}_{jurisdiction.lower()}_{int(datetime.utcnow().timestamp()) % 10000}"
        
        # Normalize severity
        severity = q.get('severity', 'medium').lower()
        severity_map = {
            'critical': 'critical',
            'high': 'high',
            'medium': 'medium',
            'low': 'low'
        }
        severity = severity_map.get(severity, 'medium')
        
        # Build structured question
        structured = {
            'question_id': question_id,
            'verification_question': q.get('verification_question', ''),
            'context': q.get('context', ''),
            'answer_options': ['yes', 'no', 'uncertain'],
            'category': q.get('category', 'general_compliance'),
            'severity': severity,
            'priority': self._severity_to_priority(severity),
            'required': severity in ['critical', 'high'],
            'regulatory_reference': q.get('regulatory_reference', ''),
            'help_text': q.get('help_text', ''),
            'document_specific': True,
            'generated_at': datetime.utcnow().isoformat(),
            'jurisdiction': jurisdiction,
            'document_type': document_analysis.get('likely_document_type', 'unknown')
        }
        
        return structured
    
    def _categorize_length(self, text_length: int) -> str:
        """Categorize document length"""
        if text_length < 1000:
            return "short"
        elif text_length < 5000:
            return "medium"
        elif text_length < 20000:
            return "long"
        else:
            return "very_long"
    
    def _severity_to_priority(self, severity: str) -> str:
        """Map severity to priority"""
        priority_map = {
            'critical': 'urgent',
            'high': 'high',
            'medium': 'medium',
            'low': 'low'
        }
        return priority_map.get(severity, 'medium')

# Global instance
context_aware_generator = ContextAwareQuestionGenerator()

def generate_context_aware_questions(
    text: str,
    filename: str,
    jurisdiction: str,
    violations: List[Dict] = None,
    risk_score: int = 0,
    briefing_info: Dict = None,
    document_id: str = None
) -> List[Dict]:
    """
    Main function to generate context-aware questions
    """
    # Analyze document first
    document_analysis = context_aware_generator.analyze_document_content(text, filename)
    
    # Generate questions based on actual content
    questions = context_aware_generator.generate_document_specific_questions(
        text=text,
        filename=filename,
        jurisdiction=jurisdiction,
        violations=violations or [],
        document_analysis=document_analysis,
        risk_score=risk_score,
        briefing_info=briefing_info
    )
    
    return questions

# Backward compatibility wrapper
def generate_questions(
    text: str,
    violations: List[Dict] = None,
    jurisdiction: str = "UK",
    document_type: str = "marketing_material",
    risk_score: int = 0,
    marketing_type: str = None,
    distribution_media: str = None,
    document_id: str = None,
    filename: str = "document.txt"  # Add filename parameter with default
) -> List[Dict]:
    """
    Backward compatibility - now uses context-aware generation
    """
    # Build briefing info if available
    briefing_info = None
    if marketing_type or distribution_media:
        briefing_info = {
            'marketing_type': marketing_type,
            'distribution_media': distribution_media
        }
    
    return generate_context_aware_questions(
        text=text,
        filename=filename,
        jurisdiction=jurisdiction,
        violations=violations or [],
        risk_score=risk_score,
        briefing_info=briefing_info,
        document_id=document_id
    )