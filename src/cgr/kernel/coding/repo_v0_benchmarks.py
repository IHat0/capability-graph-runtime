"""Small repo-style executable coding benchmark tasks."""

from pydantic import BaseModel, ConfigDict, Field

from cgr.kernel.swe import SWETask

from .code_test_case import CodeTestCase


class RepoCodingTask(BaseModel):
    """A compact repository-shaped coding benchmark task."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    issue_description: str = Field(min_length=1)
    repo_files: dict[str, str] = Field(min_length=1)
    visible_test_files: dict[str, str] = Field(min_length=1)
    hidden_test_files: dict[str, str] = Field(min_length=1)
    test_commands: list[list[str]] = Field(min_length=1)
    allowed_files_to_edit: list[str] = Field(min_length=1)
    reference_solution_files: dict[str, str] = Field(min_length=1)
    expected_behavior_summary: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)

    def to_swe_task(self) -> SWETask:
        """Convert to the shared SWE-style A/B runner task."""
        issue = (
            f"{self.title}\n\nIssue:\n{self.issue_description}\n\n"
            f"Expected behavior:\n{self.expected_behavior_summary}\n\n"
            "Return JSON only with a files object mapping allowed file paths to "
            "full replacement file contents. Edit only allowed files."
        )
        return SWETask(
            id=self.task_id,
            issue=issue,
            files=self.repo_files,
            expected_files=self.reference_solution_files,
            allowed_files_to_edit=self.allowed_files_to_edit,
            visible_test_files=self.visible_test_files,
            hidden_test_files=self.hidden_test_files,
            visible_test_commands=[
                CodeTestCase(name="visible", command=["python", "visible_tests.py"])
            ],
            hidden_test_commands=[
                CodeTestCase(name="hidden", command=["python", "hidden_tests.py"])
            ],
        )


def create_repo_v0_tasks() -> list[SWETask]:
    """Return the deterministic 10-task repo-style benchmark suite."""
    return [task.to_swe_task() for task in create_repo_v0_repo_tasks()]


def create_repo_v0_repo_tasks() -> list[RepoCodingTask]:
    """Return repo task metadata plus executable visible and hidden tests."""
    return [
        RepoCodingTask(
            task_id="v0.query_parser_repeated_keys",
            title="Query parser repeated keys",
            issue_description=(
                "Query parser loses repeated keys and mishandles blank values, "
                "percent decoding, and keys without '='."
            ),
            repo_files={
                "src/url_utils.py": (
                    "from urllib.parse import unquote_plus\n\n"
                    "def decode(value):\n    return unquote_plus(value)\n"
                ),
                "src/query_parser.py": (
                    "from src.url_utils import decode\n\n"
                    "def parse_query(query):\n"
                    "    result = {}\n"
                    "    for part in query.split('&'):\n"
                    "        if not part:\n            continue\n"
                    "        key, value = part.split('=', 1)\n"
                    "        result[decode(key)] = decode(value)\n"
                    "    return result\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.query_parser import parse_query\n"
                    "assert parse_query('a=1&a=2') == {'a': ['1', '2']}\n"
                    "assert parse_query('a=&b=2') == {'a': [''], 'b': ['2']}\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.query_parser import parse_query\n"
                    "assert parse_query('a%20b=hello+world&a%20b=') == "
                    "{'a b': ['hello world', '']}\n"
                    "assert parse_query('flag&x=1') == {'flag': [''], 'x': ['1']}\n"
                    "assert parse_query('') == {}\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/query_parser.py"],
            reference_solution_files={
                "src/query_parser.py": (
                    "from src.url_utils import decode\n\n"
                    "def parse_query(query):\n"
                    "    result = {}\n"
                    "    if not query:\n        return result\n"
                    "    for part in query.split('&'):\n"
                    "        if not part:\n            continue\n"
                    "        key, sep, value = part.partition('=')\n"
                    "        decoded_key = decode(key)\n"
                    "        decoded_value = decode(value if sep else '')\n"
                    "        result.setdefault(decoded_key, []).append(decoded_value)\n"
                    "    return result\n"
                )
            },
            expected_behavior_summary=(
                "parse_query returns a dictionary; each key maps to a list of "
                "values; repeated keys append values in order; single keys still "
                "map to one-item lists; blank values are preserved as empty "
                "strings; keys without equals become empty string values; empty "
                "query returns empty dict; percent decoding is applied and plus "
                "handling follows url_utils.decode."
            ),
            tags=["parsing", "multifile"],
        ),
        RepoCodingTask(
            task_id="v0.report_generator_multifile",
            title="Report generator totals",
            issue_description=(
                "Report totals are wrong because averages ignore zero values and "
                "formatting drops missing categories."
            ),
            repo_files={
                "src/stats.py": (
                    "def summarize(values):\n"
                    "    nonzero = [value for value in values if value]\n"
                    "    total = sum(nonzero)\n"
                    "    count = len(nonzero)\n"
                    "    return {'total': total, 'count': count, "
                    "'average': total / count if count else 0}\n"
                ),
                "src/report.py": (
                    "from src.stats import summarize\n\n"
                    "def build_report(categories):\n"
                    "    lines = []\n"
                    "    for name, values in categories.items():\n"
                    "        if values:\n"
                    "            data = summarize(values)\n"
                    "            lines.append(f\"{name}: {data['total']} "
                    "({data['average']:.2f})\")\n"
                    "    return '\\n'.join(lines)\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.stats import summarize\n"
                    "assert summarize([0, 2, 4]) == "
                    "{'total': 6, 'count': 3, 'average': 2.0}\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.report import build_report\n"
                    "text = build_report({'b': [], 'a': [0, 2]})\n"
                    "assert text == 'a: 2 (1.00)\\nb: 0 (0.00)'\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/stats.py", "src/report.py"],
            reference_solution_files={
                "src/stats.py": (
                    "def summarize(values):\n"
                    "    total = sum(values)\n"
                    "    count = len(values)\n"
                    "    return {'total': total, 'count': count, "
                    "'average': total / count if count else 0.0}\n"
                ),
                "src/report.py": (
                    "from src.stats import summarize\n\n"
                    "def build_report(categories):\n"
                    "    lines = []\n"
                    "    for name in sorted(categories):\n"
                    "        data = summarize(categories[name])\n"
                    "        lines.append(f\"{name}: {data['total']} "
                    "({data['average']:.2f})\")\n"
                    "    return '\\n'.join(lines)\n"
                ),
            },
            expected_behavior_summary=(
                "Zero values count; empty categories are included; report lines "
                "are sorted by category."
            ),
            tags=["aggregation", "multifile"],
        ),
        RepoCodingTask(
            task_id="v0.retry_client",
            title="Retry client",
            issue_description=(
                "Retry wrapper retries the wrong number of times and swallows the "
                "final exception."
            ),
            repo_files={
                "src/retry.py": (
                    "def retry(fn, max_attempts, retry_exceptions=(Exception,)):\n"
                    "    for _ in range(max_attempts - 1):\n"
                    "        try:\n            return fn()\n"
                    "        except retry_exceptions:\n            pass\n"
                    "    return None\n"
                ),
                "src/client.py": (
                    "from src.retry import retry\n\n"
                    "def fetch_with_retry(fn, max_attempts=3):\n"
                    "    return retry(fn, max_attempts)\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.retry import retry\n"
                    "calls = {'n': 0}\n"
                    "def fn():\n"
                    "    calls['n'] += 1\n"
                    "    if calls['n'] < 3: raise ValueError('no')\n"
                    "    return 'ok'\n"
                    "assert retry(fn, 3, (ValueError,)) == 'ok'\n"
                    "assert calls['n'] == 3\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.retry import retry\n"
                    "calls = {'n': 0}\n"
                    "def bad():\n"
                    "    calls['n'] += 1\n"
                    "    raise ValueError('final')\n"
                    "try:\n retry(bad, 2, (ValueError,))\n"
                    "except ValueError as exc:\n assert str(exc) == 'final'\n"
                    "else: raise AssertionError('final error must be raised')\n"
                    "assert calls['n'] == 2\n"
                    "try:\n retry(lambda: (_ for _ in ()).throw(TypeError('x')), 3, (ValueError,))\n"
                    "except TypeError: pass\n"
                    "else: raise AssertionError('non retryable must not retry')\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/retry.py"],
            reference_solution_files={
                "src/retry.py": (
                    "def retry(fn, max_attempts, retry_exceptions=(Exception,)):\n"
                    "    if max_attempts <= 0:\n        raise ValueError('max_attempts must be positive')\n"
                    "    last_error = None\n"
                    "    for _ in range(max_attempts):\n"
                    "        try:\n            return fn()\n"
                    "        except retry_exceptions as exc:\n            last_error = exc\n"
                    "    raise last_error\n"
                )
            },
            expected_behavior_summary=(
                "Attempt exactly max_attempts, return first success, re-raise "
                "final retryable error, and never catch non-retryable errors."
            ),
            tags=["error-handling"],
        ),
        RepoCodingTask(
            task_id="v0.lru_cache_repo",
            title="LRU cache recency",
            issue_description=(
                "LRU cache updates values but not recency; eviction is wrong "
                "after get and update."
            ),
            repo_files={
                "src/cache.py": (
                    "class LRUCache:\n"
                    "    def __init__(self, capacity):\n"
                    "        self.capacity = capacity\n        self.data = {}\n        self.order = []\n"
                    "    def get(self, key, default=None):\n"
                    "        return self.data.get(key, default)\n"
                    "    def put(self, key, value):\n"
                    "        self.data[key] = value\n"
                    "        if key not in self.order:\n            self.order.append(key)\n"
                    "        if len(self.order) > self.capacity:\n"
                    "            old = self.order.pop(0)\n            self.data.pop(old, None)\n"
                ),
                "src/store.py": "from src.cache import LRUCache\n",
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.cache import LRUCache\n"
                    "c = LRUCache(2)\n"
                    "c.put('a', 1); c.put('b', 2); assert c.get('a') == 1\n"
                    "c.put('c', 3); assert c.get('b') is None\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.cache import LRUCache\n"
                    "c = LRUCache(1); c.put('a', 1); c.put('a', 2); c.put('b', 3)\n"
                    "assert c.get('a') is None and c.get('b') == 3\n"
                    "try:\n LRUCache(0)\n"
                    "except ValueError: pass\n"
                    "else: raise AssertionError('capacity must be positive')\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/cache.py"],
            reference_solution_files={
                "src/cache.py": (
                    "class LRUCache:\n"
                    "    def __init__(self, capacity):\n"
                    "        if capacity <= 0:\n            raise ValueError('capacity must be positive')\n"
                    "        self.capacity = capacity\n        self.data = {}\n        self.order = []\n"
                    "    def get(self, key, default=None):\n"
                    "        if key not in self.data:\n            return default\n"
                    "        self.order.remove(key); self.order.append(key)\n"
                    "        return self.data[key]\n"
                    "    def put(self, key, value):\n"
                    "        if key in self.data:\n            self.order.remove(key)\n"
                    "        self.data[key] = value; self.order.append(key)\n"
                    "        while len(self.order) > self.capacity:\n"
                    "            old = self.order.pop(0); self.data.pop(old, None)\n"
                )
            },
            expected_behavior_summary=(
                "get and updating put mark keys as recent; eviction removes the "
                "least recently used key; capacity must be positive."
            ),
            tags=["stateful", "data-structures"],
        ),
        RepoCodingTask(
            task_id="v0.config_loader_precedence",
            title="Config precedence",
            issue_description=(
                "Config precedence is wrong. Merge defaults < file_config < "
                "env_config < explicit_overrides; None does not override."
            ),
            repo_files={
                "src/env_utils.py": "def clean_env(env):\n    return dict(env)\n",
                "src/config.py": (
                    "def resolve_config(defaults, file_config, env_config, overrides):\n"
                    "    result = {}\n"
                    "    for source in (overrides, env_config, file_config, defaults):\n"
                    "        result.update(source)\n"
                    "    return result\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.config import resolve_config\n"
                    "assert resolve_config({'a':1}, {'a':2}, {'b':3}, {'c':4}) == "
                    "{'a':2, 'b':3, 'c':4}\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.config import resolve_config\n"
                    "result = resolve_config({'db': {'host':'localhost','port':1}}, "
                    "{'db': {'port':2}}, {'db': {'user':'u'}}, {'db': {'host': None}})\n"
                    "assert result == {'db': {'host':'localhost','port':2,'user':'u'}}\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/config.py"],
            reference_solution_files={
                "src/config.py": (
                    "def _merge(a, b):\n"
                    "    result = dict(a)\n"
                    "    for key, value in b.items():\n"
                    "        if value is None:\n            continue\n"
                    "        if isinstance(result.get(key), dict) and isinstance(value, dict):\n"
                    "            result[key] = _merge(result[key], value)\n"
                    "        else:\n            result[key] = value\n"
                    "    return result\n\n"
                    "def resolve_config(defaults, file_config, env_config, overrides):\n"
                    "    result = {}\n"
                    "    for source in (defaults, file_config, env_config, overrides):\n"
                    "        result = _merge(result, source)\n"
                    "    return result\n"
                )
            },
            expected_behavior_summary=(
                "Later sources override earlier sources, nested dictionaries merge, "
                "and None values do not override existing values."
            ),
            tags=["configuration", "nested-data"],
        ),
        RepoCodingTask(
            task_id="v0.csv_importer_cleaning",
            title="CSV importer cleaning",
            issue_description=(
                "CSV importer fails on whitespace, comments, blank lines, and bad "
                "numeric rows."
            ),
            repo_files={
                "src/validators.py": "def parse_int(value):\n    return int(value)\n",
                "src/csv_importer.py": (
                    "from src.validators import parse_int\n\n"
                    "def import_rows(text):\n"
                    "    rows = []\n"
                    "    for line in text.splitlines():\n"
                    "        name, count = line.split(',')\n"
                    "        rows.append({'name': name, 'count': parse_int(count)})\n"
                    "    return rows\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.csv_importer import import_rows\n"
                    "assert import_rows(' a , 1\\n# skip\\n\\n b,2 ') == "
                    "[{'name':'a','count':1},{'name':'b','count':2}]\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.csv_importer import import_rows\n"
                    "try:\n import_rows('bad,x')\n"
                    "except ValueError: pass\n"
                    "else: raise AssertionError('bad numeric count must raise')\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/csv_importer.py"],
            reference_solution_files={
                "src/csv_importer.py": (
                    "from src.validators import parse_int\n\n"
                    "def import_rows(text):\n"
                    "    rows = []\n"
                    "    for raw in text.splitlines():\n"
                    "        line = raw.strip()\n"
                    "        if not line or line.startswith('#'):\n            continue\n"
                    "        name, count = [part.strip() for part in line.split(',', 1)]\n"
                    "        rows.append({'name': name, 'count': parse_int(count)})\n"
                    "    return rows\n"
                )
            },
            expected_behavior_summary=(
                "Trim fields, skip blank/comment lines, parse integer counts, and "
                "raise ValueError for invalid counts."
            ),
            tags=["csv", "validation"],
        ),
        RepoCodingTask(
            task_id="v0.router_path_params",
            title="Router path params",
            issue_description=(
                "Router only matches exact paths and fails path parameters; static "
                "routes should outrank param routes."
            ),
            repo_files={
                "src/matching.py": "def normalize(path):\n    return path.rstrip('/') or '/'\n",
                "src/router.py": (
                    "from src.matching import normalize\n\n"
                    "def match_route(routes, path):\n"
                    "    path = normalize(path)\n"
                    "    for pattern, handler in routes:\n"
                    "        if normalize(pattern) == path:\n"
                    "            return handler, {}\n"
                    "    return None\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.router import match_route\n"
                    "routes=[('/users/:id','user')]\n"
                    "assert match_route(routes, '/users/123') == ('user', {'id':'123'})\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.router import match_route\n"
                    "routes=[('/users/:id','param'),('/users/new','new')]\n"
                    "assert match_route(routes, '/users/new/') == ('new', {})\n"
                    "assert match_route(routes, '/missing') is None\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/router.py"],
            reference_solution_files={
                "src/router.py": (
                    "from src.matching import normalize\n\n"
                    "def _score(pattern):\n"
                    "    return sum(1 for part in normalize(pattern).split('/') if part and not part.startswith(':'))\n\n"
                    "def match_route(routes, path):\n"
                    "    target = [part for part in normalize(path).split('/') if part]\n"
                    "    for pattern, handler in sorted(routes, key=lambda route: _score(route[0]), reverse=True):\n"
                    "        parts = [part for part in normalize(pattern).split('/') if part]\n"
                    "        if len(parts) != len(target):\n            continue\n"
                    "        params = {}; matched = True\n"
                    "        for expected, actual in zip(parts, target):\n"
                    "            if expected.startswith(':'):\n                params[expected[1:]] = actual\n"
                    "            elif expected != actual:\n                matched = False; break\n"
                    "        if matched:\n            return handler, params\n"
                    "    return None\n"
                )
            },
            expected_behavior_summary=(
                "Path params are captured, trailing slash is normalized, static "
                "routes outrank parameter routes, and no match returns None."
            ),
            tags=["routing"],
        ),
        RepoCodingTask(
            task_id="v0.shopping_cart_totals",
            title="Shopping cart totals",
            issue_description=(
                "Cart total applies discount and tax in the wrong order and mutates "
                "input items."
            ),
            repo_files={
                "src/discounts.py": "def discount_amount(subtotal, rate):\n    return subtotal * rate\n",
                "src/cart.py": (
                    "from src.discounts import discount_amount\n\n"
                    "def total(items, discount_rate=0, tax_rate=0):\n"
                    "    subtotal = 0\n"
                    "    for item in items:\n"
                    "        item['line_total'] = item['price'] * item.get('qty', 1)\n"
                    "        subtotal += item['line_total']\n"
                    "    return round(subtotal * (1 + tax_rate) - discount_amount(subtotal, discount_rate), 2)\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.cart import total\n"
                    "items=[{'price':10,'qty':2},{'price':5,'qty':1}]\n"
                    "assert total(items, discount_rate=.1, tax_rate=.2) == 27.0\n"
                    "assert 'line_total' not in items[0]\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.cart import total\n"
                    "assert total([{'price':.1,'qty':3}], tax_rate=.1) == .33\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/cart.py"],
            reference_solution_files={
                "src/cart.py": (
                    "from src.discounts import discount_amount\n\n"
                    "def total(items, discount_rate=0, tax_rate=0):\n"
                    "    subtotal = sum(item['price'] * item.get('qty', 1) for item in items)\n"
                    "    discounted = subtotal - discount_amount(subtotal, discount_rate)\n"
                    "    return round(discounted * (1 + tax_rate), 2)\n"
                )
            },
            expected_behavior_summary=(
                "Compute subtotal, apply discount before tax, avoid mutating input, "
                "and round final total to two decimals."
            ),
            tags=["business-logic"],
        ),
        RepoCodingTask(
            task_id="v0.token_bucket_clock",
            title="Token bucket clock",
            issue_description=(
                "Token bucket uses real time directly and refill math is wrong; it "
                "should use an injectable clock."
            ),
            repo_files={
                "src/clock.py": "import time\n\ndef now():\n    return time.time()\n",
                "src/token_bucket.py": (
                    "from src.clock import now\n\n"
                    "class TokenBucket:\n"
                    "    def __init__(self, capacity, refill_rate, clock=now):\n"
                    "        self.capacity=capacity; self.refill_rate=refill_rate; self.clock=clock\n"
                    "        self.tokens=0; self.last=clock()\n"
                    "    def consume(self, n=1):\n"
                    "        self.tokens += self.refill_rate\n"
                    "        if self.tokens >= n:\n self.tokens -= n; return True\n"
                    "        return False\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.token_bucket import TokenBucket\n"
                    "time=[0]\n"
                    "b=TokenBucket(5, 2, clock=lambda: time[0])\n"
                    "assert b.consume(1) is True\n"
                    "time[0]=1.5\n"
                    "assert b.consume(3) is True\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.token_bucket import TokenBucket\n"
                    "time=[0]\n"
                    "b=TokenBucket(2, 10, clock=lambda: time[0])\n"
                    "time[0]=10; assert b.consume(2) is True\n"
                    "assert b.consume(1) is False\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/token_bucket.py"],
            reference_solution_files={
                "src/token_bucket.py": (
                    "from src.clock import now\n\n"
                    "class TokenBucket:\n"
                    "    def __init__(self, capacity, refill_rate, clock=now):\n"
                    "        self.capacity=capacity; self.refill_rate=refill_rate; self.clock=clock\n"
                    "        self.tokens=capacity; self.last=clock()\n"
                    "    def _refill(self):\n"
                    "        current=self.clock(); elapsed=current-self.last; self.last=current\n"
                    "        self.tokens=min(self.capacity, self.tokens + elapsed*self.refill_rate)\n"
                    "    def consume(self, n=1):\n"
                    "        self._refill()\n"
                    "        if self.tokens >= n:\n            self.tokens -= n; return True\n"
                    "        return False\n"
                )
            },
            expected_behavior_summary=(
                "Use injectable clock, refill by elapsed time, cap at capacity, and "
                "return bool from consume."
            ),
            tags=["time", "stateful"],
        ),
        RepoCodingTask(
            task_id="v0.markdown_toc",
            title="Markdown TOC",
            issue_description=(
                "Markdown TOC generation creates duplicate slugs and includes "
                "headings inside fenced code blocks."
            ),
            repo_files={
                "src/slugify.py": (
                    "import re\n\n"
                    "def slugify(text):\n"
                    "    return re.sub(r'[^a-z0-9]+','-',text.lower()).strip('-')\n"
                ),
                "src/markdown.py": (
                    "from src.slugify import slugify\n\n"
                    "def toc(markdown):\n"
                    "    entries=[]\n"
                    "    for line in markdown.splitlines():\n"
                    "        if line.startswith('#'):\n"
                    "            title=line.lstrip('#').strip()\n"
                    "            entries.append((title, slugify(title)))\n"
                    "    return entries\n"
                ),
            },
            visible_test_files={
                "visible_tests.py": (
                    "from src.markdown import toc\n"
                    "assert toc('# Intro\\n## Intro') == "
                    "[('Intro','intro'),('Intro','intro-1')]\n"
                )
            },
            hidden_test_files={
                "hidden_tests.py": (
                    "from src.markdown import toc\n"
                    "doc='```\\n# Not Heading\\n```\\n# Hello, World!\\n# Hello World'\n"
                    "assert toc(doc) == [('Hello, World!','hello-world'),('Hello World','hello-world-1')]\n"
                )
            },
            test_commands=[["python", "visible_tests.py"], ["python", "hidden_tests.py"]],
            allowed_files_to_edit=["src/markdown.py"],
            reference_solution_files={
                "src/markdown.py": (
                    "from src.slugify import slugify\n\n"
                    "def toc(markdown):\n"
                    "    entries=[]; counts={}; in_code=False\n"
                    "    for line in markdown.splitlines():\n"
                    "        if line.startswith('```'):\n            in_code = not in_code; continue\n"
                    "        if in_code or not line.startswith('#'):\n            continue\n"
                    "        title=line.lstrip('#').strip(); base=slugify(title)\n"
                    "        index=counts.get(base,0); counts[base]=index+1\n"
                    "        slug=base if index == 0 else f'{base}-{index}'\n"
                    "        entries.append((title, slug))\n"
                    "    return entries\n"
                )
            },
            expected_behavior_summary=(
                "Build TOC entries from headings, ignore fenced code blocks, and "
                "deduplicate slugs with numeric suffixes."
            ),
            tags=["markdown", "text"],
        ),
    ]
