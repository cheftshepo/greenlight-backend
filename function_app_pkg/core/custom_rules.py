"""
CUSTOM RULES ENGINE
===================
Client-defined compliance rules for brand guidelines & legal preferences

REVENUE: +$400/month enterprise feature

File: function_app_pkg/core/custom_rules.py
"""

import logging
import uuid
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class CustomRule:
    """Custom compliance rule definition"""
    id: str
    organization_id: str
    name: str
    description: str
    rule_type: str  # 'keyword', 'pattern', 'phrase', 'ai_check'
    pattern: str  # Regex or keyword to match
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    category: str  # 'brand_guidelines', 'legal_preference', 'custom'
    remediation: str
    enabled: bool
    created_by: str
    created_at: str
    updated_at: str
    
    # Optional AI reasoning
    ai_reasoning_prompt: str = ""
    
    # Test cases
    test_cases: List[Dict] = None
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        d['type'] = 'custom_rule'
        d['partition_key'] = self.organization_id
        return d


class CustomRulesEngine:
    """
    Execute custom compliance rules defined by clients
    
    Features:
    - Keyword/phrase matching
    - Regex pattern matching
    - AI-powered contextual checks
    - Brand guideline enforcement
    - Legal department preferences
    """
    
    def __init__(self):
        logger.info("✅ Custom Rules Engine initialized")
    
    def create_rule(
        self,
        organization_id: str,
        name: str,
        description: str,
        rule_type: str,
        pattern: str,
        severity: str,
        category: str,
        remediation: str,
        created_by: str,
        ai_reasoning_prompt: str = "",
        test_cases: List[Dict] = None
    ) -> CustomRule:
        """
        Create new custom rule
        """
        
        # Validate rule type
        valid_types = ['keyword', 'pattern', 'phrase', 'ai_check']
        if rule_type not in valid_types:
            raise ValueError(f"Invalid rule_type. Must be one of: {valid_types}")
        
        # Validate severity
        valid_severities = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
        if severity not in valid_severities:
            raise ValueError(f"Invalid severity. Must be one of: {valid_severities}")
        
        # Validate pattern for regex rules
        if rule_type == 'pattern':
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")
        
        rule = CustomRule(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            name=name,
            description=description,
            rule_type=rule_type,
            pattern=pattern,
            severity=severity,
            category=category,
            remediation=remediation,
            enabled=True,
            created_by=created_by,
            created_at=datetime.utcnow().isoformat() + 'Z',
            updated_at=datetime.utcnow().isoformat() + 'Z',
            ai_reasoning_prompt=ai_reasoning_prompt,
            test_cases=test_cases or []
        )
        
        # Save to database
        self._save_rule(rule)
        
        logger.info(f"✅ Custom rule created: {rule.name} ({rule.id})")
        
        return rule
    
    def _save_rule(self, rule: CustomRule):
        """Save rule to database"""
        try:
            from function_app_pkg.core.database import get_db
            
            db = get_db()
            container = db.get_container('custom_rules')
            
            container.upsert_item(rule.to_dict())
            
        except Exception as e:
            logger.error(f"❌ Failed to save rule: {e}")
            raise
    
    def get_rules_for_org(self, organization_id: str, enabled_only: bool = True) -> List[CustomRule]:
        """Get all custom rules for organization"""
        try:
            from function_app_pkg.core.database import get_db
            
            db = get_db()
            container = db.get_container('custom_rules')
            
            query = "SELECT * FROM c WHERE c.organization_id = @org_id AND c.type = 'custom_rule'"
            if enabled_only:
                query += " AND c.enabled = true"
            
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@org_id", "value": organization_id}],
                partition_key=organization_id
            ))
            
            rules = []
            for item in items:
                try:
                    # Remove Cosmos DB system fields
                    item_copy = {k: v for k, v in item.items() if not k.startswith('_') and k not in ['type', 'partition_key']}
                    rules.append(CustomRule(**item_copy))
                except Exception as e:
                    logger.error(f"Failed to parse rule: {e}")
            
            logger.info(f"📋 Loaded {len(rules)} custom rules for org {organization_id}")
            
            return rules
            
        except Exception as e:
            logger.error(f"❌ Failed to load rules: {e}")
            return []
    
    def execute_rules(
        self,
        text: str,
        organization_id: str,
        document_id: str
    ) -> List[Dict]:
        """
        Execute all custom rules against document text
        
        Returns list of violations
        """
        
        rules = self.get_rules_for_org(organization_id, enabled_only=True)
        
        if not rules:
            logger.info(f"No custom rules for org {organization_id}")
            return []
        
        logger.info(f"🔍 Executing {len(rules)} custom rules...")
        
        violations = []
        
        for rule in rules:
            try:
                rule_violations = self._execute_single_rule(rule, text, document_id)
                violations.extend(rule_violations)
                
                if rule_violations:
                    logger.info(f"✅ Rule '{rule.name}': {len(rule_violations)} violations")
                
            except Exception as e:
                logger.error(f"❌ Rule '{rule.name}' failed: {e}")
        
        logger.info(f"✅ Custom rules complete: {len(violations)} violations")
        
        return violations
    
    def _execute_single_rule(
        self,
        rule: CustomRule,
        text: str,
        document_id: str
    ) -> List[Dict]:
        """Execute one rule"""
        
        if rule.rule_type == 'keyword':
            return self._check_keyword(rule, text, document_id)
        
        elif rule.rule_type == 'pattern':
            return self._check_pattern(rule, text, document_id)
        
        elif rule.rule_type == 'phrase':
            return self._check_phrase(rule, text, document_id)
        
        elif rule.rule_type == 'ai_check':
            return self._check_ai(rule, text, document_id)
        
        else:
            logger.warning(f"Unknown rule type: {rule.rule_type}")
            return []
    
    def _check_keyword(self, rule: CustomRule, text: str, document_id: str) -> List[Dict]:
        """Check for keyword presence (case-insensitive)"""
        violations = []
        
        keyword = rule.pattern.lower()
        text_lower = text.lower()
        
        # Find all occurrences
        pos = 0
        while True:
            pos = text_lower.find(keyword, pos)
            if pos == -1:
                break
            
            # Get context
            start = max(0, pos - 50)
            end = min(len(text), pos + len(keyword) + 50)
            context = text[start:end]
            
            violations.append({
                'violation_id': f"custom_{rule.id}_{document_id}_{pos}",
                'rule_id': rule.id,
                'rule_name': rule.name,
                'rule_type': 'custom',
                'category': rule.category,
                'severity': rule.severity,
                'matched_text': text[pos:pos+len(keyword)],
                'context_snippet': context,
                'ai_reasoning': f"Custom rule violation: {rule.description}",
                'remediation': rule.remediation,
                'source': 'custom_rule',
                'confidence': 1.0,
                'start_pos': pos,
                'end_pos': pos + len(keyword)
            })
            
            pos += 1
        
        return violations
    
    def _check_pattern(self, rule: CustomRule, text: str, document_id: str) -> List[Dict]:
        """Check for regex pattern match"""
        violations = []
        
        try:
            pattern = re.compile(rule.pattern, re.IGNORECASE)
            matches = pattern.finditer(text)
            
            for match in matches:
                pos = match.start()
                matched_text = match.group(0)
                
                # Get context
                start = max(0, pos - 50)
                end = min(len(text), pos + len(matched_text) + 50)
                context = text[start:end]
                
                violations.append({
                    'violation_id': f"custom_{rule.id}_{document_id}_{pos}",
                    'rule_id': rule.id,
                    'rule_name': rule.name,
                    'rule_type': 'custom',
                    'category': rule.category,
                    'severity': rule.severity,
                    'matched_text': matched_text,
                    'context_snippet': context,
                    'ai_reasoning': f"Custom rule violation: {rule.description}",
                    'remediation': rule.remediation,
                    'source': 'custom_rule',
                    'confidence': 0.95,
                    'start_pos': pos,
                    'end_pos': match.end()
                })
            
        except Exception as e:
            logger.error(f"Pattern match failed: {e}")
        
        return violations
    
    def _check_phrase(self, rule: CustomRule, text: str, document_id: str) -> List[Dict]:
        """Check for exact phrase (case-insensitive)"""
        return self._check_keyword(rule, text, document_id)
    
    def _check_ai(self, rule: CustomRule, text: str, document_id: str) -> List[Dict]:
        """AI-powered contextual check"""
        
        if not rule.ai_reasoning_prompt:
            logger.warning(f"No AI prompt for rule: {rule.name}")
            return []
        
        try:
            import os
            from openai import AzureOpenAI
            
            client = AzureOpenAI(
                api_key=os.getenv('AZURE_OPENAI_API_KEY'),
                api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
                azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT')
            )
            
            # Truncate text if too long
            if len(text) > 8000:
                text = text[:8000]
            
            prompt = f"""{rule.ai_reasoning_prompt}

DOCUMENT TEXT:
\"\"\"
{text}
\"\"\"

Does this document violate the rule? Respond with JSON:
{{
    "violates": true/false,
    "matched_text": "specific text that violates",
    "reasoning": "why it violates",
    "confidence": 0.0-1.0
}}
"""
            
            response = client.chat.completions.create(
                model=os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o'),
                messages=[
                    {"role": "system", "content": "Compliance rule checker. Be precise."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            if result.get('violates'):
                return [{
                    'violation_id': f"custom_ai_{rule.id}_{document_id}",
                    'rule_id': rule.id,
                    'rule_name': rule.name,
                    'rule_type': 'custom_ai',
                    'category': rule.category,
                    'severity': rule.severity,
                    'matched_text': result.get('matched_text', '')[:200],
                    'context_snippet': text[:400],
                    'ai_reasoning': result.get('reasoning', ''),
                    'remediation': rule.remediation,
                    'source': 'custom_rule_ai',
                    'confidence': result.get('confidence', 0.85),
                    'start_pos': 0,
                    'end_pos': 0
                }]
            
        except Exception as e:
            logger.error(f"❌ AI rule check failed: {e}")
        
        return []
    
    def test_rule(self, rule: CustomRule, test_text: str) -> Dict:
        """Test rule against sample text"""
        violations = self._execute_single_rule(rule, test_text, "test")
        
        return {
            'rule_id': rule.id,
            'rule_name': rule.name,
            'test_text': test_text[:500],
            'violations_found': len(violations),
            'violations': violations,
            'passed': len(violations) > 0  # Assuming test text should trigger violation
        }


# Global instance
custom_rules_engine = CustomRulesEngine()


# =============================================================================
# API ENDPOINTS
# =============================================================================

def handle_create_rule(req, user) -> dict:
    """
    POST /custom-rules
    Create custom compliance rule (Enterprise feature)
    """
    try:
        from function_app_pkg.shared.http_utils import json_response
        
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        body = req.get_json()
        
        rule = custom_rules_engine.create_rule(
            organization_id=org_id,
            name=body['name'],
            description=body['description'],
            rule_type=body['rule_type'],
            pattern=body['pattern'],
            severity=body['severity'],
            category=body.get('category', 'custom'),
            remediation=body['remediation'],
            created_by=user.get('email', 'unknown'),
            ai_reasoning_prompt=body.get('ai_reasoning_prompt', ''),
            test_cases=body.get('test_cases', [])
        )
        
        return json_response(201, data=rule.to_dict())
        
    except KeyError as e:
        return json_response(400, error=f"Missing required field: {e}")
    except ValueError as e:
        return json_response(400, error=str(e))
    except Exception as e:
        logger.error(f"❌ Create rule error: {e}")
        return json_response(500, error=str(e))


def handle_list_rules(req, user) -> dict:
    """
    GET /custom-rules
    List all custom rules for organization
    """
    try:
        from function_app_pkg.shared.http_utils import json_response
        
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        if not org_id:
            return json_response(400, error="Organization ID required")
        
        rules = custom_rules_engine.get_rules_for_org(org_id, enabled_only=False)
        
        return json_response(200, data={
            'rules': [r.to_dict() for r in rules],
            'total': len(rules)
        })
        
    except Exception as e:
        logger.error(f"❌ List rules error: {e}")
        return json_response(500, error=str(e))


def handle_test_rule(req, user) -> dict:
    """
    POST /custom-rules/{ruleId}/test
    Test custom rule against sample text
    """
    try:
        from function_app_pkg.shared.http_utils import json_response
        
        rule_id = req.route_params.get('ruleId')
        body = req.get_json()
        test_text = body.get('test_text', '')
        
        if not test_text:
            return json_response(400, error="test_text required")
        
        # Get rule
        org_id = user.organization_id if hasattr(user, 'organization_id') else user.get('organization_id')
        rules = custom_rules_engine.get_rules_for_org(org_id, enabled_only=False)
        
        rule = next((r for r in rules if r.id == rule_id), None)
        if not rule:
            return json_response(404, error="Rule not found")
        
        # Test it
        result = custom_rules_engine.test_rule(rule, test_text)
        
        return json_response(200, data=result)
        
    except Exception as e:
        logger.error(f"❌ Test rule error: {e}")
        return json_response(500, error=str(e))