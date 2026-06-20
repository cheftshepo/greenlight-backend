# function_app_pkg/core/audit_decorator.py
"""
Decorator for automatic audit logging of API actions
"""
import logging
import functools
from typing import Callable
from .audit_repository import AuditRepository

logger = logging.getLogger(__name__)
audit_repo = AuditRepository()

def audit_action(action_type: str):
    """
    Decorator to automatically log API actions to audit trail
    Usage: @audit_action("document_uploaded")
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Extract request from args (usually first arg in Azure Functions)
            req = args[0]