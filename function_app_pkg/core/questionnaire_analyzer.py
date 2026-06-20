"""
QUESTIONNAIRE ANALYZER
Understands user answers to provide smarter AI responses
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class QuestionnaireAnalyzer:
    """Analyzes questionnaire answers to understand user intent"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def analyze_questionnaire(
        self, 
        answers: List[Dict], 
        questions: List[Dict],
        ai_violations: List[Dict]
    ) -> Dict:
        """
        Analyze questionnaire to understand:
        - Knowledge gaps (uncertain answers)
        - Risk tolerance (ignored violations)
        - Compliance maturity
        - Personalized guidance needs
        """
        
        # Map questions by ID
        question_map = {q['question_id']: q for q in questions}
        
        analysis = {
            'knowledge_gaps': [],
            'risk_profile': 'conservative',
            'compliance_maturity': 'beginner',
            'focus_areas': [],
            'confidence_level': 'high',
            'suggested_approach': 'detailed',
            'answer_summary': {
                'yes': 0,
                'no': 0,
                'uncertain': 0
            }
        }
        
        # Analyze answers
        for answer in answers:
            q_id = answer.get('question_id')
            question = question_map.get(q_id, {})
            answer_value = answer.get('answer', '').lower()
            
            # Count answer types
            if answer_value in analysis['answer_summary']:
                analysis['answer_summary'][answer_value] += 1
            
            # Identify knowledge gaps (uncertain answers)
            if answer_value == 'uncertain':
                analysis['knowledge_gaps'].append({
                    'question_id': q_id,
                    'question': question.get('verification_question', question.get('question', '')),
                    'severity': question.get('severity', 'medium'),
                    'category': question.get('category', 'general'),
                    'user_notes': answer.get('notes', '')
                })
            
            # Track "no" answers for risk assessment
            if answer_value == 'no':
                analysis['risk_profile'] = 'risky'
        
        # Calculate compliance maturity
        total_answers = len(answers)
        if total_answers > 0:
            yes_percentage = (analysis['answer_summary']['yes'] / total_answers) * 100
            
            if yes_percentage >= 90:
                analysis['compliance_maturity'] = 'expert'
                analysis['confidence_level'] = 'very_high'
                analysis['suggested_approach'] = 'concise'
            elif yes_percentage >= 70:
                analysis['compliance_maturity'] = 'intermediate'
                analysis['confidence_level'] = 'high'
                analysis['suggested_approach'] = 'balanced'
            else:
                analysis['compliance_maturity'] = 'beginner'
                analysis['confidence_level'] = 'medium'
                analysis['suggested_approach'] = 'detailed'
        
        # Compare with AI violations
        if ai_violations:
            critical_violations = [v for v in ai_violations if v.get('severity') == 'CRITICAL']
            if critical_violations:
                analysis['focus_areas'].append('critical_violations')
                analysis['risk_profile'] = 'very_risky'
        
        # Determine focus areas
        if analysis['knowledge_gaps']:
            critical_gaps = [g for g in analysis['knowledge_gaps'] if g['severity'] == 'critical']
            if critical_gaps:
                analysis['focus_areas'].append('critical_knowledge_gaps')
        
        # Calculate overall guidance style
        if 'critical_violations' in analysis['focus_areas']:
            analysis['suggested_approach'] = 'urgent_detailed'
        elif 'critical_knowledge_gaps' in analysis['focus_areas']:
            analysis['suggested_approach'] = 'educational_detailed'
        
        return analysis
    
    def create_ai_context(
        self,
        analysis: Dict,
        questionnaire_answers: List[Dict],
        questions: List[Dict]
    ) -> str:
        """
        Create context string for AI to understand questionnaire results
        """
        
        context_parts = []
        
        # Add summary
        context_parts.append(f"""
📊 QUESTIONNAIRE ANALYSIS
========================

User Profile:
- Compliance Maturity: {analysis['compliance_maturity'].upper()}
- Risk Profile: {analysis['risk_profile'].upper()}
- Suggested Approach: {analysis['suggested_approach'].replace('_', ' ').title()}

Answer Summary:
- ✅ YES: {analysis['answer_summary']['yes']} answers
- ❌ NO: {analysis['answer_summary']['no']} answers
- ❓ UNCERTAIN: {analysis['answer_summary']['uncertain']} answers
""")
        
        # Add knowledge gaps
        if analysis['knowledge_gaps']:
            context_parts.append(f"""
📝 KNOWLEDGE GAPS ({len(analysis['knowledge_gaps'])} areas)
=========================================================""")
            
            for i, gap in enumerate(analysis['knowledge_gaps'][:3], 1):
                context_parts.append(f"""
GAP {i}: {gap['severity'].upper()} - {gap['category'].replace('_', ' ').title()}
Question: "{gap['question']}"
Notes: {gap.get('user_notes', 'No notes provided')}
""")
            
            if len(analysis['knowledge_gaps']) > 3:
                context_parts.append(f"\n... and {len(analysis['knowledge_gaps']) - 3} more areas")
        
        # Add focus areas
        if analysis['focus_areas']:
            context_parts.append(f"""
🎯 RECOMMENDED FOCUS AREAS
===========================""")
            
            for area in analysis['focus_areas']:
                area_name = area.replace('_', ' ').title()
                if area == 'critical_violations':
                    context_parts.append(f"• ⚠️ Critical Violations: User has critical AI violations that need immediate attention")
                elif area == 'critical_knowledge_gaps':
                    context_parts.append(f"• 📚 Knowledge Gaps: User is uncertain about critical compliance requirements")
                else:
                    context_parts.append(f"• {area_name}")
        
        # Add guidance for AI
        context_parts.append(f"""
🤖 GUIDANCE FOR AI RESPONSE
===========================
Based on the user's questionnaire, tailor your response:

APPROACH: {analysis['suggested_approach'].replace('_', ' ').title()}

RECOMMENDATIONS:
1. {"Focus on urgent fixes first" if analysis['suggested_approach'] == 'urgent_detailed' else "Provide comprehensive guidance"}
2. {"Explain basic concepts clearly" if analysis['compliance_maturity'] == 'beginner' else "Focus on advanced topics"}
3. {"Be patient and educational" if 'knowledge_gaps' in analysis['focus_areas'] else "Be direct and concise"}
4. {"Emphasize risk consequences" if analysis['risk_profile'] == 'risky' else "Focus on best practices"}
""")
        
        return "\n".join(context_parts)


# Global instance
questionnaire_analyzer = QuestionnaireAnalyzer()