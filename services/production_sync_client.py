"""Read-only adapter for the observed UNI_MES Login/List protocol."""
import http.cookiejar
from datetime import date, datetime
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
    pass


def common():
    return {'SO.NET.START': 'SO.NET.START', 'SO.NET.SOLUTION': 'UNI_MES', 'ENCRYPT.KEY': '1631',
            'SO.NET.MODULE': 'MES', 'SO.NET.USERIP': '', 'user.lang.code': 'ko', 'client.web.mode': 'Web'}


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
        raise SyncError('예상한 생산 시스템 응답이 아닙니다.')
    if any(key.startswith('THROW.') for key in values):
        # Server exception text may contain credentials or SQL. Never persist it.
        raise SyncError('생산 시스템에서 조회 오류를 반환했습니다. 연동 계정과 조회 설정을 확인해 주세요.')
    return values


class ProductionClient:
    def __init__(self, username, password):
        try:
            self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with self.opener.open(BASE + '/', timeout=30) as response:
                response.read(1024)
            payload = common()
            payload.update({'run.object.name': 'Common.Login.LoginObj', 'jump.form.code': 'Login',
                            'Common.BeforeService.CheckSet.IS_CHECK': 'Y',
                            'Common.Set.LoginShortSet.USER_ID': username,
                            'Common.Set.LoginShortSet.USER_PASS': password})
            login = post(self.opener, payload)
            ids = login.get('Common.Set.LoginUserSet.USER_ID', [''])[0].split(',')
            if username not in ids:
                raise SyncError('외부 시스템 로그인을 확인할 수 없습니다. 계정 정보를 확인해 주세요.')
            row_index = ids.index(username)
            self.context = {}
            for key, values in login.items():
                if key.startswith('Common.Set.LoginUserSet.') and not key.endswith('USER_PASS'):
                    pieces = values[0].split(',')
                    if key.endswith('USER_STRING'):
                        continue  # Display text, contains unescaped commas; not an auth field.
                    if row_index >= len(pieces):
                        raise SyncError('로그인 사용자 정보 형식을 확인할 수 없습니다.')
                    self.context[key] = pieces[row_index]
            self.context['Common.Set.LoginUserSet.USER_PASS'] = ''
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
            raise SyncError('외부 생산 시스템에 접속하거나 로그인할 수 없습니다.') from exc

    def fetch_day(self, day):
        payload = common()
        payload.update({'MES.Product.Set.JobCondSet.' + key: '' for key in CONDITIONS})
        payload.update(self.context)
        payload.update({'run.object.name': 'MES.Product.ProcLotList.ProcLotListObj', 'jump.form.code': 'List',
                        'MES.Product.Set.JobCondSet.JOB_DATE1': day.isoformat(),
                        'MES.Product.Set.JobCondSet.JOB_DATE2': day.isoformat()})
        try:
            result = post(self.opener, payload)
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
                actual = datetime.strptime(row['JOB_TIME'], '%Y%m%d').date()
                if actual != day:
                    raise SyncError('요청한 작업일과 반환된 실적 날짜가 일치하지 않습니다.')
                for key in ('JOB_QTY', 'LOT_QTY', 'FAULT_QTY', 'F10'):
                    number = Decimal(row[key])
                    if not number.is_finite() or number < 0:
                        raise SyncError('실적 수량이 유효하지 않습니다.')
                for key in ('JOB_ST_TIME', 'JOB_END_TIME'):
                    if row[key]:
                        datetime.strptime(row[key], '%Y%m%d%H%M%S')
                rows.append(row)
            return rows, warnings
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError, InvalidOperation) as exc:
            raise SyncError('실적 조회 통신 또는 날짜·수량 형식 확인에 실패했습니다.') from exc
