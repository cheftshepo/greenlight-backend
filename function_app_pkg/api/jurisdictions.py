"""Jurisdictions API - Dynamic from Database with Smart Fallback"""
import azure.functions as func
import logging
from function_app_pkg.shared.http_utils import json_response

logger = logging.getLogger(__name__)

# Full jurisdiction definitions matching your compliance_rules_*.py files
JURISDICTION_DEFINITIONS = {
    'UK': {
        'code': 'UK',
        'name': 'United Kingdom',
        'regulator': 'Financial Conduct Authority (FCA)',
        'flag': '🇬🇧',
        'description': 'FCA regulations including COBS, PRIN, and Consumer Duty',
        'language': 'en'
    },
    'EU': {
        'code': 'EU',
        'name': 'European Union',
        'regulator': 'ESMA / National Competent Authorities',
        'flag': '🇪🇺',
        'description': 'MiFID II, PRIIPs, SFDR, and EU-wide regulations',
        'language': 'en'
    },
    'US': {
        'code': 'US',
        'name': 'United States',
        'regulator': 'SEC / FINRA / CFTC',
        'flag': '🇺🇸',
        'description': 'SEC regulations, FINRA rules, and federal securities laws',
        'language': 'en'
    },
    'DE': {
        'code': 'DE',
        'name': 'Germany',
        'regulator': 'BaFin (Federal Financial Supervisory Authority)',
        'flag': '🇩🇪',
        'description': 'German WpHG, KWG, and BaFin guidelines',
        'language': 'de'
    },
    'FR': {
        'code': 'FR',
        'name': 'France',
        'regulator': 'AMF (Autorité des marchés financiers)',
        'flag': '🇫🇷',
        'description': 'French AMF regulations and Code monétaire et financier',
        'language': 'fr'
    },
    'AU': {
        'code': 'AU',
        'name': 'Australia',
        'regulator': 'ASIC (Australian Securities and Investments Commission)',
        'flag': '🇦🇺',
        'description': 'ASIC regulations and Corporations Act 2001',
        'language': 'en'
    },
    'ZA': {
        'code': 'ZA',
        'name': 'South Africa',
        'regulator': 'FSCA (Financial Sector Conduct Authority)',
        'flag': '🇿🇦',
        'description': 'FSCA regulations and FAIS Act',
        'language': 'en'
    },
    'GLOBAL': {
        'code': 'GLOBAL',
        'name': 'Global Standards',
        'regulator': 'IOSCO / Cross-jurisdictional',
        'flag': '🌍',
        'description': 'International standards and cross-border compliance',
        'language': 'en'
    }
}


def handle(req: func.HttpRequest, user: dict = None) -> func.HttpResponse:
    """Get available jurisdictions - DB first, then fallback to definitions"""
    try:
        jurisdiction_code = req.params.get('jurisdiction', '').upper()
        
        jurisdictions = []
        
        # Try database first
        try:
            from function_app_pkg.core.database import list_jurisdictions
            db_jurisdictions = list_jurisdictions()
            if db_jurisdictions and len(db_jurisdictions) > 0:
                jurisdictions = db_jurisdictions
                logger.info(f"✅ Loaded {len(jurisdictions)} jurisdictions from database")
        except Exception as e:
            logger.warning(f"⚠️ Could not load from database: {e}")
        
        # If database empty, use full definitions
        if not jurisdictions:
            logger.info("📋 Using jurisdiction definitions (database empty)")
            jurisdictions = list(JURISDICTION_DEFINITIONS.values())
        
        # Single jurisdiction lookup
        if jurisdiction_code:
            for j in jurisdictions:
                if j.get('code', '').upper() == jurisdiction_code:
                    return json_response(200, data=j)
            return json_response(404, error=f"Jurisdiction not found: {jurisdiction_code}")
        
        # Return all - ensure consistent format
        formatted = []
        for j in jurisdictions:
            formatted.append({
                'code': j.get('code', ''),
                'name': j.get('name', ''),
                'regulator': j.get('regulator', ''),
                'flag': j.get('flag', '🏳️'),
                'description': j.get('description', ''),
                'language': j.get('language', 'en')
            })
        
        # Sort by code
        formatted.sort(key=lambda x: x['code'])
        
        return json_response(200, data={
            'jurisdictions': formatted,
            'count': len(formatted),
            'default': 'UK'
        })
        
    except Exception as e:
        logger.error(f"❌ Jurisdictions error: {e}")
        import traceback
        traceback.print_exc()
        return json_response(500, error=str(e))