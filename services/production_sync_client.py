"""Read-only adapter for the observed UNI_MES Login/List protocol."""
import http.cookiejar
import time
import re
from html import unescape
from html.parser import HTMLParser
from core import config
from http.client import IncompleteRead, RemoteDisconnected
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = 'http://14.63.172.132:8081'
ENDPOINT = BASE + '/SoServerNet/Web/Port/HttpPort.aspx'
CONDITIONS = ('FIELD_JOB_NUM FIELD_RULE_NO IS_CONFIRM IS_REST ITEM_COMP_NUM ITEM_NUM ITEM_TYPE '
              'JOB_DATE1 JOB_DATE2 JOB_END_TIME JOB_MACH_NO JOB_NUM JOB_ST_TIME JOB_USER_NO LOAD_PAGE '
              'LOT_NUM ORDER_NO ORDER_NUM PLAN_TYPE PO_NUM PROC_ITEM_NUM PROC_TYPE PRODUCT_NM '
              'RADIO_VALUE RULE_NO STAT_CODE WARE_NO').split()
FIELDS = ('ITEM_NUM PROC_TYPE_NM JOB_TIME JOB_ST_TIME JOB_END_TIME JOB_NUM LOT_NUM JOB_QTY LOT_QTY FAULT_QTY F10').split()


class SyncError(Exception):
    def __init__(self,message,diagnostic=None):
        super().__init__(message)
        self.diagnostic=diagnostic


def response_diagnostic(values,payload,kind):
    # Allowlisted protocol metadata only; never copy response text, passwords, cookies or SQL.
    fields=('TYPE','MESS','CLASS','METHOD','STACK')
    ids=values.get('Common.Set.LoginUserSet.USER_ID',[''])[0].split(',')
    return dict(schema_version=3,client_revision='protocol-key-20261012',stage='login' if payload.get('run.object.name')=='Common.Login.LoginObj' else 'query',error_kind=kind,
                response_complete=values.get('SO.NET.END')==['SO.NET.END'],
                protocol_key=payload.get('ENCRYPT.KEY') if re.fullmatch(r'[0-9]{1,8}',str(payload.get('ENCRYPT.KEY',''))) else None,
                throw_fields={f:bool(any(v.strip() for v in values.get('THROW.'+f,[]))) for f in fields},
                login_user_returned=bool(any(ids)),requested_user_returned=payload.get('Common.Set.LoginShortSet.USER_ID') in ids,
                company_returned=bool(values.get('Common.Set.LoginUserSet.COMP_NO',[''])[0]))


def server_error_kind(values):
    message=' '.join(v for k,items in values.items() if k.startswith('THROW.') for v in items).lower()
    if 'file_not_exist' in message or 'filenotfound' in message:return 'server_object_not_found'
    if 'sqlexception' in message:return 'server_database_error'
    if 'timeout' in message or 'timedout' in message:return 'server_timeout'
    for marker,kind in [('nullreferenceexception','server_null_reference'),('argumentnullexception','server_argument_missing'),('formatexception','server_format_error'),('keynotfoundexception','server_key_not_found'),('unauthorizedaccessexception','server_access_denied'),('typeloadexception','server_object_not_found')]:
        if marker in message:return kind
    return 'server_exception'


def protocol_key(value):
    value=str(value or '').strip()
    if not re.fullmatch(r'[0-9]{1,8}',value):
        raise SyncError('외부 연동 접속키(ENCRYPT.KEY)는 숫자 1~8자리로 설정하세요.')
    return value


