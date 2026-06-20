"""
AI DECISION AUDIT TRAIL - ENHANCED
===================================
Complete audit trail for regulatory compliance
Captures: What they saw, what AI said, what they decided, WHY they decided
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)


@dataclass
class DecisionContext:
    """What the user SAW when making the decision"""
    risk_score_at_decision: int
    violations_count_at_decision: int
    ai_recommendation: str  # 'approve', 'reject', 'review'
    ai_confidence: float
    violations_shown: List[Dict]
    pii_summary_shown: Dict
    questionnaire_status: str
    questionnaire_answers: List[Dict]
    briefing_data: Dict
    time_since_upload_hours: float
    time_since_scan_hours: float
    previous_decisions_on_doc: List[Dict]  # Was it rejected before?


@dataclass  
class UserJourney:
    """HOW the user reviewed the document"""
    session_duration_seconds: float
    pages_viewed: List[str]  # ['overview', 'violations', 'ai_chat', 'references']
    violations_expanded: List[str]  # Which violation IDs did they click to read?
    ai_chat_messages_count: int
    ai_chat_questions_asked: List[str]  # What did they ask AI?
    questionnaire_time_seconds: float
    total_review_sessions: int  # Did they come back multiple times?
    last_activity_before_decision: str  # What was the last thing they did?


@dataclass
class AIInfluence:
    """How AI influenced the decision"""
    ai_recommendation: str
    ai_confidence_score: float
    ai_risk_assessment: Dict
    ai_key_concerns: List[str]  # Top violations AI flagged
    user_agreed_with_ai: bool
    user_override_reason: str  # If disagreed, why?
    ai_chat_influenced_decision: bool
    ai_suggestions_followed: List[str]
    ai_suggestions_ignored: List[str]


@dataclass
class RegulationConsideration:
    """Which regulations were considered in decision"""
    regulation_id: str
    section_reference: str
    title: str
    jurisdiction: str
    was_violated: bool
    violation_severity: str
    user_assessment: str  # 'valid_violation', 'false_positive', 'acceptable_risk'
    user_reasoning: str
    remediation_applied: bool


@dataclass
class EnhancedAIDecisionAudit:
    """Complete audit trail for AI-influenced decisions"""
    # Identity
    id: str
    document_id: str
    organization_id: str
    
    # Decision details
    decision_type: str  # 'approval', 'rejection', 'escalation'
    decision_outcome: str  # 'approved', 'rejected', 'escalated'
    decision_timestamp: str
    
    # Who decided
    decision_maker_id: str
    decision_maker_email: str
    decision_maker_name: str
    decision_maker_roles: List[str]
    decision_maker_department: str
    
    # What they saw
    decision_context: DecisionContext
    
    # How they reviewed
    user_journey: UserJourney
    
    # AI's role
    ai_influence: AIInfluence
    ai_conversation_ids: List[str]
    
    # Regulations
    regulations_considered: List[RegulationConsideration]
    jurisdiction: str
    
    # User's reasoning
    user_notes: str
    user_conditions: List[str]  # Conditions of approval
    user_required_changes: List[str]  # For rejections
    
    # Override tracking
    is_ai_override: bool
    override_category: str  # 'false_positive', 'business_judgment', 'client_instruction', 'additional_context'
    override_justification: str
    override_approved_by: str  # If override required manager approval
    
    # Risk acknowledgment
    risk_acknowledged: bool
    risk_acknowledgment_text: str
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + 'Z')
    client_ip: str = ''
    user_agent: str = ''
    session_id: str = ''


class EnhancedDecisionTrail:
    """Manages enhanced decision audit trails"""
    
    def __init__(self):
        self.db = None
    
    def _get_db(self):
        if not self.db:
            from .database import get_db
            self.db = get_db()
        return self.db
    
    def record_approval(
        self,
        document_id: str,
        org_id: str,
        user: dict,
        document: dict,
        approval_data: dict,
        request_metadata: dict = None
    ) -> Dict:
        """Record an approval decision with full context"""
        
        # Build decision context - what they SAW
        decision_context = self._build_decision_context(document)
        
        # Build user journey - HOW they reviewed
        user_journey = self._build_user_journey(document_id, user, document)
        
        # Build AI influence - what AI said
        ai_influence = self._build_ai_influence(document, approval_data)
        
        # Build regulation considerations
        regulations = self._build_regulation_considerations(document, approval_data)
        
        # Determine if this is an override
        ai_recommended_reject = document.get('compliance_outcome') in ['non_compliant', 'requires_review']
        is_override = ai_recommended_reject and approval_data.get('decision') == 'approved'
        
        audit_record = {
            'id': f"decision_{uuid.uuid4().hex[:16]}",
            'type': 'decision_audit',
            'document_id': document_id,
            'organization_id': org_id,
            
            # Decision
            'decision_type': 'approval',
            'decision_outcome': 'approved',
            'decision_timestamp': datetime.utcnow().isoformat() + 'Z',
            
            # Who
            'decision_maker': {
                'user_id': user.get('id') or user.get('email'),
                'email': user.get('email'),
                'name': user.get('name'),
                'roles': user.get('roles', []),
                'department': user.get('department', ''),
                'title': approval_data.get('approver_title', ''),
            },
            
            # What they saw
            'decision_context': decision_context,
            
            # How they reviewed
            'user_journey': user_journey,
            
            # AI's role
            'ai_influence': ai_influence,
            'ai_conversation_ids': self._get_conversation_ids(document_id),
            
            # Regulations
            'regulations_considered': regulations,
            'jurisdiction': document.get('jurisdiction'),
            
            # User input
            'user_notes': approval_data.get('approval_notes') or approval_data.get('comments', ''),
            'user_conditions': approval_data.get('conditions', []),
            
            # Override tracking
            'is_ai_override': is_override,
            'override_category': approval_data.get('override_category', '') if is_override else '',
            'override_justification': approval_data.get('override_reasoning', '') if is_override else '',
            
            # Risk acknowledgment
            'risk_acknowledged': approval_data.get('risk_acknowledged', False),
            'risk_acknowledgment_text': f"Approved with risk score {document.get('risk_score', 0)}/100",
            
            # Metadata
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'client_ip': request_metadata.get('ip', '') if request_metadata else '',
            'user_agent': request_metadata.get('user_agent', '') if request_metadata else '',
            
            # For querying
            'partition_key': org_id,
        }
        
        # Save to database
        db = self._get_db()
        container = db.get_container('audit_logs')
        container.upsert_item(audit_record)
        
        logger.info(f"✅ Decision audit recorded: {audit_record['id']} | Override: {is_override}")
        
        # Link AI conversations
        if audit_record['ai_conversation_ids']:
            self._link_conversations(audit_record['ai_conversation_ids'], audit_record['id'], document_id)
        
        return audit_record
    
    def record_rejection(
        self,
        document_id: str,
        org_id: str,
        user: dict,
        document: dict,
        rejection_data: dict,
        request_metadata: dict = None
    ) -> Dict:
        """Record a rejection decision with full context"""
        
        decision_context = self._build_decision_context(document)
        user_journey = self._build_user_journey(document_id, user, document)
        ai_influence = self._build_ai_influence(document, rejection_data)
        regulations = self._build_regulation_considerations(document, rejection_data)
        
        # Determine if this agrees with AI
        ai_recommended_reject = document.get('compliance_outcome') in ['non_compliant', 'requires_review']
        agrees_with_ai = ai_recommended_reject
        
        audit_record = {
            'id': f"decision_{uuid.uuid4().hex[:16]}",
            'type': 'decision_audit',
            'document_id': document_id,
            'organization_id': org_id,
            
            # Decision
            'decision_type': 'rejection',
            'decision_outcome': 'rejected',
            'decision_timestamp': datetime.utcnow().isoformat() + 'Z',
            
            # Who
            'decision_maker': {
                'user_id': user.get('id') or user.get('email'),
                'email': user.get('email'),
                'name': user.get('name'),
                'roles': user.get('roles', []),
                'department': user.get('department', ''),
            },
            
            # Context
            'decision_context': decision_context,
            'user_journey': user_journey,
            'ai_influence': ai_influence,
            'ai_conversation_ids': self._get_conversation_ids(document_id),
            'regulations_considered': regulations,
            'jurisdiction': document.get('jurisdiction'),
            
            # Rejection specifics
            'rejection_reason': rejection_data.get('rejection_reason') or rejection_data.get('reason', ''),
            'rejection_severity': rejection_data.get('severity', 'medium'),
            'required_changes': rejection_data.get('required_changes', []),
            'cited_violations': rejection_data.get('cited_violations', []),
            
            # AI agreement
            'agrees_with_ai': agrees_with_ai,
            'is_ai_override': not agrees_with_ai,  # Rejecting when AI said approve
            
            # Metadata
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'partition_key': org_id,
        }
        
        db = self._get_db()
        container = db.get_container('audit_logs')
        container.upsert_item(audit_record)
        
        logger.info(f"✅ Rejection audit recorded: {audit_record['id']}")
        
        return audit_record
    
    def _build_decision_context(self, document: dict) -> dict:
        """Build snapshot of what user saw"""
        return {
            'risk_score': document.get('risk_score', 0),
            'violations_count': document.get('violations_count', 0),
            'ai_recommendation': self._get_ai_recommendation(document),
            'ai_confidence': document.get('scan_stats', {}).get('confidence', 0.85),
            'compliance_outcome': document.get('compliance_outcome'),
            'pii_summary': document.get('pii_summary', {}),
            'questionnaire_status': document.get('questionnaire_status'),
            'questionnaire_answers_count': len(document.get('answers', [])),
            'briefing_completed': bool(document.get('briefing')),
            'document_status': document.get('status'),
            'violations_by_severity': self._count_violations_by_severity(document.get('violations', [])),
        }
    
    def _build_user_journey(self, document_id: str, user: dict, document: dict) -> dict:
        """Build record of how user reviewed"""
        # This would ideally come from frontend tracking
        # For now, estimate based on document data
        
        created = document.get('created_at', '')
        scanned = document.get('scanned_at', '')
        now = datetime.utcnow()
        
        time_since_scan = 0
        if scanned:
            try:
                scan_time = datetime.fromisoformat(scanned.replace('Z', '+00:00'))
                time_since_scan = (now - scan_time).total_seconds()
            except:
                pass
        
        return {
            'time_since_scan_seconds': time_since_scan,
            'ai_chat_messages_count': len(document.get('ai_conversations', [])),
            'questionnaire_completed': document.get('questionnaire_status') == 'submitted',
            'briefing_completed': bool(document.get('briefing')),
            'scan_count': document.get('scan_count', 1),
            'assignment_history': document.get('assignment_history', []),
        }
    
    def _build_ai_influence(self, document: dict, decision_data: dict) -> dict:
        """Build record of AI's influence"""
        ai_recommendation = self._get_ai_recommendation(document)
        user_decision = decision_data.get('decision', 'approved')
        
        return {
            'ai_recommendation': ai_recommendation,
            'ai_risk_score': document.get('risk_score', 0),
            'ai_violations_found': document.get('violations_count', 0),
            'ai_key_concerns': [v.get('category') for v in document.get('violations', [])[:5]],
            'user_agreed_with_ai': (
                (ai_recommendation == 'approve' and user_decision == 'approved') or
                (ai_recommendation == 'reject' and user_decision == 'rejected')
            ),
            'ai_suggestions_in_response': document.get('pii_analysis', {}).get('smart_suggestions', []),
        }
    
    def _build_regulation_considerations(self, document: dict, decision_data: dict) -> List[dict]:
        """Build list of regulations considered"""
        regulations = []
        
        for violation in document.get('violations', []):
            regulations.append({
                'regulation_id': violation.get('regulation_citation', {}).get('regulation_id', ''),
                'section_reference': violation.get('regulation', ''),
                'category': violation.get('category'),
                'severity': violation.get('severity'),
                'was_flagged': True,
                'matched_text_preview': violation.get('matched_text', '')[:100],
            })
        
        return regulations
    
    def _get_ai_recommendation(self, document: dict) -> str:
        """Determine what AI recommended"""
        outcome = document.get('compliance_outcome', '')
        risk_score = document.get('risk_score', 0)
        
        if outcome == 'compliant' or risk_score < 30:
            return 'approve'
        elif outcome == 'non_compliant' or risk_score >= 70:
            return 'reject'
        else:
            return 'review'
    
    def _count_violations_by_severity(self, violations: list) -> dict:
        """Count violations by severity"""
        counts = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        for v in violations:
            severity = v.get('severity', 'MEDIUM').upper()
            if severity in counts:
                counts[severity] += 1
        return counts
    
    def _get_conversation_ids(self, document_id: str) -> List[str]:
        """Get AI conversation IDs for document"""
        try:
            db = self._get_db()
            container = db.get_container('ai_conversations')
            
            query = "SELECT c.id FROM c WHERE c.document_id = @doc_id"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@doc_id", "value": document_id}],
                enable_cross_partition_query=True
            ))
            
            return [item['id'] for item in items]
        except:
            return []
    
    def _link_conversations(self, conversation_ids: list, decision_id: str, document_id: str):
        """Link conversations to decision"""
        try:
            db = self._get_db()
            container = db.get_container('ai_conversations')
            
            for conv_id in conversation_ids:
                try:
                    conv = container.read_item(conv_id, partition_key=document_id)
                    if 'linked_decisions' not in conv:
                        conv['linked_decisions'] = []
                    conv['linked_decisions'].append({
                        'decision_id': decision_id,
                        'linked_at': datetime.utcnow().isoformat() + 'Z'
                    })
                    container.upsert_item(conv)
                except:
                    pass
        except Exception as e:
            logger.warning(f"Failed to link conversations: {e}")
    
    # === QUERY METHODS ===
    
    def get_document_decisions(self, document_id: str, org_id: str) -> List[Dict]:
        """Get all decisions for a document"""
        db = self._get_db()
        container = db.get_container('audit_logs')
        
        query = """
        SELECT * FROM c 
        WHERE c.document_id = @doc_id 
        AND c.type = 'decision_audit'
        ORDER BY c.decision_timestamp DESC
        """
        
        return list(container.query_items(
            query=query,
            parameters=[{"name": "@doc_id", "value": document_id}],
            enable_cross_partition_query=True
        ))
    
    def get_user_decisions(self, user_email: str, org_id: str, days: int = 30) -> List[Dict]:
        """Get all decisions by a user"""
        db = self._get_db()
        container = db.get_container('audit_logs')
        
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        
        query = """
        SELECT * FROM c 
        WHERE c.decision_maker.email = @email
        AND c.organization_id = @org_id
        AND c.type = 'decision_audit'
        AND c.decision_timestamp >= @cutoff
        ORDER BY c.decision_timestamp DESC
        """
        
        return list(container.query_items(
            query=query,
            parameters=[
                {"name": "@email", "value": user_email},
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff}
            ],
            partition_key=org_id
        ))
    
    def get_ai_overrides(self, org_id: str, days: int = 30) -> List[Dict]:
        """Get all AI overrides - critical for audit!"""
        db = self._get_db()
        container = db.get_container('audit_logs')
        
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        
        query = """
        SELECT * FROM c 
        WHERE c.organization_id = @org_id
        AND c.type = 'decision_audit'
        AND c.is_ai_override = true
        AND c.decision_timestamp >= @cutoff
        ORDER BY c.decision_timestamp DESC
        """
        
        return list(container.query_items(
            query=query,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@cutoff", "value": cutoff}
            ],
            partition_key=org_id
        ))


# Global instance
from datetime import timedelta
decision_trail = EnhancedDecisionTrail()