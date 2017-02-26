#!/usr/bin/python3
#
# atta-atk-atspi
#
# Accessible Technology Test Adapter for ATK/AT-SPI2
# Tests ATK (server-side) implementations via AT-SPI2 (client-side)
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import argparse
import gi
import json
import os
import pyatspi
import re
import signal
import sys
import threading
import time
import traceback

gi.require_version("Atk", "1.0")

from gi.repository import Atk, Gio, GLib
from http.server import BaseHTTPRequestHandler, HTTPServer


class Assertion():

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NOT_RUN = "NOT RUN"

    EXPECTATION_IS = "is"
    EXPECTATION_IS_NOT = "isNot"
    EXPECTATION_CONTAINS = "contains"
    EXPECTATION_DOES_NOT_CONTAIN = "doesNotContain"
    EXPECTATION_IS_ANY = "isAny"
    EXPECTATION_IS_TYPE = "isType"
    EXPECTATION_EXISTS = "exists"

    TEST_EVENT = "event"
    TEST_PROPERTY = "property"
    TEST_RELATION = "relation"
    TEST_RESULT = "result"
    TEST_TBD = "TBD"

    PROPERTIES = ["accessible",
                  "id",
                  "role",
                  "name",
                  "description",
                  "childCount",
                  "objectAttributes",
                  "states",
                  "relations",
                  "interfaces",
                  "parentID"]

    # N.B. These are not all possible interfaces; these are interfaces
    # with methods test writers may need to include in their assertions
    # for the purpose of verifying those methods have been correctly
    # implemented.
    INTERFACES = ["Action",
                  "Component",
                  "Document",
                  "EditableText",
                  "Hyperlink",
                  "Hypertext",
                  "Image",
                  "Selection",
                  "Table",
                  "TableCell",
                  "Text",
                  "Value"]

    def __init__(self, obj, assertion, verbose=False):
        self._obj = obj
        self._test_string = assertion[1]
        self._expectation = assertion[2]
        self._expected_value = assertion[3]
        self._actual_value = None
        self._msgs = []
        self._verbose = verbose
        self._status = self.NOT_RUN

    def __str__(self):
        rv = "%s: %s %s %s" % (self._status, self._test_string, self._expectation, self._expected_value)

        return rv

    @classmethod
    def get_test_class(cls, assertion):
        test_class = assertion[0]
        if test_class == cls.TEST_PROPERTY:
            return PropertyAssertion
        if test_class == cls.TEST_EVENT:
            return EventAssertion
        if test_class == cls.TEST_RELATION:
            return RelationAssertion
        if test_class == cls.TEST_RESULT:
            return ResultAssertion
        if test_class == cls.TEST_TBD:
            return DumpInfoAssertion

        print("ERROR: Unhandled test class: %s (assertion: %s)" % (test_class, assertion))
        return None

    @staticmethod
    def _enum_to_string(enum):
        try:
            rv = enum.value_name.replace("ATSPI_", "")
        except:
            rv = str(enum)

        # ATK (which we're testing) has ROLE_STATUSBAR; AT-SPI (which we're using)
        # has ROLE_STATUS_BAR. ATKify the latter so we can verify the former.
        rv = rv.replace("ROLE_STATUS_BAR", "ROLE_STATUSBAR")

        return rv

    def _get_arg_type(self, arg):
        typeinfo = arg.get_type()
        typetag = typeinfo.get_tag()
        return gi._gi.TypeTag(typetag)

    def _get_arg_info(self, arg):
        name = arg.get_name()
        argtype = self._get_arg_type(arg)
        return "%s %s" % (argtype.__name__, name)

    def _get_method_details(self, method):
        name = method.get_name()
        args = list(map(self._get_arg_info, method.get_arguments()))
        return "%s(%s)" % (name, ", ".join(args))

    def _get_interface_methods(self, interface_name):
        gir = gi.Repository.get_default()

        try:
            info = gir.find_by_name("Atspi", interface_name)
        except:
            self._on_exception()
            return []

        if not info:
            return []

        return info.get_methods()

    def _get_interfaces(self, obj):
        if not obj:
            return []

        try:
            interfaces = pyatspi.utils.listInterfaces(obj)
        except:
            self._on_exception()
            return []

        return list(filter(lambda x: x in self.INTERFACES, interfaces))

    def _get_relations(self, obj):
        if not obj:
            return {}

        try:
            relation_set = obj.getRelationSet()
        except:
            self._on_exception()
            return {}

        relations = {}
        for r in relation_set:
            relation = self._enum_to_string(r.getRelationType())
            targets = [r.getTarget(i) for i in range(r.getNTargets())]
            relations[relation] = list(map(self._get_id, targets))

        return relations

    def _get_states(self, obj):
        if not obj:
            return []

        try:
            states = obj.getState().getStates()
        except:
            self._on_exception()
            return []

        return [self._enum_to_string(s) for s in states]

    def _get_role(self, obj):
        if not obj:
            return None

        try:
            role = obj.getRole()
        except:
            self._on_exception()
            return None

        return self._enum_to_string(role)

    def _get_object_attribute(self, obj, attr):
        if not obj:
            return None

        try:
            obj.clearCache()
            attrs = dict([a.split(':', 1) for a in obj.getAttributes()])
        except:
            self._on_exception()
            return None

        return attrs.get(attr)

    def _get_id(self, obj):
        return self._get_object_attribute(obj, "id") \
            or self._get_object_attribute(obj, "html-id")

    def _get_property(self, prop):
        if prop not in self.PROPERTIES:
            self._msgs.append("ERROR: Unknown property: %s" % prop)
            return None

        if prop == "accessible":
            return bool(self._obj)

        if not self._obj:
            self._msgs.append("ERROR: Accessible not found")
            return None

        try:
            self._obj.clearCache()
        except:
            self._on_exception()

        if prop == "id":
            return self._get_id(self._obj)

        if prop == "role":
            return self._get_role(self._obj)

        if prop == "name":
            return self._obj.name

        if prop == "description":
            return self._obj.description

        if prop == "childCount":
            return self._obj.childCount

        if prop == "objectAttributes":
            return self._obj.getAttributes()

        if prop == "interfaces":
            return self._get_interfaces(self._obj)

        if prop == "states":
            return self._get_states(self._obj)

        if prop == "relations":
            return self._get_relations(self._obj)

        if prop == "parentID":
            return self._get_id(self._obj.parent)

        self._msgs.append("ERROR: Unhandled property: %s" % prop)
        return None

    def _value_to_harness_string(self, value):
        if self._expectation == self.EXPECTATION_IS_TYPE:
            return value

        if isinstance(value, bool):
            return str(value).lower()

        if isinstance(value, (int, float)):
            return str(value)

        if isinstance(value, (pyatspi.Accessible, pyatspi.Atspi.Accessible)):
            return self._get_id(value)

        return value

    def _get_value(self):
        pass

    def _get_result(self):
        self._actual_value = self._get_value()
        if self._expectation == self.EXPECTATION_IS:
            result = self._expected_value == self._actual_value
        elif self._expectation == self.EXPECTATION_IS_NOT:
            result = self._expected_value != self._actual_value
        elif self._expectation == self.EXPECTATION_CONTAINS:
            result = self._actual_value and self._expected_value in self._actual_value
        elif self._expectation == self.EXPECTATION_DOES_NOT_CONTAIN:
            if self._actual_value is None:
                self._actual_value = []
            result = self._expected_value not in self._actual_value
        elif self._expectation == self.EXPECTATION_IS_ANY:
            result = self._actual_value in self._expected_value
        elif self._expectation == self.EXPECTATION_IS_TYPE:
            result = type(self._actual_value).__name__ == self._expected_value
        elif self._expectation == self.EXPECTATION_EXISTS:
            # TODO - JD: This sanity check may be needed elsewhere.
            actual = self._value_to_harness_string(self._actual_value)
            result = self._expected_value == actual
        else:
            result = False

        if result:
            self._status = self.PASS
        else:
            self._status = self.FAIL

        return result

    def _on_exception(self):
        etype, evalue, tb = sys.exc_info()
        error = traceback.format_exc(limit=1, chain=False)
        self._msgs.append(error)

    def run(self):
        result, log = self._get_result(), ""
        if not result or self._verbose:
            log = "(Got: %s)\n" % re.sub("[\[\]\"\']", "", str(self._actual_value))
            self._msgs.append(log)

        return self._status, "\n".join(self._msgs), log


