"""Server-owned endpoint permissions. Client menu headers are audit metadata only."""
import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.routing import compile_path, get_route_path

from core.security import check_admin_permission


@lru_cache(maxsize=1)
def policies():
    rows = json.loads(Path(__file__).with_name('endpoint_permissions.json').read_text())
    result = {}
    for row in rows:
        key = (row['method'], row['path'], row['handler'])
        if key in result:
            raise RuntimeError('Duplicate endpoint permission: ' + repr(key))
        result[key] = row
    return result


def route_entries(routes):
    """Support both copied APIRoutes and newer FastAPI lazy included routers."""
    for route in routes:
        if hasattr(route, 'effective_candidates'):
            for context in route.effective_candidates():
                if hasattr(context, 'effective_candidates'):
                    yield from route_entries([context])
                elif isinstance(context.original_route, APIRoute):
                    original = context.original_route
                    yield context.path, original.methods, context.endpoint or original.endpoint
        elif isinstance(route, APIRoute):
            yield route.path, route.methods, route.endpoint


@lru_cache(maxsize=8)
def route_table(app):
    return tuple((compile_path(path)[0], path, methods, endpoint)
                 for path, methods, endpoint in route_entries(app.routes))


def resolve_policy(request):
    method = request.method.upper()
    if method == 'HEAD':
        method = 'GET'
    path = get_route_path(request.scope).rstrip('/') or '/'
    for regex, template, methods, endpoint in route_table(request.app):
        match = regex.fullmatch(path)
        if match and method in methods:
            handler = endpoint.__module__ + ':' + endpoint.__name__
            return policies().get((method, template, handler)), match.groupdict()
    return None, {}


def raw_permissions(user):
    value = getattr(user, 'permissions', None)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def level(value):
    if value is True:
        return 'READ'
    if value is False or value is None:
        return 'NONE'
    normalized = str(value).upper().strip()
    return normalized if normalized in {'READ', 'WRITE'} else 'NONE'


# Explicit compatibility for the old account editor. No permissive default merge.
LEGACY_GROUPS = {
    'basic_info': ('/basic-info/',),
    'production': ('/production/',), 'sales': ('/sales/orders', '/sales/unsold'),
    'purchase': ('/purchase/', '/subcontract/'), 'inbound': ('/inventory/',),
    'quality': ('/quality/',), 'shipping': ('/shipping/', '/sales/shipping'),
}


def menu_level(user, menu):
    permissions = raw_permissions(user)
    if 'menu_access' in permissions:
        access = permissions['menu_access']
        return level(access.get(menu)) if isinstance(access, dict) else 'NONE'
    # Match the legacy editor's explicit enabled-group conversion: READ only.
    # New document menus never inherit broad basic-info rights.
    if menu.startswith('/standard-documents/'):
        return 'NONE'
    if menu == '/production/pipeline-dashboard':
        group = 'home'
    elif menu.startswith('/production/packing'):
        group = 'shipping'
    else:
        group = next((name for name, prefixes in LEGACY_GROUPS.items()
                      if any(menu.startswith(prefix) for prefix in prefixes)), None)
    parent = permissions.get(group)
    if not isinstance(parent, dict) or parent.get('enabled') is not True:
        return 'NONE'
    if group == 'shipping' and menu.startswith('/shipping/'):
        name = menu.rsplit('/', 1)[-1]
        if name in {'history', 'print', 'manual', 'verify'}:
            return level(parent.get('menus', {}).get(name))
    return 'READ'


def can_access(user, menus, action, require_all=False):
    checks = [menu_level(user, menu) == 'WRITE' if action == 'WRITE'
              else menu_level(user, menu) in {'READ', 'WRITE'} for menu in menus]
    return bool(checks) and (all(checks) if require_all else any(checks))


LABEL_MENUS = {
    'external-production': ['/production/performance/status'],
    'external-packing': ['/production/packing/status'],
    'purchase': ['/purchase/inbound', '/purchase/inquiry/inbounds'],
    'production': ['/production/performance/status'],
    'subcontract-outbound': ['/subcontract/outbound'],
    'subcontract-inbound': ['/subcontract/inbound', '/purchase/inquiry/inbounds'],
    'packing': ['/production/packing', '/production/packing/status'],
    'direct-shipment': ['/sales/shipping', '/shipping/inquiry'],
}


