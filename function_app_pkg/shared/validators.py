"""Input validators for the Compliance Scanner API - FIXED VERSION"""
import re
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import azure.functions as func
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# FILE VALIDATORS - FIXED TO MATCH EXTRACTOR CAPABILITIES
# =============================================================================

def validate_file_type(filename: str) -> bool:
    """
    Validate file type - ALIGNED WITH EXTRACTOR CAPABILITIES
    
    Supported formats:
    - Documents: PDF, DOCX, DOC, TXT (via Document Intelligence + plain text)
    - Images: PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP (via Computer Vision OCR)
    """
    # These are the ACTUAL formats that extractor.py can handle
    allowed_extensions = {
        # Document Intelligence formats
        '.pdf',      # Document Intelligence with page tracking
        '.docx',     # Document Intelligence  
        '.doc',      # Document Intelligence
        
        # Plain text (no AI needed)
        '.txt',      # Direct UTF-8/Latin-1 decoding
        
        # Computer Vision OCR formats
        '.png',      # OCR with page tracking
        '.jpg',      # OCR with page tracking
        '.jpeg',     # OCR with page tracking
        '.gif',      # OCR with page tracking
        '.bmp',      # OCR with page tracking
        '.tiff',     # OCR with page tracking
        '.webp'      # OCR with page tracking
    }
    
    if not filename:
        return False
    
    ext = os.path.splitext(filename)[1].lower()
    is_valid = ext in allowed_extensions
    
    if not is_valid:
        logger.warning(f"❌ Unsupported file type: {ext}")
        logger.info(f"✅ Supported types: {', '.join(sorted(allowed_extensions))}")
    
    return is_valid


