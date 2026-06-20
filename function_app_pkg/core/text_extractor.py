"""
Text Extractor - Simplified version matching your imports
Location: function_app_pkg/core/text_extractor.py
"""
import logging
import os
from typing import Dict, Tuple, Optional
import io

logger = logging.getLogger(__name__)

class TextExtractor:
    """Extract text from documents"""
    
    def __init__(self):
        self.doc_intelligence_client = None
        self._init_services()
    
    def _init_services(self):
        """Initialize Azure Document Intelligence"""
        try:
            from azure.ai.formrecognizer import DocumentAnalysisClient
            from azure.core.credentials import AzureKeyCredential
            
            endpoint = os.getenv('DOCUMENT_INTELLIGENCE_ENDPOINT')
            key = os.getenv('DOCUMENT_INTELLIGENCE_KEY')
            
            if endpoint and key:
                self.doc_intelligence_client = DocumentAnalysisClient(
                    endpoint=endpoint,
                    credential=AzureKeyCredential(key)
                )
                logger.info("✅ Document Intelligence ready")
        except Exception as e:
            logger.warning(f"⚠️ Document Intelligence init failed: {e}")
    
    def extract(self, file_content: bytes, filename: str, mimetype: str = None) -> Dict:
        """Extract text and return dictionary format"""
        try:
            text, metadata = self.extract_text(file_content, filename, mimetype or 'application/octet-stream')
            
            return {
                'success': True,
                'text': text,
                'method': metadata.get('extraction_method', 'unknown'),
                'metadata': metadata
            }
        except Exception as e:
            logger.error(f"❌ Extraction failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'text': '',
                'method': 'none',
                'metadata': {}
            }
    
    def extract_text(self, file_content: bytes, filename: str, mimetype: str) -> Tuple[str, Dict]:
        """Extract text using best method"""
        file_ext = filename.lower().split('.')[-1]
        metadata = {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": len(file_content),
            "file_type": file_ext
        }
        
        # Plain text
        if file_ext == 'txt' or 'text' in mimetype.lower():
            try:
                text = file_content.decode('utf-8')
                metadata["extraction_method"] = "utf8"
                return text, metadata
            except:
                text = file_content.decode('latin-1', errors='ignore')
                metadata["extraction_method"] = "latin1"
                return text, metadata
        
        # PDF with Azure Document Intelligence
        if file_ext == 'pdf' and self.doc_intelligence_client:
            text, di_metadata = self._extract_with_document_intelligence(file_content)
            if text:
                metadata.update(di_metadata)
                return text, metadata
        
        # Fallback PDF
        if file_ext == 'pdf':
            text, fallback = self._extract_pdf_fallback(file_content)
            if text:
                metadata.update(fallback)
                return text, metadata
        
        raise ValueError(f"Could not extract text from {filename}")
    
    def _extract_with_document_intelligence(self, file_content: bytes) -> Tuple[Optional[str], Dict]:
        """Extract using Azure Document Intelligence"""
        try:
            poller = self.doc_intelligence_client.begin_analyze_document(
                "prebuilt-layout",
                document=io.BytesIO(file_content)
            )
            result = poller.result()
            
            text_parts = []
            for page in result.pages:
                for line in page.lines:
                    text_parts.append(line.content)
            
            text = '\n'.join(text_parts)
            
            return text, {
                "extraction_method": "document_intelligence",
                "pages_processed": len(result.pages),
                "confidence": 95
            }
        except Exception as e:
            logger.error(f"❌ Document Intelligence failed: {e}")
            return None, {}
    
    def _extract_pdf_fallback(self, file_content: bytes) -> Tuple[Optional[str], Dict]:
        """Fallback PDF extraction"""
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_content))
            text = '\n'.join(page.extract_text() for page in reader.pages)
            
            return text, {
                "method": "pypdf",
                "confidence": 80,
                "pages_processed": len(reader.pages)
            }
        except:
            return None, {}

# Global instance
extractor = TextExtractor()