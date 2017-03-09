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
import re
import sys
import threading
import time
import traceback

gi.require_version("Atk", "1.0")
gi.require_version("Atspi", "2.0")

from gi.repository import Atk, Atspi, Gio, GLib
from atta_base import Atta
from atta_request_handler import AttaRequestHandler
from atta_assertion import *


class Assertion(AttaAssertion):

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    @classmethod
    def get_test_class(cls, assertion):
        if cls.CLASS_TBD in assertion:
            return DumpInfoAssertion

        test_class = assertion[0]
        if test_class == cls.CLASS_PROPERTY:
            return PropertyAssertion
        if test_class == cls.CLASS_EVENT:
            return EventAssertion
        if test_class == cls.CLASS_RELATION:
            return RelationAssertion
        if test_class == cls.CLASS_RESULT:
            return ResultAssertion

        print("ERROR: Unhandled test class: %s (assertion: %s)" % (test_class, assertion))
        return None

    def _value_to_string(self, value):
        value_type = type(value)

        if value_type == Atspi.Accessible:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "Object"
            try:
                attrs = Atspi.Accessible.get_attributes(value)
                return attrs.get("id") or attrs.get("html-id")
            except:
                return ""

        if value_type == Atspi.Relation:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "Object"
            return self._value_to_string(Atspi.Relation.get_relation_type(value))

        if value_type == Atspi.StateSet:
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "List"

            all_states = [Atspi.StateType(i) for i in range(Atspi.StateType.LAST_DEFINED)]
            states = [s for s in all_states if value.contains(s)]
            return list(map(self._value_to_string, states))

        if value_type in (Atspi.Role, Atspi.RelationType, Atspi.StateType):
            if self._expectation == self.EXPECTATION_IS_TYPE:
                return "Constant"

            if not (0 <= value.real < value_type.LAST_DEFINED):
                self._messages.append("ERROR: %s is not valid value" % value)
                return str(value)

            value_name = value.value_name.replace("ATSPI_", "")
            if value_type is Atspi.Role:
                # ATK (which we're testing) has ROLE_STATUSBAR; AT-SPI (which we're using)
                # has ROLE_STATUS_BAR. ATKify the latter so we can verify the former.
                value_name = value_name.replace("ROLE_STATUS_BAR", "ROLE_STATUSBAR")
            return value_name

        return super()._value_to_string(value)

    def _get_result(self):
        self._actual_value = self._get_value()
        self._actual_value = self._value_to_string(self._actual_value)

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
        "accessible": lambda x: x is not None,
        "childCount": lambda x: Atspi.Accessible.get_child_count(x),
        "description": lambda x: Atspi.Accessible.get_description(x),
        "name": lambda x: Atspi.Accessible.get_name(x),
        "interfaces": lambda x: Atspi.Accessible.get_interfaces(x),
        "objectAttributes": lambda x: Atspi.Accessible.get_attributes_as_array(x),
        "parent": lambda x: Atspi.Accessible.get_parent(x),
        "relations": lambda x: Atspi.Accessible.get_relation_set(x),
        "role": lambda x: Atspi.Accessible.get_role(x),
        "states": lambda x: Atspi.Accessible.get_state_set(x),
    }

    def __init__(self, obj, assertion):
        super().__init__(obj, assertion)

    def get_property_value(self):
        if not (self._obj or self._test_string == "accessible"):
            self._messages.append("ERROR: Accessible object not found")
            return None

        if self._obj:
            Atspi.Accessible.clear_cache(self._obj)

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
            relation_set = Atspi.Accessible.get_relation_set(self._obj)
        except:
            self._on_exception()
            return []

        for r in relation_set:
            rtype = Atspi.Relation.get_relation_type(r)
            if self._value_to_string(rtype) == self._test_string:
                n_targets = Atspi.Relation.get_n_targets(r)
                return [Atspi.Relation.get_target(r, i) for i in range(n_targets)]

        return []

    def _get_value(self):
        targets = self._value_to_string(self.get_relation_targets())
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
            return self._value_to_string(value)

        return None

    def run(self):
        self._get_result()
        return self._status, " ".join(self._messages), str(self)


class EventAssertion(Assertion, AttaEventAssertion):

    def __init__(self, obj, assertion, events=[]):
        super().__init__(obj, assertion)
        self._actual_value = list(map(self._event_to_string, events))
        self._obj_events = list(filter(lambda x: x.source == obj, events))
        self._matching_events = []

        # At the moment, the assumption is that we are only testing that
        # we have an event which matches the asserted event properties.

        e_type = self._expected_value.get("type")
        if e_type is not None:
            matches = filter(lambda x: x.type == e_type, events)

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

    def _event_to_string(self, e):
        try:
            role = Atspi.Accessible.get_role(e.source)
            objid = self._value_to_string(e.source) or ""
        except:
            role = "[DEAD]"
            objid = "EXCEPTION GETTING ID"
        else:
            role = self._value_to_string(role)
            if objid:
                objid = " (%s)" % objid

        return "%s(%i,%i,%s) by %s%s" % (e.type, e.detail1, e.detail2, e.any_data, role, objid)

    def _get_result(self):
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

        info = self._value_to_string(info)
        log = json.dumps(info, indent=4, sort_keys=True)
        self._status = self.STATUS_FAIL
        return self._status, " ".join(self._messages), log


