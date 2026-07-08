"""Harder executable coding challenges for real-provider A/B evaluation."""

from cgr.kernel.swe.swe_task import SWETask

from .code_test_case import CodeTestCase


def _task(
    task_id: str,
    issue: str,
    filename: str,
    initial: str,
    expected: str,
    tests: str,
) -> SWETask:
    return SWETask(
        id=f"hard.{task_id}",
        issue=issue,
        files={filename: initial},
        expected_files={filename: expected},
        test_files={"test_task.py": tests},
        test_commands=[
            CodeTestCase(name=f"test_{task_id}", command=["python", "test_task.py"])
        ],
    )


def create_hard_coding_tasks() -> list[SWETask]:
    """Return deterministic coding tasks verified by executable behavior tests."""
    return [
        _task(
            "normalize_email",
            "Fix normalize_email so it trims whitespace and lowercases addresses.",
            "email_utils.py",
            "def normalize_email(value):\n    return value\n",
            "def normalize_email(value):\n    return value.strip().lower()\n",
            (
                "from email_utils import normalize_email\n"
                "assert normalize_email('  USER@Example.COM ') == 'user@example.com'\n"
                "assert normalize_email('User+tag@Example.com') == "
                "'user+tag@example.com'\n"
            ),
        ),
        _task(
            "safe_divide",
            "Implement safe_divide(a, b, default=None), returning default for zero.",
            "math_utils.py",
            "def safe_divide(a, b, default=None):\n    return a / b\n",
            (
                "def safe_divide(a, b, default=None):\n"
                "    return default if b == 0 else a / b\n"
            ),
            (
                "from math_utils import safe_divide\n"
                "assert safe_divide(10, 2) == 5\n"
                "assert safe_divide(1, 0) is None\n"
                "assert safe_divide(1, 0, default=0) == 0\n"
            ),
        ),
        _task(
            "parse_bool",
            "Fix parse_bool to support bools and common case-insensitive strings.",
            "parse_utils.py",
            (
                "def parse_bool(value):\n"
                "    if value == 'true':\n        return True\n"
                "    if value == 'false':\n        return False\n"
                "    raise ValueError(value)\n"
            ),
            (
                "def parse_bool(value):\n"
                "    if isinstance(value, bool):\n        return value\n"
                "    normalized = str(value).strip().lower()\n"
                "    if normalized in {'true', 'yes', '1', 'on'}:\n        return True\n"
                "    if normalized in {'false', 'no', '0', 'off'}:\n        return False\n"
                "    raise ValueError(value)\n"
            ),
            (
                "from parse_utils import parse_bool\n"
                "assert parse_bool(True) is True\n"
                "assert parse_bool('YES') is True\n"
                "assert parse_bool('off') is False\n"
                "try:\n    parse_bool('maybe')\n"
                "except ValueError:\n    pass\n"
                "else:\n    raise AssertionError('ValueError not raised')\n"
            ),
        ),
        _task(
            "merge_counts",
            "Fix merge_counts to return summed counts without mutating inputs.",
            "counter_utils.py",
            "def merge_counts(a, b):\n    a.update(b)\n    return a\n",
            (
                "def merge_counts(a, b):\n"
                "    merged = dict(a)\n"
                "    for key, value in b.items():\n"
                "        merged[key] = merged.get(key, 0) + value\n"
                "    return merged\n"
            ),
            (
                "from counter_utils import merge_counts\n"
                "\n"
                "def assert_equal(actual, expected, label):\n"
                "    assert actual == expected, "
                "f'{label}: expected {expected!r}, got {actual!r}'\n"
                "\n"
                "a = {'x': 1}\nb = {'x': 2, 'y': 3}\n"
                "result = merge_counts(a, b)\n"
                "\n"
                "assert_equal(result, {'x': 3, 'y': 3}, "
                "'overlapping keys must be summed')\n"
                "assert_equal(a, {'x': 1}, 'first input must not be mutated')\n"
                "assert_equal(b, {'x': 2, 'y': 3}, "
                "'second input must not be mutated')\n"
                "assert_equal(merge_counts({}, {'a': 4}), {'a': 4}, "
                "'empty first dict')\n"
                "assert_equal(merge_counts({'a': 4}, {}), {'a': 4}, "
                "'empty second dict')\n"
                "assert_equal(\n"
                "    merge_counts({'a': 1, 'b': 2}, {'b': 5, 'c': 7}),\n"
                "    {'a': 1, 'b': 7, 'c': 7},\n"
                "    'overlapping b count must be summed, not overwritten',\n"
                ")\n"
            ),
        ),
        _task(
            "chunk_list",
            "Fix chunk_list to keep remainders and reject non-positive sizes.",
            "list_utils.py",
            (
                "def chunk_list(items, size):\n"
                "    return [items[i:i + size] for i in range(0, len(items) - 1, size)]\n"
            ),
            (
                "def chunk_list(items, size):\n"
                "    if size <= 0:\n        raise ValueError('size must be positive')\n"
                "    return [items[i:i + size] for i in range(0, len(items), size)]\n"
            ),
            (
                "from list_utils import chunk_list\n"
                "assert chunk_list([1,2,3,4,5], 2) == [[1,2],[3,4],[5]]\n"
                "assert chunk_list([], 3) == []\n"
                "try:\n    chunk_list([1], 0)\n"
                "except ValueError:\n    pass\n"
                "else:\n    raise AssertionError('ValueError not raised')\n"
            ),
        ),
        _task(
            "dedupe_preserve_order",
            "Fix dedupe_preserve_order so first-occurrence order is retained.",
            "list_utils.py",
            "def dedupe_preserve_order(items):\n    return list(set(items))\n",
            (
                "def dedupe_preserve_order(items):\n"
                "    result = []\n"
                "    for item in items:\n"
                "        if item not in result:\n            result.append(item)\n"
                "    return result\n"
            ),
            (
                "from list_utils import dedupe_preserve_order\n"
                "assert dedupe_preserve_order([3,1,3,2,1]) == [3,1,2]\n"
                "assert dedupe_preserve_order(['a','b','a']) == ['a','b']\n"
            ),
        ),
        _task(
            "validate_password",
            "Fix validate_password to require length, lower, upper, and digit.",
            "security_utils.py",
            "def validate_password(password):\n    return len(password) >= 8\n",
            (
                "def validate_password(password):\n"
                "    return (len(password) >= 8 and any(c.islower() for c in password) "
                "and any(c.isupper() for c in password) "
                "and any(c.isdigit() for c in password))\n"
            ),
            (
                "from security_utils import validate_password\n"
                "assert validate_password('Password1') is True\n"
                "assert validate_password('password1') is False\n"
                "assert validate_password('PASSWORD1') is False\n"
                "assert validate_password('Password') is False\n"
                "assert validate_password('Pass1') is False\n"
            ),
        ),
        _task(
            "fibonacci",
            "Fix fibonacci base cases and reject negative n.",
            "math_utils.py",
            (
                "def fibonacci(n):\n"
                "    if n <= 1:\n        return 1\n"
                "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
            ),
            (
                "def fibonacci(n):\n"
                "    if n < 0:\n        raise ValueError('n must be non-negative')\n"
                "    if n <= 1:\n        return n\n"
                "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
            ),
            (
                "from math_utils import fibonacci\n"
                "assert fibonacci(0) == 0\nassert fibonacci(1) == 1\n"
                "assert fibonacci(7) == 13\n"
                "try:\n    fibonacci(-1)\n"
                "except ValueError:\n    pass\n"
                "else:\n    raise AssertionError('ValueError not raised')\n"
            ),
        ),
    ]
