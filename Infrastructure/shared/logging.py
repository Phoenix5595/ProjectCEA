"""Structured logging utilities for automation service."""
from __future__ import annotations

import logging
import sys
import json
from typing import Optional, Any, Dict
from datetime import datetime
from contextlib import contextmanager
import threading


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""
    
    def __init__(self, service_name: str = "automation-service"):
        super().__init__()
        self.service_name = service_name
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add context from LoggingContext if available
        context = LoggingContext.get_context()
        if context:
            log_data["context"] = context
        
        # Add extra fields if any
        if hasattr(record, 'extra') and record.extra:
            log_data["extra"] = record.extra
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """Console formatter with colored output."""
    
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record for console output."""
        color = self.COLORS.get(record.levelname, '')
        reset = self.RESET if color else ''
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Add context from LoggingContext if available
        context_str = ""
        context = LoggingContext.get_context()
        if context:
            context_parts = [f"{k}={v}" for k, v in context.items()]
            context_str = f" [{', '.join(context_parts)}]"
        
        return f"{timestamp} {color}{record.levelname:8}{reset} [{record.name}]{context_str} {record.getMessage()}"


class LoggingContext:
    """Thread-local context for structured logging.
    
    Allows adding context (like correlation IDs) that will be included
    in all log messages within the context.
    
    Usage:
        with LoggingContext(request_id="abc123", user="admin"):
            logger.info("Processing request")  # Includes request_id and user
    """
    
    _context = threading.local()
    
    def __init__(self, **kwargs):
        """Initialize context with key-value pairs."""
        self.context_data = kwargs
        self._previous = None
    
    def __enter__(self):
        """Enter context and store previous context."""
        self._previous = getattr(self._context, 'data', {}).copy()
        current = self._previous.copy()
        current.update(self.context_data)
        self._context.data = current
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and restore previous context."""
        self._context.data = self._previous or {}
        return False
    
    @classmethod
    def get_context(cls) -> Dict[str, Any]:
        """Get current logging context."""
        return getattr(cls._context, 'data', {})
    
    @classmethod
    def set_context(cls, **kwargs):
        """Set context values (not recommended, use context manager instead)."""
        if not hasattr(cls._context, 'data'):
            cls._context.data = {}
        cls._context.data.update(kwargs)
    
    @classmethod
    def clear_context(cls):
        """Clear all context."""
        cls._context.data = {}


class StructuredLogger(logging.Logger):
    """Logger with support for structured logging and context."""
    
    def __init__(self, name: str, level: int = logging.NOTSET):
        super().__init__(name, level)
    
    def _log_with_context(
        self,
        level: int,
        msg: str,
        args: tuple,
        exc_info: Any = None,
        extra: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Log with context support."""
        if extra is None:
            extra = {}
        
        # Add any keyword arguments to extra
        if kwargs:
            extra.update(kwargs)
        
        # Call parent _log
        super()._log(level, msg, args, exc_info=exc_info, extra=extra)


# Global logger cache
_loggers: Dict[str, logging.Logger] = {}
_root_logger: Optional[logging.Logger] = None


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger with the given name.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Configured logger instance
    """
    if name in _loggers:
        return _loggers[name]
    
    logger = logging.getLogger(name)
    _loggers[name] = logger
    return logger


def setup_structured_logging(
    service_name: str = "automation-service",
    log_level: str = "INFO",
    console_output: bool = True,
    json_format: bool = True
) -> logging.Logger:
    """Configure structured logging for the service.
    
    Args:
        service_name: Name of the service for log identification
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_output: Whether to output to console
        json_format: Whether to use JSON format (False for readable console)
    
    Returns:
        Root logger instance
    """
    global _root_logger
    
    # Get root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Add console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        
        if json_format:
            console_handler.setFormatter(JsonFormatter(service_name))
        else:
            console_handler.setFormatter(ConsoleFormatter())
        
        root.addHandler(console_handler)
    
    _root_logger = root
    
    # Log startup message
    root.info(f"Structured logging initialized for {service_name}")
    
    return root
