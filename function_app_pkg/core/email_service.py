"""
Azure Communication Services Email Service
==========================================
Enterprise email notifications using 100% Azure stack.

File: function_app_pkg/core/email_service.py

Setup:
1. Create Azure Communication Services resource
2. Create Email Communication Services resource  
3. Link them together
4. Add verified domain
5. Set environment variables:
   - ACS_CONNECTION_STRING
   - ACS_EMAIL_FROM (e.g., DoNotReply@yourverifieddomain.com)
"""

import os
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class EmailConfig:
    """Email configuration from environment"""
    CONNECTION_STRING = os.getenv('ACS_CONNECTION_STRING', '')
    EMAIL_FROM = os.getenv('ACS_EMAIL_FROM', 'compliance@yourdomain.com')
    EMAIL_FROM_NAME = os.getenv('ACS_EMAIL_FROM_NAME', 'Compliance Platform')
    APP_URL = os.getenv('APP_URL', 'https://compliance.yourdomain.com')
    SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL', 'support@yourdomain.com')
    
    # Feature flags
    EMAIL_ENABLED = os.getenv('EMAIL_ENABLED', 'true').lower() == 'true'
    EMAIL_LOG_ONLY = os.getenv('EMAIL_LOG_ONLY', 'false').lower() == 'true'


class NotificationType(Enum):
    """Types of notifications"""
    ASSIGNMENT = "assignment"
    MENTION = "mention"
    APPROVAL = "approval"
    REJECTION = "rejection"
    ESCALATION = "escalation"
    SLA_WARNING = "sla_warning"
    SLA_BREACH = "sla_breach"
    HANDOFF = "handoff"
    DISCUSSION = "discussion"
    WELCOME = "welcome"
    DAILY_DIGEST = "daily_digest"
    LEGAL_ESCALATION = "legal_escalation"
    WATCHER_UPDATE = "watcher_update"