async def production_scope(request, policy, params, db):
    """Use persisted run type for record IDs, never a caller-claimed type."""
    kind = None
    handler = policy['handler'].split(':')[-1]
    if 'performance_id' in params or params.get('source') == 'production':
        from models.production import ProductionPerformance
        try:
            record_id = int(params.get('performance_id', params.get('record_id', '')))
        except ValueError:
            raise HTTPException(404, '생산실적을 찾을 수 없습니다.')
        record = db.get(ProductionPerformance, record_id)
        kind = record.performance_type if record else None
    elif 'run_id' in params:
        from models.production_run import ProductionRun
        try:
            run_id = int(params['run_id'])
        except ValueError:
            raise HTTPException(404, '생산실적을 찾을 수 없습니다.')
        record = db.get(ProductionRun, run_id)
        kind = record.performance_type if record else None
    elif handler == 'start_run':
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            body = {}
        # The handler separately validates this type against its actual process.
        kind = body.get('performance_type', 'MACHINING') if isinstance(body, dict) else None
    elif handler in {'list_runs', 'production_processes'}:
        kind = request.query_params.get('performance_type')
    elif handler in {'selectable_orders', 'production_workers', 'production_equipments', 'performance_source_lots', 'performance_orders'}:
        from models.models import ProcessModel
        from services.production_process_type import performance_type_for_process
        code = request.query_params.get('process_code', '').strip()
        process = db.query(ProcessModel).filter(ProcessModel.process_code == code).first() if code else None
        kind = performance_type_for_process(process) if process else None
    elif handler == 'downtime_types':
        kind = 'MACHINING'
    elif handler == 'defect_types':
        # Code labels only; no production record data.
        return policy['menus'], False
    kind = kind.strip().upper() if isinstance(kind, str) else None
    menu = {'MACHINING': '/production/performance/machining',
            'ASSEMBLY': '/production/performance/assembly'}.get(kind)
    return ([menu], False) if menu else (policy['menus'], True)


async def enforce_endpoint_access(request, user, db):
    if not user or getattr(user, 'is_active', True) is False:
        raise HTTPException(401, '로그인이 필요합니다.')
    if check_admin_permission(user):
        return
    policy, params = resolve_policy(request)
    if not policy:
        raise HTTPException(403, '접근 권한이 등록되지 않은 기능입니다.')
    kind = policy.get('kind')
    if kind in {'public', 'session'}:
        return
    if kind == 'admin':
        raise HTTPException(403, '관리자 권한이 필요합니다.')
    if kind == 'lookup':
        # Only non-sensitive common-code/location lookup endpoints use this rule.
        access = raw_permissions(user).get('menu_access', {})
        if isinstance(access, dict) and any(level(v) in {'READ', 'WRITE'} for v in access.values()):
            return
        permissions = raw_permissions(user)
        if 'menu_access' not in permissions and any(
                isinstance(permissions.get(group), dict) and permissions[group].get('enabled') is True
                for group in (*LEGACY_GROUPS, 'home')):
            return
        raise HTTPException(403, '업무 메뉴 조회 권한이 필요합니다.')
    menus = policy.get('menus', [])
    if kind == 'labels':
        if policy['path'] == '/internal-labels/packing-waiting':
            menus = LABEL_MENUS['packing']
        else:
            menus = LABEL_MENUS.get(params.get('source'), [])
    require_all = policy.get('all', False)
    action = policy.get('action', 'READ' if request.method in {'GET', 'HEAD', 'OPTIONS'} else 'WRITE')
    scoped = policy.get('scope')
    if kind == 'labels' and params.get('source') == 'production':
        scoped = 'production-performance'
        menus = ['/production/performance/machining', '/production/performance/assembly']
    if scoped == 'production-performance' and can_access(user, ['/production/performance/status'], action):
        return
    if scoped in {'production-run', 'production-process', 'production-performance'}:
        menus, require_all = await production_scope(request, dict(policy, menus=menus), params, db)
    if not can_access(user, menus, action, require_all):
        raise HTTPException(403, '해당 기능의 ' + ('수정' if action == 'WRITE' else '조회') + ' 권한이 없습니다.')
