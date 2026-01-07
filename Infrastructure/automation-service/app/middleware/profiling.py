"""Request profiling middleware for FastAPI."""
import time
from typing import Callable
from fastapi import Request, Response
from shared.logging import get_logger

logger = get_logger(__name__)

# Performance metrics storage
_performance_metrics = {
    'request_times': [],
    'slow_requests': [],  # Requests > 1 second
    'total_requests': 0,
}


async def profiling_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware to profile request performance.
    
    Args:
        request: FastAPI request object
        call_next: Next middleware/handler in chain
    
    Returns:
        Response from next handler
    """
    start_time = time.time()
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Track metrics
        _performance_metrics['total_requests'] += 1
        _performance_metrics['request_times'].append(process_time)
        
        # Keep only last 1000 request times
        if len(_performance_metrics['request_times']) > 1000:
            _performance_metrics['request_times'] = _performance_metrics['request_times'][-1000:]
        
        # Log slow requests
        if process_time > 1.0:
            _performance_metrics['slow_requests'].append({
                'path': request.url.path,
                'method': request.method,
                'time': process_time,
                'timestamp': time.time()
            })
            # Keep only last 100 slow requests
            if len(_performance_metrics['slow_requests']) > 100:
                _performance_metrics['slow_requests'] = _performance_metrics['slow_requests'][-100:]
            
            logger.warning(
                f"Slow request detected: {request.method} {request.url.path} "
                f"took {process_time:.3f}s"
            )
        
        # Add timing header
        response.headers["X-Process-Time"] = str(process_time)
        
        return response
    
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(
            f"Request error: {request.method} {request.url.path} "
            f"failed after {process_time:.3f}s: {e}"
        )
        raise


def get_performance_metrics() -> dict:
    """Get current performance metrics.
    
    Returns:
        Dictionary with performance statistics
    """
    request_times = _performance_metrics['request_times']
    
    if not request_times:
        return {
            'total_requests': _performance_metrics['total_requests'],
            'average_time': 0.0,
            'p95_time': 0.0,
            'p99_time': 0.0,
            'slow_requests_count': len(_performance_metrics['slow_requests']),
            'slow_requests': _performance_metrics['slow_requests'][-10:]  # Last 10
        }
    
    sorted_times = sorted(request_times)
    n = len(sorted_times)
    
    return {
        'total_requests': _performance_metrics['total_requests'],
        'average_time': sum(request_times) / n,
        'p95_time': sorted_times[int(n * 0.95)] if n > 0 else 0.0,
        'p99_time': sorted_times[int(n * 0.99)] if n > 0 else 0.0,
        'slow_requests_count': len(_performance_metrics['slow_requests']),
        'slow_requests': _performance_metrics['slow_requests'][-10:]  # Last 10 slow requests
    }