@dataclass
class EmailResult:
    """Result of email send operation"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# EMAIL SERVICE CLASS
# =============================================================================

class EmailService:
    """Azure Communication Services Email Client"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._client = None
        self._initialized = True
        
        if EmailConfig.CONNECTION_STRING and EmailConfig.EMAIL_ENABLED:
            try:
                from azure.communication.email import EmailClient
                self._client = EmailClient.from_connection_string(EmailConfig.CONNECTION_STRING)
                logger.info("✅ Azure Communication Services Email initialized")
            except ImportError:
                logger.warning("⚠️ azure-communication-email not installed")
            except Exception as e:
                logger.error(f"❌ Email client init failed: {e}")
        else:
            logger.warning("⚠️ Email disabled or no connection string")
    
    def send(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str = None,
        cc: List[str] = None,
        bcc: List[str] = None,
        reply_to: str = None,
        importance: str = "normal"
    ) -> EmailResult:
        """
        Send an email via Azure Communication Services
        
        Args:
            to_email: Recipient email
            subject: Email subject
            html_content: HTML body
            text_content: Plain text body (auto-generated if not provided)
            cc: CC recipients
            bcc: BCC recipients
            reply_to: Reply-to address
            importance: normal, low, high
        
        Returns:
            EmailResult with success status and message_id
        """
        # Log only mode for testing
        if EmailConfig.EMAIL_LOG_ONLY:
            logger.info(f"📧 [LOG ONLY] Email to {to_email}: {subject}")
            return EmailResult(success=True, message_id="log_only")
        
        if not self._client:
            logger.warning(f"📧 Email client not available. Would send to {to_email}: {subject}")
            return EmailResult(success=False, error="Email client not configured")
        
        try:
            # Build message
            message = {
                "senderAddress": EmailConfig.EMAIL_FROM,
                "recipients": {
                    "to": [{"address": to_email}]
                },
                "content": {
                    "subject": subject,
                    "html": html_content,
                    "plainText": text_content or self._html_to_text(html_content)
                }
            }
            
            # Add CC
            if cc:
                message["recipients"]["cc"] = [{"address": email} for email in cc]
            
            # Add BCC
            if bcc:
                message["recipients"]["bcc"] = [{"address": email} for email in bcc]
            
            # Add reply-to
            if reply_to:
                message["replyTo"] = [{"address": reply_to}]
            
            # Add importance header
            if importance != "normal":
                message["headers"] = {"x-priority": "1" if importance == "high" else "5"}
            
            # Send with polling
            poller = self._client.begin_send(message)
            result = poller.result()
            
            logger.info(f"✅ Email sent to {to_email}: {subject} (ID: {result.get('id', 'unknown')})")
            return EmailResult(success=True, message_id=result.get('id'))
            
        except Exception as e:
            logger.error(f"❌ Email send failed to {to_email}: {e}")
            return EmailResult(success=False, error=str(e))
    
    def send_bulk(
        self,
        recipients: List[Dict],
        subject: str,
        html_content: str,
        text_content: str = None
    ) -> Dict:
        """
        Send bulk emails (one at a time with ACS)
        
        Args:
            recipients: List of {email, name} dicts
            subject: Email subject
            html_content: HTML body (can include {name} placeholder)
            text_content: Plain text body
        
        Returns:
            Dict with sent count, failed count, errors
        """
        results = {"sent": 0, "failed": 0, "errors": []}
        
        for recipient in recipients:
            email = recipient.get('email')
            name = recipient.get('name', email.split('@')[0])
            
            # Personalize content
            personalized_html = html_content.replace('{name}', name)
            personalized_text = text_content.replace('{name}', name) if text_content else None
            
            result = self.send(
                to_email=email,
                subject=subject,
                html_content=personalized_html,
                text_content=personalized_text
            )
            
            if result.success:
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({"email": email, "error": result.error})
        
        return results
    
    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text"""
        text = re.sub(r'<br\s*/?>', '\n', html)
        text = re.sub(r'<p[^>]*>', '\n', text)
        text = re.sub(r'</p>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        return text.strip()


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_email_service: Optional[EmailService] = None

def get_email_service() -> EmailService:
    """Get the singleton email service"""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service


# =============================================================================
# HTML TEMPLATES
# =============================================================================

def _base_template(title: str, content: str, footer_text: str = None) -> str:
    """Base HTML email template with Microsoft-style design"""
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f5f5f5; padding: 20px 0;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #0078d4 0%, #106ebe 100%); padding: 30px 40px; border-radius: 8px 8px 0 0;">
                            <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 600;">
                                ⚖️ Compliance Platform
                            </h1>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding: 40px;">
                            {content}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 20px 40px; border-radius: 0 0 8px 8px; border-top: 1px solid #e9ecef;">
                            <p style="color: #6c757d; font-size: 12px; margin: 0; text-align: center;">
                                {footer_text or "This is an automated message from the Compliance Platform."}
                            </p>
                            <p style="color: #6c757d; font-size: 12px; margin: 8px 0 0 0; text-align: center;">
                                <a href="{EmailConfig.APP_URL}" style="color: #0078d4; text-decoration: none;">Open Platform</a>
                                &nbsp;|&nbsp;
                                <a href="{EmailConfig.APP_URL}/settings/notifications" style="color: #0078d4; text-decoration: none;">Notification Settings</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _button(text: str, url: str, color: str = "#0078d4") -> str:
    """Generate a CTA button"""
    return f"""
    <a href="{url}" style="display: inline-block; background-color: {color}; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 600; margin: 16px 0;">
        {text}
    </a>
    """


def _priority_badge(priority: str) -> str:
    """Generate a priority badge"""
    colors = {
        'urgent': '#dc3545',
        'high': '#fd7e14',
        'medium': '#0078d4',
        'low': '#28a745'
    }
    color = colors.get(priority.lower(), '#6c757d')
    return f'<span style="display: inline-block; background-color: {color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase;">{priority}</span>'


# =============================================================================
# NOTIFICATION EMAIL FUNCTIONS
# =============================================================================

def send_assignment_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    assigned_by: str,
    priority: str = "medium",
    deadline: str = None,
    notes: str = None,
    ticket_id: str = None
) -> EmailResult:
    """Send notification when document is assigned to user"""
    
    deadline_html = f"<tr><td style='padding: 8px 0; color: #6c757d;'>Deadline:</td><td style='padding: 8px 0; color: #212529; font-weight: 600;'>{deadline}</td></tr>" if deadline else ""
    notes_html = f"<div style='background-color: #f8f9fa; padding: 16px; border-radius: 4px; margin: 16px 0;'><strong>Notes:</strong><br>{notes}</div>" if notes else ""
    ticket_html = f"<p style='color: #6c757d; font-size: 14px;'>Ticket ID: <strong>{ticket_id}</strong></p>" if ticket_id else ""
    
    content = f"""
    <h2 style="color: #212529; margin: 0 0 16px 0;">New Document Assigned</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        A document has been assigned to you for compliance review.
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Assigned by:</td>
            <td style="padding: 8px 0; color: #212529;">{assigned_by}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Priority:</td>
            <td style="padding: 8px 0;">{_priority_badge(priority)}</td>
        </tr>
        {deadline_html}
    </table>
    
    {ticket_html}
    {notes_html}
    
    {_button("Review Document", f"{EmailConfig.APP_URL}/documents/{document_id}")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"📋 Document Assigned: {document_name}",
        html_content=_base_template("Document Assignment", content),
        importance="high" if priority in ["urgent", "high"] else "normal"
    )


