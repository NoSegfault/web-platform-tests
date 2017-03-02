#!/usr/bin/env python3
#
# atta-atk-atspi
#
# Accessible Technology Test Adapter for ATK/AT-SPI2
# Tests ATK (server-side) implementations via AT-SPI2 (client-side)
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2017 Igalia, S.L.
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
from http.server import HTTPServer

from atta_request_handler import AttaRequestHandler
from atta_assertion import *


class Assertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    @classmethod
    def get_test_class(cls, assertion):
        test_class = assertion[0]
        if test_class == cls.CLASS_PROPERTY:
            return PropertyAssertion
        if test_class == cls.CLASS_EVENT:
            return EventAssertion
        if test_class == cls.CLASS_RELATION:
            return RelationAssertion
        if test_class == cls.CLASS_RESULT:
            return ResultAssertion
        if test_class == cls.CLASS_TBD:
            return DumpInfoAssertion

        print("ERROR: Unhandled test class: %s (assertion: %s)" % (test_class, assertion))
        return None

    def _value_to_harness_string(self, value):
        if self._expectation == self.EXPECTATION_IS_TYPE:
            return type(value).__name__

        value_type = type(value)
        if value_type is bool:
            return str(value).lower()

        if value_type in (int, float):
            return str(value)

        if value_type in (pyatspi.Accessible, pyatspi.Atspi.Accessible):
            try:
                attrs = dict([a.split(':', 1) for a in value.getAttributes()])
                return attrs.get("id") or attrs.get("html-id")
            except:
                return None

        if value_type in (tuple, list):
            return value_type(map(self._value_to_harness_string, value))

        if value_type is dict:
            return {self._value_to_harness_string(k): self._value_to_harness_string(v) for k, v in value.items()}

        try:
            value_name = value.value_name.replace("ATSPI_", "")
        except:
            value_name = str(value)

        # ATK (which we're testing) has ROLE_STATUSBAR; AT-SPI (which we're using)
        # has ROLE_STATUS_BAR. ATKify the latter so we can verify the former.
        value_name = value_name.replace("ROLE_STATUS_BAR", "ROLE_STATUSBAR")

        return value_name

    def _get_result(self):
        self._actual_value = self._get_value()
        self._actual_value = self._value_to_harness_string(self._actual_value)

        if self._expectation == self.EXPECTATION_IS:
            result = self._expected_value == self._actual_value
        elif self._expectation == self.EXPECTATION_IS_NOT:
            result = self._expected_value != self._actual_value
        elif self._expectation == self.EXPECTATION_CONTAINS:
            result = self._actual_value and self._expected_value in self._actual_value
        elif self._expectation == self.EXPECTATION_DOES_NOT_CONTAIN:
            result = self._actual_value is not None and self._expected_value not in self._actual_value
        elif self._expectation == self.EXPECTATION_IS_ANY:
            result = self._actual_value in self._expected_value
        elif self._expectation == self.EXPECTATION_IS_TYPE:
            result = self._actual_value == self._expected_value
        elif self._expectation == self.EXPECTATION_EXISTS:
            result = self._expected_value == self._actual_value
        else:
            result = False

        if result:
            self._status = self.STATUS_PASS
        else:
            self._status = self.STATUS_FAIL

        return result


class PropertyAssertion(Assertion, AttaPropertyAssertion):

    GETTERS = {
        "accessible": lambda x: bool(x),
        "childCount": lambda x: x.childCount if x else None,
        "description": lambda x: x.description if x else None,
        "name": lambda x: x.name if x else None,
        "interfaces": lambda x: pyatspi.utils.listInterfaces(x) if x else [],
        "objectAttributes": lambda x: x.getAttributes() if x else [],
        "parent": lambda x: x.parent if x else None,
        "relations": lambda x: [r.getRelationType() for r in x.getRelationSet()] if x else [],
        "role": lambda x: x.getRole() if x else None,
        "states": lambda x: x.getState().getStates() if x else None,
    }

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_property_value(self):
        if self._obj:
            self._obj.clearCache()

        getter = self.GETTERS.get(self._test_string)
        if getter:
            return getter(self._obj)

        self._messages.append("ERROR: Unhandled property: %s" % self._test_string)
        return None

    def _get_value(self):
        return self.get_property_value()

    def run(self):
        self._get_result()
        return self._status, " ".join(self._messages), str(self)