class DumpInfoAssertion(Assertion):

    def __init__(self, obj, assertion=None, verbose=False):
        assertion = [""] * 4
        super().__init__(obj, assertion, verbose)

    def _get_interfaces(self, obj):
        if not self._verbose:
            return pyatspi.utils.listInterfaces(obj)

        interfaces = {}
        for iface in pyatspi.utils.listInterfaces(obj):
            if iface not in self.INTERFACES:
                continue

            methods = self._get_interface_methods(iface)
            interfaces[iface] = list(map(self._get_method_details, methods))

        return interfaces

    def run(self):
        self._msgs.append("DRY RUN")

        info = {}
        info["properties"] = {}
        for prop in self.PROPERTIES:
            info["properties"][prop] = self._get_property(prop)

        log = json.dumps(info, indent=4, sort_keys=True)
        return True, "\n".join(self._msgs), log

class PropertyAssertion(Assertion):

    def _get_value(self):
        if self._test_string == "objectAttributes" and self._expected_value.count(":") == 1:
            attr_name = self._expected_value.split(":")[0]
            attr_value = self._get_object_attribute(self._obj, attr_name)
            if attr_value is not None:
                return "%s:%s" % (attr_name, attr_value)

        return self._get_property(self._test_string)


class RelationAssertion(Assertion):

    def _get_value(self):
        relations = self._get_relations(self._obj)
        return re.sub("['\s]", "", str(relations.get(self._test_string)))


