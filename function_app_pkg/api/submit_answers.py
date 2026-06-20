"""
Submit Answers API - AI-POWERED RISK ASSESSMENT (HARDENED GPT)
==============================================================
Fixes:
  - response_format removed (breaks on many Azure deployments)
  - JSON parsed from text with robust fallback
  - Model deployment name from env (AZURE_OPENAI_DEPLOYMENT)
  - Detailed logging so you know exactly why GPT is/isn't working
  - Retry once on JSON parse error
"""

import azure.functions as func
import logging
import os
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from function_app_pkg.core.database import get_document, update_document, get_db, log_action
from function_app_pkg.shared.http_utils import json_response
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ============================================================================
# GPT CLIENT — hardened, with startup diagnostics
# ============================================================================

def get_ai_client():
    """
    Get Azure OpenAI client.
    Logs exactly what env vars are present so you can debug missing config.
    """
    try:
        from openai import AzureOpenAI
    except ImportError:
        logger.error("❌ openai package not installed")
        return None

    api_key  = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    # Detailed diagnostics — safe (only logs presence, not actual values)
    logger.info(f"🔑 AZURE_OPENAI_API_KEY  : {'SET (' + str(len(api_key)) + ' chars)' if api_key else 'NOT SET ❌'}")
    logger.info(f"🌐 AZURE_OPENAI_ENDPOINT : {'SET → ' + endpoint[:40] + '...' if endpoint else 'NOT SET ❌'}")
    logger.info(f"🤖 AZURE_OPENAI_DEPLOYMENT: {os.getenv('AZURE_OPENAI_DEPLOYMENT', 'not set — will use AZURE_OPENAI_MODEL fallback')}")

    if not api_key or not endpoint:
        logger.warning("⚠️  OpenAI credentials missing — falling back to basic scoring")
        return None

    # Normalize endpoint (strip trailing slash)
    endpoint = endpoint.rstrip('/')

    try:
        client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-15-preview",   # wider compatibility than 2024-02-01
            azure_endpoint=endpoint,
        )
        logger.info("✅ AzureOpenAI client initialized")
        return client
    except Exception as e:
        logger.error(f"❌ Failed to initialize AzureOpenAI client: {e}")
        return None