class AtkAtspiAtta(Atta):
    """Accessible Technology Test Adapter using AT-SPI2 to test ATK support."""

    def __init__(self, host, port, name="ATTA for ATK", version="0.1", api="ATK"):
        self._api_min_version = "2.20.0"
        self._listener_thread = None
        self._proxy = None

        try:
            desktop = Atspi.get_desktop(0)
        except:
            self._print(self.LOG_ERROR, "Could not get desktop from AT-SPI2.")
            self._enabled = False
            return

        super().__init__(host, port, name, version, api, Atta.LOG_INFO)

    def start(self, **kwargs):
        if not self._enabled:
            return

        self._register_listener("document:load-complete", self._on_load_complete)
        if self._listener_thread is None:
            self._listener_thread = threading.Thread(target=Atspi.event_main)
            self._listener_thread.setDaemon(True)
            self._listener_thread.setName("ATSPI2 Client")
            self._listener_thread.start()

        super().start(**kwargs)

    def shutdown(self, signum=None, frame=None, **kwargs):
        if not self._enabled:
            return

        self._deregister_listener("document:load-complete", self._on_load_complete)
        if self._listener_thread is not None:
            Atspi.event_quit()
            self._listener_thread.join()
            self._listener_thread = None

        super().shutdown(signum, frame, **kwargs)

    def _get_system_api_version(self, **kwargs):
        try:
            version = Atk.get_version()
        except:
            self._print(self.LOG_ERROR, "Could not get ATK version.")
            return ""

        actual_version = list(map(int, version.split(".")))
        minimum_version = list(map(int, self._api_min_version.split(".")))
        if actual_version < minimum_version:
            msg = "ATK %s < %s." % (version, self._api_min_version)
            self._print(self.LOG_WARNING, msg)

        return version

    def _get_accessibility_enabled(self, **kwargs):
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
            self._print(self.LOG_ERROR, self._on_exception())
            return False

        enabled = self._proxy.Get("(ss)", "org.a11y.Status", "IsEnabled")
        return enabled

    def _set_accessibility_enabled(self, enable, **kwargs):
        if not self._proxy:
            return False

        vEnable = GLib.Variant("b", enable)
        self._proxy.Set("(ssv)", "org.a11y.Status", "IsEnabled", vEnable)
        success = self._get_accessibility_enabled() == enable

        if success and enable:
            msg = "Accessibility support was just enabled. Browser restart may be needed."
            self._print(self.LOG_WARNING, msg)

        return success

    def _register_listener(self, event_type, callback, **kwargs):
        listener = self._listeners.get(callback, Atspi.EventListener.new(callback))
        Atspi.EventListener.register(listener, event_type)
        self._listeners[callback] = listener

    def _deregister_listener(self, event_type, callback, **kwargs):
        listener = self._listeners.get(callback)
        if listener:
            Atspi.EventListener.deregister(listener, event_type)

    def _create_platform_assertions(self, assertions, **kwargs):
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

    def _run_test(self, obj, assertion, **kwargs):
        test_class = Assertion.get_test_class(assertion)

        if test_class is None:
            result_value = Assertion.STATUS_FAIL
            messages = "ERROR: %s is not a valid assertion" % assertion
            log = messages
        elif test_class == EventAssertion:
            test = test_class(obj, assertion, self._event_history)
            result_value, messages, log = test.run()
        else:
            test = test_class(obj, assertion)
            result_value, messages, log = test.run()

        return {"result": result_value, "message": str(messages), "log": log}

    def _get_uri(self, document, **kwargs):
        if document is None:
            return ""

        try:
            Atspi.Accessible.clear_cache(document)
        except:
            self._print(self.LOG_ERROR, self._on_exception())
            return False

        # Gecko and WebKitGtk respectively
        for name in ("DocURL", "URI"):
            try:
                uri = Atspi.Document.get_document_attribute_value(document, name)
            except:
                return ""
            if uri:
                return uri

        return ""

    def _find_descendant(self, root, pred):
        if pred(root) or root is None:
            return root

        try:
            child_count = Atspi.Accessible.get_child_count(root)
        except:
            print(self._on_exception())
            return None

        for i in range(child_count):
            child = Atspi.Accessible.get_child_at_index(root, i)
            element = self._find_descendant(child, pred)
            if element:
                return element

        return None

    def _get_element_with_id(self, root, element_id, **kwargs):
        if not element_id:
            return None

        def has_id(x):
            try:
                attrs = Atspi.Accessible.get_attributes(x) or {}
            except:
                return False

            # Gecko and WebKitGtk respectively
            id_attr = attrs.get("id") or attrs.get("html-id")
            return element_id == id_attr

        if has_id(root):
            return root

        return self._find_descendant(root, has_id)

    def _in_current_document(self, obj):
        if not (self._current_document and obj):
            return False

        parent = obj
        while parent:
            if parent == self._current_document:
                return True
            parent = Atspi.Accessible.get_parent(parent)

        return False

    def _on_load_complete(self, data, **kwargs):
        if self.is_ready(data.source):
            application = Atspi.Accessible.get_application(data.source)
            Atspi.Accessible.set_cache_mask(application, Atspi.Cache.DEFAULT)

    def _on_test_event(self, data, **kwargs):
        if self._in_current_document(data.source):
            self._event_history.append(data)


def get_cmdline_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", action="store")
    parser.add_argument("--port", action="store")
    return vars(parser.parse_args())

if __name__ == "__main__":
    args = get_cmdline_options()
    host = args.get("host") or "localhost"
    port = args.get("port") or "4119"

    print("Attempting to start AtkAtspiAtta")
    atta = AtkAtspiAtta(host, port)
    if not atta.is_enabled():
        sys.exit(1)

    atta.start()