class RelationAssertion(Assertion, AttaRelationAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_relation_targets(self):
        if not self._obj:
            return []

        try:
            relation_set = self._obj.getRelationSet()
        except:
            self._on_exception()
            return []

        for r in relation_set:
            if self._value_to_harness_string(r.getRelationType()) == self._test_string:
                return [r.getTarget(i) for i in range(r.getNTargets())]

        return []

    def _get_value(self):
        targets = self._value_to_harness_string(self.get_relation_targets())
        return "[%s]" % " ".join(targets)

    def run(self):
        self._get_result()
        return self._status, " ".join(self._messages), str(self)


class ResultAssertion(Assertion, AttaResultAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)
        self._error = False
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

        methods = self.get_interface_methods(iface)
        if not methods:
            self._messages.append("ERROR: '%s' interface not found." % iface)
            self._error = True
            return

        names = list(map(lambda x: x.get_name(), methods))
        if names and function not in names:
            self._messages.append("ERROR: '%s' method not found." % function)
            self._error = True
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
                self._messages.append("ERROR: Argument %i should be %s (got: %s)\n" % (i, info, arg))
                self._error = True

    @classmethod
    def _get_arg_type(cls, arg):
        typeinfo = arg.get_type()
        typetag = typeinfo.get_tag()
        return gi._gi.TypeTag(typetag)

    @classmethod
    def _get_arg_info(cls, arg):
        name = arg.get_name()
        argtype = cls._get_arg_type(arg)
        return "%s %s" % (argtype.__name__, name)

    @classmethod
    def get_method_details(cls, method):
        name = method.get_name()
        args = list(map(cls._get_arg_info, method.get_arguments()))
        return "%s(%s)" % (name, ", ".join(args))

    @staticmethod
    def get_interface_methods(interface_name):
        gir = gi.Repository.get_default()

        try:
            atspi_info = gir.find_by_name("Atspi", interface_name)
            atk_info = gir.find_by_name("Atk", interface_name)
        except:
            return []

        # If an interface is in AT-SPI2 but not ATK, it's a utility which user
        # agent implementors do not implement. If it's in ATK, but not AT-SPI2,
        # implementors implement it, but we cannot directly call the implemented
        # methods via an AT-SPI2 interface.
        if not (atspi_info and atk_info):
            return []

        return atspi_info.get_methods()

    def _get_value(self):
        if self._error:
            return None

        try:
            value = self._method.invoke(self._obj, *self._args)
        except RuntimeError:
            self._messages.append("ERROR: Exception calling %s\n" % self._method.get_name())
        except:
            self._on_exception()
        else:
            return self._value_to_harness_string(value)

        return None

    def run(self):
        self._get_result()
        return self._status, " ".join(self._messages), str(self)


class EventAssertion(Assertion):

    def __init__(self, obj, assertion, events=[]):
        super().__init__(obj, assertion)
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
        string = "%s (%i,%i,%s) from %s (id: %s)" % \
                 (event.type, event.detail1, event.detail2, event.any_data,
                  self._value_to_harness_string(event.source.getRole()),
                  self._value_to_harness_string(event.source))
        return string.replace(" ", "\u00a0")

    def _get_result(self):
        self._actual_value = self._obj_events or self._events
        self._actual_value = list(map(self._event_to_string, self._actual_value))

        # At the moment, the assumption is that we are only testing that
        # we have an event which matches the asserted event properties.
        if self._matching_events:
            self._status = self.STATUS_PASS
            return True

        self._status = self.STATUS_FAIL
        return False

    def run(self):
        self._get_result()
        return self._status, " ".join(self._messages), str(self)


class DumpInfoAssertion(Assertion, AttaDumpInfoAssertion):

    def __init__(self, obj, assertion=None):
        assertion = [""] * 4
        super().__init__(obj, assertion)

    def run(self):
        info = {}
        info["PropertyAssertion Candidates"] = {}
        for prop, getter in PropertyAssertion.GETTERS.items():
            info["PropertyAssertion Candidates"][prop] = getter(self._obj)

        methods = []
        ifaces = dict.fromkeys(info["PropertyAssertion Candidates"].get("interfaces", []))
        for iface in ifaces:
            iface_methods = ResultAssertion.get_interface_methods(iface)
            details = map(ResultAssertion.get_method_details, iface_methods)
            methods.extend(list(map(lambda x: "%s.%s" % (iface, x), details)))
        info["ResultAssertion Candidate Methods"] = sorted(methods)

        info = self._value_to_harness_string(info)
        log = json.dumps(info, indent=4, sort_keys=True)
        self._status = self.STATUS_FAIL
        return self._status, " ".join(self._messages), log


class AtkAtspiAtta():
    """Accessible Technology Test Adapter using AT-SPI2 to test ATK support."""

    STATUS_ERROR = "ERROR"
    STATUS_OK = "OK"

    FAILURE_ATTA_NOT_ENABLED = "ATTA not enabled"
    FAILURE_ATTA_NOT_READY = "ATTA not ready"
    FAILURE_ELEMENT_NOT_FOUND = "Element not found"

    # Gecko and WebKitGtk respectively
    UA_URI_ATTRIBUTE_NAMES = ("DocURL", "URI")

    def __init__(self, host, port, verify_dependencies=True):
        """Initializes this ATTA.

        Arguments:
        - verify_dependencies: Boolean reflecting if we should verify that the
          client environment meets the minimum requirements needed for reliable
          test results. Note: If verify_dependencies is False, the installed
          versions of the accessibility libraries will not be obtained and thus
          will not be reported in the results. DEFAULT: True
        """

        self._host = host
        self._port = int(port)
        self._server = None
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
        self._proxy = None

        if verify_dependencies and not self._check_environment():
            return

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except:
            print(self._on_exception())
        else:
            self._enabled = True

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

        test_class = Assertion.get_test_class(assertion)

        if test_class is None:
            result_value = Assertion.STATUS_FAIL
            messages = "ERROR: %s is not a valid assertion" % assertion
            log = messages
        elif test_class == EventAssertion:
            time.sleep(0.5)
            test = test_class(obj, assertion, self._event_history)
            result_value, messages, log = test.run()
        else:
            test = test_class(obj, assertion)
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
        """

        is_event = lambda x: x and x[0] == "event"
        event_assertions = list(filter(is_event, assertions))
        if not event_assertions:
            return assertions

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
        return platform_assertions

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

        to_run = self._create_platform_assertions(assertions)
        obj = self._get_element_with_id(self._current_document, obj_id)
        if not obj:
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ELEMENT_NOT_FOUND,
                    "results": []}

        results = [self._run_test(obj, a) for a in to_run]
        return {"status": self.STATUS_OK,
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
            print("START FAILED: ATTA is not enabled.")

        for event_type, callback in self._callbacks.items():
            self._register_listener(event_type, callback)

        if self._listener_thread is None:
            self._listener_thread = threading.Thread(target=pyatspi.Registry.start)
            self._listener_thread.setDaemon(True)
            self._listener_thread.setName("ATSPI2 Client")
            self._listener_thread.start()

        print("Starting server on http://%s:%s/" % (self._host, self._port))
        self._server = HTTPServer((self._host, self._port), AttaRequestHandler)
        AttaRequestHandler.set_atta(self)
        self._server.serve_forever()

    def stop(self, signum=None, frame=None):
        """Stops this ATTA, notifying the AT-SPI2 registry.

        Returns:
        - A boolean reflecting if this ATTA was stopped successfully.
        """

        if not self._enabled:
            return False

        self._ready = False

        if signum is not None:
            # The 'Signals' enum was introduced to signal module in 3.5.
            try:
                signal_string = signal.Signals(signum).name
            except:
                signal_string = str(signum)
            print("\nShutting down on signal %s" % signal_string)

        for event_type, callback in self._callbacks.items():
            self._deregister_listener(event_type, callback)

        if self._listener_thread is not None:
            pyatspi.Registry.stop()
            self._listener_thread.join()
            self._listener_thread = None

        if self._server is not None:
            thread = threading.Thread(target=self._server.shutdown)
            thread.start()

        return True

    def _get_document_uri(self, obj):
        """Returns the URI associated with obj.

        Arguments:
        - obj: The AtspiAccessible which implements AtspiDocument

        Returns:
        - A string containing the URI or an empty string upon failure
        """

        try:
            document = obj.queryDocument()
        except:
            return ""

        for name in self.UA_URI_ATTRIBUTE_NAMES:
            uri = document.getAttributeValue(name)
            if uri:
                return uri

        return ""

    def _get_element_id(self, obj):
        """Returns the id associated with obj.

        Arguments:
        - obj: The AtspiAccessible which implements AtspiDocument

        Returns:
        - A string containing the id or an empty string upon failure
        """

        try:
            attrs = dict([attr.split(':', 1) for attr in obj.getAttributes()])
        except:
            return ""

        return attrs.get("id") or attrs.get("html-id") or ""

    def _get_element_with_id(self, root, element_id, timeout=2):
        """Returns the descendent of root which has the specified id.

        Arguments:
        - root: An AtspiAccessible, typically a document object
        - element_id: A string containing the id to look for
        - timeout: Time in seconds before giving up

        Returns:
        - The AtspiAccessible if found and valid or None upon failure
        """

        self._current_element = None
        if not element_id:
            return None

        def _on_timeout(root, pred):
            try:
                obj = pyatspi.utils.findDescendant(root, pred)
            except:
                pass
            else:
                if obj:
                    self._current_element = obj
                    return False

            return True

        timestamp = time.time()
        pred = lambda x: self._get_element_id(x) == element_id
        callback_id = GLib.timeout_add(100, _on_timeout, root, pred)

        while int(time.time() - timestamp) < timeout:
            if self._current_element:
                break

        if not self._current_element:
            GLib.source_remove(callback_id)

        return self._current_element

    def _in_current_document(self, obj):
        """Returns True if obj is, or is a descendant of, the current document."""

        if not (self._current_document and obj):
            return False

        if obj.getApplication() != self._current_application:
            return False

        is_document = lambda x: x == self._current_document
        if is_document(obj):
            return True

        return pyatspi.utils.findAncestor(obj, is_document) is not None

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

        uri = self._get_document_uri(event.source)
        self._ready = uri and uri == test_uri

        if self._ready:
            print("READY (ON LOAD COMPLETE): Next test is '%s' (%s)" % (test_name, test_uri))
            self._current_document = event.source
            self._current_application = event.host_application

    def _on_test_event(self, event):
        """Generic callback for a variety of object: AtspiEvent types. It caches
        the event for later examination when evaluating assertion results.

        Arguments:
        - event: The AtspiEvent which was emitted
        """

        if self._in_current_document(event.source):
            self._event_history.append(event)


def get_cmdline_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", action="store")
    parser.add_argument("--port", action="store")
    parser.add_argument("--ignore-dependencies", action="store_true")
    return vars(parser.parse_args())

if __name__ == "__main__":
    args = get_cmdline_options()
    verify_dependencies = not args.get("ignore_dependencies")
    host = args.get("host") or "localhost"
    port = args.get("port") or "4119"

    print("Attempting to start AtkAtspiAtta")
    atta = AtkAtspiAtta(host, port, verify_dependencies)
    if not atta.is_enabled():
        print("ERROR: Unable to enable ATTA")
        sys.exit(1)

    signal.signal(signal.SIGINT, atta.stop)
    signal.signal(signal.SIGTERM, atta.stop)

    atta.start()