def _extract_json_from_text(text: str) -> Optional[Dict]:
    """
    Robustly extract JSON from GPT output.
    Handles:
      - Pure JSON response
      - JSON wrapped in ```json ... ``` fences
      - JSON embedded in prose
    """
    if not text:
        return None

    # Try 1: direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try 2: strip markdown fences
    fenced = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass

    # Try 3: find first { … } block
    brace_match = re.search(r'\{[\s\S]+\}', text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except Exception:
            pass

    logger.warning(f"⚠️  Could not extract JSON from GPT response (first 200 chars): {text[:200]}")
    return None


async def analyze_answers_with_gpt(
    document_content: Dict,
    answers: List[Dict],
    questions: List[Dict],
    ai_violations: List[Dict],
) -> Dict[str, Any]:
    """
    Use GPT to analyze questionnaire answers and return structured risk assessment.
    Falls back to basic scoring if GPT unavailable or response unparseable.
    """
    client = get_ai_client()
    if not client:
        return _get_fallback_analysis(answers, questions, reason="credentials_missing")

    # Deployment name: prefer AZURE_OPENAI_DEPLOYMENT, then AZURE_OPENAI_MODEL, then 'gpt-4'
    deployment = (
        os.getenv("AZURE_OPENAI_DEPLOYMENT") or
        os.getenv("AZURE_OPENAI_MODEL") or
        "gpt-4"
    )
    logger.info(f"🤖 Using deployment: {deployment}")

    doc_summary = {
        "filename":          document_content.get("filename", "unknown"),
        "document_type":     document_content.get("document_type", "unknown"),
        "jurisdiction":      document_content.get("jurisdiction", "ZA"),
        "ai_risk_score":     document_content.get("risk_score", 0),
        "ai_violation_count": len(ai_violations),
        "ai_violations_summary": [v.get("category", "") for v in ai_violations[:5]],
    }

    qa_pairs = []
    for ans in answers:
        q = next((q for q in questions if q.get("question_id") == ans.get("question_id")), {})
        qa_pairs.append({
            "question":    q.get("verification_question", q.get("question", "")),
            "answer":      ans.get("answer", ""),
            "user_notes":  ans.get("notes", ""),
            "category":    q.get("category", ""),
            "severity":    q.get("severity", "medium"),
        })

    # NOTE: No response_format param — parse JSON from text instead.
    # This works on ALL Azure OpenAI deployments regardless of API version.
    system_prompt = (
        "You are a compliance risk assessment expert for DLA Piper's compliance platform. "
        "Analyze questionnaire answers and return ONLY a valid JSON object — no prose, no markdown fences. "
        "The JSON must have these exact keys:\n"
        "  contextual_risk_score (integer 0-100)\n"
        "  risk_adjustment_reasoning (string)\n"
        "  critical_risk_factors (array of strings)\n"
        "  compliance_confidence (float 0.0-1.0)\n"
        "  recommended_outcome (one of: compliant, requires_review, non_compliant)\n"
        "  detailed_analysis (object with category breakdown)"
    )

    user_prompt = (
        f"DOCUMENT CONTEXT:\n{json.dumps(doc_summary, indent=2)}\n\n"
        f"QUESTIONNAIRE Q&A:\n{json.dumps(qa_pairs, indent=2)}\n\n"
        "Provide contextual compliance risk assessment. "
        "Consider severity of 'no' answers, user notes/context, and jurisdiction. "
        "Return ONLY the JSON object."
    )

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1200,
                # NO response_format — parse manually for max compatibility
            )

            raw = response.choices[0].message.content or ""
            logger.info(f"✅ GPT response received ({len(raw)} chars, attempt {attempt + 1})")

            parsed = _extract_json_from_text(raw)
            if parsed:
                # Validate required keys exist
                score = parsed.get("contextual_risk_score")
                if score is not None:
                    logger.info(f"✅ GPT analysis OK — contextual_risk_score: {score}")
                    return parsed
                else:
                    logger.warning(f"⚠️  GPT JSON missing contextual_risk_score, attempt {attempt + 1}")
            else:
                logger.warning(f"⚠️  GPT JSON parse failed, attempt {attempt + 1}")

        except Exception as e:
            logger.error(f"❌ GPT API call failed (attempt {attempt + 1}): {type(e).__name__}: {e}")
            if attempt == 1:
                break

    logger.warning("⚠️  Both GPT attempts failed — using basic scoring")
    return _get_fallback_analysis(answers, questions, reason="gpt_api_error")


def _get_fallback_analysis(
    answers: List[Dict],
    questions: List[Dict],
    reason: str = "unknown",
) -> Dict[str, Any]:
    """Fallback analysis when GPT is unavailable. Logs the reason clearly."""
    logger.warning(f"📊 FALLBACK SCORING — reason: {reason}")

    question_map = {q["question_id"]: q for q in questions}
    no_count = sum(1 for a in answers if a.get("answer", "").lower() == "no")
    total = len(questions) or 1

    critical_no = sum(
        1 for a in answers
        if a.get("answer", "").lower() == "no"
        and question_map.get(a.get("question_id", ""), {}).get("severity") == "critical"
    )

    risk_score = min(100, int((no_count / total) * 100))

    if critical_no > 0 or risk_score > 60:
        outcome = "non_compliant"
    elif risk_score > 30:
        outcome = "requires_review"
    else:
        outcome = "compliant"

    return {
        "contextual_risk_score":    risk_score,
        "risk_adjustment_reasoning": f"Basic scoring used (GPT unavailable: {reason}). {no_count}/{total} questions answered 'no'.",
        "critical_risk_factors":    [f"{critical_no} critical question(s) answered 'no'"] if critical_no else [],
        "compliance_confidence":     0.4,
        "recommended_outcome":       outcome,
        "detailed_analysis": {
            "fallback_reason":  reason,
            "no_count":         no_count,
            "critical_no":      critical_no,
            "total_questions":  total,
            "basic_risk_score": risk_score,
        },
    }


# ============================================================================
# MAIN HANDLER
# ============================================================================

