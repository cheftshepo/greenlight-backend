"""
Email templates for Compliance Scanner
"""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class EmailTemplate:
    """Email template with subject and body"""
    subject: str
    html_body: str
    text_body: str = ""

class EmailTemplates:
    """Collection of email templates"""
    
    @staticmethod
    def welcome(user_name: str, login_url: str) -> EmailTemplate:
        return EmailTemplate(
            subject="🎯 Welcome to Compliance Scanner",
            html_body=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                    .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                    .button {{ display: inline-block; background: #667eea; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; color: #777; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Welcome to Compliance Scanner!</h1>
                        <p>Enterprise-grade compliance automation</p>
                    </div>
                    <div class="content">
                        <h2>Hello {user_name},</h2>
                        <p>Your account has been successfully created. You now have access to our AI-powered compliance scanning platform.</p>
                        
                        <h3>🚀 Get Started:</h3>
                        <ol>
                            <li><strong>Upload Documents</strong> - PDF, DOCX, images</li>
                            <li><strong>AI-Powered Scanning</strong> - Detect compliance violations</li>
                            <li><strong>Automated Reports</strong> - Detailed compliance insights</li>
                            <li><strong>Approval Workflow</strong> - Team collaboration</li>
                        </ol>
                        
                        <p style="text-align: center; margin: 30px 0;">
                            <a href="{login_url}" class="button">Login to Your Account</a>
                        </p>
                        
                        <p>Need help? Check our <a href="https://help.compliancescanner.com">documentation</a> or contact our support team.</p>
                        
                        <div class="footer">
                            <p>This email was sent by Compliance Scanner. Please do not reply to this email.</p>
                            <p>© 2024 Compliance Scanner. All rights reserved.</p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
        )
    
    @staticmethod
    def password_reset(reset_url: str, expiry_hours: int = 24) -> EmailTemplate:
        return EmailTemplate(
            subject="🔐 Password Reset Request - Compliance Scanner",
            html_body=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                    .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                    .button {{ display: inline-block; background: #f5576c; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Password Reset</h1>
                    </div>
                    <div class="content">
                        <h2>Reset Your Password</h2>
                        <p>We received a request to reset your password for your Compliance Scanner account.</p>
                        
                        <div class="warning">
                            <strong>⚠️ Important:</strong> This link will expire in {expiry_hours} hours.
                        </div>
                        
                        <p style="text-align: center; margin: 30px 0;">
                            <a href="{reset_url}" class="button">Reset Your Password</a>
                        </p>
                        
                        <p>If you didn't request a password reset, you can safely ignore this email. Your password won't be changed.</p>
                        
                        <p><strong>Security Tip:</strong> Always ensure you're on the official Compliance Scanner website before entering your credentials.</p>
                        
                        <div class="footer">
                            <p>This link will expire in {expiry_hours} hours.</p>
                            <p>If the button doesn't work, copy and paste this link:</p>
                            <p><code>{reset_url}</code></p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
        )
    
    @staticmethod
    def document_approved(user_name: str, document_name: str, document_id: str, portal_url: str) -> EmailTemplate:
        return EmailTemplate(
            subject=f"✅ Document Approved: {document_name}",
            html_body=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                    .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                    .button {{ display: inline-block; background: #4facfe; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .success {{ background: #d4edda; border-left: 4px solid #28a745; padding: 15px; margin: 20px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🎉 Document Approved!</h1>
                        <p>Your document has passed compliance review</p>
                    </div>
                    <div class="content">
                        <h2>Hello {user_name},</h2>
                        
                        <div class="success">
                            <h3>✅ <strong>{document_name}</strong> has been approved by the compliance team.</h3>
                            <p>Document ID: {document_id}</p>
                        </div>
                        
                        <h3>📋 Next Steps:</h3>
                        <ol>
                            <li><strong>Review</strong> the approval details</li>
                            <li><strong>Download</strong> the compliance certificate</li>
                            <li><strong>Publish</strong> the document to your channels</li>
                        </ol>
                        
                        <p style="text-align: center; margin: 30px 0;">
                            <a href="{portal_url}" class="button">View Document Details</a>
                        </p>
                        
                        <p><strong>Note:</strong> This approval is valid for 12 months from the approval date.</p>
                        
                        <div class="footer">
                            <p>Need to make changes? Upload a new version through the portal.</p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
        )
    
    @staticmethod
    def document_rejected(user_name: str, document_name: str, reasons: list, reviewer_comments: str, portal_url: str) -> EmailTemplate:
        reasons_html = "".join([f"<li>{reason}</li>" for reason in reasons])
        
        return EmailTemplate(
            subject=f"❌ Document Requires Changes: {document_name}",
            html_body=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #ff758c 0%, #ff7eb3 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                    .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                    .button {{ display: inline-block; background: #ff758c; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .alert {{ background: #f8d7da; border-left: 4px solid #dc3545; padding: 15px; margin: 20px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>⚠️ Changes Required</h1>
                        <p>Your document needs compliance adjustments</p>
                    </div>
                    <div class="content">
                        <h2>Hello {user_name},</h2>
                        
                        <div class="alert">
                            <h3>❌ <strong>{document_name}</strong> requires changes before approval.</h3>
                            <p>The compliance team has identified issues that need to be addressed.</p>
                        </div>
                        
                        <h3>🔍 Required Changes:</h3>
                        <ul>
                            {reasons_html}
                        </ul>
                        
                        <h3>💬 Reviewer Comments:</h3>
                        <blockquote style="background: #e9ecef; padding: 15px; border-left: 4px solid #6c757d; margin: 20px 0;">
                            {reviewer_comments}
                        </blockquote>
                        
                        <h3>🛠️ How to Fix:</h3>
                        <ol>
                            <li><strong>Review</strong> each flagged violation</li>
                            <li><strong>Amend</strong> your document based on the AI recommendations</li>
                            <li><strong>Re-upload</strong> the updated version</li>
                            <li><strong>Re-scan</strong> to verify compliance</li>
                        </ol>
                        
                        <p style="text-align: center; margin: 30px 0;">
                            <a href="{portal_url}" class="button">View Detailed Report</a>
                        </p>
                        
                        <p>Need help? Our AI chat assistant can provide specific guidance on fixing these issues.</p>
                        
                        <div class="footer">
                            <p>You have 30 days to resubmit this document.</p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
        )
    
    @staticmethod
    def compliance_alert(
        client_name: str,
        document_name: str,
        violation_count: int,
        risk_score: int,
        critical_count: int,
        document_url: str
    ) -> EmailTemplate:
        return EmailTemplate(
            subject=f"🚨 Compliance Alert: {document_name} - Risk Score: {risk_score}/100",
            html_body=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                    .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                    .button {{ display: inline-block; background: #ff6b6b; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
                    .critical {{ background: #ffe5e5; border: 2px solid #ff6b6b; padding: 15px; margin: 20px 0; }}
                    .stats {{ display: flex; justify-content: space-around; margin: 20px 0; }}
                    .stat-item {{ text-align: center; }}
                    .stat-value {{ font-size: 24px; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🚨 COMPLIANCE ALERT</h1>
                        <p>High-risk document requires immediate attention</p>
                    </div>
                    <div class="content">
                        <h2>Attention: {client_name}</h2>
                        
                        <div class="critical">
                            <h3>⚠️ URGENT ACTION REQUIRED</h3>
                            <p><strong>{document_name}</strong> contains serious compliance violations.</p>
                            <p><strong>DO NOT PUBLISH</strong> this document in its current form.</p>
                        </div>
                        
                        <div class="stats">
                            <div class="stat-item">
                                <div class="stat-value" style="color: #ff6b6b;">{risk_score}</div>
                                <div>Risk Score</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-value" style="color: #ff6b6b;">{violation_count}</div>
                                <div>Total Violations</div>
                            </div>
                            <div class="stat-item">
                                <div class="stat-value" style="color: #ff6b6b;">{critical_count}</div>
                                <div>Critical Issues</div>
                            </div>
                        </div>
                        
                        <h3>🔍 Immediate Actions:</h3>
                        <ol>
                            <li><strong>Review</strong> all flagged violations</li>
                            <li><strong>Contact</strong> your compliance officer</li>
                            <li><strong>Do not publish</strong> until resolved</li>
                            <li><strong>Consider</strong> DLA Piper escalation if needed</li>
                        </ol>
                        
                        <p style="text-align: center; margin: 30px 0;">
                            <a href="{document_url}" class="button">Review Document</a>
                        </p>
                        
                        <div class="footer">
                            <p><strong>⚠️ Regulatory Risk:</strong> Publishing this document could result in regulatory action and fines.</p>
                            <p><strong>⏰ Time Sensitivity:</strong> Address within 48 hours to prevent workflow delays.</p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
        )