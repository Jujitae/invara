"""Lossless JSON fractional numbers, with legacy native-number serialization.

Decimal is used only when the native float's JSON spelling would lose value.
No user object tags or strings stand in for numbers. Artifact readers retain
legacy non-JSON constants; observation readers explicitly reject them.
"""
from __future__ import annotations

import json
import math
from decimal import Context, Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, localcontext
from typing import Any

LIMIT = 10_000


def decimal_value(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def number_equal(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return decimal_value(a) == decimal_value(b)
    return a == b


def parse_float(text: str, *, limit: int = 4 * LIMIT) -> float | Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError('JSON decimal exponent is unsupported') from error
    digits = value.as_tuple()
    if not value.is_finite() or len(digits.digits) > limit or abs(digits.exponent) > limit:
        raise ValueError(f'JSON decimal exceeds exact-number limits ({limit} digits/exponent)')
    native = float(value)
    if math.isfinite(native) and Decimal(str(native)) == value:
        return native
    return Decimal(number_text(value))


def reject_constant(text: str) -> None:
    raise ValueError(f'not a JSON number: {text}')


def reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            rendered = json.dumps(key, ensure_ascii=False)
            raise ValueError(f'duplicate JSON object member: {rendered[:100]}')
        value[key] = item
    return value


def loads(text: str, *, strict_numbers: bool = False, bounded_numbers: bool = False,
          reject_duplicates: bool = False, **kwargs: Any) -> Any:
    if strict_numbers:
        kwargs['parse_constant'] = reject_constant
    if reject_duplicates:
        if 'object_pairs_hook' in kwargs:
            raise TypeError('reject_duplicates cannot be combined with object_pairs_hook')
        kwargs['object_pairs_hook'] = reject_duplicate_members
    hook = (lambda token: parse_float(token, limit=LIMIT)) if strict_numbers or bounded_numbers else parse_float
    return json.loads(text, parse_float=hook, **kwargs)


def number_text(value: Decimal) -> str:
    """Context-independent canonical scientific spelling, without expansion."""
    if not value.is_finite():
        raise ValueError('cannot serialize a non-finite Decimal')
    native = float(value)
    if math.isfinite(native) and Decimal(str(native)) == value:
        return json.dumps(native)
    sign, digits, exponent = value.as_tuple()
    coefficient = ''.join(map(str, digits)).rstrip('0')
    if not coefficient:
        return '-0.0' if sign else '0.0'
    adjusted = exponent + len(digits) - 1
    return ('-' if sign else '') + coefficient[0] + '.' + (coefficient[1:] or '0') + f'e{adjusted:+d}'


def dumps(value: Any, **kwargs: Any) -> str:
    """Standard JSON formatting, extended only with unquoted Decimal tokens."""
    # Recurse through containers so Decimal never passes through default=str.
    # Delegate all scalar and key escaping to the public stdlib JSON encoder.
    indent = kwargs.get('indent')
    unit = (' ' * indent if isinstance(indent, int) else indent) if indent is not None else None
    item_sep, key_sep = kwargs.get('separators', (',', ': ') if indent is not None else (', ', ': '))
    active: set[int] = set()

    def encode(node: Any, level: int) -> str:
        if isinstance(node, Decimal):
            return number_text(node)
        if not isinstance(node, (dict, list, tuple)):
            return json.dumps(node, **kwargs)
        ident = id(node)
        if ident in active:
            raise ValueError('Circular reference detected')
        active.add(ident)
        try:
            if isinstance(node, dict):
                pairs = sorted(node.items()) if kwargs.get('sort_keys') else node.items()
                items = []
                for key, item in pairs:
                    # Use the stdlib's exact object-key rules (including bool/int).
                    key_json = json.dumps({key: None}, ensure_ascii=kwargs.get('ensure_ascii', True),
                                          allow_nan=kwargs.get('allow_nan', True), separators=(',', ':'))
                    items.append(key_json[1:-6] + key_sep + encode(item, level + 1))
                opening, closing = '{', '}'
            else:
                items = [encode(item, level + 1) for item in node]
                opening, closing = '[', ']'
            if not items:
                return opening + closing
            if unit is not None:
                return opening + '\n' + unit * (level + 1) + (item_sep + '\n' + unit * (level + 1)).join(items) + '\n' + unit * level + closing
            return opening + item_sep.join(items) + closing
        finally:
            active.remove(ident)

    return encode(value, 0)


def exact_tolerance(a: Any, b: Any, bound: Any, *, relative: bool = False) -> tuple[bool, dict[str, Any]]:
    """Exact decision; division is used only for the displayed relative ratio."""
    left, right, limit = map(decimal_value, (a, b, bound))
    values = (left, right, limit)
    precision = max(v.adjusted() for v in values) - min(v.as_tuple().exponent for v in values) + sum(len(v.as_tuple().digits) for v in values) + 10
    with localcontext(Context(prec=max(precision, 32), rounding=ROUND_HALF_EVEN,
                              Emin=MIN_EMIN, Emax=MAX_EMAX)) as context:
        delta = abs(left - right)
        if not relative:
            return delta <= limit, {'delta': parse_float(number_text(delta)), 'abs': bound}
        scale = max(abs(left), abs(right))
        within = delta <= limit * scale
        # This rounded diagnostic is explicitly separate from the exact decision.
        context.prec = 32
        ratio = Decimal(0) if scale == 0 else delta / scale
        return within, {'ratio': parse_float(number_text(ratio)), 'rel': bound}
