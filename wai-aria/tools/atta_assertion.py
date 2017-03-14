#!/usr/bin/env python3
#
# atta_assertion
# Shareable Assertion support for Accessible Technology Test Adapters
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2017 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import re
import traceback

from textwrap import TextWrapper


class AttaAssertion:

    STATUS_PASS = "PASS"
    STATUS_FAIL = "FAIL"
    STATUS_NOT_RUN = "NOT RUN"

    EXPECTATION_EXISTS = "exists"
    EXPECTATION_IS = "is"
    EXPECTATION_IS_NOT = "isNot"
    EXPECTATION_CONTAINS = "contains"
    EXPECTATION_DOES_NOT_CONTAIN = "doesNotContain"
    EXPECTATION_IS_LESS_THAN = "isLT"
    EXPECTATION_IS_LESS_THAN_OR_EQUAL = "isLTE"
    EXPECTATION_IS_GREATER_THAN = "isGT"
    EXPECTATION_IS_GREATER_THAN_OR_EQUAL = "isGTE"
    EXPECTATION_IS_TYPE = "isType"
    EXPECTATION_IS_ANY = "isAny"

    CLASS_EVENT = "event"
    CLASS_PROPERTY = "property"
    CLASS_RELATION = "relation"
    CLASS_RESULT = "result"
    CLASS_TBD = "TBD"

    _text_wrapper = TextWrapper(width=80, break_on_hyphens=False, break_long_words=False)
    _labels = ["ASSERTION:", "STATUS:", "ACTUAL VALUE:", "MESSAGES:"]

    def __init__(self, obj, assertion, atta):
        self._atta = atta
        self._obj = obj
        self._as_string = " ".join(map(str, assertion))
        self._test_class = assertion[0]
        self._test_string = assertion[1]
        self._expectation = assertion[2]
        self._expected_value = assertion[3]
        self._actual_value = None
        self._messages = []
        self._status = self.STATUS_NOT_RUN

    def __str__(self):
        label_width = max(list(map(len, self._labels))) + 2
        self._text_wrapper.subsequent_indent = " " * (label_width+1)

        def _wrap(towrap):
            if isinstance(towrap, list):
                towrap = ",\n".join(towrap)
            return "\n".join(self._text_wrapper.wrap(str(towrap)))

        return "\n\n{self._labels[0]:>{width}} {self._as_string}" \
               "\n{self._labels[1]:>{width}} {self._status}" \
               "\n{self._labels[2]:>{width}} {actual_value}" \
               "\n{self._labels[3]:>{width}} {messages}\n".format(
                   width=label_width,
                   self=self,
                   actual_value=_wrap(self._actual_value),
                   messages=_wrap(self._messages))

    def _on_exception(self):
        error = traceback.format_exc(limit=1, chain=False)
        self._messages.append(re.sub("\s+", " ", error))

    def _value_to_string(self, value):
        value_type = type(value)

        if value_type == str:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "String"
            return value

        if value_type == bool:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "Boolean"
            return str(value).lower()

        if value_type in (int, float):
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "Number"
            return str(value)

        if value_type in (tuple, list, set):
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "List"
            return value_type(map(self._value_to_string, value))

        if value_type == range:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "List"
            return str(range)

        if value_type == dict:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "List"
            return {self._value_to_string(k): self._value_to_string(v) for k, v in value.items()}

        if self._expectation == self.EXPECTATION_IS_TYPE:
            return "Undefined"

        return str(value)


class AttaEventAssertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaPropertyAssertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    def get_property_value(self):
        return None

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaRelationAssertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    def get_relation_targets(self):
        return []

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaResultAssertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    def get_method_result(self):
        return None

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaDumpInfoAssertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    def run(self):
        return self._status, " ".join(self._messages), str(self)