def get_supported_file_types() -> dict:
    """
    Get comprehensive list of supported file types with extraction methods
    """
    return {
        'documents': {
            'pdf': {
                'extension': '.pdf',
                'method': 'Azure Document Intelligence',
                'features': ['Text extraction', 'Page mapping', 'Layout analysis']
            },
            'docx': {
                'extension': '.docx',
                'method': 'Azure Document Intelligence',
                'features': ['Text extraction', 'Page mapping', 'Layout analysis']
            },
            'doc': {
                'extension': '.doc',
                'method': 'Azure Document Intelligence',
                'features': ['Text extraction', 'Page mapping', 'Layout analysis']
            },
            'txt': {
                'extension': '.txt',
                'method': 'Direct text decoding (UTF-8/Latin-1)',
                'features': ['Instant text extraction']
            }
        },
        'images': {
            'png': {
                'extension': '.png',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'jpg': {
                'extension': '.jpg',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'jpeg': {
                'extension': '.jpeg',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'gif': {
                'extension': '.gif',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'bmp': {
                'extension': '.bmp',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'tiff': {
                'extension': '.tiff',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            },
            'webp': {
                'extension': '.webp',
                'method': 'Azure Computer Vision OCR',
                'features': ['Text extraction via OCR', 'Page mapping']
            }
        }
    }


def validate_file_size(file_bytes: bytes, max_mb: int = 50) -> bool:
    """Validate file size"""
    if not file_bytes:
        return False
    
    size_mb = len(file_bytes) / (1024 * 1024)
    
    if size_mb > max_mb:
        logger.warning(f"❌ File too large: {size_mb:.2f}MB (max: {max_mb}MB)")
        return False
    
    return True


def validate_filename(filename: str) -> bool:
    """Validate filename for security"""
    if not filename:
        return False
    
    # Check for path traversal attempts
    if '..' in filename or '/' in filename or '\\' in filename:
        logger.warning(f"❌ Path traversal attempt detected: {filename}")
        return False
    
    # Check for dangerous characters
    dangerous_chars = ['<', '>', ':', '"', '|', '?', '*']
    for char in dangerous_chars:
        if char in filename:
            logger.warning(f"❌ Dangerous character in filename: {char}")
            return False
    
    return True


# =============================================================================
# JURISDICTION VALIDATORS
# =============================================================================

def validate_jurisdiction(jurisdiction: str) -> bool:
    """Validate jurisdiction code"""
    valid_jurisdictions = [
        'UK',  # United Kingdom
        'US',  # United States
        'EU',  # European Union
        'AU',  # Australia
        'ZA',  # South Africa
        'DE',  # Germany
        'FR',  # France
        'ES',  # Spain
        'IT',  # Italy
        'NL',  # Netherlands
        'PT',  # Portugal
        'PL',  # Poland
        'CH',  # Switzerland
        'LU',  # Luxembourg
        'IE',  # Ireland
        'GLOBAL'  # Global standards
    ]
    
    return jurisdiction.upper() in valid_jurisdictions


def get_jurisdiction_name(jurisdiction_code: str) -> Optional[str]:
    """Get jurisdiction name from code"""
    jurisdiction_map = {
        'UK': 'United Kingdom',
        'US': 'United States',
        'EU': 'European Union',
        'AU': 'Australia',
        'ZA': 'South Africa',
        'DE': 'Germany',
        'FR': 'France',
        'ES': 'Spain',
        'IT': 'Italy',
        'NL': 'Netherlands',
        'PT': 'Portugal',
        'PL': 'Poland',
        'CH': 'Switzerland',
        'LU': 'Luxembourg',
        'IE': 'Ireland',
        'GLOBAL': 'Global Standards'
    }
    
    return jurisdiction_map.get(jurisdiction_code.upper())


# =============================================================================
# DATE VALIDATORS
# =============================================================================

def validate_date_format(date_string: str) -> bool:
    """
    Validate date format (ISO 8601)
    Accepts: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ
    """
    if not date_string:
        return False
    
    try:
        # Try parsing with timezone
        if 'T' in date_string:
            datetime.fromisoformat(date_string.rstrip('Z'))
        else:
            # Try simple date format
            datetime.strptime(date_string, '%Y-%m-%d')
        return True
    except (ValueError, AttributeError):
        return False


def validate_date_range(start_date: str, end_date: str) -> bool:
    """
    Validate that start_date is before end_date
    """
    if not start_date or not end_date:
        return False
    
    try:
        start = datetime.fromisoformat(start_date.rstrip('Z'))
        end = datetime.fromisoformat(end_date.rstrip('Z'))
        return start <= end
    except (ValueError, AttributeError):
        return False


# =============================================================================
# TEXT VALIDATORS
# =============================================================================

def validate_text_length(text: str, min_length: int = 10, max_length: int = 1000000) -> bool:
    """Validate text length"""
    if not text:
        return False
    
    text_length = len(text.strip())
    return min_length <= text_length <= max_length


def sanitize_text(text: str) -> str:
    """Sanitize text by removing harmful characters"""
    if not text:
        return ""
    
    # Remove null bytes
    text = text.replace('\x00', '')
    
    # Remove control characters except newline and tab
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    return text.strip()


# =============================================================================
# EMAIL and phone VALIDATORS
# =============================================================================

def validate_email(email: str) -> bool:
    """Validate email address"""
    if not email:
        return False
    
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_regex, email))


def validate_phone(phone: str) -> bool:
    """Validate phone number (basic international format)"""
    if not phone:
        return True  # Phone is optional
    
    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)
    
    # Check if valid international format
    pattern = r'^\+?[0-9]{10,15}$'
    return bool(re.match(pattern, cleaned))
# =============================================================================
# AUTHENTICATION VALIDATORS
# =============================================================================

def validate_auth(req: func.HttpRequest) -> Dict[str, Any]:
    """Validate authentication token and return user info"""
    try:
        auth_header = req.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return {
                'success': False,
                'error': 'Missing or invalid Authorization header'
            }
        
        from ..api.auth import verify_token
        token = auth_header.replace('Bearer ', '')
        
        # Verify token directly
        auth_result = verify_token(req)
        
        if not isinstance(auth_result, dict) or not auth_result.get('authenticated', False):
            return {
                'success': False,
                'error': 'Invalid or expired token'
            }
        
        # Get user info from the verify_token response
        user_data = auth_result.get('user', {})
        
        # Create AuthenticatedUser object if needed
        from ..api.auth import AuthenticatedUser
        
        if isinstance(user_data, dict) and 'user_id' in user_data:
            user = AuthenticatedUser(
                user_id=user_data.get('user_id'),
                email=user_data.get('email', ''),
                name=user_data.get('name', ''),
                roles=user_data.get('roles', []),
                subscription_tier=user_data.get('subscription_tier', 'trial'),
                organization_id=user_data.get('organization_id', ''),
                organization_name=user_data.get('organization_name', ''),
                tenant_id=user_data.get('tenant_id', ''),
                token_expires=user_data.get('token_expires', None),
                raw_claims=user_data.get('raw_claims', {})
            )
        else:
            # Fallback - create minimal user
            user = AuthenticatedUser(
                user_id='unknown',
                email='unknown',
                name='Unknown User',
                roles=['Marketing.User'],
                subscription_tier='trial',
                organization_id='unknown',
                organization_name='Unknown Organization',
                tenant_id='unknown',
                token_expires=None,
                raw_claims={}
            )
        
        return {
            'success': True,
            'user': user
        }
        
    except Exception as e:
        logger.error(f"Auth validation error: {e}", exc_info=True)
        return {
            'success': False,
            'error': f'Authentication failed: {str(e)}'
        }


# =============================================================================
# REQUEST VALIDATORS
# =============================================================================

def validate_request_params(req: func.HttpRequest, required_params: list) -> Tuple[bool, Optional[str]]:
    """Validate that all required parameters are present in request"""
    try:
        # Check query parameters
        for param in required_params:
            if param not in req.params:
                return False, f"Missing required parameter: {param}"
        
        return True, None
    except Exception as e:
        return False, f"Parameter validation error: {str(e)}"


def validate_request_body(req: func.HttpRequest, required_fields: list) -> Tuple[bool, Optional[str], Optional[dict]]:
    """Validate request body has all required fields"""
    try:
        body = req.get_json()
        
        for field in required_fields:
            if field not in body:
                return False, f"Missing required field in request body: {field}", None
        
        return True, None, body
    except ValueError:
        return False, "Invalid JSON in request body", None
    except Exception as e:
        return False, f"Request body validation error: {str(e)}", None


# =============================================================================
# BUSINESS LOGIC VALIDATORS
# =============================================================================

def validate_compliance_answers(answers: list) -> Tuple[bool, Optional[str]]:
    """Validate compliance questionnaire answers"""
    if not answers or not isinstance(answers, list):
        return False, "Answers must be a non-empty list"
    
    for answer in answers:
        if not isinstance(answer, dict):
            return False, "Each answer must be an object"
        
        if 'question_id' not in answer:
            return False, "Each answer must have a question_id"
        
        if 'answer' not in answer:
            return False, "Each answer must have an answer field"
        
        # Validate answer value
        valid_answers = ['yes', 'no', 'na', 'uncertain']
        if answer['answer'].lower() not in valid_answers:
            return False, f"Answer must be one of: {', '.join(valid_answers)}"
    
    return True, None


def validate_briefing_form(data: dict) -> Tuple[bool, Optional[str]]:
    """Validate briefing form data"""
    required_fields = ['client_name', 'client_email', 'company_name', 
                      'briefing_notes', 'marketing_type', 'distribution_media']
    
    for field in required_fields:
        if field not in data or not data[field]:
            return False, f"Missing required field: {field}"
    
    # Validate email
    if not validate_email(data['client_email']):
        return False, "Invalid email address"
    
    # Validate marketing_type
    valid_marketing_types = ['investment', 'insurance', 'pension', 'crypto', 'other']
    if data['marketing_type'].lower() not in valid_marketing_types:
        return False, f"Invalid marketing type. Must be one of: {', '.join(valid_marketing_types)}"
    
    # Validate distribution_media
    valid_media = ['email', 'website', 'social_media', 'print', 'tv', 'radio', 'other']
    if data['distribution_media'].lower() not in valid_media:
        return False, f"Invalid distribution media. Must be one of: {', '.join(valid_media)}"
    
    return True, None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_file_extension(filename: str) -> str:
    """Get file extension from filename"""
    if not filename:
        return ''
    
    return os.path.splitext(filename)[1].lower()


def is_image_file(filename: str) -> bool:
    """Check if file is an image - UPDATED TO MATCH EXTRACTOR"""
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
    return get_file_extension(filename) in image_extensions


def is_document_file(filename: str) -> bool:
    """Check if file is a document - UPDATED TO MATCH EXTRACTOR"""
    document_extensions = {'.pdf', '.docx', '.doc', '.txt'}
    return get_file_extension(filename) in document_extensions


def format_file_size(bytes_size: int) -> str:
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


# =============================================================================
# EXTRACTION METHOD DETECTOR
# =============================================================================

def get_extraction_method(filename: str) -> str:
    """
    Get the extraction method that will be used for a file
    Useful for showing users what to expect
    """
    ext = get_file_extension(filename)
    
    if ext == '.txt':
        return 'plain_text'
    elif ext in ['.pdf', '.docx', '.doc']:
        return 'document_intelligence'
    elif ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp']:
        return 'computer_vision_ocr'
    else:
        return 'unsupported'


def get_extraction_method_description(filename: str) -> dict:
    """Get detailed description of extraction method"""
    method = get_extraction_method(filename)
    
    descriptions = {
        'plain_text': {
            'method': 'Plain Text Decoding',
            'description': 'Direct UTF-8/Latin-1 text extraction',
            'speed': 'Instant',
            'accuracy': 100,
            'page_tracking': True
        },
        'document_intelligence': {
            'method': 'Azure Document Intelligence',
            'description': 'Advanced layout analysis with page tracking',
            'speed': '2-5 seconds',
            'accuracy': 95,
            'page_tracking': True
        },
        'computer_vision_ocr': {
            'method': 'Azure Computer Vision OCR',
            'description': 'Optical Character Recognition with page tracking',
            'speed': '3-8 seconds',
            'accuracy': 90,
            'page_tracking': True
        },
        'unsupported': {
            'method': 'Unsupported',
            'description': 'This file type is not supported',
            'speed': 'N/A',
            'accuracy': 0,
            'page_tracking': False
        }
    }
    
    return descriptions.get(method, descriptions['unsupported'])