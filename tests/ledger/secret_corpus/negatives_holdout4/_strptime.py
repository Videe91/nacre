"""Strptime-related classes and functions.

CLASSES:
    LocaleTime -- Discovers and stores locale-specific time information
    TimeRE -- Creates regexes for pattern matching a string of text containing
                time information

FUNCTIONS:
    _getlang -- Figure out what language is being used for the locale
    strptime -- Calculates the time struct represented by the passed-in string

"""
import os
import time
import locale
import calendar
import re
from re import compile as re_compile
from re import sub as re_sub
from re import IGNORECASE
from re import escape as re_escape
from datetime import (date as datetime_date,
                      timedelta as datetime_timedelta,
                      timezone as datetime_timezone)
from _thread import allocate_lock as _thread_allocate_lock

__all__ = []

def _getlang():
    # Figure out what the current language is set to.
    return locale.getlocale(locale.LC_TIME)

def _findall(haystack, needle):
    # Find all positions of needle in haystack.
    if not needle:
        return
    i = 0
    while True:
        i = haystack.find(needle, i)
        if i < 0:
            break
        yield i
        i += len(needle)

def _fixmonths(months):
    yield from months
    # The lower case of 'İ' ('\u0130') is 'i\u0307'.
    # The re module only supports 1-to-1 character matching in
    # case-insensitive mode.
    for s in months:
        if 'i\u0307' in s:
            yield s.replace('i\u0307', '\u0130')

lzh_TW_alt_digits = (
    # 〇:一:二:三:四:五:六:七:八:九
    '\u3007', '\u4e00', '\u4e8c', '\u4e09', '\u56db',
    '\u4e94', '\u516d', '\u4e03', '\u516b', '\u4e5d',
    # 十:十一:十二:十三:十四:十五:十六:十七:十八:十九
    '\u5341', '\u5341\u4e00', '\u5341\u4e8c', '\u5341\u4e09', '\u5341\u56db',
    '\u5341\u4e94', '\u5341\u516d', '\u5341\u4e03', '\u5341\u516b', '\u5341\u4e5d',
    # 廿:廿一:廿二:廿三:廿四:廿五:廿六:廿七:廿八:廿九
    '\u5eff', '\u5eff\u4e00', '\u5eff\u4e8c', '\u5eff\u4e09', '\u5eff\u56db',
    '\u5eff\u4e94', '\u5eff\u516d', '\u5eff\u4e03', '\u5eff\u516b', '\u5eff\u4e5d',
    # 卅:卅一
    '\u5345', '\u5345\u4e00')


