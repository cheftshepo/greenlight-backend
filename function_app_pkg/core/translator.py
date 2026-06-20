"""
Azure Translator Service
Multi-language support for compliance violations
"""
import os
import logging
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

class TranslationService:
    """Enterprise translation service with robust error handling"""
    
    # Add this back - scan.py needs it
    LANGUAGE_NAMES = {
        'en': 'English',
        'de': 'German',
        'fr': 'French',
        'es': 'Spanish',
        'it': 'Italian',
        'nl': 'Dutch',
        'pt': 'Portuguese',
        'pl': 'Polish'
    }
    
    def __init__(self):
        """Initialize with environment validation"""
        self.endpoint = "https://api.cognitive.microsofttranslator.com"
        self.key = os.getenv('TRANSLATOR_KEY')
        self.location = os.getenv('TRANSLATOR_LOCATION', 'global')
        self.api_version = '3.0'
        
        # Validate
        self.enabled = bool(self.key and len(self.key) > 20)
        
        if not self.enabled:
            logger.warning("Translation service disabled - missing key")
        else:
            logger.info(f"✅ Translation service ready")
        
        # Cache
        self._cache = {}
        self._cache_ttl = timedelta(minutes=30)
    
    def detect_language(self, text: str) -> str:
        """Detect language using Azure Translator"""
        if not self.enabled or not text:
            return 'en'
        
        try:
            # Use sample for efficiency
            sample = text[:500].strip()
            if len(sample) < 10:
                return 'en'
            
            path = '/detect'
            constructed_url = f"{self.endpoint}{path}"
            
            params = {'api-version': self.api_version}
            headers = {
                'Ocp-Apim-Subscription-Key': self.key,
                'Ocp-Apim-Subscription-Region': self.location,
                'Content-type': 'application/json'
            }
            body = [{'text': sample}]
            
            response = requests.post(
                constructed_url,
                params=params,
                headers=headers,
                json=body,
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                if result and len(result) > 0:
                    lang = result[0]['language']
                    confidence = result[0].get('score', 0)
                    
                    # Only trust high confidence
                    if confidence > 0.7:
                        logger.info(f"🌍 Detected: {lang} (confidence: {confidence:.2f})")
                        return lang
                    else:
                        logger.warning(f"Low confidence detection: {lang} ({confidence:.2f})")
                        return 'en'
            
            return 'en'
            
        except Exception as e:
            logger.warning(f"Language detection failed: {e}")
            return 'en'
    
    def translate_violations(
        self, 
        violations: List[Dict], 
        target_language: str = 'en'
    ) -> List[Dict]:
        """Translate violation messages with error tolerance"""
        if not self.enabled or not violations or target_language == 'en':
            return violations
        
        logger.info(f"Translating {len(violations)} items to {target_language}")
        
        translated = []
        for v in violations:
            try:
                # Clone the violation
                translated_v = v.copy()
                
                # Translate only if field exists and has content
                if 'rule_description' in v and v['rule_description']:
                    translated_v['rule_description_translated'] = self._translate_safe(
                        v['rule_description'], target_language
                    )
                
                if 'ai_reasoning' in v and v['ai_reasoning']:
                    translated_v['ai_reasoning_translated'] = self._translate_safe(
                        v['ai_reasoning'], target_language
                    )
                
                if 'remediation' in v and v['remediation']:
                    translated_v['remediation_translated'] = self._translate_safe(
                        v['remediation'], target_language
                    )
                
                translated_v['translation_language'] = target_language
                translated.append(translated_v)
                
            except Exception as e:
                logger.error(f"Failed to translate violation: {e}")
                translated.append(v)  # Keep original
        
        logger.info(f"✅ Translation complete")
        return translated
    
    def _translate_safe(self, text: str, target_lang: str) -> Optional[str]:
        """Safe translation with fallback"""
        if not text or not text.strip():
            return text
        
        try:
            return self._translate_text(text, target_lang)
        except:
            return text  # Return original on failure
    
    def _translate_text(self, text: str, target_lang: str) -> Optional[str]:
        """Core translation with caching"""
        if not text or not text.strip():
            return text
        
        # Cache check
        cache_key = f"{hash(text[:100])}_{target_lang}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if datetime.utcnow() - cached['timestamp'] < self._cache_ttl:
                return cached['translation']
        
        try:
            path = '/translate'
            constructed_url = f"{self.endpoint}{path}"
            
            # Let Azure auto-detect source language
            params = {
                'api-version': self.api_version,
                'to': target_lang
            }
            
            headers = {
                'Ocp-Apim-Subscription-Key': self.key,
                'Ocp-Apim-Subscription-Region': self.location,
                'Content-type': 'application/json'
            }
            
            body = [{'text': text}]
            
            response = requests.post(
                constructed_url,
                params=params,
                headers=headers,
                json=body,
                timeout=10
            )
            
            response.raise_for_status()
            result = response.json()
            
            if result and len(result) > 0:
                translation = result[0]['translations'][0]['text']
                
                # Cache
                self._cache[cache_key] = {
                    'translation': translation,
                    'timestamp': datetime.utcnow()
                }
                
                return translation
            
            return text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Translation API error: {e}")
            return text
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return text
    
    def get_language_for_jurisdiction(self, jurisdiction: str) -> str:
        """Get default language for jurisdiction"""
        # Simple fallback mapping
        mapping = {
            'UK': 'en', 'US': 'en', 'ZA': 'en',
            'DE': 'de', 'FR': 'fr', 'ES': 'es',
            'IT': 'it', 'NL': 'nl', 'PT': 'pt'
        }
        return mapping.get(jurisdiction.upper(), 'en')
    
    def translate_summary(self, summary_text: str, target_language: str = 'en') -> str:
        """Translate summary text"""
        if not self.enabled or target_language == 'en':
            return summary_text
        return self._translate_safe(summary_text, target_language) or summary_text
    
    def get_supported_languages(self) -> Dict[str, str]:
        """Return supported languages"""
        return self.LANGUAGE_NAMES.copy()

# Singleton instance
translator = TranslationService()