class ResultAssertion(Assertion):

    def __init__(self, obj, assertion, verbose=False):
        super().__init__(obj, assertion, verbose)
        self._errors = []
        self._method = None
        self._args = []

        iface, call = re.split("\.", self._test_string, maxsplit=1)
        call = call.replace("atk_%s_" % iface.lower(), "")
        try:
            function, args = re.split("\(", call, maxsplit=1)
            args = args[:-1]
        except ValueError:
            function = call
            args = ""

        methods = self._get_interface_methods(iface)
        if not methods:
            self._errors.append("ERROR: '%s' interface not found." % iface)
            self._errors.append("INTERFACES: %s\n" % ", ".join(self.INTERFACES))
            return

        names = list(map(lambda x: x.get_name(), methods))
        if names and function not in names:
            self._errors.append("ERROR: '%s' method not found." % function)
            self._errors.append("METHODS: %s\n" % ", ".join(names))
            return

        self._method = list(filter(lambda x: x.get_name() == function, methods))[0]

        expectedargs = self._method.get_arguments()
        actualargs = list(filter(lambda x: x != "", args.split(",")))
        argtypes = list(map(self._get_arg_type, expectedargs))
        for i, argtype in enumerate(argtypes):
            arg = actualargs[i]
            try:
                self._args.append(argtype(arg))
            except:
                info = self._get_arg_info(expectedargs[i])
                self._errors.append("ERROR: Argument %i should be %s (got: %s)\n" % (i, info, arg))

        return

    def _get_value(self):
        if self._errors:
            return None

        try:
            value = self._method.invoke(self._obj, *self._args)
        except RuntimeError:
            self._errors.append("ERROR: Exception calling %s\n" % self._method.get_name())
        except:
            self._on_exception()
        else:
            return self._value_to_harness_string(value)

        return None

    def run(self):
        result, log = self._get_result(), ""
        if not result or self._verbose:
            log = "(Got: %s)\n" % str(self._actual_value)
            self._msgs.append(log)
            log += "\n".join(self._errors)
            self._msgs.extend(self._errors)

        return self._status, "\n".join(self._msgs), log


class EventAssertion(Assertion):

    def __init__(self, obj, assertion, verbose=False, events=[]):
        super().__init__(obj, assertion, verbose)
        self._events = events
        self._obj_events = list(filter(lambda x: x.source == obj, events))
        self._matching_events = []

        e_type = self._expected_value.get("type")
        if e_type is not None:
            matches = filter(lambda x: x.type == e_type, self._events)

        detail1 = self._expected_value.get("detail1")
        if detail1 is not None:
            matches = filter(lambda x: x.detail1 == int(detail1), matches)

        detail2 = self._expected_value.get("detail2")
        if detail2 is not None:
            matches = filter(lambda x: x.detail2 == int(detail2), matches)

        any_data = self._expected_value.get("any_data")
        if any_data is not None:
            # TODO: We need to know any_data's type and adjust accordingly
            matches = filter(lambda x: x.any_data == any_data, matches)

        self._matching_events = list(matches)

    def _event_to_string(self, event):
        return "%s (%i, %i, %s) from %s with id: %s" % \
            (event.type,
             event.detail1,
             event.detail2,
             event.any_data,
             self._get_role(event.source),
             self._get_id(event.source))

    def _get_result(self):
        if self._verbose:
            self._actual_value = self._events
        else:
            self._actual_value = self._obj_events

        # At the moment, the assumption is that we are only testing that
        # we have an event which matches the asserted event properties.
        if self._matching_events:
            self._status = self.PASS
            return True

        self._status = self.FAIL
        return False

    def run(self):
        result, log = self._get_result(), ""
        if not result or self._verbose:
            log = "(Got: %s)\n" % "\n".join(map(self._event_to_string, self._actual_value))
            self._msgs.append(log)

        return self._status, "\n".join(self._msgs), log