def send_mention_notification(
    to_email: str,
    to_name: str,
    mentioned_by: str,
    document_name: str,
    document_id: str,
    discussion_preview: str,
    discussion_id: str = None
) -> EmailResult:
    """Send notification when user is @mentioned"""
    
    content = f"""
    <h2 style="color: #212529; margin: 0 0 16px 0;">You Were Mentioned</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        <strong>{mentioned_by}</strong> mentioned you in a discussion about <strong>{document_name}</strong>:
    </p>
    
    <div style="background-color: #f8f9fa; border-left: 4px solid #0078d4; padding: 16px; margin: 24px 0; border-radius: 0 4px 4px 0;">
        <p style="color: #495057; margin: 0; font-style: italic;">
            "{discussion_preview[:300]}{'...' if len(discussion_preview) > 300 else ''}"
        </p>
    </div>
    
    {_button("View Discussion", f"{EmailConfig.APP_URL}/documents/{document_id}#discussions")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"💬 {mentioned_by} mentioned you in {document_name}",
        html_content=_base_template("You Were Mentioned", content)
    )


def send_approval_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    approved_by: str,
    comments: str = None,
    certificate_url: str = None
) -> EmailResult:
    """Send notification when document is approved"""
    
    comments_html = f"""
    <div style="background-color: #d4edda; border-left: 4px solid #28a745; padding: 16px; margin: 24px 0; border-radius: 0 4px 4px 0;">
        <strong>Comments:</strong><br>
        {comments}
    </div>
    """ if comments else ""
    
    cert_button = _button("Download Certificate", certificate_url, "#28a745") if certificate_url else ""
    
    content = f"""
    <h2 style="color: #28a745; margin: 0 0 16px 0;">✅ Document Approved</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Great news! Your document <strong>{document_name}</strong> has been approved for publication.
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Approved by:</td>
            <td style="padding: 8px 0; color: #212529;">{approved_by}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Status:</td>
            <td style="padding: 8px 0;"><span style="background-color: #28a745; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px;">APPROVED</span></td>
        </tr>
    </table>
    
    {comments_html}
    
    {_button("View Document", f"{EmailConfig.APP_URL}/documents/{document_id}")}
    {cert_button}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"✅ Approved: {document_name}",
        html_content=_base_template("Document Approved", content)
    )


