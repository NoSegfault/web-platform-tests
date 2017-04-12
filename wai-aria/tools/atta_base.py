#!/usr/bin/env python3
#
# atta_base
# Optional base class for python3 Accessible Technology Test Adapters
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2017 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import faulthandler
import signal
import sys
import threading
import traceback

from http.server import HTTPServer
from atta_assertion import AttaAssertion
from atta_request_handler import AttaRequestHandler


class Atta:
    """Optional base class for python3 Accessible Technology Test Adapters."""

    STATUS_ERROR = "ERROR"
    STATUS_OK = "OK"

    FAILURE_ATTA_NOT_ENABLED = "ATTA not enabled"
    FAILURE_ATTA_NOT_READY = "ATTA not ready"
    FAILURE_ELEMENT_NOT_FOUND = "Element not found"

    LOG_DEBUG = 0
    LOG_INFO = 1
    LOG_WARNING = 2
    LOG_ERROR = 3
    LOG_NONE = 100

    LOG_LEVELS = {
        LOG_DEBUG: "DEBUG",
        LOG_INFO: "INFO",
        LOG_WARNING: "WARNING",
        LOG_ERROR: "ERROR",
    }

    FORMAT_NORMAL = "\x1b[1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_GOOD = "\x1b[32;1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_WARNING = "\x1b[33;1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_BAD = "\x1b[31;1m%(label)s\x1b[22m%(msg)s\x1b[0m"

    def __init__(self, host, port, name, version, api, log_level=None):
        """Initializes this ATTA."""

        self._log_level = log_level or self.LOG_DEBUG
        self._host = host
        self._port = int(port)
        self._server = None
        self._server_thread = None
        self._atta_name = name
        self._atta_version = version
        self._api_name = api
        self._api_version = ""
        self._enabled = False
        self._ready = False
        self._next_test = None, ""
        self._current_document = None
        self._monitored_event_types = []
        self._event_history = []
        self._listeners = {}
        self._supported_methods = {}
        self._supported_relation_types = []

        if not sys.version_info[0] == 3:
            self._print(self.LOG_ERROR, "This ATTA requires Python 3.")
            return

        self._api_version = self._get_system_api_version()

        if not self._get_accessibility_enabled() \
           and not self._set_accessibility_enabled(True):
            return

        self._supported_methods = self.get_supported_methods()
        self._supported_relation_types = self.get_supported_relation_types()
        self._enabled = True

    @staticmethod
    def _on_exception():
        """Handles exceptions, returning a string with the error."""

        return "EXCEPTION: %s" % traceback.format_exc(limit=1, chain=False)

    def _print(self, level, string, label=None, formatting=None, **kwargs):
        """Prints the string, typically to stdout."""

        if level < self._log_level:
            return

        if label is None:
            label = "%s: " % self.LOG_LEVELS.get(level)

        if formatting is None:
            if level == self.LOG_ERROR:
                formatting = self.FORMAT_BAD
            elif level == self.LOG_WARNING:
                formatting = self.FORMAT_WARNING
            else:
                formatting = self.FORMAT_NORMAL

        print(formatting % {"label": label, "msg": string})

    def start(self, **kwargs):
        """Starts this ATTA (i.e. before running a series of tests)."""

        if not self._enabled:
            self._print(self.LOG_ERROR, "Start failed because ATTA is not enabled.")
            return

        faulthandler.enable(all_threads=False)
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

        self._print(self.LOG_INFO, "Starting on http://%s:%s/" % (self._host, self._port), "SERVER: ")
        self._server = HTTPServer((self._host, self._port), AttaRequestHandler)
        AttaRequestHandler.set_atta(self)

        if self._server_thread is None:
            self._server_thread = threading.Thread(target=self._server.serve_forever)
            self._server_thread.start()

    def get_info(self, **kwargs):
        """Returns a dict of details about this ATTA needed by the harness."""

        return {"ATTAname": self._atta_name,
                "ATTAversion": self._atta_version,
                "API": self._api_name,
                "APIversion": self._api_version}

    def is_enabled(self, **kwargs):
        """Returns True if this ATTA is enabled."""

        return self._enabled

    def is_ready(self, document=None, **kwargs):
        """Returns True if this ATTA is able to proceed with a test run."""

        if self._ready:
            return True

        test_name, test_uri = self._next_test
        if test_name is None:
            return False

        document = document or self._current_document
        if document is None:
            return False

        uri = self._get_uri(document)
        self._ready = uri and uri == test_uri
        if self._ready:
            self._current_document = document
            self._print(self.LOG_DEBUG, "Test is '%s' (%s)" % (test_name, test_uri), "READY: ")

        return self._ready

    def start_test_run(self, name, url, **kwargs):
        """Sets the test details the ATTA should be looking for. The ATTA should
        update its "ready" status upon finding that file."""

        self._print(self.LOG_INFO, "%s (%s)" % (name, url), "START TEST RUN: ")
        self._next_test = name, url
        self._ready = False

    def start_listen(self, event_types, **kwargs):
        """Causes the ATTA to start listening for the specified events."""

        self._print(self.LOG_DEBUG, "%s" % event_types, "START LISTEN: ")
        self._monitored_event_types = []
        self._event_history = []

        for event_type in event_types:
            self._register_listener(event_type, self._on_test_event, **kwargs)
            self._monitored_event_types.append(event_type)

    def _run_test(self, obj, assertion, **kwargs):
        """Runs a single assertion on obj, returning a results dict."""

        test_class = self._get_assertion_test_class(assertion)
        if test_class is None:
            result = AttaAssertion.STATUS_FAIL
            message = "ERROR: %s is not a valid assertion" % assertion
            log = message
        else:
            test = test_class(obj, assertion, self)
            result, message, log = test.run()

        label = "%s: " % result
        string = " ".join(map(str, assertion))
        if result == AttaAssertion.STATUS_PASS:
            formatting = self.FORMAT_GOOD
        elif result == AttaAssertion.STATUS_FAIL:
            if not (test_class and test.is_known_issue()):
                formatting = self.FORMAT_BAD
            else:
                formatting = self.FORMAT_WARNING
            if message:
                string = "%s (%s)" % (string, message)
        else:
            formatting = self.FORMAT_WARNING

        self._print(self.LOG_INFO, string, label, formatting)
        return {"result": result, "message": message, "log": log}

    def run_tests(self, obj_id, assertions):
        """Runs the assertions on the object with the specified id, returning
        a dict with the results, the status of the run, and any messages."""

        if not self.is_enabled():
            self._print(self.LOG_WARNING, "ATTA is not enabled", "RUN TESTS: ")
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_ENABLED,
                    "results": []}

        if not self.is_ready():
            self._print(self.LOG_WARNING, "ATTA is not ready", "RUN TESTS: ")
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_READY,
                    "results": []}

        to_run = self._create_platform_assertions(assertions)
        self._print(self.LOG_DEBUG, "%i assertion(s) for '%s' " % (len(to_run), obj_id), "RUN TESTS: ")

        obj = self._get_element_with_id(self._current_document, obj_id)
        if not obj:
            # We may be testing that an object is not exposed (e.g. because it is hidden).
            # But we may instead have a test-file error or an accessibility bug. So warn.
            self._print(self.LOG_WARNING, "Accessible element not found", "RUN TESTS: ")

        results = [self._run_test(obj, a) for a in to_run]
        return {"status": self.STATUS_OK, "results": results}

    def stop_listen(self, **kwargs):
        """Causes the ATTA to stop listening for the specified events."""

        self._print(self.LOG_DEBUG, "%s" % self._monitored_event_types, "STOP LISTEN: ")
        for event_type in self._monitored_event_types:
            self._deregister_listener(event_type, self._on_test_event, **kwargs)

        self._monitored_event_types = []
        self._event_history = []

    def end_test_run(self, **kwargs):
        """Cleans up cached information at the end of a test run."""

        name, url = self._next_test
        self._print(self.LOG_DEBUG, "%s (%s)" % (name, url), "STOP TEST RUN: ")

        self._current_document = None
        self._next_test = None, ""
        self._ready = False

    def shutdown(self, signum=None, frame=None, **kwargs):
        """Shuts down this ATTA (i.e. after all tests have been run)."""

        if not self._enabled:
            return

        self._ready = False

        try:
            signal_string = "on signal %s" % signal.Signals(signum).name
        except AttributeError:
            signal_string = "on signal %s" % str(signum)
        except:
            signal_string = ""
        self._print(self.LOG_INFO, "Shutting down %s" % signal_string, "SERVER:")

        if self._server is not None:
            thread = threading.Thread(target=self._server.shutdown)
            thread.start()

    def _get_element_with_id(self, root, element_id, **kwargs):
        """Returns the accessible descendant of root with the specified id."""

        if not element_id:
            return None

        pred = lambda x: self._get_id(x) == element_id
        return self._find_descendant(root, pred, **kwargs)

    def _in_current_document(self, obj, **kwargs):
        """Returns True if obj is an element in the current test's document."""

        if not self._current_document:
            return False

        pred = lambda x: x == self._current_document
        return self._find_ancestor(obj, pred, **kwargs) is not None

    def _find_ancestor(self, obj, pred, **kwargs):
        """Returns the ancestor of obj for which pred returns True."""

        if obj is None:
            return None

        parent = self._get_parent(obj)
        while parent:
            if pred(parent):
                return parent
            parent = self._get_parent(parent)

        return None

    def _find_descendant(self, root, pred, **kwargs):
        """Returns the descendant of root for which pred returns True."""

        if pred(root) or root is None:
            return root

        children = self._get_children(root, **kwargs)
        for child in children:
            element = self._find_descendant(child, pred, **kwargs)
            if element:
                return element

        return None

    def _get_rendering_engine(self, **kwargs):
        """Returns a string with details of the user agent's rendering engine."""

        self._print(self.LOG_DEBUG, "_get_rendering_engine() not implemented")
        return ""

    def _get_system_api_version(self, **kwargs):
        """Returns a string with the installed version of the accessibility API."""

        self._print(self.LOG_DEBUG, "_get_system_api_version() not implemented")
        return ""

    def _get_accessibility_enabled(self, **kwargs):
        """Returns True if accessibility support is enabled on this platform."""

        self._print(self.LOG_DEBUG, "_get_accessibility_enabled() not implemented")
        return False

    def _set_accessibility_enabled(self, enable, **kwargs):
        """Returns True if accessibility support was successfully set."""

        self._print(self.LOG_DEBUG, "_set_accessibility_enabled() not implemented")
        return False

    def _register_listener(self, event_type, callback, **kwargs):
        """Registers an accessible-event listener on the platform."""

        self._print(self.LOG_DEBUG, "_register_listener() not implemented")

    def _deregister_listener(self, event_type, callback, **kwargs):
        """De-registers an accessible-event listener on the platform."""

        self._print(self.LOG_DEBUG, "_deregister_listener() not implemented")

    def _get_assertion_test_class(self, assertion, **kwargs):
        """Returns the appropriate Assertion class for assertion."""

        return AttaAssertion.get_test_class(assertion)

    def _create_platform_assertions(self, assertions, **kwargs):
        """Performs platform-specific changes needed to harness assertions."""

        return assertions

    def _get_id(self, obj, **kwargs):
        """Returns the element id associated with obj or an empty string upon failure."""

        self._print(self.LOG_DEBUG, "_get_id() not implemented")
        return ""

    def _get_uri(self, document, **kwargs):
        """Returns the URI associated with document or an empty string upon failure."""

        self._print(self.LOG_DEBUG, "_get_uri() not implemented")
        return ""

    def _get_children(self, obj, **kwargs):
        """Returns the children of obj or [] upon failure or absence of children."""

        self._print(self.LOG_DEBUG, "_get_children() not implemented")
        return []

    def _get_parent(self, obj, **kwargs):
        """Returns the parent of obj or None upon failure."""

        self._print(self.LOG_DEBUG, "_get_parent() not implemented")
        return None

    def get_property_value(self, obj, property_name, **kwargs):
        """Returns the value of property_name for obj."""

        self._print(self.LOG_DEBUG, "get_property_value() not implemented")
        return None

    def get_relation_targets(self, obj, relation_type, **kwargs):
        """Returns the elements of pointed to by relation_type for obj."""

        self._print(self.LOG_DEBUG, "get_relation_targets() not implemented")
        return []

    def get_client_side_method(self, server_side_method, **kwargs):
        """Returns the client-side API method for server_side_method."""

        self._print(self.LOG_DEBUG, "get_client_side_method() not implemented")
        return server_side_method

    def get_supported_methods(self, obj=None, **kwargs):
        """Returns a name:callable dict of supported platform methods."""

        self._print(self.LOG_DEBUG, "get_supported_methods() not implemented")
        return {}

    def get_bug(self, expected_result, actual_result, **kwargs):
        """Returns a string containing bug information for an assertion."""

        self._print(self.LOG_DEBUG, "get_bug() not implemented")
        return ""

    def string_to_method_and_arguments(self, callable_as_string, **kwargs):
        """Converts callable_as_string into the appropriate callable platform method
        and list of arguments with the appropriate types."""

        self._print(self.LOG_DEBUG, "string_to_method_and_arguments() not implemented")
        return None, []

    def get_result(self, method, arguments, **kwargs):
        """Returns the result of calling method with the specified arguments."""

        self._print(self.LOG_DEBUG, "get_result_for_method_and_arguments() not implemented")
        return None

    def get_supported_properties(self, obj, **kwargs):
        """Returns a list of supported platform properties for obj."""

        self._print(self.LOG_DEBUG, "get_supported_properties() not implemented")
        return []

    def get_supported_relation_types(self, obj=None, **kwargs):
        """Returns a list of supported platform relation types."""

        self._print(self.LOG_DEBUG, "get_supported_relation_types() not implemented")
        return []

    def get_event_history(self, **kwargs):
        """Returns the list of accessibility events recorded by this ATTA."""

        return self._event_history

    def string_to_value(self, string, **kwargs):
        """Returns the value (e.g. a platform constant) represented by string."""

        self._print(self.LOG_DEBUG, "string_to_value() not implemented")
        return None

    def type_to_string(self, value, **kwargs):
        """Returns the type of value as a harness-compliant string."""

        value_type = type(value)

        if value_type == str:
            return "String"

        if value_type == bool:
            return "Boolean"

        if value_type in (int, float):
            return "Number"

        if value_type in (tuple, list, set, range, dict):
            return "List"

        return "Undefined"

    def value_to_string(self, value, **kwargs):
        """Returns the string representation of value (e.g. a platform constant)."""

        value_type = type(value)

        if value_type == str:
            return value

        if value_type == bool:
            return str(value).lower()

        if value_type in (int, float):
            return str(value)

        if value_type in (tuple, list, set):
            return value_type(map(self.value_to_string, value))

        if value_type == range:
            return str(range)

        if value_type == dict:
            return {self.value_to_string(k): self.value_to_string(v) for k, v in value.items()}

        return str(value)

    def _on_load_complete(self, data, **kwargs):
        """Callback for the platform's signal that a document has loaded."""

        self._print(self.LOG_DEBUG, "_on_load_complete() not implemented")

    def _on_test_event(self, data, **kwargs):
        """Callback for platform accessibility events the ATTA is testing."""

        self._print(self.LOG_DEBUG, "_on_test_event() not implemented")
