"""Redis Stream reader utility for querying sensor data from Redis Stream.

Supports reading from unified sensor:raw stream with filtering by type (can/soil) and time range.
"""
import redis
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RedisStreamReader:
    """Reads sensor data from Redis Stream by time range and type."""
    
    def __init__(self, redis_url: str = None, stream_name: str = "sensor:raw"):
        """Initialize Redis Stream reader.
        
        Args:
            redis_url: Redis connection URL (default: from env or localhost)
            stream_name: Name of the Redis Stream (default: 'sensor:raw')
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.stream_name = stream_name
        self.client: Optional[redis.Redis] = None
    
    def connect(self) -> bool:
        """Connect to Redis.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=False,  # Keep binary for stream reads
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.client.ping()
            logger.info(f"Connected to Redis Stream: {self.stream_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            return False
    
    def get_stream_length(self) -> int:
        """Get current length of the stream.
        
        Returns:
            Number of entries in stream, or 0 if error
        """
        if not self.client:
            if not self.connect():
                return 0
        
        try:
            return self.client.xlen(self.stream_name)
        except Exception as e:
            logger.warning(f"Error getting stream length: {e}")
            return 0
    
    def read_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        sensor_type: Optional[str] = None,
        max_count: int = 20000
    ) -> List[Dict[str, Any]]:
        """Read stream entries within a time range.
        
        Args:
            start_time: Start timestamp
            end_time: End timestamp
            sensor_type: Filter by type ('can' or 'soil'), None for all
            max_count: Maximum number of entries to read (default: 20000)
        
        Returns:
            List of decoded stream entries matching the criteria
        """
        if not self.client:
            if not self.connect():
                return []
        
        try:
            # Convert timestamps to milliseconds for comparison
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            
            # Read entries in-range using stream IDs to avoid full scans
            max_id = f"{end_ms}-9999"
            min_id = f"{start_ms}-0"
            entries = self.client.xrevrange(
                self.stream_name,
                max=max_id,
                min=min_id,
                count=max_count
            )
            
            if not entries:
                return []
            
            results = []
            for entry_id, fields in entries:
                # Extract timestamp from stream entry
                ts_bytes = fields.get(b'ts')
                if not ts_bytes:
                    continue
                
                try:
                    entry_ts_ms = int(ts_bytes.decode('utf-8'))
                except (ValueError, AttributeError):
                    continue
                
                # Filter by time range
                if entry_ts_ms < start_ms or entry_ts_ms > end_ms:
                    continue
                
                # Filter by type if specified
                if sensor_type:
                    entry_type = fields.get(b'type')
                    if not entry_type:
                        continue
                    try:
                        entry_type_str = entry_type.decode('utf-8')
                        if entry_type_str != sensor_type:
                            continue
                    except (AttributeError, UnicodeDecodeError):
                        continue
                
                # Decode entry
                decoded_entry = self._decode_stream_entry(entry_id, fields)
                if decoded_entry:
                    results.append(decoded_entry)
            
            # Sort by timestamp (oldest first)
            results.sort(key=lambda x: x.get('timestamp_ms', 0))
            
            return results
        
        except Exception as e:
            logger.error(f"Error reading from Redis Stream: {e}")
            return []
    
    def _decode_stream_entry(self, entry_id: bytes, fields: Dict[bytes, bytes]) -> Optional[Dict[str, Any]]:
        """Decode a stream entry to a dictionary.
        
        Args:
            entry_id: Stream entry ID
            fields: Stream entry fields
        
        Returns:
            Decoded entry dictionary, or None if decoding fails
        """
        try:
            entry_id_str = entry_id.decode('utf-8') if isinstance(entry_id, bytes) else str(entry_id)
            
            # Extract timestamp
            ts_bytes = fields.get(b'ts')
            ts_ms = None
            if ts_bytes:
                try:
                    ts_ms = int(ts_bytes.decode('utf-8'))
                except (ValueError, AttributeError):
                    pass
            
            # Extract type
            type_bytes = fields.get(b'type')
            entry_type = None
            if type_bytes:
                try:
                    entry_type = type_bytes.decode('utf-8')
                except (AttributeError, UnicodeDecodeError):
                    pass
            
            # Extract decoded data if available
            decoded_data = None
            decoded_bytes = fields.get(b'decoded')
            if decoded_bytes:
                try:
                    decoded_str = decoded_bytes.decode('utf-8')
                    decoded_data = json.loads(decoded_str)
                except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                    pass
            
            # Extract raw data
            raw_data = None
            data_bytes = fields.get(b'data')
            if data_bytes:
                try:
                    raw_data = data_bytes.decode('utf-8')
                except (AttributeError, UnicodeDecodeError):
                    pass
            
            return {
                'id': entry_id_str,
                'timestamp_ms': ts_ms,
                'type': entry_type,
                'raw_data': raw_data,
                'decoded': decoded_data
            }
        
        except Exception as e:
            logger.debug(f"Error decoding stream entry: {e}")
            return None
    
    def close(self):
        """Close Redis connection."""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