def send_rejection_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    rejected_by: str,
    reason: str,
    required_changes: List[str] = None
) -> EmailResult:
    """Send notification when document is rejected"""
    
    changes_html = ""
    if required_changes:
        changes_list = "".join([f"<li style='margin: 8px 0;'>{change}</li>" for change in required_changes])
        changes_html = f"""
        <div style="margin: 24px 0;">
            <strong>Required Changes:</strong>
            <ul style="color: #495057; padding-left: 20px;">
                {changes_list}
            </ul>
        </div>
        """
    
    content = f"""
    <h2 style="color: #dc3545; margin: 0 0 16px 0;">❌ Document Requires Changes</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Your document <strong>{document_name}</strong> requires changes before it can be approved.
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Reviewed by:</td>
            <td style="padding: 8px 0; color: #212529;">{rejected_by}</td>
        </tr>
    </table>
    
    <div style="background-color: #f8d7da; border-left: 4px solid #dc3545; padding: 16px; margin: 24px 0; border-radius: 0 4px 4px 0;">
        <strong>Reason:</strong><br>
        {reason}
    </div>
    
    {changes_html}
    
    {_button("View Details & Resubmit", f"{EmailConfig.APP_URL}/documents/{document_id}")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"❌ Changes Required: {document_name}",
        html_content=_base_template("Document Requires Changes", content),
        importance="high"
    )


def send_escalation_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    escalated_by: str,
    reason: str,
    priority: str = "high",
    escalation_target: str = "legal"
) -> EmailResult:
    """Send notification when document is escalated to legal/advisory"""
    
    target_text = {
        'legal': 'internal legal team',
        'dla_piper': 'DLA Piper advisory',
        'senior_compliance': 'senior compliance officer',
        'management': 'management'
    }.get(escalation_target, 'advisory team')
    
    content = f"""
    <h2 style="color: #fd7e14; margin: 0 0 16px 0;">🔺 Document Escalated for Review</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        A document has been escalated to the {target_text} for your review.
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Escalated by:</td>
            <td style="padding: 8px 0; color: #212529;">{escalated_by}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Priority:</td>
            <td style="padding: 8px 0;">{_priority_badge(priority)}</td>
        </tr>
    </table>
    
    <div style="background-color: #fff3cd; border-left: 4px solid #fd7e14; padding: 16px; margin: 24px 0; border-radius: 0 4px 4px 0;">
        <strong>Escalation Reason:</strong><br>
        {reason}
    </div>
    
    {_button("Review Document", f"{EmailConfig.APP_URL}/legal/queue", "#fd7e14")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"🔺 Escalation: {document_name} requires your review",
        html_content=_base_template("Document Escalated", content),
        importance="high"
    )


def send_sla_warning_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    hours_remaining: float,
    deadline: str,
    ticket_id: str = None
) -> EmailResult:
    """Send warning when SLA deadline is approaching"""
    
    if hours_remaining <= 0:
        urgency_color = "#dc3545"
        urgency_text = "OVERDUE"
        subject_prefix = "🚨 SLA BREACHED"
    elif hours_remaining < 4:
        urgency_color = "#dc3545"
        urgency_text = "CRITICAL"
        subject_prefix = "🚨 SLA Critical"
    elif hours_remaining < 8:
        urgency_color = "#fd7e14"
        urgency_text = "WARNING"
        subject_prefix = "⚠️ SLA Warning"
    else:
        urgency_color = "#ffc107"
        urgency_text = "REMINDER"
        subject_prefix = "⏰ SLA Reminder"
    
    content = f"""
    <h2 style="color: {urgency_color}; margin: 0 0 16px 0;">{subject_prefix}: Action Required</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Your assigned document requires attention to meet the SLA deadline.
    </p>
    
    <div style="background-color: #f8f9fa; border: 2px solid {urgency_color}; padding: 20px; border-radius: 8px; margin: 24px 0; text-align: center;">
        <span style="background-color: {urgency_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600;">{urgency_text}</span>
        <h3 style="margin: 16px 0 8px 0; color: #212529;">{document_name}</h3>
        <p style="margin: 0; color: #6c757d;">Deadline: <strong>{deadline}</strong></p>
        <p style="margin: 8px 0 0 0; color: {urgency_color}; font-weight: 600;">
            {f"{hours_remaining:.1f} hours remaining" if hours_remaining > 0 else "OVERDUE"}
        </p>
    </div>
    
    {f"<p style='color: #6c757d;'>Ticket: <strong>{ticket_id}</strong></p>" if ticket_id else ""}
    
    {_button("Complete Review Now", f"{EmailConfig.APP_URL}/documents/{document_id}", urgency_color)}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"{subject_prefix}: {document_name}",
        html_content=_base_template("SLA Warning", content),
        importance="high"
    )


def send_handoff_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    handed_off_by: str,
    reason: str,
    notes: str = None,
    priority: str = "medium"
) -> EmailResult:
    """Send notification when assignment is handed off"""
    
    notes_html = f"""
    <div style="background-color: #f8f9fa; padding: 16px; border-radius: 4px; margin: 24px 0;">
        <strong>Handoff Notes:</strong><br>
        {notes}
    </div>
    """ if notes else ""
    
    content = f"""
    <h2 style="color: #17a2b8; margin: 0 0 16px 0;">🔄 Assignment Handed Off to You</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        <strong>{handed_off_by}</strong> has handed off a document review to you.
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">From:</td>
            <td style="padding: 8px 0; color: #212529;">{handed_off_by}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Priority:</td>
            <td style="padding: 8px 0;">{_priority_badge(priority)}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Reason:</td>
            <td style="padding: 8px 0; color: #212529;">{reason}</td>
        </tr>
    </table>
    
    {notes_html}
    
    {_button("Accept & Review", f"{EmailConfig.APP_URL}/documents/{document_id}")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"🔄 Handoff: {document_name} from {handed_off_by}",
        html_content=_base_template("Assignment Handoff", content)
    )