def bootstrap_protocol_key(page):
    """Read explicit protocol fields, without saving bootstrap HTML or scanning unrelated numbers."""
    found=set()
    class Inputs(HTMLParser):
        def handle_starttag(self,tag,attrs):
            values=dict(attrs)
            if tag.lower()=='input' and str(values.get('name','')).upper()=='ENCRYPT.KEY':
                value=values.get('value','')
                if re.fullmatch(r'[0-9]{1,8}',value):found.add(value)
    Inputs().feed(page)
    text=unescape(page)
    for pattern in (r"[\"']ENCRYPT\.KEY[\"']\s*[:=,]\s*[\"']?([0-9]{1,8})(?![0-9])",r'(?:^|[?&\s])ENCRYPT\.KEY\s*=\s*([0-9]{1,8})(?![0-9])'):
        found.update(re.findall(pattern,text,re.IGNORECASE))
    if len(found)>1:
        raise SyncError('외부 초기 화면의 접속키가 여러 개라 확인할 수 없습니다.',dict(schema_version=3,client_revision='protocol-key-20261012',stage='landing',error_kind='protocol_key_ambiguous'))
    return next(iter(found),None)


def common(encrypt_key=None):
    return {'SO.NET.START': 'SO.NET.START', 'SO.NET.SOLUTION': 'UNI_MES', 'ENCRYPT.KEY': protocol_key(config.PRODUCTION_SYNC_ENCRYPT_KEY if encrypt_key is None else encrypt_key),
            'SO.NET.MODULE': 'MES', 'SO.NET.USERIP': '', 'user.lang.code': 'ko', 'client.web.mode': 'Web'}


def client_common(client):
    return common(getattr(client,'encrypt_key',None))


