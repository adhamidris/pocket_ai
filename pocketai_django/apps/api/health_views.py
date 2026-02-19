"""Health check endpoints for load balancers and monitoring."""

from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
import time

from core.cache_resilience import circuit_status


@never_cache
@require_GET
def health_check(request):
    """
    Comprehensive system health check.
    
    Used by load balancers to determine if the instance is healthy.
    Returns 200 if all critical systems are operational, 503 otherwise.
    """
    checks = {
        'status': 'healthy',
        'timestamp': int(time.time()),
        'checks': {}
    }
    
    # Database check (critical)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            result = cursor.fetchone()
            if result and result[0] == 1:
                checks['checks']['database'] = 'ok'
            else:
                raise Exception("Unexpected database response")
    except Exception as e:
        checks['status'] = 'unhealthy'
        checks['checks']['database'] = f'error: {str(e)}'
    
    # Cache check (warning if fails, not critical)
    try:
        test_key = 'health_check_test'
        cache.set(test_key, '1', 10)
        value = cache.get(test_key)
        if value == '1':
            checks['checks']['cache'] = 'ok'
        else:
            checks['checks']['cache'] = 'warning: cache read/write failed'
    except Exception as e:
        checks['checks']['cache'] = f'warning: {str(e)}'

    try:
        redis_status = circuit_status()
        if redis_status.using_redis_cache:
            checks['checks']['cache_circuit'] = 'open' if redis_status.is_open else 'closed'
            if redis_status.is_open:
                checks['checks']['cache'] = 'warning: redis circuit open'
    except Exception:
        # Health endpoint should never fail because of diagnostics wiring.
        pass
    
    # Return appropriate status code
    status_code = 200 if checks['status'] == 'healthy' else 503
    return JsonResponse(checks, status=status_code)


@never_cache
@require_GET
def readiness_check(request):
    """
    Kubernetes-style readiness probe.
    
    Indicates whether the application is ready to accept traffic.
    Used by orchestrators to know when to route traffic to this instance.
    """
    return JsonResponse({
        'ready': True,
        'timestamp': int(time.time())
    })


@never_cache
@require_GET
def liveness_check(request):
    """
    Kubernetes-style liveness probe.
    
    Indicates whether the application is alive and responding.
    If this fails, the orchestrator should restart the instance.
    """
    return JsonResponse({
        'alive': True,
        'timestamp': int(time.time())
    })