class AtkAtspiAtta():
    """Accessible Technology Test Adapter using AT-SPI2 to test ATK support."""

    STATUS_ERROR = "ERROR"
    STATUS_OK = "OK"
    STATUS_READY = "READY"

    RESULT_PASS = "PASS"
    RESULT_FAIL = "FAIL"
    RESULT_NOT_RUN = "NOTRUN"
    RESULT_ERROR = "ERROR"

    FAILURE_ATTA_NOT_ENABLED = "ATTA not enabled"
    FAILURE_ATTA_NOT_READY = "ATTA not ready"
    FAILURE_INVALID_REQUEST = "Invalid request"
    FAILURE_NOT_FOUND = "Not found"
    FAILURE_RESULTS = "Expected result does not match actual result"
    SUCCESS = "Success"

    # Gecko and WebKitGtk respectively
    UA_URI_ATTRIBUTE_NAMES = ("DocURL", "URI")

    def __init__(self, verify_dependencies=True, dry_run=False, verbose=False):
        """Initializes this ATTA.

        Arguments:
        - verify_dependencies: Boolean reflecting if we should verify that the
          client environment meets the minimum requirements needed for reliable
          test results. Note: If verify_dependencies is False, the installed
          versions of the accessibility libraries will not be obtained and thus
          will not be reported in the results. DEFAULT: True
        - dry_run: Boolean reflecting we shouldn't actually run the assertions,
          but just try to find the specified element(s) and dump out everything
          we know about them. DEFAULT: False
        - verbose: Boolean reflecting whether or not verbose output is desired.
          DEFAULT: False
        """

        self._atta_name = "WPT ATK/AT-SPI2 ATTA"
        self._atta_version = "0.1"
        self._api_name = "ATK"
        self._api_version = ""
        self._minimum_api_version = "2.20.0"
        self._enabled = False
        self._ready = False
        self._next_test = None, ""
        self._current_element = None
        self._current_document = None
        self._current_application = None
        self._callbacks = {"document:load-complete": self._on_load_complete}
        self._monitored_event_types = []
        self._event_history = []
        self._listener_thread = None
        self._dry_run = dry_run
        self._verbose = verbose
        self._proxy = None

        if verify_dependencies and not self._check_environment():
            return

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except:
            print(self._on_exception())
        else:
            self._enabled = True

        if self._dry_run:
            print("DRY RUN ONLY: No assertions will be tested.")

    def _on_exception(self):
        """Handles exceptions encountered by this ATTA.

        Returns:
        - A string containing the exception.
        """

        etype, evalue, tb = sys.exc_info()
        error = "EXCEPTION: %s" % traceback.format_exc(limit=1, chain=False)
        return error

    def _get_accessibility_enabled(self):
        """Returns True if accessibility support is enabled on this platform."""

        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.a11y.Bus",
                "/org/a11y/bus",
                "org.freedesktop.DBus.Properties",
                None)
        except:
            print(self._on_exception())
            return False

        enabled = self._proxy.Get("(ss)", "org.a11y.Status", "IsEnabled")
        print("Platform accessibility support is enabled: %s" % enabled)
        return enabled

    def _set_accessibility_enabled(self, enable):
        """Enables or disables platform accessibility support.

        Arguments:
        - enable: A boolean indicating if support should be enabled or disabled

        Returns:
        - A boolean indicating success or failure.
        """

        if not self._proxy:
            return False

        vEnable = GLib.Variant("b", enable)
        self._proxy.Set("(ssv)", "org.a11y.Status", "IsEnabled", vEnable)
        return self._get_accessibility_enabled() == enable

    def _check_environment(self):
        """Returns True if the client environment has all expected dependencies."""

        try:
            self._api_version = Atk.get_version()
            print("INFO: Installed ATK version: %s" % self._api_version)
        except:
            print(self._on_exception())
            can_enable = False
        else:
            minimum = list(map(int, self._minimum_api_version.split(".")))
            actual = list(map(int, self._api_version.split(".")))
            can_enable = actual >= minimum
            if not can_enable:
                print("ERROR: Minimum ATK version: %s" % self._minimum_api_version)

        # GNOME migrated to Python 3 several years ago. AT-SPI2-based ATs and
        # testing tools, as well as the Python bindings for AT-SPI2, now expect
        # Python 3. Because Python 2 is no longer officially supported or being
        # used by existing ATs for this platform, we don't know if and to what
        # extent accessibility support might fail to work as expected in a
        # Python 2 environment. Thus in order to maximize reliability of test
        # results obtained by this ATTA, Python 3 is required.
        if not sys.version_info[0] == 3:
            print("ERROR: This ATTA requires Python 3.")
            can_enable = False

        if can_enable and not self._get_accessibility_enabled():
            can_enable = self._set_accessibility_enabled(True)
            if can_enable:
                print("IMPORTANT: Accessibility support was just enabled. "\
                      "Please quit and relaunch the browser being tested.")

        return can_enable

    def _register_listener(self, event_type, callback):
        """Registers an accessible-event listener with the ATSPI2 registry.

        Arguments:
        - event_type: A string containing the accessible-event type
        - callback: The method to be connected with the signal
        """

        pyatspi.Registry.registerEventListener(callback, event_type)

    def _deregister_listener(self, event_type, callback):
        """De-registers an accessible-event listener from the ATSPI2 registry.

        Arguments:
        - event_type: A string containing the accessible-event type
        - callback: The method connected with the signal
        """

        pyatspi.Registry.deregisterEventListener(callback, event_type)

    def is_enabled(self):
        """Returns True if this ATTA is enabled."""

        return self._enabled

    def is_ready(self):
        """Returns True if this ATTA is able to proceed with a test run."""

        return self._ready

    def get_info(self):
        """Returns a dict containing the basic details about this ATTA which
        the ARIA test harness script will use to identify this ATTA and send
        the platform-specific assertions."""

        return {"ATTAname": self._atta_name,
                "ATTAversion": self._atta_version,
                "API": self._api_name,
                "APIversion": self._api_version}

    def set_next_test(self, name, url):
        """Sets the next test to be run to the specified name and url. This
        method should be called prior to the test document being loaded so
        that we can listen for document:load-complete accessibility events.
        We set this ATTA's ready state to False here, and set it to True once
        we have received a document:load-complete event for the next test.

        Arguments:
        - name: A string containing the name of the test. This name is used
          for information only.
        - url: A string containing the url of the next test file. This url
          is used to determine if a subsequent page load is associated with
          the next test.
        """

        self._next_test = name, url
        self._ready = False

    def monitor_events(self, event_types):
        """Registers an accessible-event listener with the ATSPI2 registry.

        Arguments:
        - event_types: a list or tuple of AtspiEvent types
        """

        for e in event_types:
            self._register_listener(e, self._on_test_event)
            self._monitored_event_types.append(e)

    def stop_event_monitoring(self):
        """De-registers the test-specific listeners from the ATSPI2 registry."""

        for e in self._monitored_event_types:
            self._deregister_listener(e, self._on_test_event)

        self._monitored_event_types = []
        self._event_history = []

    def _run_test(self, obj, assertion):
        """Runs a single assertion on the specified object.

        Arguments:
        - obj: The AtspiAccessible being tested
        - assertion: A tokenized list containing the components of the property
          or other condition being tested. Note that this is a consequence of
          what we receive from the ARIA test harness and not an indication of
          what is desired or required by this ATTA.

        Returns:
        - A dict containing the result (e.g. "PASS" or "FAIL"), messages to be
          displayed by WPT explaining any failures, and logging output.
        """

        if self._dry_run:
            test_class = DumpInfoAssertion
        else:
            test_class = Assertion.get_test_class(assertion)

        if test_class is None:
            result_value = Assertion.FAIL
            messages = "ERROR: %s is not a valid assertion" % assertion
            log = messages
        elif test_class == EventAssertion:
            time.sleep(0.5)
            test = test_class(obj, assertion, self._verbose, self._event_history)
            result_value, messages, log = test.run()
        else:
            test = test_class(obj, assertion, verbose=self._verbose)
            result_value, messages, log = test.run()

        return {"result": result_value, "message": str(messages), "log": log}

    def _create_platform_assertions(self, assertions):
        """Converts a list of assertions received from the test harness into
        a list of assertions the platform can handle.

        Arguments:
        - assertions: A list of [Test Class, Test Type, Assertion Type, Value]
          assertion lists as received from the harness

        Returns:
        - A list of [Test Class, Test Type, Assertion Type, Value] assertions
          which are ready to be run by this ATTA.
        - A boolean reflecting if the conversion was successfully performed.
        - A string indicating the error if conversion failed.
        """

        is_event = lambda x: x and x[0] == "event"
        event_assertions = list(filter(is_event, assertions))
        if not event_assertions:
            return assertions, True, ""

        platform_assertions = list(filter(lambda x: x not in event_assertions, assertions))

        # The properties associated with accessible events are currently given to
        # us as individual subtests. Unlike other assertions, event properties are
        # not independent of one another. Because these should be tested as an all-
        # or-nothing assertion, we'll combine the subtest values into a dictionary
        # passed along with each subtest.
        properties = {}
        for test, name, verb, value in event_assertions:
            properties[name] = value

        combined_event_assertions = ["event", "event", "contains", properties]
        platform_assertions.append(combined_event_assertions)
        return platform_assertions, True, ""

    def run_tests(self, obj_id, assertions):
        """Runs the provided assertions on the object with the specified id.

        Arguments:
        - obj_id: A string containing the id of the host-language element
        - assertions: A list of tokenized lists containing the components of
          the property or other condition being tested.

        Returns:
        - A dict containing the response
        """

        if not self._enabled:
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_ENABLED,
                    "results": []}

        if not self._ready:
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_READY,
                    "results": []}

        to_run, success, message = self._create_platform_assertions(assertions)
        if not success:
            return {"status": self.STATUS_ERROR,
                    "message": message,
                    "results": []}

        obj, message = self._get_element_with_id(self._current_document, obj_id)
        results = [self._run_test(obj, a) for a in to_run]
        if not results:
            return {"status": self.STATUS_ERROR,
                    "message": message,
                    "results": []}

        return {"status": self.STATUS_OK,
                "message": message,
                "results": results}

    def end_test_run(self):
        """Cleans up cached information at the end of a test run."""

        self.stop_event_monitoring()
        self._current_document = None

    def start(self):
        """Starts this ATTA, registering for ATTA-required events, and
        spawning a listener thread if one does not already exist.

        Returns:
        - A boolean reflecting if this ATTA was started successfully.
        """

        if not self._enabled:
            return False

        for event_type, callback in self._callbacks.items():
            self._register_listener(event_type, callback)

        if self._listener_thread is None:
            self._listener_thread = threading.Thread(target=pyatspi.Registry.start)
            self._listener_thread.setDaemon(True)
            self._listener_thread.setName("ATSPI2 Client")
            self._listener_thread.start()

        return True

    def stop(self):
        """Stops this ATTA, notifying the AT-SPI2 registry.

        Returns:
        - A boolean reflecting if this ATTA was stopped successfully.
        """

        if not self._enabled:
            return False

        self._ready = False

        for event_type, callback in self._callbacks.items():
            self._deregister_listener(event_type, callback)

        if self._listener_thread is not None:
            pyatspi.Registry.stop()
            self._listener_thread.join()
            self._listener_thread = None

        return True

    def _get_document_uri(self, obj):
        """Returns the URI associated with obj.

        Arguments:
        - obj: The AtspiAccessible which implements AtspiDocument

        Returns:
        - A string containing the URI or an empty string upon failure
        - A string indicating success, or the cause of failure
        """

        uri = None
        try:
            document = obj.queryDocument()
            for name in self.UA_URI_ATTRIBUTE_NAMES:
                uri = document.getAttributeValue(name)
                if uri:
                    break
        except:
            return "", self._on_exception()

        if not uri:
            return "", self.FAILURE_NOT_FOUND

        return uri, self.SUCCESS

    def _get_element_id(self, obj):
        """Returns the id associated with obj.

        Arguments:
        - obj: The AtspiAccessible which implements AtspiDocument

        Returns:
        - A string containing the id or an empty string upon failure
        - A string indicating success, or the cause of failure
        """

        try:
            attrs = dict([attr.split(':', 1) for attr in obj.getAttributes()])
        except:
            return "", self._on_exception()

        result = attrs.get("id") or attrs.get("html-id")
        if not result:
            return "", self.FAILURE_NOT_FOUND

        return result, self.SUCCESS

    def _get_element_with_id(self, root, element_id, timeout=5):
        """Returns the descendent of root which has the specified id.

        Arguments:
        - root: An AtspiAccessible, typically a document object
        - element_id: A string containing the id to look for
        - timeout: Time in seconds before giving up

        Returns:
        - The AtspiAccessible if found and valid or None upon failure
        - A string indicating success, or the cause of failure
        """

        self._current_element = None
        if not element_id:
            return None, self.FAILURE_INVALID_REQUEST

        def _on_timeout(root, pred):
            try:
                obj = pyatspi.utils.findDescendant(root, pred)
            except:
                self._on_exception()
            else:
                if self._is_valid_object(obj):
                    self._current_element = obj
                    return False

            return True

        timestamp = time.time()
        pred = lambda x: self._get_element_id(x)[0] == element_id
        callback_id = GLib.timeout_add(100, _on_timeout, root, pred)

        msg = self.FAILURE_NOT_FOUND
        while int(time.time() - timestamp) < timeout:
            if self._current_element:
                msg = self.SUCCESS
                break

        if not self._current_element:
            GLib.source_remove(callback_id)

        return self._current_element, msg

    def _in_current_document(self, obj):
        """Returns True if obj is, or is a descendant of, the current document."""

        if not self._current_document:
            return False

        if not self._is_valid_object(obj):
            return False

        if obj.getApplication() != self._current_application:
            return False

        is_document = lambda x: x == self._current_document
        if is_document(obj):
            return True

        return pyatspi.utils.findAncestor(obj, is_document) is not None

    def _is_valid_object(self, obj):
        """Performs a quick-and-dirty sanity check on obj, taking advantage
        of the fact that AT-SPI2 tends to raise an exception if you ask for
        the name of a defunct, invalid, or otherwise bogus object.

        Arguments:
        - obj: The AtspiAccessible being tested

        Returns:
        - A boolean indicating whether obj is believed to be a valid object
        """

        try:
            name = obj.name
        except:
            return False

        return True

    def _on_load_complete(self, event):
        """Callback for the document:load-complete AtspiEvent. We are interested
        in this event because it greatly simplifies locating the document which
        contains the elements which will be tested. In order for this to work,
        the ATTA must be loaded before the test starts.

        Arguments:
        - event: The AtspiEvent which was emitted
        """

        test_name, test_uri = self._next_test
        if test_name is None:
            return

        # There appears to be a to-be-debugged race condition in either Firefox
        # or AT-SPI2 in which the object emitting document:load-complete is not
        # always valid at the time the event is emitted. When this error occurs,
        # clearing AT-SPI2's cache for the user agent seems to fix it.
        if not self._is_valid_object(event.source):
            print("ERROR: load-complete from invalid source %s." % event.source)
            event.host_application.clearCache()
            print("INFO: AT-SPI2 cached cleared. Source is %s." % event.source)

        uri, status = self._get_document_uri(event.source)
        self._ready = uri and uri == test_uri

        if self._ready:
            print("READY: Next test is '%s' (%s)" % (test_name, test_uri))
            self._current_document = event.source
            self._current_application = event.host_application
            return

        if not uri:
            print("ERROR: No URI for %s (%s)" % (event.source, status))
            return

    def _on_test_event(self, event):
        """Generic callback for a variety of object: AtspiEvent types. It caches
        the event for later examination when evaluating assertion results.

        Arguments:
        - event: The AtspiEvent which was emitted
        """

        if self._in_current_document(event.source):
            self._event_history.append(event)


