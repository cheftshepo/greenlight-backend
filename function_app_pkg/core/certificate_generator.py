"""
COMPLIANCE CERTIFICATE GENERATOR - PRODUCTION READY
===================================================
Professional PDF certificates with QR verification

IMPROVEMENTS:
- Proper error handling with custom exceptions
- Separated concerns (generation, storage, validation)
- Type hints throughout
- Configuration management
- Comprehensive logging
- Input validation
- Better resource management

File: function_app_pkg/core/certificate_generator.py
"""

import logging
import os
import io
import qrcode
import uuid
from datetime import datetime
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, 
    Table, TableStyle, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class CertificateError(Exception):
    """Base exception for certificate operations"""
    pass


class CertificateGenerationError(CertificateError):
    """Raised when PDF generation fails"""
    pass


class CertificateStorageError(CertificateError):
    """Raised when storage operations fail"""
    pass


class CertificateValidationError(CertificateError):
    """Raised when input validation fails"""
    pass


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class CertificateConfig:
    """Certificate generation configuration"""
    verification_url_base: str
    storage_container: str = "certificates"
    page_size: tuple = A4
    margin: float = inch
    qr_size: float = 1.5 * inch
    title_font_size: int = 24
    heading_font_size: int = 14
    
    @classmethod
    def from_env(cls) -> 'CertificateConfig':
        """Load config from environment variables"""
        return cls(
            verification_url_base=os.getenv(
                'CERTIFICATE_VERIFICATION_URL',
                'https://compliance.mattmurdock.ai/verify'
            )
        )


class ComplianceOutcome(str, Enum):
    """Standard compliance outcomes"""
    COMPLIANT = "compliant"
    REQUIRES_REVIEW = "requires_review"
    NON_COMPLIANT = "non_compliant"
    UNKNOWN = "unknown"


# =============================================================================
# CERTIFICATE DATA MODEL
# =============================================================================

@dataclass
class CertificateData:
    """Validated certificate input data"""
    document_id: str
    document_filename: str
    organization_name: str
    jurisdiction: str
    compliance_outcome: ComplianceOutcome
    risk_score: int
    scan_date: str
    violations_count: int
    reviewer_name: str = ""
    reviewer_email: str = ""
    organization_logo_url: str = ""
    notes: str = ""
    
    def __post_init__(self):
        """Validate inputs after initialization"""
        self._validate()
    
    def _validate(self):
        """Validate all fields"""
        if not self.document_id:
            raise CertificateValidationError("document_id is required")
        
        if not self.document_filename:
            raise CertificateValidationError("document_filename is required")
        
        if not self.organization_name:
            raise CertificateValidationError("organization_name is required")
        
        if self.risk_score < 0 or self.risk_score > 100:
            raise CertificateValidationError("risk_score must be 0-100")
        
        if self.violations_count < 0:
            raise CertificateValidationError("violations_count must be >= 0")
        
        # Validate outcome enum
        if isinstance(self.compliance_outcome, str):
            try:
                self.compliance_outcome = ComplianceOutcome(self.compliance_outcome)
            except ValueError:
                raise CertificateValidationError(
                    f"Invalid compliance_outcome: {self.compliance_outcome}"
                )


