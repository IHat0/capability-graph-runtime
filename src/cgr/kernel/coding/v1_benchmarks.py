"""Executable Coding v1 benchmark catalog with prompt-hidden edge tests."""

from cgr.kernel.swe import SWETask

from .code_test_case import CodeTestCase


def _task(
    task_id: str,
    issue: str,
    filename: str,
    initial: str,
    solution: str,
    visible: str,
    hidden: str,
) -> SWETask:
    return SWETask(
        id=f"v1.{task_id}",
        issue=issue,
        files={filename: initial},
        expected_files={filename: solution},
        visible_test_files={"visible_tests.py": visible},
        hidden_test_files={"hidden_tests.py": hidden},
        visible_test_commands=[
            CodeTestCase(name="visible", command=["python", "visible_tests.py"])
        ],
        hidden_test_commands=[
            CodeTestCase(name="hidden", command=["python", "hidden_tests.py"])
        ],
    )


def create_coding_v1_tasks() -> list[SWETask]:
    """Return the deterministic 26-task Coding v1 executable suite."""
    return [
        _task(
            "normalize_username",
            "Implement normalize_username; strip surrounding whitespace; lowercase text; replace internal whitespace with underscores; empty results raise ValueError.",
            "username_utils.py",
            "def normalize_username(value):\n    return value\n",
            "def normalize_username(value):\n    result = '_'.join(value.strip().lower().split())\n    if not result:\n        raise ValueError('empty username')\n    return result\n",
            "from username_utils import normalize_username\nassert normalize_username(' Alice Smith ') == 'alice_smith'\n",
            "from username_utils import normalize_username\nassert normalize_username('A   B') == 'a_b'\ntry:\n normalize_username('   ')\nexcept ValueError: pass\nelse: raise AssertionError('empty normalized username must raise')\n",
        ),
        _task(
            "parse_bool_extended",
            "Implement parse_bool; bool inputs return themselves; true values include true, yes, y, 1, on; false values include false, no, n, 0, off; matching is case-insensitive; strings are stripped; invalid values raise ValueError.",
            "parse_utils.py",
            "def parse_bool(value):\n    return value == 'true'\n",
            "def parse_bool(value):\n    if isinstance(value, bool):\n        return value\n    value = str(value).strip().lower()\n    if value in {'true','yes','y','1','on'}:\n        return True\n    if value in {'false','no','n','0','off'}:\n        return False\n    raise ValueError(value)\n",
            "from parse_utils import parse_bool\nassert parse_bool(True) is True\nassert parse_bool(' YES ') is True\nassert parse_bool('off') is False\n",
            "from parse_utils import parse_bool\nfor value in ('y','1','ON'): assert parse_bool(value) is True\nfor value in ('n','0','FALSE'): assert parse_bool(value) is False\ntry:\n parse_bool('maybe')\nexcept ValueError: pass\nelse: raise AssertionError('invalid value must raise')\n",
        ),
        _task(
            "merge_counts_nested",
            "Implement merge_counts; overlapping numeric counts are summed; inputs are not mutated; empty dictionaries are supported; non-numeric overlapping values raise TypeError.",
            "count_utils.py",
            "def merge_counts(a, b):\n    return {**a, **b}\n",
            "def merge_counts(a, b):\n    result = dict(a)\n    for key, value in b.items():\n        if key in result:\n            if not isinstance(result[key], (int, float)) or not isinstance(value, (int, float)):\n                raise TypeError('overlapping counts must be numeric')\n            result[key] += value\n        else:\n            result[key] = value\n    return result\n",
            "from count_utils import merge_counts\na={'x':1}; b={'x':2,'y':3}\nassert merge_counts(a,b) == {'x':3,'y':3}\nassert a == {'x':1} and b == {'x':2,'y':3}\n",
            "from count_utils import merge_counts\nassert merge_counts({}, {'a':4}) == {'a':4}\ntry:\n merge_counts({'x':'a'}, {'x':2})\nexcept TypeError: pass\nelse: raise AssertionError('nonnumeric overlap must raise')\n",
        ),
        _task(
            "flatten_once",
            "Implement flatten_once; expand lists and tuples one level; strings are not expanded; preserve order.",
            "list_utils.py",
            "def flatten_once(items):\n    return sum(items, [])\n",
            "def flatten_once(items):\n    result=[]\n    for item in items:\n        result.extend(item if isinstance(item, (list, tuple)) else [item])\n    return result\n",
            "from list_utils import flatten_once\nassert flatten_once([1,[2,3],(4,5)]) == [1,2,3,4,5]\n",
            "from list_utils import flatten_once\nassert flatten_once(['ab',['cd'],[[1]]]) == ['ab','cd',[1]]\nassert flatten_once([]) == []\n",
        ),
        _task(
            "chunk_list_strict",
            "Implement chunk_list; size must be a positive integer; the final chunk may be smaller; input is not mutated.",
            "chunk_utils.py",
            "def chunk_list(items, size):\n    return [items]\n",
            "def chunk_list(items, size):\n    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:\n        raise ValueError('size must be a positive integer')\n    return [items[i:i+size] for i in range(0, len(items), size)]\n",
            "from chunk_utils import chunk_list\nassert chunk_list([1,2,3,4,5],2) == [[1,2],[3,4],[5]]\n",
            "from chunk_utils import chunk_list\nitems=[1,2]; assert chunk_list(items,5) == [[1,2]] and items == [1,2]\nfor size in (0,-1,1.5):\n try: chunk_list(items,size)\n except ValueError: pass\n else: raise AssertionError('invalid size must raise')\n",
        ),
        _task(
            "dedupe_by_key",
            "Implement dedupe_by_key(items, key), preserving the first item for each hashable key result and input order.",
            "dedupe_utils.py",
            "def dedupe_by_key(items, key):\n    return list(set(items))\n",
            "def dedupe_by_key(items, key):\n    seen=set(); result=[]\n    for item in items:\n        marker=key(item)\n        if marker not in seen:\n            seen.add(marker); result.append(item)\n    return result\n",
            "from dedupe_utils import dedupe_by_key\nitems=[{'id':1,'v':'a'},{'id':1,'v':'b'},{'id':2,'v':'c'}]\nassert dedupe_by_key(items, lambda x:x['id']) == [items[0],items[2]]\n",
            "from dedupe_utils import dedupe_by_key\nassert dedupe_by_key(['A','a','B'], str.lower) == ['A','B']\nassert dedupe_by_key([], lambda x:x) == []\n",
        ),
        _task(
            "safe_get_nested",
            "Implement safe_get_nested for dot paths or key/index lists across dictionaries and lists, returning default when missing.",
            "nested_utils.py",
            "def safe_get_nested(data, path, default=None):\n    return data.get(path, default)\n",
            "def safe_get_nested(data, path, default=None):\n    parts = path.split('.') if isinstance(path, str) else path\n    current=data\n    try:\n        for part in parts:\n            if isinstance(current, list):\n                current=current[int(part)]\n            else:\n                current=current[part]\n        return current\n    except (KeyError, IndexError, TypeError, ValueError):\n        return default\n",
            "from nested_utils import safe_get_nested\ndata={'a':{'b':[10,20]}}\nassert safe_get_nested(data,'a.b.1') == 20\n",
            "from nested_utils import safe_get_nested\ndata={'a':[{'x':3}]}\nassert safe_get_nested(data,['a',0,'x']) == 3\nassert safe_get_nested(data,'a.9','missing') == 'missing'\n",
        ),
        _task(
            "group_by",
            "Implement group_by(items, key) preserving original item order within each group.",
            "group_utils.py",
            "def group_by(items, key):\n    return {}\n",
            "def group_by(items, key):\n    result={}\n    for item in items:\n        result.setdefault(key(item), []).append(item)\n    return result\n",
            "from group_utils import group_by\nassert group_by([1,2,3,4], lambda x:x%2) == {1:[1,3],0:[2,4]}\n",
            "from group_utils import group_by\nitems=['a','bb','c']; assert group_by(items,len) == {1:['a','c'],2:['bb']}\nassert group_by([],lambda x:x) == {}\n",
        ),
        _task(
            "top_k_counts",
            "Implement top_k_counts sorted by count descending then key ascending; nonpositive k returns empty and input is unchanged.",
            "rank_utils.py",
            "def top_k_counts(counts, k):\n    return list(counts.items())[:k]\n",
            "def top_k_counts(counts, k):\n    if k <= 0:\n        return []\n    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:k]\n",
            "from rank_utils import top_k_counts\nassert top_k_counts({'b':2,'a':2,'c':3},2) == [('c',3),('a',2)]\n",
            "from rank_utils import top_k_counts\ncounts={'x':1}; assert top_k_counts(counts,0) == [] and counts == {'x':1}\nassert top_k_counts(counts,5) == [('x',1)]\n",
        ),
        _task(
            "parse_key_value_lines",
            "Implement key=value line parsing with whitespace, blank/comment skipping, latest duplicate wins, and malformed-line ValueError.",
            "kv_utils.py",
            "def parse_key_value_lines(text):\n    return {}\n",
            "def parse_key_value_lines(text):\n    result={}\n    for raw in text.splitlines():\n        line=raw.strip()\n        if not line or line.startswith('#'):\n            continue\n        if '=' not in line:\n            raise ValueError(f'malformed line: {raw}')\n        key,value=line.split('=',1)\n        result[key.strip()]=value.strip()\n    return result\n",
            "from kv_utils import parse_key_value_lines\nassert parse_key_value_lines(' a = 1\\n# no\\nb=2') == {'a':'1','b':'2'}\n",
            "from kv_utils import parse_key_value_lines\nassert parse_key_value_lines('a=1\\na=2\\n') == {'a':'2'}\ntry:\n parse_key_value_lines('broken')\nexcept ValueError: pass\nelse: raise AssertionError('malformed line must raise')\n",
        ),
        _task(
            "format_table",
            "Implement CSV-like format_table for rows of dictionaries, with first-seen column order, empty missing cells, string conversion, and newline separation.",
            "table_utils.py",
            "def format_table(rows):\n    return str(rows)\n",
            "def format_table(rows):\n    columns=[]\n    for row in rows:\n        for key in row:\n            if key not in columns:\n                columns.append(key)\n    if not columns:\n        return ''\n    lines=[','.join(columns)]\n    lines.extend(','.join(str(row.get(key,'')) for key in columns) for row in rows)\n    return '\\n'.join(lines)\n",
            "from table_utils import format_table\nassert format_table([{'a':1,'b':2},{'b':3,'c':4}]) == 'a,b,c\\n1,2,\\n,3,4'\n",
            "from table_utils import format_table\nassert format_table([]) == ''\nassert format_table([{'x':None}]) == 'x\\nNone'\n",
        ),
        _task(
            "slugify",
            "Implement slugify: trim/lowercase, collapse non-alphanumeric runs to one hyphen, strip edge hyphens, and reject empty output.",
            "text_utils.py",
            "def slugify(text):\n    return text.lower().replace(' ','-')\n",
            "import re\n\ndef slugify(text):\n    result=re.sub(r'[^a-z0-9]+','-',text.strip().lower()).strip('-')\n    if not result:\n        raise ValueError('empty slug')\n    return result\n",
            "from text_utils import slugify\nassert slugify(' Hello, World! ') == 'hello-world'\n",
            "from text_utils import slugify\nassert slugify('A___B  C') == 'a-b-c'\ntry:\n slugify('***')\nexcept ValueError: pass\nelse: raise AssertionError('empty slug must raise')\n",
        ),
        _task(
            "retry_result",
            "Implement retry(fn, attempts, exceptions=(Exception,)): positive attempts, retry matching exceptions, return success, and re-raise the last failure.",
            "retry_utils.py",
            "def retry(fn, attempts, exceptions=(Exception,)):\n    return fn()\n",
            "def retry(fn, attempts, exceptions=(Exception,)):\n    if attempts <= 0:\n        raise ValueError('attempts must be positive')\n    for index in range(attempts):\n        try:\n            return fn()\n        except exceptions:\n            if index == attempts - 1:\n                raise\n",
            "from retry_utils import retry\nstate={'n':0}\ndef fn():\n state['n']+=1\n if state['n']<3: raise ValueError('wait')\n return 'ok'\nassert retry(fn,3,(ValueError,)) == 'ok'\n",
            "from retry_utils import retry\ntry:\n retry(lambda: (_ for _ in ()).throw(ValueError('x')),2,(ValueError,))\nexcept ValueError: pass\nelse: raise AssertionError('last exception must escape')\ntry:\n retry(lambda:1,0)\nexcept ValueError: pass\nelse: raise AssertionError('zero attempts must raise')\n",
        ),
        _task(
            "RunningAverage",
            "Implement RunningAverage with add, count/total/average properties, zero empty average, and reset.",
            "average.py",
            "class RunningAverage:\n    pass\n",
            "class RunningAverage:\n    def __init__(self):\n        self.reset()\n    def add(self,value):\n        self._total += value; self._count += 1\n    @property\n    def count(self): return self._count\n    @property\n    def total(self): return self._total\n    @property\n    def average(self): return self._total / self._count if self._count else 0.0\n    def reset(self):\n        self._total=0; self._count=0\n",
            "from average import RunningAverage\nr=RunningAverage(); assert r.count == 0 and r.average == 0.0\nr.add(2); r.add(4); assert r.total == 6 and r.average == 3\n",
            "from average import RunningAverage\nr=RunningAverage(); r.add(-2); r.add(2); assert r.average == 0\nr.reset(); assert r.count == 0 and r.total == 0 and r.average == 0.0\n",
        ),
        _task(
            "LRUCacheTiny",
            "Implement LRUCache(capacity) with get/put, access recency, least-recent eviction, defaults, and positive capacity validation.",
            "cache.py",
            "class LRUCache:\n    def __init__(self, capacity): self.data={}\n",
            "from collections import OrderedDict\n\nclass LRUCache:\n    def __init__(self,capacity):\n        if capacity <= 0: raise ValueError('capacity must be positive')\n        self.capacity=capacity; self.data=OrderedDict()\n    def get(self,key,default=None):\n        if key not in self.data: return default\n        self.data.move_to_end(key); return self.data[key]\n    def put(self,key,value):\n        if key in self.data: self.data.move_to_end(key)\n        self.data[key]=value\n        if len(self.data)>self.capacity: self.data.popitem(last=False)\n",
            "from cache import LRUCache\nc=LRUCache(2); c.put('a',1); c.put('b',2); assert c.get('a') == 1\nc.put('c',3); assert c.get('b') is None\n",
            "from cache import LRUCache\nc=LRUCache(1); c.put('x',1); c.put('x',2); assert c.get('x') == 2\ntry:\n LRUCache(0)\nexcept ValueError: pass\nelse: raise AssertionError('capacity must be positive')\n",
        ),
        _task(
            "parse_int_list",
            "Implement comma-separated integer parsing with whitespace, ignored empty fields, negatives, and ValueError for invalid integers.",
            "int_utils.py",
            "def parse_int_list(text):\n    return text.split(',')\n",
            "def parse_int_list(text):\n    return [int(part.strip()) for part in text.split(',') if part.strip()]\n",
            "from int_utils import parse_int_list\nassert parse_int_list('1, 2,, -3') == [1,2,-3]\n",
            "from int_utils import parse_int_list\nassert parse_int_list(' , ') == []\ntry:\n parse_int_list('1,nope')\nexcept ValueError: pass\nelse: raise AssertionError('invalid integer must raise')\n",
        ),
        _task(
            "windowed",
            "Implement sliding tuple windows with positive size, empty result when oversized, and no mutation.",
            "window_utils.py",
            "def windowed(items, size):\n    return []\n",
            "def windowed(items, size):\n    if size <= 0: raise ValueError('size must be positive')\n    return [tuple(items[i:i+size]) for i in range(len(items)-size+1)]\n",
            "from window_utils import windowed\nassert windowed([1,2,3,4],3) == [(1,2,3),(2,3,4)]\n",
            "from window_utils import windowed\nitems=[1,2]; assert windowed(items,3) == [] and items == [1,2]\ntry:\n windowed(items,0)\nexcept ValueError: pass\nelse: raise AssertionError('nonpositive size must raise')\n",
        ),
        _task(
            "deep_merge_dicts",
            "Implement recursive deep_merge: nested dictionaries merge, b overrides other values, and neither input mutates.",
            "merge_utils.py",
            "def deep_merge(a, b):\n    return {**a, **b}\n",
            "def deep_merge(a,b):\n    result={}\n    for key,value in a.items():\n        result[key]=deep_merge(value,{}) if isinstance(value,dict) else value\n    for key,value in b.items():\n        if key in result and isinstance(result[key],dict) and isinstance(value,dict):\n            result[key]=deep_merge(result[key],value)\n        else:\n            result[key]=deep_merge(value,{}) if isinstance(value,dict) else value\n    return result\n",
            "from merge_utils import deep_merge\na={'x':{'a':1},'v':1}; b={'x':{'b':2},'v':3}\nassert deep_merge(a,b) == {'x':{'a':1,'b':2},'v':3}\n",
            "from merge_utils import deep_merge\na={'x':{'a':1}}; b={'x':{'b':2}}; deep_merge(a,b)\nassert a == {'x':{'a':1}} and b == {'x':{'b':2}}\nassert deep_merge({'x':1},{'x':{'y':2}}) == {'x':{'y':2}}\n",
        ),
        _task(
            "invert_mapping",
            "Implement invert_mapping so values map to ordered lists of original keys, including duplicate values.",
            "mapping_utils.py",
            "def invert_mapping(mapping):\n    return {v:k for k,v in mapping.items()}\n",
            "def invert_mapping(mapping):\n    result={}\n    for key,value in mapping.items():\n        result.setdefault(value,[]).append(key)\n    return result\n",
            "from mapping_utils import invert_mapping\nassert invert_mapping({'a':1,'b':1,'c':2}) == {1:['a','b'],2:['c']}\n",
            "from mapping_utils import invert_mapping\nassert invert_mapping({}) == {}\nassert invert_mapping({'x':'v','y':'v'})['v'] == ['x','y']\n",
        ),
        _task(
            "find_first",
            "Implement find_first returning the first predicate match, stopping early, or default when none.",
            "find_utils.py",
            "def find_first(items, predicate, default=None):\n    return default\n",
            "def find_first(items, predicate, default=None):\n    for item in items:\n        if predicate(item): return item\n    return default\n",
            "from find_utils import find_first\nassert find_first([1,4,6],lambda x:x%2==0) == 4\nassert find_first([1],lambda x:False,'none') == 'none'\n",
            "from find_utils import find_first\ncalls=[]\ndef pred(x):\n calls.append(x); return x==2\nassert find_first([1,2,3],pred) == 2 and calls == [1,2]\n",
        ),
        _task(
            "TokenBucket",
            "Implement TokenBucket capacity/tokens, consume/refill, capacity clamping, booleans, and negative-value rejection.",
            "bucket.py",
            "class TokenBucket:\n    pass\n",
            "class TokenBucket:\n    def __init__(self,capacity,tokens=None):\n        if capacity < 0: raise ValueError('capacity')\n        self.capacity=capacity; self.tokens=capacity if tokens is None else min(tokens,capacity)\n    def consume(self,n=1):\n        if n < 0: raise ValueError('negative consume')\n        if n > self.tokens: return False\n        self.tokens -= n; return True\n    def refill(self,n):\n        if n < 0: raise ValueError('negative refill')\n        self.tokens=min(self.capacity,self.tokens+n)\n",
            "from bucket import TokenBucket\nb=TokenBucket(3); assert b.consume(2) is True and b.tokens == 1\nassert b.consume(2) is False; b.refill(9); assert b.tokens == 3\n",
            "from bucket import TokenBucket\nb=TokenBucket(5,2); assert b.tokens == 2\nfor action in (lambda:b.consume(-1),lambda:b.refill(-1)):\n try: action()\n except ValueError: pass\n else: raise AssertionError('negative values must raise')\n",
        ),
        _task(
            "parse_query_string",
            "Implement query-string parsing with leading ?, percent/+ decoding, repeated-key lists, and empty missing values using stdlib only.",
            "query_utils.py",
            "def parse_query_string(qs):\n    return {}\n",
            "from urllib.parse import parse_qsl\n\ndef parse_query_string(qs):\n    result={}\n    for key,value in parse_qsl(qs.lstrip('?'),keep_blank_values=True):\n        result.setdefault(key,[]).append(value)\n    return result\n",
            "from query_utils import parse_query_string\nassert parse_query_string('?a=1&b=hello+world') == {'a':['1'],'b':['hello world']}\n",
            "from query_utils import parse_query_string\nassert parse_query_string('a=1&a=2&empty') == {'a':['1','2'],'empty':['']}\nassert parse_query_string('x=%2B') == {'x':['+']}\n",
        ),
        _task(
            "rotate_list",
            "Implement non-mutating right rotation by n, with negative n rotating left and empty lists supported.",
            "rotate_utils.py",
            "def rotate_list(items, n):\n    return items\n",
            "def rotate_list(items,n):\n    if not items: return []\n    offset=n % len(items)\n    return list(items[-offset:] + items[:-offset]) if offset else list(items)\n",
            "from rotate_utils import rotate_list\nitems=[1,2,3,4]; assert rotate_list(items,1) == [4,1,2,3] and items == [1,2,3,4]\n",
            "from rotate_utils import rotate_list\nassert rotate_list([1,2,3],-1) == [2,3,1]\nassert rotate_list([],10) == []\nassert rotate_list([1,2],4) == [1,2]\n",
        ),
        _task(
            "is_palindrome_normalized",
            "Implement is_palindrome ignoring case and non-alphanumeric characters; empty normalized text is true.",
            "palindrome.py",
            "def is_palindrome(text):\n    return text == text[::-1]\n",
            "def is_palindrome(text):\n    normalized=''.join(char.lower() for char in text if char.isalnum())\n    return normalized == normalized[::-1]\n",
            "from palindrome import is_palindrome\nassert is_palindrome('A man, a plan, a canal: Panama!') is True\n",
            "from palindrome import is_palindrome\nassert is_palindrome('') is True\nassert is_palindrome('!!!') is True\nassert is_palindrome('hello') is False\n",
        ),
        _task(
            "resolve_config",
            "Implement non-mutating recursive resolve_config where overrides merge deeply and None removes the corresponding key.",
            "config_utils.py",
            "def resolve_config(defaults, overrides):\n    return {**defaults, **overrides}\n",
            "def resolve_config(defaults,overrides):\n    result={}\n    for key,value in defaults.items():\n        result[key]=resolve_config(value,{}) if isinstance(value,dict) else value\n    for key,value in overrides.items():\n        if value is None:\n            result.pop(key,None)\n        elif key in result and isinstance(result[key],dict) and isinstance(value,dict):\n            result[key]=resolve_config(result[key],value)\n        else:\n            result[key]=resolve_config(value,{}) if isinstance(value,dict) else value\n    return result\n",
            "from config_utils import resolve_config\ndefaults={'a':1,'db':{'host':'x','port':1}}\nassert resolve_config(defaults,{'db':{'port':2}}) == {'a':1,'db':{'host':'x','port':2}}\n",
            "from config_utils import resolve_config\na={'x':1,'n':{'a':1,'b':2}}; b={'x':None,'n':{'a':None}}\nassert resolve_config(a,b) == {'n':{'b':2}}\nassert a == {'x':1,'n':{'a':1,'b':2}} and b == {'x':None,'n':{'a':None}}\n",
        ),
        SWETask(
            id="v1.multi_file_report",
            issue=(
                "Fix the two-module report utility so render_report normalizes "
                "names through format_name and emits one 'name: score' line per "
                "record without mutating records."
            ),
            files={
                "formatters.py": "def format_name(value):\n    return value\n",
                "report.py": (
                    "from formatters import format_name\n\n"
                    "def render_report(records):\n"
                    "    return ','.join(record['name'] for record in records)\n"
                ),
            },
            expected_files={
                "formatters.py": (
                    "def format_name(value):\n"
                    "    return ' '.join(value.strip().title().split())\n"
                ),
                "report.py": (
                    "from formatters import format_name\n\n"
                    "def render_report(records):\n"
                    "    return '\\n'.join(f\"{format_name(record['name'])}: "
                    "{record['score']}\" for record in records)\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from report import render_report\n"
                    "records=[{'name':' alice smith ','score':3}]\n"
                    "assert render_report(records) == 'Alice Smith: 3'\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from report import render_report\n"
                    "records=[{'name':'BOB','score':2},{'name':'eve  doe','score':5}]\n"
                    "before=[dict(record) for record in records]\n"
                    "assert render_report(records) == 'Bob: 2\\nEve Doe: 5'\n"
                    "assert records == before, 'records must not be mutated'\n"
                )
            },
            visible_test_commands=[
                CodeTestCase(name="visible", command=["python", "visible_tests.py"])
            ],
            hidden_test_commands=[
                CodeTestCase(name="hidden", command=["python", "hidden_tests.py"])
            ],
        ),
    ]
