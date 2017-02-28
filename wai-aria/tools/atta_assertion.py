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


class AttaAssertion:

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NOT_RUN = "NOT RUN"

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
        self._test_class = assertion[0]
        self._test_string = assertion[1]
        self._expectation = assertion[2]
        self._expected_value = assertion[3]
        self._actual_value = None
        self._msgs = []
        self._status = self.NOT_RUN

    def __str__(self):
        return "\n ASSERTION: %s %s %s %s" \
               "\n    STATUS: %s (Actual value: %s)" \
               "\n  MESSAGES: %s\n" % \
               (self._test_class,
                self._test_string,
                self._expectation,
                self._expected_value,
                self._status,
                self._actual_value,
                ", ".join(self._msgs))

    def _on_exception(self):
        etype, evalue, tb = sys.exc_info()
        error = traceback.format_exc(limit=1, chain=False)
        self._msgs.append(error)


class AttaEventAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def run(self):
        pass


class AttaPropertyAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_property_value(self):
        return None

    def run(self):
        pass


class AttaRelationAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_relation_targets(self):
        return []

    def run(self):
        pass


class AttaResultAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_method_result(self):
        pass


class AttaDumpInfoAssertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)