@dataclass
class CertificateResult:
    """Certificate generation result"""
    certificate_id: str
    pdf_bytes: bytes
    pdf_size: int
    generated_at: str
    verification_url: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response"""
        return {
            'certificate_id': self.certificate_id,
            'pdf_size_bytes': self.pdf_size,
            'generated_at': self.generated_at,
            'verification_url': self.verification_url
        }


# =============================================================================
# PDF GENERATION ENGINE
# =============================================================================

class PDFGenerator:
    """Handles PDF creation logic"""
    
    def __init__(self, config: CertificateConfig):
        self.config = config
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Define custom paragraph styles"""
        self.title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=self.config.title_font_size,
            textColor=colors.HexColor('#1a5490'),
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        self.heading_style = ParagraphStyle(
            'CustomHeading',
            parent=self.styles['Heading2'],
            fontSize=self.config.heading_font_size,
            textColor=colors.HexColor('#333333'),
            spaceAfter=12
        )
        
        self.footer_style = ParagraphStyle(
            'Footer',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.grey,
            alignment=TA_CENTER
        )
    
    def generate(
        self, 
        cert_data: CertificateData, 
        cert_id: str
    ) -> bytes:
        """Generate PDF bytes"""
        try:
            buffer = io.BytesIO()
            
            doc = SimpleDocTemplate(
                buffer,
                pagesize=self.config.page_size,
                rightMargin=self.config.margin,
                leftMargin=self.config.margin,
                topMargin=self.config.margin,
                bottomMargin=self.config.margin
            )
            
            story = self._build_story(cert_data, cert_id)
            doc.build(story)
            
            pdf_bytes = buffer.getvalue()
            buffer.close()
            
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"PDF generation failed: {e}", exc_info=True)
            raise CertificateGenerationError(f"Failed to generate PDF: {e}") from e
    
    def _build_story(self, cert_data: CertificateData, cert_id: str) -> list:
        """Build PDF content elements"""
        story = []
        
        # Title
        story.append(Paragraph("COMPLIANCE CERTIFICATE", self.title_style))
        story.append(Spacer(1, 0.3 * inch))
        
        # Certificate info table
        story.extend(self._create_info_section(cert_id, cert_data))
        story.append(Spacer(1, 0.4 * inch))
        
        # Organization
        story.extend(self._create_org_section(cert_data))
        story.append(Spacer(1, 0.3 * inch))
        
        # Document details
        story.extend(self._create_document_section(cert_data))
        story.append(Spacer(1, 0.3 * inch))
        
        # Compliance status
        story.extend(self._create_status_section(cert_data))
        story.append(Spacer(1, 0.3 * inch))
        
        # Scan results
        story.extend(self._create_results_section(cert_data))
        story.append(Spacer(1, 0.3 * inch))
        
        # Reviewer (if present)
        if cert_data.reviewer_name:
            story.extend(self._create_reviewer_section(cert_data))
            story.append(Spacer(1, 0.3 * inch))
        
        # Notes (if present)
        if cert_data.notes:
            story.extend(self._create_notes_section(cert_data))
            story.append(Spacer(1, 0.3 * inch))
        
        # QR code
        story.append(Spacer(1, 0.5 * inch))
        story.extend(self._create_qr_section(cert_id))
        
        # Footer
        story.append(Spacer(1, 0.5 * inch))
        story.append(self._create_footer())
        
        return story
    
    def _create_info_section(self, cert_id: str, cert_data: CertificateData) -> list:
        """Create certificate info table"""
        info_data = [
            ["Certificate ID:", cert_id],
            ["Issue Date:", datetime.utcnow().strftime("%B %d, %Y")],
            ["Jurisdiction:", cert_data.jurisdiction]
        ]
        
        table = Table(info_data, colWidths=[2*inch, 3.5*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (0,-1), colors.grey),
            ('TEXTCOLOR', (1,0), (1,-1), colors.black),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        
        return [table]
    
    def _create_org_section(self, cert_data: CertificateData) -> list:
        """Create organization section"""
        return [
            Paragraph("Organization", self.heading_style),
            Paragraph(cert_data.organization_name, self.styles['Normal'])
        ]
    
    def _create_document_section(self, cert_data: CertificateData) -> list:
        """Create document details section"""
        return [
            Paragraph("Document Reviewed", self.heading_style),
            Paragraph(
                f"<b>Filename:</b> {cert_data.document_filename}", 
                self.styles['Normal']
            ),
            Paragraph(
                f"<b>Document ID:</b> {cert_data.document_id}", 
                self.styles['Normal']
            )
        ]
    
    def _create_status_section(self, cert_data: CertificateData) -> list:
        """Create compliance status section with color coding"""
        outcome = cert_data.compliance_outcome
        
        if outcome == ComplianceOutcome.COMPLIANT:
            status_color = colors.green
            status_text = "✓ COMPLIANT"
            status_desc = "This document meets all regulatory requirements."
        elif outcome == ComplianceOutcome.REQUIRES_REVIEW:
            status_color = colors.orange
            status_text = "⚠ REVIEW REQUIRED"
            status_desc = "Minor issues detected. Review recommended."
        else:
            status_color = colors.red
            status_text = "✗ NON-COMPLIANT"
            status_desc = "Critical violations detected. Immediate action required."
        
        status_style = ParagraphStyle(
            'Status',
            parent=self.styles['Normal'],
            fontSize=16,
            textColor=status_color,
            fontName='Helvetica-Bold'
        )
        
        return [
            Paragraph("Compliance Status", self.heading_style),
            Paragraph(status_text, status_style),
            Spacer(1, 0.1*inch),
            Paragraph(status_desc, self.styles['Normal'])
        ]
    
    def _create_results_section(self, cert_data: CertificateData) -> list:
        """Create scan results section"""
        results_data = [
            ["Risk Score:", f"{cert_data.risk_score}/100"],
            ["Violations Found:", str(cert_data.violations_count)],
            ["Scan Date:", cert_data.scan_date]
        ]
        
        table = Table(results_data, colWidths=[2*inch, 3.5*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (0,-1), colors.grey),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        
        return [
            Paragraph("Scan Results", self.heading_style),
            table
        ]
    
    def _create_reviewer_section(self, cert_data: CertificateData) -> list:
        """Create reviewer section"""
        elements = [
            Paragraph("Reviewed By", self.heading_style),
            Paragraph(f"<b>{cert_data.reviewer_name}</b>", self.styles['Normal'])
        ]
        
        if cert_data.reviewer_email:
            elements.append(
                Paragraph(cert_data.reviewer_email, self.styles['Normal'])
            )
        
        return elements
    
    def _create_notes_section(self, cert_data: CertificateData) -> list:
        """Create notes section"""
        return [
            Paragraph("Additional Notes", self.heading_style),
            Paragraph(cert_data.notes, self.styles['Normal'])
        ]
    
    def _create_qr_section(self, cert_id: str) -> list:
        """Create QR code section"""
        qr_url = f"{self.config.verification_url_base}/{cert_id}"
        
        # Generate QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to bytes
        qr_buffer = io.BytesIO()
        qr_image.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)
        
        qr_img = RLImage(qr_buffer, width=self.config.qr_size, height=self.config.qr_size)
        
        verify_style = ParagraphStyle(
            'Verify',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.grey,
            alignment=TA_CENTER
        )
        
        return [
            Paragraph("Certificate Verification", self.heading_style),
            qr_img,
            Spacer(1, 0.1*inch),
            Paragraph(f"Scan to verify: {qr_url}", verify_style)
        ]
    
    def _create_footer(self) -> Paragraph:
        """Create footer"""
        return Paragraph(
            "This certificate was generated by    Compliance Platform<br/>"
            "Certificate ID can be verified at compliance.mattmurdock.ai",
            self.footer_style
        )


# =============================================================================
# STORAGE MANAGER
# =============================================================================

class CertificateStorageManager:
    """Handles certificate storage operations"""
    
    def __init__(self, config: CertificateConfig):
        self.config = config
        self._storage_client = None
    
    @property
    def storage_client(self):
        """Lazy load storage client"""
        if self._storage_client is None:
            connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
            if not connection_string:
                raise CertificateStorageError(
                    "AZURE_STORAGE_CONNECTION_STRING not configured"
                )
            
            from azure.storage.blob import BlobServiceClient
            self._storage_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        
        return self._storage_client
    
    def save_to_blob(
        self, 
        pdf_bytes: bytes, 
        cert_id: str, 
        org_id: str
    ) -> str:
        """
        Save certificate to Azure Blob Storage
        
        Returns:
            Blob URL
        """
        try:
            # Ensure container exists
            container_client = self._ensure_container_exists()
            
            # Upload with org-specific path
            blob_name = f"{org_id}/{cert_id}.pdf"
            blob_client = self.storage_client.get_blob_client(
                self.config.storage_container, 
                blob_name
            )
            
            blob_client.upload_blob(pdf_bytes, overwrite=True)
            
            blob_url = blob_client.url
            logger.info(f"✅ Certificate saved to blob: {blob_url}")
            
            return blob_url
            
        except Exception as e:
            logger.error(f"Blob upload failed: {e}", exc_info=True)
            raise CertificateStorageError(f"Failed to save certificate: {e}") from e
    
    def _ensure_container_exists(self):
        """Ensure storage container exists"""
        try:
            container_client = self.storage_client.get_container_client(
                self.config.storage_container
            )
            container_client.get_container_properties()
            return container_client
        except Exception:
            logger.info(f"Creating container: {self.config.storage_container}")
            return self.storage_client.create_container(
                self.config.storage_container
            )
    
    def store_certificate_record(
        self,
        cert_id: str,
        document_id: str,
        org_id: str,
        blob_url: str,
        issued_by: str,
        metadata: Dict
    ) -> bool:
        """Store certificate record in database for verification"""
        try:
            from function_app_pkg.core.database import get_db
            
            db = get_db()
            container = db.get_container('documents')
            
            # Get document
            doc = container.read_item(document_id, partition_key=org_id)
            
            # Initialize certificates array if needed
            if 'certificates' not in doc:
                doc['certificates'] = []
            
            # Add certificate record
            doc['certificates'].append({
                'certificate_id': cert_id,
                'issued_at': datetime.utcnow().isoformat() + 'Z',
                'issued_by': issued_by,
                'blob_url': blob_url,
                'metadata': metadata
            })
            
            # Update document
            container.replace_item(document_id, doc)
            
            logger.info(f"✅ Certificate record stored: {cert_id}")
            return True
            
        except Exception as e:
            logger.error(f"Certificate storage failed: {e}", exc_info=True)
            raise CertificateStorageError(
                f"Failed to store certificate record: {e}"
            ) from e


# =============================================================================
# MAIN CERTIFICATE GENERATOR
# =============================================================================

class ComplianceCertificateGenerator:
    """
    Main certificate generator with proper error handling
    
    Features:
    - Input validation
    - Comprehensive error handling
    - Proper resource management
    - Separation of concerns
    """
    
    def __init__(self, config: Optional[CertificateConfig] = None):
        self.config = config or CertificateConfig.from_env()
        self.pdf_generator = PDFGenerator(self.config)
        self.storage_manager = CertificateStorageManager(self.config)
        
        logger.info("✅ Certificate generator initialized")
    
    def generate_certificate(
        self,
        document_id: str,
        document_filename: str,
        organization_name: str,
        jurisdiction: str,
        compliance_outcome: str,
        risk_score: int,
        scan_date: str,
        violations_count: int,
        reviewer_name: str = "",
        reviewer_email: str = "",
        organization_logo_url: str = "",
        notes: str = ""
    ) -> Tuple[bytes, str]:
        """
        Generate PDF compliance certificate
        
        Args:
            document_id: Unique document identifier
            document_filename: Name of the document file
            organization_name: Organization name
            jurisdiction: Regulatory jurisdiction (e.g., 'UK', 'US')
            compliance_outcome: Compliance result
            risk_score: Risk score (0-100)
            scan_date: ISO format scan date
            violations_count: Number of violations found
            reviewer_name: Optional reviewer name
            reviewer_email: Optional reviewer email
            organization_logo_url: Optional logo URL
            notes: Optional additional notes
        
        Returns:
            Tuple of (PDF bytes, certificate ID)
        
        Raises:
            CertificateValidationError: If inputs are invalid
            CertificateGenerationError: If PDF generation fails
        """
        try:
            # Validate and create data object
            cert_data = CertificateData(
                document_id=document_id,
                document_filename=document_filename,
                organization_name=organization_name,
                jurisdiction=jurisdiction,
                compliance_outcome=compliance_outcome,
                risk_score=risk_score,
                scan_date=scan_date,
                violations_count=violations_count,
                reviewer_name=reviewer_name,
                reviewer_email=reviewer_email,
                organization_logo_url=organization_logo_url,
                notes=notes
            )
            
            # Generate unique certificate ID
            cert_id = f"CERT-{uuid.uuid4().hex[:12].upper()}"
            
            # Generate PDF
            pdf_bytes = self.pdf_generator.generate(cert_data, cert_id)
            
            logger.info(
                f"✅ Certificate generated: {cert_id} "
                f"({len(pdf_bytes)} bytes)"
            )
            
            return pdf_bytes, cert_id
            
        except CertificateValidationError:
            raise
        except CertificateGenerationError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating certificate: {e}", exc_info=True)
            raise CertificateGenerationError(
                f"Unexpected error: {e}"
            ) from e
    
    def generate_and_store(
        self,
        document_id: str,
        document_filename: str,
        organization_id: str,
        organization_name: str,
        jurisdiction: str,
        compliance_outcome: str,
        risk_score: int,
        scan_date: str,
        violations_count: int,
        issued_by: str,
        reviewer_name: str = "",
        reviewer_email: str = "",
        notes: str = ""
    ) -> CertificateResult:
        """
        Generate certificate and save to storage
        
        This is the main method for the API endpoint
        
        Returns:
            CertificateResult with all details
        """
        try:
            # Generate PDF
            pdf_bytes, cert_id = self.generate_certificate(
                document_id=document_id,
                document_filename=document_filename,
                organization_name=organization_name,
                jurisdiction=jurisdiction,
                compliance_outcome=compliance_outcome,
                risk_score=risk_score,
                scan_date=scan_date,
                violations_count=violations_count,
                reviewer_name=reviewer_name,
                reviewer_email=reviewer_email,
                notes=notes
            )
            
            # Save to blob storage
            blob_url = self.storage_manager.save_to_blob(
                pdf_bytes, 
                cert_id, 
                organization_id
            )
            
            # Store database record
            self.storage_manager.store_certificate_record(
                cert_id=cert_id,
                document_id=document_id,
                org_id=organization_id,
                blob_url=blob_url,
                issued_by=issued_by,
                metadata={
                    'reviewer_name': reviewer_name,
                    'reviewer_email': reviewer_email,
                    'notes': notes
                }
            )
            
            # Return result
            return CertificateResult(
                certificate_id=cert_id,
                pdf_bytes=pdf_bytes,
                pdf_size=len(pdf_bytes),
                generated_at=datetime.utcnow().isoformat() + 'Z',
                verification_url=f"{self.config.verification_url_base}/{cert_id}"
            )
            
        except (CertificateError, Exception) as e:
            logger.error(f"Failed to generate and store certificate: {e}")
            raise


# =============================================================================
# GLOBAL INSTANCE & BACKWARD COMPATIBILITY
# =============================================================================

# Singleton instance
_certificate_generator_instance = None

def get_certificate_generator() -> ComplianceCertificateGenerator:
    """Get singleton certificate generator instance"""
    global _certificate_generator_instance
    if _certificate_generator_instance is None:
        _certificate_generator_instance = ComplianceCertificateGenerator()
    return _certificate_generator_instance

# Backward compatibility
certificate_generator = get_certificate_generator()
CertificateGenerator = ComplianceCertificateGenerator