# TODO: The code in this class was largely lifted from atta-example.py.
# This should probably be a shared tool, which calls ATTA-provided
# API (e.g. to verify the ATTA is ready to proceed with a test run).
class AttaRequestHandler(BaseHTTPRequestHandler):

    _atta = None

    @classmethod
    def set_atta(self, atta):
        self._atta = atta

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        if self.path.endswith("start"):
            self.start_test()
        elif self.path.endswith("end"):
            self.end_test()
        elif self.path.endswith("test"):
            self.run_tests()
        elif self.path.endswith("startlisten"):
            self.start_listen()
        elif self.path.endswith("stoplisten"):
            self.stop_listen()
        else:
            print("UNHANDLED PATH: %s" % self.path)
            self.send_error()

    def send_error(self):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.add_headers()
        self.wfile.write(bytes("Error: bad request\n", "utf-8"))

    @staticmethod
    def dump_json(obj):
        return json.dumps(obj, indent=4, sort_keys=True)

    def add_aria_headers(self):
        self.send_header("Content-Type", "application/json")
        self.add_headers()

    def add_headers(self):
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Allow, Content-Type")
        self.send_header("Allow", "POST")
        self.end_headers()

    def get_params(self, *params):
        submission = {}
        response = {}
        errors = []

        try:
            length = self.headers.__getitem__("content-length")
            content = self.rfile.read(int(length))
            submission = json.loads(content.decode("utf-8"))
        except:
            etype, evalue, tb = sys.exc_info()
            error = traceback.format_exc(limit=1, chain=False)
            errors.append(error)

        for param in params:
            value = submission.get(param)
            if value is None:
                errors.append("Parameter %s not found" % param)
            else:
                response[param] = value

        response["error"] = "; ".join(errors)
        return response

    def _send_response(self, response):
        if response.get("statusText") is None:
            response["statusText"] = ""

        self.send_response(200)
        self.add_aria_headers()
        dump = self.dump_json(response)
        try:
            self.wfile.write(bytes(dump, "utf-8"))
        except BrokenPipeError:
            print("ERROR: Broken pipe")
            self.wfile._wbuf = []
            self.wfile._wbuf_len = 0

    def start_test(self):
        print("==================================")
        response = {}
        params = self.get_params("test", "url")
        error = params.get("error")
        if error:
            response["status"] = "ERROR"
            response["statusText"] = error
            self._send_response(response)
            return

        if self._atta is None:
            print("RUNNING ATTA NOT FOUND. TEST MUST BE RUN MANUALLY.")
        else:
            response.update(self._atta.get_info())
            self._atta.set_next_test(name=params.get("test"), url=params.get("url"))
            while not self._atta.is_ready():
                pass

        response["status"] = "READY"
        self._send_response(response)

    def start_listen(self):
        params = self.get_params("events")
        error = params.get("error")
        response = {}
        if error:
            response["status"] = "ERROR"
            response["statusText"] = error
            self._send_response(response)
            return

        if self._atta is None:
            print("AUTOMATIC EVENT MONITORING NOT POSSIBLE WITHOUT RUNNING ATTA.")
        else:
            self._atta.monitor_events(params.get("events"))

        response["status"] = "READY"
        self._send_response(response)

    def stop_listen(self):
        if self._atta is not None:
            self._atta.stop_event_monitoring()

        response = {"status": "READY"}
        self._send_response(response)

    def run_tests(self):
        params = self.get_params("title", "id", "data")
        response = {}
        if self._atta is not None:
            result = self._atta.run_tests(params.get("id"), params.get("data", {}))
            response.update(result)

        if not response.get("results"):
            response["statusText"] = params.get("error")

        self._send_response(response)

    def end_test(self):
        self._atta.end_test_run()
        response = {"status": "DONE"}
        self._send_response(response)

def shutdown(signum, frame):
    print("\nShutting down on signal %s" % signal.Signals(signum).name)
    if atta is not None:
        atta.stop()

    if server is not None:
        thread = threading.Thread(target=server.shutdown)
        thread.start()

def get_cmdline_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", action="store")
    parser.add_argument("--port", action="store")
    parser.add_argument("--ignore-dependencies", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return vars(parser.parse_args())

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    args = get_cmdline_options()
    verify_dependencies = not args.get("ignore_dependencies")
    dry_run = args.get("dry_run")
    verbose = args.get("verbose")
    host = args.get("host") or "localhost"
    port = args.get("port") or "4119"

    print("Attempting to start AtkAtspiAtta")
    atta = AtkAtspiAtta(verify_dependencies, dry_run, verbose)
    if not atta.is_enabled():
        print("ERROR: Unable to enable ATTA")
        sys.exit(1)

    atta.start()

    print("Starting server on http://%s:%s/" % (host, port))
    server = HTTPServer((host, int(port)), AttaRequestHandler)
    AttaRequestHandler.set_atta(atta)
    server.serve_forever()
