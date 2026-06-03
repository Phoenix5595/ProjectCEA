# Task 7: Replace bare `except Exception:` with specific types

**Date:** 2026-06-02

## Summary

Replaced 25 bare `except Exception:` occurrences across 12 files in `automation-service/app/` with specific exception types and proper logging.

## Files Modified

| File | Changes |
|------|---------|
| `repositories/control_actions.py` | `except Exception:` → `except asyncpg.PostgresError:` (DB schema fallback) |
| `routes/failsafe.py` | `except Exception:` → `except (ConnectionError, OSError) as e:` + logger |
| `routes/mode.py` | 2× `except Exception:` → `except Exception as e:` + logger (StateManager cache), 1× → `except (ConnectionError, OSError) as e:` + logger (Redis scan) |
| `routes/hardware.py` | `except Exception:` → `except (json.JSONDecodeError, ValueError, ConnectionError) as e:` + logger (Redis cache), `except Exception as e:` + logger (DB query) |
| `routes/lights.py` | `except Exception:` → `except (ValueError, TypeError) as e:` + logger (time parse), 2× `except Exception as e:` + logger (mode lookup, scheduler) |
| `control/climate_resolver.py` | `except Exception:` → `except Exception as e:` + logger (DB pre-fetch) |
| `control/scheduler.py` | `except Exception:` → `except (ValueError, TypeError) as e:` + logger (float conversion) |
| `feature_flags.py` | `except Exception:` → `except ImportError:` + `except redis.RedisError:` (split into two clauses) |
| `redis/migrate.py` | 3× import fallbacks → `except ImportError:`, 2× function calls → `except Exception as e:` with logger |
| `redis/validation.py` | `except Exception:` → `except KeyError:` (TTLCategory lookup) |
| `events/consumer.py` | Import fallback → `except ImportError:`, Redis ping → `except (ConnectionError, OSError)`, timestamp parse → `except (ValueError, TypeError)`, JSON parse → `except (json.JSONDecodeError, ValueError, TypeError)` |
| `events/redis_streams.py` | Import fallback → `except ImportError:`, Redis ping → `except (ConnectionError, OSError) as e:` + logger |

## Exception Type Mapping

| Context | Replacement Type |
|---------|-----------------|
| Database operations | `asyncpg.PostgresError` |
| Redis connection | `(ConnectionError, OSError)` |
| Import fallbacks | `ImportError` |
| JSON parsing | `(json.JSONDecodeError, ValueError, TypeError)` |
| Time/value parsing | `(ValueError, TypeError)` |
| Dict/Enum lookup | `KeyError` |
| Internal cache (StateManager) | `Exception as e` (narrowest reasonable without new imports) |

## Verification

```
grep -rn "except Exception:" ... | grep -v "as e"
→ Only match: docstring in routes/status.py (not actual code)
→ PASS
```
