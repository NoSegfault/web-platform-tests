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
import sys
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

    def __init__(self, obj, assertion):
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
        labels = ["ASSERTION:", "STATUS:", "ACTUAL VALUE:", "MESSAGES:"]
        label_width = max(list(map(len, labels))) + 2
        indent = " " * (label_width+1)
        wrapper = TextWrapper(subsequent_indent=indent, width=80, break_on_hyphens=False, break_long_words=False)

        def _wrap(towrap):
            if isinstance(towrap, list):
                return "\n".join(wrapper.wrap(", ".join(towrap)))
            return "\n".join(wrapper.wrap(str(towrap)))

        return "\n\n{labels[0]:>{width}} {self._as_string}" \
               "\n{labels[1]:>{width}} {self._status}" \
               "\n{labels[2]:>{width}} {actual_value}" \
               "\n{labels[3]:>{width}} {messages}\n".format(
                   width=label_width,
                   self=self,
                   actual_value=_wrap(self._actual_value),
                   messages=_wrap(self._messages),
                   labels=labels)

    def _on_exception(self):
        etype, evalue, tb = sys.exc_info()
        error = traceback.format_exc(limit=1, chain=False)
        self._messages.append(re.sub("\s+", " ", error))


class AttaEventAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaPropertyAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_property_value(self):
        return None

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaRelationAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_relation_targets(self):
        return []

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaResultAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_method_result(self):
        return None

    def run(self):
        return self._status, " ".join(self._messages), str(self)


class AttaDumpInfoAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def run(self):
        return self._status, " ".join(self._messages), str(self)
