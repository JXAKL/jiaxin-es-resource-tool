"""Shared filename rules for script channels, including legacy TCode aliases.

The named aliases cover the original OSR player's TCode 0.2/0.3 configuration.
Only explicit suffix fields are stripped; ordinary title words remain intact.
"""
import re
import unicodedata

AXIS_NAMES = frozenset({
    'stroke', 'heave', 'up', 'surge', 'forward', 'sway', 'left',
    'twist', 'yaw', 'roll', 'pitch',
    'valve', 'suck', 'suction', 'pump', 'lube',
    'vib', 'vibrate', 'vibration',
})
_TOKEN = '(?:' + '|'.join(sorted(AXIS_NAMES, key=lambda value: (-len(value), value))) + r'|[LRVA][0-9]|vib[0-9])'
AXIS_FIELD = re.compile(r'\s*(?:[._-]\s*' + _TOKEN + r'|\[\s*' + _TOKEN +
                        r'\s*\]|\(\s*' + _TOKEN + r'\s*\)|【\s*' + _TOKEN + r'\s*】)\s*$', re.I)
# Dot is the player convention; underscore/hyphen and bracketed fields are
# common filename variants. A bare trailing word such as "Twist" is a title.
AXES = re.compile(r'(?:\s*(?:[._-]\s*' + _TOKEN + r'|\[\s*' + _TOKEN +
                  r'\s*\]|\(\s*' + _TOKEN + r'\s*\)|【\s*' + _TOKEN + r'\s*】)\s*)+$', re.I)


def base_stem(stem, *, normalize=True):
    value = (unicodedata.normalize('NFKC', stem) if normalize else stem).strip()
    base = AXES.sub('', value).strip()
    return base or value


def has_axis(stem):
    value = unicodedata.normalize('NFKC', stem).strip()
    return base_stem(value) != value