def post(opener, values):
    ordered = {key: value for key, value in values.items() if key != 'SO.NET.END'}
    ordered['SO.NET.END'] = 'SO.NET.END'
    req = Request(ENDPOINT, data=urlencode(ordered).encode('utf-8'), headers={
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': BASE, 'Referer': BASE + '/', 'User-Agent': 'MES-Production-Sync/1.0'})
    with opener.open(req, timeout=45) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise SyncError('일별 응답이 16MB를 초과했습니다. 연동 범위를 확인해 주세요.')
        values = parse_qs(raw.decode(response.headers.get_content_charset() or 'utf-8'), keep_blank_values=True)
    if values.get('SO.NET.END') != ['SO.NET.END']:
        raise SyncError('예상한 생산 시스템 응답이 아닙니다.',response_diagnostic(values,ordered,'unexpected_response'))
    if any(key.startswith('THROW.') and any(value.strip() for value in items) for key,items in values.items()):
        # Server exception text may contain credentials or SQL. Never persist it.
        if ordered.get('run.object.name') == 'Common.Login.LoginObj':
            raise SyncError('외부 생산 시스템이 로그인 요청에서 오류를 반환했습니다. 진단정보를 다운로드해 확인할 수 있습니다.',response_diagnostic(values,ordered,server_error_kind(values)))
        raise SyncError('생산 시스템에서 조회 오류를 반환했습니다. 연동 계정과 조회 설정을 확인해 주세요.',response_diagnostic(values,ordered,server_error_kind(values)))
    return values


def query_with_retry(opener, payload):
    """Retry only read-only List transport failures, never login or bad data."""
    for attempt in range(3):
        try:
            return post(opener, payload)
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise SyncError(f'외부 실적 조회 HTTP 오류({exc.code}). 로그인과 조회 권한을 확인해 주세요.') from exc
            failure = f'외부 실적 조회 HTTP 오류({exc.code}).'
        except (TimeoutError, URLError, OSError, IncompleteRead, RemoteDisconnected) as exc:
            failure = ('외부 실적 조회 응답 시간이 초과되었습니다.' if isinstance(exc, TimeoutError)
                       or isinstance(getattr(exc, 'reason', None), TimeoutError)
                       else '외부 실적 조회 연결이 끊기거나 연결할 수 없습니다.')
        if attempt < 2:
            time.sleep(attempt + 1)
    raise SyncError(failure + ' 같은 날짜를 총 3회 시도했으나 실패했습니다.')


class ProductionClient:
    def __init__(self, username, password):
        phase='landing'
        try:
            self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with self.opener.open(BASE + '/', timeout=30) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise SyncError('외부 초기 화면이 너무 커 접속키를 확인할 수 없습니다.')
                headers=getattr(response,'headers',None)
                charset=headers.get_content_charset() if headers is not None else None
                detected=bootstrap_protocol_key(raw.decode(charset or 'utf-8',errors='replace'))
            self.encrypt_key=protocol_key(detected or config.PRODUCTION_SYNC_ENCRYPT_KEY)
            self.encrypt_key_source='landing' if detected else 'configured'
            payload = client_common(self)
            payload.update({'run.object.name': 'Common.Login.LoginObj', 'jump.form.code': 'Login',
                            'Common.BeforeService.CheckSet.IS_CHECK': 'Y',
                            'Common.Set.LoginShortSet.USER_ID': username,
                            'Common.Set.LoginShortSet.USER_PASS': password})
            phase='login'
            login = post(self.opener, payload)
            ids = login.get('Common.Set.LoginUserSet.USER_ID', [''])[0].split(',')
            if username not in ids:
                raise SyncError('외부 시스템 로그인을 확인할 수 없습니다. 계정 정보를 확인해 주세요.',response_diagnostic(login,payload,'login_user_not_returned'))
            row_index = ids.index(username)
            self.context = {}
            for key, values in login.items():
                if key.startswith('Common.Set.LoginUserSet.') and not key.endswith('USER_PASS'):
                    pieces = values[0].split(',')
                    if key.endswith('USER_STRING'):
                        continue  # Display text, contains unescaped commas; not an auth field.
                    if row_index >= len(pieces):
                        raise SyncError('로그인 사용자 정보 형식을 확인할 수 없습니다.',response_diagnostic(login,payload,'login_context_format'))
                    self.context[key] = pieces[row_index]
            self.context['Common.Set.LoginUserSet.USER_PASS'] = ''
        except SyncError as exc:
            if exc.diagnostic is not None:exc.diagnostic['protocol_key_source']=getattr(self,'encrypt_key_source',None)
            raise
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
            raise SyncError('외부 생산 시스템에 접속하거나 로그인할 수 없습니다.',dict(schema_version=3,client_revision='protocol-key-20261012',stage=phase,error_kind='http_error' if isinstance(exc,HTTPError) else 'transport_error',http_status=exc.code if isinstance(exc,HTTPError) else None)) from exc

    def fetch_day(self, day):
        # The successful external probe used distinct date bounds. Same-day
        # bounds can describe an empty interval on this server. Pad both ends
        # to cover inclusive/exclusive boundaries, then retain only this day.
        query_start, query_end = day - timedelta(days=1), day + timedelta(days=1)
        payload = client_common(self)
        payload.update({'MES.Product.Set.JobCondSet.' + key: '' for key in CONDITIONS})
        payload.update(self.context)
        payload.update({'run.object.name': 'MES.Product.ProcLotList.ProcLotListObj', 'jump.form.code': 'List',
                        'MES.Product.Set.JobCondSet.JOB_DATE1': query_start.isoformat(),
                        'MES.Product.Set.JobCondSet.JOB_DATE2': query_end.isoformat()})
        try:
            result = query_with_retry(self.opener, payload)
            prefix = 'Common.Set.LotSet.'
            if (any(values != [''] for key, values in result.items()
                    if key in ('jump.url', 'jump.object.name', 'jump.form.message'))
                    or result.get('jump.form.code', [''])[0] not in ('', 'List')):
                raise SyncError('생산실적 조회 대신 화면 전환 응답을 받았습니다. 로그인과 조회 권한을 확인해 주세요.')
            if prefix + 'ITEM_NUM' not in result:
                # UNI_MES may omit the entire dataset on days without production.
                # Require its query completion envelope rather than accepting an
                # arbitrary response (such as a login page) as an empty day.
                invocation = result.get('log.invoke.object', [''])[0]
                query_completed = (invocation.startswith(('MES.Product.ProcLotL_',
                                                          'MES.Product.ProcLotList.ProcLotListObj_'))
                                   and bool(result.get('MONITOR.QUERY.START', [''])[0])
                                   and bool(result.get('MONITOR.QUERY.END', [''])[0]))
                dataset_values = [value for key, values in result.items()
                                  if key.startswith(prefix) and key != prefix + 'ROW_COUNT'
                                  for value in values]
                declared = result.get(prefix + 'ROW_COUNT', [''])[0]
                if query_completed and not any(dataset_values) and declared in ('', '0'):
                    return [], []
                raise SyncError('생산실적 응답 항목이 없으며 정상적인 빈 조회인지 확인할 수 없습니다. 로그인과 조회 권한을 확인해 주세요.')
            if not result[prefix + 'ITEM_NUM'][0]:
                declared = result.get(prefix + 'ROW_COUNT', [''])[0]
                if declared and declared != '0':
                    raise SyncError('서버 표시 건수와 빈 조회 결과가 다릅니다.')
                return [], []
            arrays = {key: result.get(prefix + key, [''])[0].split(',') for key in FIELDS}
            count = len(arrays['ITEM_NUM'])
            if any(len(values) != count for values in arrays.values()):
                raise SyncError('실적 항목별 건수가 일치하지 않아 해당 날짜의 저장을 중단했습니다.')
            declared = result.get(prefix + 'ROW_COUNT', [''])[0]
            if declared and (not declared.isdecimal() or int(declared) != count):
                raise SyncError('서버 표시 건수와 수신 건수가 다릅니다. 페이지 처리 확인이 필요합니다.')
            names = result.get(prefix + 'PRODUCT_NM', [''])[0].split(',')
            warnings = []
            if len(names) != count:
                warnings.append('품명 구분자가 모호한 날짜는 내부 품목 마스터의 품명을 표시합니다.')
            rows = []
            for i in range(count):
                row = {key: values[i] for key, values in arrays.items()}
                row['PRODUCT_NM'] = names[i] if len(names) == count else ''
                if not all(row[key].strip() for key in ('ITEM_NUM', 'PROC_TYPE_NM', 'JOB_TIME', 'JOB_NUM', 'LOT_NUM')):
                    raise SyncError('품번·공정·작업일·작업번호·LOT 중 누락된 항목이 있습니다.')
                location = f'{i + 1}행'
                try:
                    actual = datetime.strptime(row['JOB_TIME'], '%Y%m%d').date()
                except ValueError as exc:
                    raise SyncError(f'{location} 작업일(JOB_TIME) 형식이 잘못되었습니다. YYYYMMDD 형식이 필요합니다.') from exc
                if not query_start <= actual <= query_end:
                    raise SyncError('요청한 작업일과 반환된 실적 날짜가 일치하지 않습니다.')
                if actual != day:
                    continue
                for key in ('JOB_QTY', 'LOT_QTY', 'FAULT_QTY', 'F10'):
                    label = {'JOB_QTY': '양품', 'LOT_QTY': 'LOT 수량', 'FAULT_QTY': '불량', 'F10': '셋업'}[key]
                    try:
                        number = Decimal(row[key])
                    except InvalidOperation as exc:
                        raise SyncError(f'{location} {label}({key}) 수량이 비어 있거나 숫자 형식이 아닙니다.') from exc
                    if not number.is_finite() or number < 0:
                        raise SyncError(f'{location} {label}({key}) 수량이 유효하지 않습니다.')
                for key in ('JOB_ST_TIME', 'JOB_END_TIME'):
                    if row[key]:
                        try:
                            datetime.strptime(row[key], '%Y%m%d%H%M%S')
                        except ValueError as exc:
                            label = '시작시간' if key == 'JOB_ST_TIME' else '종료시간'
                            raise SyncError(f'{location} {label}({key}) 형식이 잘못되었습니다. YYYYMMDDHHMMSS 형식이 필요합니다.') from exc
                rows.append(row)
            return rows, warnings
        except UnicodeError as exc:
            raise SyncError('외부 실적 조회 응답의 문자 인코딩을 읽을 수 없습니다.') from exc