async def handle(req: func.HttpRequest, user=None) -> func.HttpResponse:
    try:
        doc_id = req.route_params.get("documentId")
        if not doc_id:
            return json_response(400, error="Document ID required")

        logger.info(f"📝 SUBMIT_ANSWERS: {doc_id}")

        try:
            body = req.get_json()
            answers = body.get("answers", [])
            submission_notes = body.get("notes", "")
        except ValueError:
            return json_response(400, error="Invalid JSON payload")

        if not answers or not isinstance(answers, list):
            return json_response(400, error="No answers provided")

        doc = get_document(doc_id)
        if not doc:
            return json_response(404, error=f"Document not found: {doc_id}")

        compliance_questions = doc.get("compliance_questions", []) or doc.get("questions", [])
        if not compliance_questions:
            return json_response(400, error="No compliance questions found. Generate questions first.")

        validation = _validate_answers(answers, compliance_questions)
        if not validation["valid"]:
            return json_response(400, error=validation["error"])

        user_context = _extract_user_context(user, doc)
        logger.info(f"👤 Submitted by: {user_context['email']}")

        ai_violations = doc.get("violations", [])
        current_risk  = doc.get("risk_score", 0)

        gpt_analysis = await analyze_answers_with_gpt(
            document_content=doc,
            answers=answers,
            questions=compliance_questions,
            ai_violations=ai_violations,
        )

        contextual_risk = int(gpt_analysis.get("contextual_risk_score", 0))
        combined_risk   = min(100, int(current_risk * 0.4 + contextual_risk * 0.6))

        basic_calc = _calculate_basic_risk(answers, compliance_questions)

        risk_calculation = {
            "base_risk":            current_risk,
            "contextual_risk_score": contextual_risk,
            "new_risk_score":        combined_risk,
            "risk_change":           combined_risk - current_risk,
            "gpt_analysis":          gpt_analysis,
            "basic_calculation":     basic_calc,
            "ai_scan_weight":        0.4,
            "questionnaire_weight":  0.6,
        }

        logger.info(f"📊 Risk: {current_risk} → {contextual_risk} (GPT) → {combined_risk} (combined)")

        outcome = _determine_compliance_outcome(
            answers=answers,
            questions=compliance_questions,
            ai_violations=ai_violations,
            risk_calculation=risk_calculation,
            gpt_analysis=gpt_analysis,
        )

        logger.info(f"🎯 Outcome: {outcome['outcome']}")

        questionnaire_id = f"questionnaire_{doc_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        questionnaire_data = _package_questionnaire_data(
            questionnaire_id=questionnaire_id,
            doc=doc,
            user_context=user_context,
            questions=compliance_questions,
            answers=answers,
            risk_calculation=risk_calculation,
            outcome=outcome,
            submission_notes=submission_notes,
        )

        _persist_questionnaire_data(questionnaire_data)

        ok = _update_document_state(
            doc_id=doc_id,
            doc=doc,
            answers=answers,
            outcome=outcome,
            risk_calculation=risk_calculation,
            questionnaire_id=questionnaire_id,
            submission_notes=submission_notes,
        )
        if not ok:
            return json_response(500, error="Failed to save questionnaire data")

        _log_questionnaire_submission(
            doc=doc,
            user_context=user_context,
            questionnaire_id=questionnaire_id,
            answers_count=len(answers),
            outcome=outcome,
            risk_calculation=risk_calculation,
        )

        response_data = _build_response_data(
            doc_id=doc_id,
            questionnaire_id=questionnaire_id,
            outcome=outcome,
            risk_calculation=risk_calculation,
            answers=answers,
            ai_violations=ai_violations,
            questionnaire_data=questionnaire_data,
        )

        logger.info(f"✅ DONE: {doc_id} → {outcome['outcome']} (risk {combined_risk})")
        return json_response(200, data=response_data)

    except Exception as e:
        logger.error(f"❌ CRITICAL: {e}", exc_info=True)
        return json_response(500, error=f"Questionnaire submission failed: {str(e)[:200]}")


# ============================================================================
# HELPERS  (unchanged from original — kept for compatibility)
# ============================================================================

def _extract_user_context(user, doc):
    user_id = user_email = None
    user_roles = []
    org_id = doc.get("organization_id", "unknown")

    if user:
        if hasattr(user, "user_id"):
            user_id, user_email, user_roles = user.user_id, getattr(user, "email", None), getattr(user, "roles", [])
            org_id = getattr(user, "organization_id", org_id)
        elif isinstance(user, dict):
            user_id, user_email, user_roles = user.get("user_id"), user.get("email"), user.get("roles", [])
            org_id = user.get("organization_id", org_id)

    return {
        "id":              user_id or doc.get("uploaded_by", "system"),
        "email":           user_email or doc.get("uploaded_by_email", "system@system.com"),
        "roles":           user_roles or ["user"],
        "organization_id": org_id,
    }