class LocaleTime(object):
    """Stores and handles locale-specific information related to time.

    ATTRIBUTES:
        f_weekday -- full weekday names (7-item list)
        a_weekday -- abbreviated weekday names (7-item list)
        f_month -- full month names (13-item list; dummy value in [0], which
                    is added by code)
        a_month -- abbreviated month names (13-item list, dummy value in
                    [0], which is added by code)
        am_pm -- AM/PM representation (2-item list)
        LC_date_time -- format string for date/time representation (string)
        LC_date -- format string for date representation (string)
        LC_time -- format string for time representation (string)
        timezone -- daylight- and non-daylight-savings timezone representation
                    (2-item list of sets)
        lang -- Language used by instance (2-item tuple)
    """

    def __init__(self):
        """Set all attributes.

        Order of methods called matters for dependency reasons.

        The locale language is set at the offset and then checked again before
        exiting.  This is to make sure that the attributes were not set with a
        mix of information from more than one locale.  This would most likely
        happen when using threads where one thread calls a locale-dependent
        function while another thread changes the locale while the function in
        the other thread is still running.  Proper coding would call for
        locks to prevent changing the locale while locale-dependent code is
        running.  The check here is done in case someone does not think about
        doing this.

        Only other possible issue is if someone changed the timezone and did
        not call tz.tzset .  That is an issue for the programmer, though,
        since changing the timezone is worthless without that call.

        """
        self.lang = _getlang()
        self.__calc_weekday()
        self.__calc_month()
        self.__calc_am_pm()
        self.__calc_alt_digits()
        self.__calc_timezone()
        self.__calc_date_time()
        if _getlang() != self.lang:
            raise ValueError("locale changed during initialization")
        if time.tzname != self.tzname or time.daylight != self.daylight:
            raise ValueError("timezone changed during initialization")

    def __calc_weekday(self):
        # Set self.a_weekday and self.f_weekday using the calendar
        # module.
        a_weekday = [calendar.day_abbr[i].lower() for i in range(7)]
        f_weekday = [calendar.day_name[i].lower() for i in range(7)]
        self.a_weekday = a_weekday
        self.f_weekday = f_weekday

    def __calc_month(self):
        # Set self.f_month and self.a_month using the calendar module.
        a_month = [calendar.month_abbr[i].lower() for i in range(13)]
        f_month = [calendar.month_name[i].lower() for i in range(13)]
        self.a_month = a_month
        self.f_month = f_month

    def __calc_am_pm(self):
        # Set self.am_pm by using time.strftime().

        # The magic date (1999,3,17,hour,44,55,2,76,0) is not really that
        # magical; just happened to have used it everywhere else where a
        # static date was needed.
        am_pm = []
        for hour in (1, 22):
            time_tuple = time.struct_time((1999,3,17,hour,44,55,2,76,0))
            # br_FR has AM/PM info (' ',' ').
            am_pm.append(time.strftime("%p", time_tuple).lower().strip())
        self.am_pm = am_pm

    def __calc_alt_digits(self):
        # Set self.LC_alt_digits by using time.strftime().

        # The magic data should contain all decimal digits.
        time_tuple = time.struct_time((1998, 1, 27, 10, 43, 56, 1, 27, 0))
        s = time.strftime("%x%X", time_tuple)
        if s.isascii():
            # Fast path -- all digits are ASCII.
            self.LC_alt_digits = ()
            return

        digits = ''.join(sorted(set(re.findall(r'\d', s))))
        if len(digits) == 10 and ord(digits[-1]) == ord(digits[0]) + 9:
            # All 10 decimal digits from the same set.
            if digits.isascii():
                # All digits are ASCII.
                self.LC_alt_digits = ()
                return

            self.LC_alt_digits = [a + b for a in digits for b in digits]
            # Test whether the numbers contain leading zero.
            time_tuple2 = time.struct_time((2000, 1, 1, 1, 1, 1, 5, 1, 0))
            if self.LC_alt_digits[1] not in time.strftime("%x %X", time_tuple2):
                self.LC_alt_digits[:10] = digits
            return

        # Either non-Gregorian calendar or non-decimal numbers.
        if {'\u4e00', '\u4e03', '\u4e5d', '\u5341', '\u5eff'}.issubset(s):
            # lzh_TW
            self.LC_alt_digits = lzh_TW_alt_digits
            return

        self.LC_alt_digits = None

    def __calc_date_time(self):
        # Set self.LC_date_time, self.LC_date, self.LC_time and
        # self.LC_time_ampm by using time.strftime().

        # Use (1999,3,17,22,44,55,2,76,0) for magic date because the amount of
        # overloaded numbers is minimized.  The order in which searches for
        # values within the format string is very important; it eliminates
        # possible ambiguity for what something represents.
        time_tuple = time.struct_time((1999,3,17,22,44,55,2,76,0))
        time_tuple2 = time.struct_time((1999,1,3,1,1,1,6,3,0))
        replacement_pairs = []

        # Non-ASCII digits
        if self.LC_alt_digits or self.LC_alt_digits is None:
            for n, d in [(19, '%OC'), (99, '%Oy'), (22, '%OH'),
                         (44, '%OM'), (55, '%OS'), (17, '%Od'),
                         (3, '%Om'), (2, '%Ow'), (10, '%OI')]:
                if self.LC_alt_digits is None:
                    s = chr(0x660 + n // 10) + chr(0x660 + n % 10)
                    replacement_pairs.append((s, d))
                    if n < 10:
                        replacement_pairs.append((s[1], d))
                elif len(self.LC_alt_digits) > n:
                    replacement_pairs.append((self.LC_alt_digits[n], d))
                else:
                    replacement_pairs.append((time.strftime(d, time_tuple), d))
        replacement_pairs += [
            ('1999', '%Y'), ('99', '%y'), ('22', '%H'),
            ('44', '%M'), ('55', '%S'), ('76', '%j'),
            ('17', '%d'), ('03', '%m'), ('3', '%m'),
            # '3' needed for when no leading zero.
            ('2', '%w'), ('10', '%I'),
        ]

        date_time = []
        for directive in ('%c', '%x', '%X', '%r'):
            current_format = time.strftime(directive, time_tuple).lower()
            current_format = current_format.replace('%', '%%')
            # The month and the day of the week formats are treated specially
            # because of a possible ambiguity in some locales where the full
            # and abbreviated names are equal or names of different types
            # are equal. See doc of __find_month_format for more details.
            lst, fmt = self.__find_weekday_format(directive)
            if lst:
                current_format = current_format.replace(lst[2], fmt, 1)
            lst, fmt = self.__find_month_format(directive)
            if lst:
                current_format = current_format.replace(lst[3], fmt, 1)
            if self.am_pm[1]:
                # Must deal with possible lack of locale info
                # manifesting itself as the empty string (e.g., Swedish's
                # lack of AM/PM info) or a platform returning a tuple of empty
                # strings (e.g., MacOS 9 having timezone as ('','')).
                current_format = current_format.replace(self.am_pm[1], '%p')
            for tz_values in self.timezone:
                for tz in tz_values:
                    if tz:
                        current_format = current_format.replace(tz, "%Z")
            # Transform all non-ASCII digits to digits in range U+0660 to U+0669.
            if not current_format.isascii() and self.LC_alt_digits is None:
                current_format = re_sub(r'\d(?<![0-9])',
                                        lambda m: chr(0x0660 + int(m[0])),
                                        current_format)
            for old, new in replacement_pairs:
                current_format = current_format.replace(old, new)
            # If %W is used, then Sunday, 2005-01-03 will fall on week 0 since
            # 2005-01-03 occurs before the first Monday of the year.  Otherwise
            # %U is used.
            if '00' in time.strftime(directive, time_tuple2):
                U_W = '%W'
            else:
                U_W = '%U'
            current_format = current_format.replace('11', U_W)
            date_time.append(current_format)
        self.LC_date_time = date_time[0]
        self.LC_date = date_time[1]
        self.LC_time = date_time[2]
        self.LC_time_ampm = date_time[3]

    def __find_month_format(self, directive):
        """Find the month format appropriate for the current locale.

        In some locales (for example French and Hebrew), the default month
        used in __calc_date_time has the same name in full and abbreviated
        form.  Also, the month name can by accident match other part of the
        representation: the day of the week name (for example in Morisyen)
        or the month number (for example in Japanese).  Thus, cycle months
        of the year and find all positions that match the month name for
        each month,  If no common positions are found, the representation
        does not use the month name.
        """
        full_indices = abbr_indices = None
        for m in range(1, 13):
            time_tuple = time.struct_time((1999, m, 17, 22, 44, 55, 2, 76, 0))
            datetime = time.strftime(directive, time_tuple).lower()
            indices = set(_findall(datetime, self.f_month[m]))
            if full_indices is None:
                full_indices = indices
            else:
                full_indices &= indices
            indices = set(_findall(datetime, self.a_month[m]))
            if abbr_indices is None:
                abbr_indices = set(indices)
            else:
                abbr_indices &= indices
            if not full_indices and not abbr_indices:
                return None, None
        if full_indices:
            return self.f_month, '%B'
        if abbr_indices:
            return self.a_month, '%b'
        return None, None

    def __find_weekday_format(self, directive):
        """Find the day of the week format appropriate for the current locale.

        Similar to __find_month_format().
        """
        full_indices = abbr_indices = None
        for wd in range(7):
            time_tuple = time.struct_time((1999, 3, 17, 22, 44, 55, wd, 76, 0))
            datetime = time.strftime(directive, time_tuple).lower()
            indices = set(_findall(datetime, self.f_weekday[wd]))
            if full_indices is None:
                full_indices = indices
            else:
                full_indices &= indices
            if self.f_weekday[wd] != self.a_weekday[wd]:
                indices = set(_findall(datetime, self.a_weekday[wd]))
            if abbr_indices is None:
                abbr_indices = set(indices)
            else:
                abbr_indices &= indices
            if not full_indices and not abbr_indices:
                return None, None
        if full_indices:
            return self.f_weekday, '%A'
        if abbr_indices:
            return self.a_weekday, '%a'
        return None, None

    def __calc_timezone(self):
        # Set self.timezone by using time.tzname.
        # Do not worry about possibility of time.tzname[0] == time.tzname[1]
        # and time.daylight; handle that in strptime.
        try:
            time.tzset()
        except AttributeError:
            pass
        self.tzname = time.tzname
        self.daylight = time.daylight
        no_saving = frozenset({"utc", "gmt", self.tzname[0].lower()})
        if self.daylight:
            has_saving = frozenset({self.tzname[1].lower()})
        else:
            has_saving = frozenset()
        self.timezone = (no_saving, has_saving)


class TimeRE(dict):
    """Handle conversion from format directives to regexes."""

    def __init__(self, locale_time=None):
        """Create keys/values.

        Order of execution is important for dependency reasons.

        """
        if locale_time:
            self.locale_time = locale_time
        else:
            self.locale_time = LocaleTime()
        base = super()
        mapping = {
            # The " [1-9]" part of the regex is to make %c from ANSI C work
            'd': r"(?P<d>3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])",
            'f': r"(?P<f>[0-9]{1,6})",
            'H': r"(?P<H>2[0-3]|[0-1]\d|\d| \d)",
            'k': r"(?P<H>2[0-3]|[0-1]\d|\d| \d)",
            'I': r"(?P<I>1[0-2]|0[1-9]|[1-9]| [1-9])",
            'l': r"(?P<I>1[0-2]|0[1-9]|[1-9]| [1-9])",
            'G': r"(?P<G>\d\d\d\d)",
            'j': r"(?P<j>36[0-6]|3[0-5]\d|[1-2]\d\d|0[1-9]\d|00[1-9]|[1-9]\d|0[1-9]|[1-9])",
            'm': r"(?P<m>1[0-2]|0[1-9]|[1-9])",
            'M': r"(?P<M>[0-5]\d|\d)",
            'S': r"(?P<S>6[0-1]|[0-5]\d|\d)",
            'U': r"(?P<U>5[0-3]|[0-4]\d|\d)",
            'w': r"(?P<w>[0-6])",
            'u': r"(?P<u>[1-7])",
            'V': r"(?P<V>5[0-3]|0[1-9]|[1-4]\d|\d)",
            # W is set below by using 'U'
            'y': r"(?P<y>\d\d)",
            'Y': r"(?P<Y>\d\d\d\d)",
            'z': r"(?P<z>[+-]\d\d:?[0-5]\d(:?[0-5]\d(\.\d{1,6})?)?|(?-i:Z))",
            'A': self.__seqToRE(self.locale_time.f_weekday, 'A'),
            'a': self.__seqToRE(self.locale_time.a_weekday, 'a'),
            'B': self.__seqToRE(_fixmonths(self.locale_time.f_month[1:]), 'B'),
            'b': self.__seqToRE(_fixmonths(self.locale_time.a_month[1:]), 'b'),
            'p': self.__seqToRE(self.locale_time.am_pm, 'p'),
            'Z': self.__seqToRE((tz for tz_names in self.locale_time.timezone
                                        for tz in tz_names),
                                'Z'),