def send_welcome_email(
    to_email: str,
    to_name: str,
    organization_name: str,
    invited_by: str = None
) -> EmailResult:
    """Send welcome email to new user"""
    
    invited_text = f"You've been invited by <strong>{invited_by}</strong> to join" if invited_by else "Welcome to"
    
    content = f"""
    <h2 style="color: #0078d4; margin: 0 0 16px 0;">Welcome to the Compliance Platform! 🎉</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        {invited_text} the <strong>{organization_name}</strong> compliance platform.
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Here's what you can do:
    </p>
    
    <ul style="color: #495057; line-height: 2;">
        <li>📄 Upload marketing documents for compliance review</li>
        <li>🤖 Get AI-powered compliance analysis</li>
        <li>💬 Chat with AI about specific violations</li>
        <li>✅ Track document approval status</li>
        <li>📊 View compliance analytics</li>
    </ul>
    
    {_button("Get Started", EmailConfig.APP_URL)}
    
    <p style="color: #6c757d; font-size: 14px; margin-top: 32px;">
        Need help? Contact us at <a href="mailto:{EmailConfig.SUPPORT_EMAIL}" style="color: #0078d4;">{EmailConfig.SUPPORT_EMAIL}</a>
    </p>
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"Welcome to {organization_name} Compliance Platform",
        html_content=_base_template("Welcome", content)
    )


def send_daily_digest(
    to_email: str,
    to_name: str,
    pending_reviews: int,
    at_risk_count: int,
    completed_today: int,
    mentions_count: int,
    queue_items: List[Dict] = None
) -> EmailResult:
    """Send daily digest email"""
    
    queue_html = ""
    if queue_items:
        rows = ""
        for item in queue_items[:5]:
            priority_badge = _priority_badge(item.get('priority', 'medium'))
            rows += f"""
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e9ecef;">{item.get('document_name', 'Unknown')}</td>
                <td style="padding: 12px; border-bottom: 1px solid #e9ecef;">{priority_badge}</td>
                <td style="padding: 12px; border-bottom: 1px solid #e9ecef;">{item.get('deadline', 'No deadline')}</td>
            </tr>
            """
        
        queue_html = f"""
        <h3 style="color: #212529; margin: 24px 0 16px 0;">Your Queue</h3>
        <table style="width: 100%; border-collapse: collapse;">
            <tr style="background-color: #f8f9fa;">
                <th style="padding: 12px; text-align: left; font-weight: 600;">Document</th>
                <th style="padding: 12px; text-align: left; font-weight: 600;">Priority</th>
                <th style="padding: 12px; text-align: left; font-weight: 600;">Deadline</th>
            </tr>
            {rows}
        </table>
        """
    
    content = f"""
    <h2 style="color: #212529; margin: 0 0 16px 0;">📊 Your Daily Digest</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name}, here's your compliance summary for today:
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 20px; background-color: #e3f2fd; border-radius: 8px; text-align: center; width: 25%;">
                <div style="font-size: 32px; font-weight: 700; color: #0078d4;">{pending_reviews}</div>
                <div style="color: #495057; font-size: 14px;">Pending Reviews</div>
            </td>
            <td style="width: 4%;"></td>
            <td style="padding: 20px; background-color: {'#ffebee' if at_risk_count > 0 else '#e8f5e9'}; border-radius: 8px; text-align: center; width: 25%;">
                <div style="font-size: 32px; font-weight: 700; color: {'#dc3545' if at_risk_count > 0 else '#28a745'};">{at_risk_count}</div>
                <div style="color: #495057; font-size: 14px;">At Risk</div>
            </td>
            <td style="width: 4%;"></td>
            <td style="padding: 20px; background-color: #e8f5e9; border-radius: 8px; text-align: center; width: 25%;">
                <div style="font-size: 32px; font-weight: 700; color: #28a745;">{completed_today}</div>
                <div style="color: #495057; font-size: 14px;">Completed</div>
            </td>
            <td style="width: 4%;"></td>
            <td style="padding: 20px; background-color: #fff3e0; border-radius: 8px; text-align: center; width: 25%;">
                <div style="font-size: 32px; font-weight: 700; color: #fd7e14;">{mentions_count}</div>
                <div style="color: #495057; font-size: 14px;">Mentions</div>
            </td>
        </tr>
    </table>
    
    {queue_html}
    
    {_button("Open Dashboard", f"{EmailConfig.APP_URL}/assignments/my-queue")}
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"📊 Daily Digest: {pending_reviews} pending, {at_risk_count} at risk",
        html_content=_base_template("Daily Digest", content)
    )