def _validate_answers(answers, questions):
    question_ids = {q["question_id"] for q in questions}
    errors = []
    for i, a in enumerate(answers):
        if not isinstance(a, dict):
            errors.append(f"Answer {i} must be an object")
            continue
        if not a.get("question_id"):
            errors.append(f"Answer {i} missing question_id")
            continue
        if a["question_id"] not in question_ids:
            errors.append(f"Answer {i} invalid question_id")
            continue
        if a.get("answer", "").lower() not in ("yes", "no", "uncertain"):
            errors.append(f"Answer {i} must be yes/no/uncertain")
    return {"valid": not errors, "error": "; ".join(errors)}


def _calculate_basic_risk(answers, questions):
    qmap = {q["question_id"]: q for q in questions}
    counts = {"yes": 0, "no": 0, "uncertain": 0, "critical_no": 0, "high_no": 0, "medium_no": 0, "low_no": 0}
    for a in answers:
        v = a.get("answer", "").lower()
        if v in counts:
            counts[v] += 1
        if v == "no":
            sev = qmap.get(a.get("question_id", ""), {}).get("severity", "medium")
            counts[f"{sev}_no"] = counts.get(f"{sev}_no", 0) + 1
    risk = min(100, counts["critical_no"]*30 + counts["high_no"]*20 + counts["medium_no"]*10 + counts["low_no"]*5 + counts["uncertain"]*3)
    total = len(questions) or 1
    return {**counts, "basic_risk_score": risk, "compliance_rate": counts["yes"] / total * 100}


def _determine_compliance_outcome(answers, questions, ai_violations, risk_calculation, gpt_analysis):
    basic  = risk_calculation.get("basic_calculation", {})
    ctx    = risk_calculation.get("contextual_risk_score", 0)
    crec   = (gpt_analysis.get("recommended_outcome") or "").lower()

    if crec in ("non_compliant",) or basic.get("critical_no", 0) > 0 or ctx >= 75:
        return {
            "outcome": "non_compliant", "color": "red",
            "message": "🚨 REJECT: Critical compliance issues detected", "action": "reject",
            "reason":  gpt_analysis.get("risk_adjustment_reasoning", "Critical issues found"),
            "next_steps": ["Review all 'NO' answers", "Address critical compliance gaps", "Re-scan and re-submit"],
            "ai_insights": gpt_analysis.get("critical_risk_factors", []),
            "ai_confidence": gpt_analysis.get("compliance_confidence", 0.5),
        }
    elif crec in ("requires_review", "review") or basic.get("high_no", 0) > 1 or basic.get("uncertain", 0) > 2 or ctx >= 45:
        return {
            "outcome": "requires_review", "color": "amber",
            "message": "⚠️ REVIEW REQUIRED: Manual compliance review needed", "action": "review",
            "reason":  gpt_analysis.get("risk_adjustment_reasoning", "Manual review required"),
            "next_steps": ["Escalate to compliance officer", "Review uncertain answers", "Document decision"],
            "ai_insights": gpt_analysis.get("critical_risk_factors", []),
            "ai_confidence": gpt_analysis.get("compliance_confidence", 0.5),
        }
    else:
        rate = basic.get("compliance_rate", 100)
        return {
            "outcome": "compliant", "color": "green",
            "message": f"✅ APPROVE: Compliance verified ({rate:.0f}% compliant)", "action": "approve",
            "reason":  gpt_analysis.get("risk_adjustment_reasoning", "All requirements met"),
            "next_steps": ["Document can be approved", "Maintain audit trail"],
            "ai_insights": gpt_analysis.get("detailed_analysis", {}),
            "ai_confidence": gpt_analysis.get("compliance_confidence", 0.8),
        }


def _package_questionnaire_data(questionnaire_id, doc, user_context, questions, answers, risk_calculation, outcome, submission_notes):
    return {
        "id": questionnaire_id, "type": "questionnaire_submission",
        "document_id": doc.get("id"), "organization_id": doc.get("organization_id"),
        "user_id": user_context["id"], "user_email": user_context["email"],
        "questions": questions, "answers": answers,
        "outcome": {"result": outcome["outcome"], "reason": outcome.get("reason"), "action_required": outcome["action"], "next_steps": outcome.get("next_steps", [])},
        "risk_assessment": {
            "base_ai_risk": risk_calculation.get("base_risk", 0),
            "contextual_risk_score": risk_calculation.get("contextual_risk_score", 0),
            "combined_risk_score": risk_calculation.get("new_risk_score", 0),
            "gpt_analysis": risk_calculation.get("gpt_analysis", {}),
        },
        "submission_notes": submission_notes,
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "version": "4.1",
    }


