import logging
import re

from .schema import NEW_KEY_PATTERNS, build_key  # type: ignore
from .ttl import TTLCategory, get_ttl_by_key_type  # type: ignore


class SchemaValidationMixin:
    """
    Mixin to validate Redis key formats and infer TTL for keys.
    - validate_key_format(key): returns bool, logs warning on invalid key
    - get_ttl_for_key(key): returns int TTL in seconds or None if not determinable
    - __init__: ensures logger exists; designed to be used with multiple inheritance
    """

    def __init__(self, *args, **kwargs):
        # initialize mixin state first by calling superclass __init__
        super().__init__(*args, **kwargs)
        self._logger = logging.getLogger(self.__class__.__name__)
        # Optional: cache for TTL inference
        self._ttl_cache = {}

    def validate_key_format(self, key: str) -> bool:
        """
        Validate Redis key format against NEW_KEY_PATTERNS.
        Returns True if key matches any known pattern, False otherwise.
        Logs a warning when validation fails to avoid breaking behavior.
        """
        if not isinstance(key, str) or key == "":
            self._logger.warning("SchemaValidation: invalid key (non-string or empty): %r", key)
            return False
        # If NEW_KEY_PATTERNS is a dict mapping category name to regex pattern
        patterns = NEW_KEY_PATTERNS
        ok = False
        if isinstance(patterns, dict):
            for _name, pat in patterns.items():
                try:
                    if re.fullmatch(pat, key):
                        ok = True
                        break
                except re.error:
                    # skip invalid regex patterns
                    continue
        elif isinstance(patterns, (list, tuple)):
            for pat in patterns:
                try:
                    if re.fullmatch(pat, key):
                        ok = True
                        break
                except re.error:
                    continue
        else:
            # Fallback: attempt to build key examples from schema
            try:
                # best-effort: if key starts with any known base types
                for pat in (getattr(build_key, "__call__", lambda: None)(),):
                    if pat and isinstance(pat, str) and key.startswith(pat):
                        ok = True
                        break
            except Exception:
                pass
        if not ok:
            self._logger.warning("SchemaValidation: key format invalid: %s", key)
        return ok

    def _infer_ttl_category(self, key: str) -> TTLCategory | None:
        """
        Infer TTL category from key using NEW_KEY_PATTERNS mapping if possible.
        Returns a TTLCategory or None if undeterminable.
        """
        if not isinstance(key, str):
            return None
        patterns = NEW_KEY_PATTERNS
        if isinstance(patterns, dict):
            for name, pat in patterns.items():
                try:
                    if re.fullmatch(pat, key):
                        try:
                            return TTLCategory[name]
                        except Exception:
                            return None
                except re.error:
                    continue
        return None

    def get_ttl_for_key(self, key: str) -> int | None:
        """
        Determine TTL (in seconds) for a given key based on inferred TTL category.
        Returns int TTL or None if TTL cannot be determined.
        """
        if not isinstance(key, str) or key == "":
            return None
        if key in self._ttl_cache:
            return self._ttl_cache[key]
        cat = self._infer_ttl_category(key)
        if cat is None:
            return None
        try:
            ttl = get_ttl_by_key_type(cat)
            # Cache for future calls
            self._ttl_cache[key] = int(ttl) if ttl is not None else None
            return ttl
        except Exception as e:
            self._logger.warning("SchemaValidation: TTL lookup failed for key %s: %s", key, e)
            return None
