"""Analytics API - ROI, performance, and compliance metrics
Aggregates from analytics_events, documents, decision_trails, and audit_logs
"""

import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
import azure.functions as func

logger = logging.getLogger(__name__)


def handle_get_analytics(req: func.HttpRequest, user=None) -> func.HttpResponse:
    """
    GET /analytics
    Query params:
      days   = 30 | 60 | 90 | 180 | 365   (default 30)
      org_id = override (super-admin only)
    """
    from function_app_pkg.shared.http_utils import json_response
    from function_app_pkg.core.database import get_container

    # ── auth ──────────────────────────────────────────────────────────────────
    if user is None:
        try:
            from function_app_pkg.api.auth import authenticate_request
            user, error = authenticate_request(req)
            if error:
                return json_response(401, error=error)
        except Exception as e:
            return json_response(401, error=str(e))

    org_id = user.organization_id if hasattr(user, "organization_id") else user.get("organization_id")
    if not org_id:
        return json_response(400, error="Organization ID required")

    is_super_admin = (
        user.has_role("Platform.SuperAdmin")
        if hasattr(user, "has_role")
        else "Platform.SuperAdmin" in (user.get("roles") or [])
    )

    # Allow super-admin to query a specific org
    override_org = req.params.get("org_id")
    if override_org and is_super_admin:
        org_id = override_org

    days = int(req.params.get("days", 30))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    try:
        docs_container     = get_container("documents")
        analytics_container = get_container("analytics_events")

        # ── 1. Pull all org documents in window ────────────────────────────────
        docs = list(docs_container.query_items(
            query="""
                SELECT c.id, c.filename, c.status, c.workflow_status,
                       c.compliance_outcome, c.risk_score, c.violations_count,
                       c.violation_count, c.jurisdiction, c.created_at, c.scanned_at,
                       c.updated_at, c.assigned_at, c.assigned_to, c.approved_at,
                       c.rejected_at, c.escalated_at, c.briefed_at,
                       c.uploaded_by, c.uploaded_by_name,
                       c.scan_duration_seconds, c.team_name,
                       c.violations, c.pii_summary, c.legal_reviewed_at
                FROM c
                WHERE c.organization_id = @org_id
                  AND c.type = 'document'
                  AND c.created_at >= @since
                ORDER BY c.created_at DESC
            """,
            parameters=[
                {"name": "@org_id", "value": org_id},
                {"name": "@since",  "value": since},
            ],
            partition_key=org_id,
        ))

        # ── 2. Pull analytics events in window ────────────────────────────────
        try:
            events = list(analytics_container.query_items(
                query="""
                    SELECT c.event_type, c.metrics, c.dimensions,
                           c.document_id, c.created_at, c.user_email
                    FROM c
                    WHERE c.organization_id = @org_id
                      AND c.created_at >= @since
                """,
                parameters=[
                    {"name": "@org_id", "value": org_id},
                    {"name": "@since",  "value": since},
                ],
                enable_cross_partition_query=True,
            ))
        except Exception:
            events = []

        # ── 3. Pull decision trail for stage-time calculation ─────────────────
        try:
            trails = list(docs_container.query_items(
                query="""
                    SELECT c.document_id, c.decision, c.decision_type,
                           c.decision_timestamp, c.decision_maker
                    FROM c
                    WHERE c.organization_id = @org_id
                      AND c.type = 'decision_trail'
                      AND c.decision_timestamp >= @since
                """,
                parameters=[
                    {"name": "@org_id", "value": org_id},
                    {"name": "@since",  "value": since},
                ],
                partition_key=org_id,
            ))
        except Exception:
            trails = []

        # ──────────────────────────────────────────────────────────────────────
        # AGGREGATE
        # ──────────────────────────────────────────────────────────────────────

        total_docs      = len(docs)
        scanned_docs    = [d for d in docs if d.get("scanned_at")]
        approved_docs   = [d for d in docs if d.get("status") == "approved"]
        rejected_docs   = [d for d in docs if d.get("status") == "rejected"]
        escalated_docs  = [d for d in docs if d.get("escalated_at")]
        assigned_docs   = [d for d in docs if d.get("assigned_to")]

        # Risk distribution
        risk_scores = [d.get("risk_score", 0) for d in scanned_docs if d.get("risk_score") is not None]
        avg_risk    = round(sum(risk_scores) / len(risk_scores), 1) if risk_scores else 0

        low_risk    = len([r for r in risk_scores if r < 40])
        med_risk    = len([r for r in risk_scores if 40 <= r < 70])
        high_risk   = len([r for r in risk_scores if r >= 70])

        # Violations
        all_violations = []
        for d in docs:
            viol = d.get("violations") or []
            if isinstance(viol, list):
                all_violations.extend(viol)
        total_violations = len(all_violations)
        critical_v = len([v for v in all_violations if (v.get("severity") or "").upper() == "CRITICAL"])
        high_v     = len([v for v in all_violations if (v.get("severity") or "").upper() == "HIGH"])

        # PII
        total_pii = sum(
            (d.get("pii_summary") or {}).get("count", 0)
            for d in docs
        )

        # Compliance outcomes
        outcomes = {}
        for d in docs:
            o = d.get("compliance_outcome") or "unknown"
            outcomes[o] = outcomes.get(o, 0) + 1

        # ── ROI from analytics events ─────────────────────────────────────────
        scan_events = [e for e in events if e.get("event_type") == "scan_completed"]
        total_time_saved_h     = sum((e.get("metrics") or {}).get("time_saved_hours", 0.5)      for e in scan_events)
        total_cost_saved       = sum((e.get("metrics") or {}).get("cost_saved_gbp", 37.5)       for e in scan_events)
        total_fines_prevented  = sum((e.get("metrics") or {}).get("potential_fines_prevented_gbp", 0) for e in scan_events)

        # Fallback: estimate if events table is sparse
        if total_time_saved_h == 0 and scanned_docs:
            total_time_saved_h    = len(scanned_docs) * 0.5
            total_cost_saved      = total_time_saved_h * 75
            total_fines_prevented = total_violations * 5000

        # ── Stage timing (hours) ──────────────────────────────────────────────
        def _hours(start_str, end_str):
            try:
                s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                return max(0, (e - s).total_seconds() / 3600)
            except Exception:
                return None

        upload_to_scan   = [_hours(d["created_at"], d["scanned_at"]) for d in scanned_docs if d.get("created_at") and d.get("scanned_at")]
        upload_to_scan   = [h for h in upload_to_scan if h is not None]

        scan_to_assign   = [_hours(d["scanned_at"], d["assigned_at"]) for d in assigned_docs if d.get("scanned_at") and d.get("assigned_at")]
        scan_to_assign   = [h for h in scan_to_assign if h is not None]

        assign_to_close  = [
            _hours(d["assigned_at"], d.get("approved_at") or d.get("rejected_at"))
            for d in docs
            if d.get("assigned_at") and (d.get("approved_at") or d.get("rejected_at"))
        ]
        assign_to_close  = [h for h in assign_to_close if h is not None]

        full_cycle       = [
            _hours(d["created_at"], d.get("approved_at") or d.get("rejected_at"))
            for d in docs
            if d.get("created_at") and (d.get("approved_at") or d.get("rejected_at"))
        ]
        full_cycle       = [h for h in full_cycle if h is not None]

        avg_upload_to_scan  = round(sum(upload_to_scan)  / len(upload_to_scan),  1) if upload_to_scan  else 0
        avg_scan_to_assign  = round(sum(scan_to_assign)  / len(scan_to_assign),  1) if scan_to_assign  else 0
        avg_assign_to_close = round(sum(assign_to_close) / len(assign_to_close), 1) if assign_to_close else 0
        avg_full_cycle      = round(sum(full_cycle)      / len(full_cycle),      1) if full_cycle      else 0

        avg_scan_duration   = round(
            sum((e.get("metrics") or {}).get("scan_duration_seconds", 0) for e in scan_events) /
            max(1, len(scan_events)), 1
        )

        # ── Daily trend (documents created per day) ───────────────────────────
        daily: dict = {}
        for d in docs:
            day = (d.get("created_at") or "")[:10]
            if day:
                if day not in daily:
                    daily[day] = {"date": day, "uploaded": 0, "scanned": 0, "approved": 0, "rejected": 0, "violations": 0}
                daily[day]["uploaded"] += 1
                if d.get("scanned_at"):   daily[day]["scanned"]   += 1
                if d.get("status") == "approved": daily[day]["approved"] += 1
                if d.get("status") == "rejected": daily[day]["rejected"] += 1
                daily[day]["violations"] += d.get("violations_count") or d.get("violation_count") or 0

        trend = sorted(daily.values(), key=lambda x: x["date"])

        # ── ROI daily trend ───────────────────────────────────────────────────
        roi_daily: dict = {}
        for e in scan_events:
            day = (e.get("created_at") or "")[:10]
            if day:
                if day not in roi_daily:
                    roi_daily[day] = {"date": day, "cost_saved": 0.0, "fines_prevented": 0.0, "time_saved_h": 0.0}
                m = e.get("metrics") or {}
                roi_daily[day]["cost_saved"]      += m.get("cost_saved_gbp", 37.5)
                roi_daily[day]["fines_prevented"] += m.get("potential_fines_prevented_gbp", 0)
                roi_daily[day]["time_saved_h"]    += m.get("time_saved_hours", 0.5)

        roi_trend = sorted(roi_daily.values(), key=lambda x: x["date"])

        # ── Jurisdiction breakdown ────────────────────────────────────────────
        jur: dict = {}
        for d in docs:
            j = d.get("jurisdiction") or "Unknown"
            if j not in jur:
                jur[j] = {"jurisdiction": j, "count": 0, "avg_risk": 0, "violations": 0, "_risks": []}
            jur[j]["count"] += 1
            jur[j]["violations"] += d.get("violations_count") or d.get("violation_count") or 0
            if d.get("risk_score"):
                jur[j]["_risks"].append(d["risk_score"])

        jurisdictions = []
        for j, v in jur.items():
            v["avg_risk"] = round(sum(v["_risks"]) / len(v["_risks"]), 1) if v["_risks"] else 0
            del v["_risks"]
            jurisdictions.append(v)
        jurisdictions.sort(key=lambda x: x["count"], reverse=True)

        # ── Top users by activity ─────────────────────────────────────────────
        user_activity: dict = {}
        for d in docs:
            u = d.get("uploaded_by_name") or d.get("uploaded_by") or "Unknown"
            if u not in user_activity:
                user_activity[u] = {"name": u, "uploaded": 0, "approved": 0, "rejected": 0}
            user_activity[u]["uploaded"] += 1
            if d.get("status") == "approved": user_activity[u]["approved"] += 1
            if d.get("status") == "rejected": user_activity[u]["rejected"] += 1

        top_users = sorted(user_activity.values(), key=lambda x: x["uploaded"], reverse=True)[:10]

        # ── Risk score histogram (buckets of 10) ──────────────────────────────
        histogram = [{"range": f"{i}-{i+9}", "count": 0} for i in range(0, 100, 10)]
        for r in risk_scores:
            bucket = min(int(r // 10), 9)
            histogram[bucket]["count"] += 1

        # ── Violation category breakdown ──────────────────────────────────────
        viol_cats: dict = {}
        for v in all_violations:
            cat = v.get("category") or "general"
            viol_cats[cat] = viol_cats.get(cat, 0) + 1
        violation_categories = [{"category": k, "count": v} for k, v in sorted(viol_cats.items(), key=lambda x: -x[1])[:10]]

        # ── Funnel ────────────────────────────────────────────────────────────
        funnel = [
            {"stage": "Uploaded",  "count": total_docs},
            {"stage": "Scanned",   "count": len(scanned_docs)},
            {"stage": "Assigned",  "count": len(assigned_docs)},
            {"stage": "Escalated", "count": len(escalated_docs)},
            {"stage": "Approved",  "count": len(approved_docs)},
        ]

        # ──────────────────────────────────────────────────────────────────────
        result = {
            "period_days": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total_documents":    total_docs,
                "scanned":            len(scanned_docs),
                "approved":           len(approved_docs),
                "rejected":           len(rejected_docs),
                "escalated":          len(escalated_docs),
                "assigned":           len(assigned_docs),
                "total_violations":   total_violations,
                "critical_violations": critical_v,
                "high_violations":    high_v,
                "total_pii_detected": total_pii,
                "avg_risk_score":     avg_risk,
                "low_risk_count":     low_risk,
                "medium_risk_count":  med_risk,
                "high_risk_count":    high_risk,
                "compliance_outcomes": outcomes,
            },
            "roi": {
                "total_time_saved_hours":   round(total_time_saved_h, 1),
                "total_cost_saved_gbp":     round(total_cost_saved, 0),
                "potential_fines_prevented_gbp": round(total_fines_prevented, 0),
                "total_value_gbp":          round(total_cost_saved + total_fines_prevented, 0),
                "avg_cost_per_document_gbp": round(total_cost_saved / max(1, len(scanned_docs)), 1),
                "avg_time_saved_per_doc_h":  round(total_time_saved_h / max(1, len(scanned_docs)), 2),
                "roi_multiple":              round((total_cost_saved + total_fines_prevented) / max(1, total_cost_saved), 1) if total_cost_saved > 0 else 0,
            },
            "timing": {
                "avg_upload_to_scan_hours":   avg_upload_to_scan,
                "avg_scan_to_assign_hours":   avg_scan_to_assign,
                "avg_assign_to_close_hours":  avg_assign_to_close,
                "avg_full_cycle_hours":       avg_full_cycle,
                "avg_scan_duration_seconds":  avg_scan_duration,
                "median_full_cycle_hours":    round(sorted(full_cycle)[len(full_cycle) // 2], 1) if full_cycle else 0,
                "p90_full_cycle_hours":       round(sorted(full_cycle)[int(len(full_cycle) * 0.9)], 1) if len(full_cycle) >= 10 else 0,
            },
            "trends": {
                "daily":     trend,
                "roi_daily": roi_trend,
            },
            "breakdown": {
                "jurisdictions":        jurisdictions,
                "violation_categories": violation_categories,
                "risk_histogram":       histogram,
                "funnel":               funnel,
                "top_users":            top_users,
            },
        }

        logger.info(f"📊 Analytics built for {org_id}: {total_docs} docs, {days} days")
        return json_response(200, data=result)

    except Exception as e:
        logger.exception(e)
        return json_response(500, error=f"Analytics failed: {str(e)[:200]}")