def _persist_questionnaire_data(data):
    try:
        db = get_db()
        if not db:
            return False
        c = db.get_container("questionnaires")
        if c:
            c.create_item(data)
            logger.info(f"✅ Questionnaire saved: {data['id']}")
            return True
    except Exception as e:
        logger.warning(f"⚠️  Could not save questionnaire: {e}")
    return False


def _update_document_state(doc_id, doc, answers, outcome, risk_calculation, questionnaire_id, submission_notes):
    wf = {"non_compliant": "rejected", "requires_review": "pending_review"}.get(outcome["outcome"], "pending_approval")
    update = {
        "questionnaire_answers": answers,
        "questionnaire_completed_at": datetime.utcnow().isoformat() + "Z",
        "questionnaire_status": "completed",
        "questionnaire_outcome": outcome["outcome"],
        "compliance_outcome": outcome["outcome"],
        "status": "answers_submitted",
        "workflow_status": wf,
        "questionnaire_id": questionnaire_id,
        "risk_score": risk_calculation.get("new_risk_score", 0),
        "ai_risk_analysis": {
            "contextual_risk": risk_calculation.get("contextual_risk_score", 0),
            "ai_confidence": outcome.get("ai_confidence", 0),
            "gpt_recommendation": risk_calculation.get("gpt_analysis", {}).get("recommended_outcome", ""),
            "critical_risk_factors": risk_calculation.get("gpt_analysis", {}).get("critical_risk_factors", []),
        },
        "questionnaire_risk_change": risk_calculation.get("risk_change", 0),
        "questionnaire_submission_notes": submission_notes,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        ok = update_document(doc_id, update)
        if ok:
            logger.info(f"✅ Document {doc_id} updated → risk {risk_calculation.get('new_risk_score')}")
        return bool(ok)
    except Exception as e:
        logger.error(f"❌ Error updating document: {e}", exc_info=True)
        return False


def _log_questionnaire_submission(doc, user_context, questionnaire_id, answers_count, outcome, risk_calculation):
    try:
        log_action(
            org_id=doc.get("organization_id"), user_id=user_context["email"],
            user_email=user_context["email"], user_roles=user_context["roles"],
            action="questionnaire.submitted", resource_type="document",
            resource_id=doc.get("id"), resource_name=doc.get("filename"),
            details={
                "questionnaire_id": questionnaire_id, "answers_count": answers_count,
                "outcome": outcome["outcome"],
                "original_risk": risk_calculation.get("base_risk", 0),
                "new_risk": risk_calculation.get("new_risk_score", 0),
                "gpt_used": "basic_risk_score" not in str(risk_calculation.get("gpt_analysis", {}).get("risk_adjustment_reasoning", "")),
            },
        )
    except Exception as e:
        logger.warning(f"⚠️  Audit log failed: {e}")


def _build_response_data(doc_id, questionnaire_id, outcome, risk_calculation, answers, ai_violations, questionnaire_data):
    basic = risk_calculation.get("basic_calculation", {})
    return {
        "document_id": doc_id, "questionnaire_id": questionnaire_id,
        "outcome": outcome["outcome"], "color": outcome["color"], "message": outcome["message"],
        "action_required": outcome["action"],
        "risk_score_update": {
            "original_risk":  risk_calculation.get("base_risk", 0),
            "contextual_risk": risk_calculation.get("contextual_risk_score", 0),
            "new_risk":        risk_calculation.get("new_risk_score", 0),
            "risk_change":     risk_calculation.get("risk_change", 0),
            "ai_confidence":   outcome.get("ai_confidence", 0),
        },
        "questions_answered": len(answers),
        "answer_summary": {k: basic.get(k, 0) for k in ("yes_count", "no_count", "uncertain_count")},
        "ai_insights": {
            "reasoning": outcome.get("reason", ""),
            "critical_risk_factors": risk_calculation.get("gpt_analysis", {}).get("critical_risk_factors", []),
            "recommended_outcome":   risk_calculation.get("gpt_analysis", {}).get("recommended_outcome", ""),
        },
        "next_steps": outcome.get("next_steps", []),
        "data_saved": True,
        "document_status": "answers_submitted",
        "workflow_status": {"non_compliant": "rejected", "requires_review": "pending_review"}.get(outcome["outcome"], "pending_approval"),
        "enterprise_metadata": {"compliance_tracking_id": questionnaire_id, "ai_contextual_analysis": True},
    }