def send_watcher_update_notification(
    to_email: str,
    to_name: str,
    document_name: str,
    document_id: str,
    action: str,
    action_by: str,
    details: str = None
) -> EmailResult:
    """Send notification to document watchers about updates"""
    
    action_icons = {
        'approved': '✅',
        'rejected': '❌',
        'escalated': '🔺',
        'comment_added': '💬',
        'status_changed': '🔄',
        'assigned': '📋'
    }
    icon = action_icons.get(action, '📢')
    
    content = f"""
    <h2 style="color: #212529; margin: 0 0 16px 0;">{icon} Document Update</h2>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        Hi {to_name},
    </p>
    
    <p style="color: #495057; font-size: 16px; line-height: 1.6;">
        A document you're watching has been updated:
    </p>
    
    <table style="width: 100%; margin: 24px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Document:</td>
            <td style="padding: 8px 0; color: #212529; font-weight: 600;">{document_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">Action:</td>
            <td style="padding: 8px 0; color: #212529;">{action.replace('_', ' ').title()}</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; color: #6c757d;">By:</td>
            <td style="padding: 8px 0; color: #212529;">{action_by}</td>
        </tr>
    </table>
    
    {f'<p style="color: #495057;">{details}</p>' if details else ''}
    
    {_button("View Document", f"{EmailConfig.APP_URL}/documents/{document_id}")}
    
    <p style="color: #6c757d; font-size: 12px; margin-top: 24px;">
        You're receiving this because you're watching this document. 
        <a href="{EmailConfig.APP_URL}/documents/{document_id}" style="color: #0078d4;">Unwatch</a>
    </p>
    """
    
    service = get_email_service()
    return service.send(
        to_email=to_email,
        subject=f"{icon} Update: {document_name} - {action.replace('_', ' ').title()}",
        html_content=_base_template("Document Update", content)
    )


# =============================================================================
# NOTIFICATION ORCHESTRATOR
# =============================================================================

def send_notification(
    notification_type: NotificationType,
    recipient_email: str,
    recipient_name: str,
    **kwargs
) -> EmailResult:
    """
    Central notification dispatcher
    
    Usage:
        send_notification(
            NotificationType.ASSIGNMENT,
            "user@example.com",
            "John Doe",
            document_name="Campaign.docx",
            document_id="123",
            assigned_by="admin@example.com"
        )
    """
    handlers = {
        NotificationType.ASSIGNMENT: send_assignment_notification,
        NotificationType.MENTION: send_mention_notification,
        NotificationType.APPROVAL: send_approval_notification,
        NotificationType.REJECTION: send_rejection_notification,
        NotificationType.ESCALATION: send_escalation_notification,
        NotificationType.LEGAL_ESCALATION: send_escalation_notification,
        NotificationType.SLA_WARNING: send_sla_warning_notification,
        NotificationType.SLA_BREACH: send_sla_warning_notification,
        NotificationType.HANDOFF: send_handoff_notification,
        NotificationType.WELCOME: send_welcome_email,
        NotificationType.DAILY_DIGEST: send_daily_digest,
        NotificationType.WATCHER_UPDATE: send_watcher_update_notification,
    }
    
    handler = handlers.get(notification_type)
    if not handler:
        logger.warning(f"Unknown notification type: {notification_type}")
        return EmailResult(success=False, error=f"Unknown notification type: {notification_type}")
    
    return handler(to_email=recipient_email, to_name=recipient_name, **kwargs)