#!/usr/bin/env python3
#
# atk_atta
#
# Accessible Technology Test Adapter for ATK
# Tests ATK (server-side) implementations via AT-SPI2 (client-side)
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2017 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import argparse
import json
import re
import sys
import threading

import gi
from gi.docstring import Direction, TypeTag
from gi.module import FunctionInfo
from gi.repository import Gio, GLib

gi.require_version("Atk", "1.0")
gi.require_version("Atspi", "2.0")
from gi.repository import Atk, Atspi

from atta_base import Atta
from atta_assertion import *


class Assertion(AttaAssertion):

    def __init__(self, obj, assertion, atta):
        super().__init__(obj, assertion, atta)

    @classmethod
    def get_test_class(cls, assertion):
        if cls.CLASS_TBD in assertion:
            return DumpInfoAssertion

        test_class = assertion[0]
        if test_class == cls.CLASS_PROPERTY:
            return AttaPropertyAssertion
        if test_class == cls.CLASS_EVENT:
            return EventAssertion
        if test_class == cls.CLASS_RELATION:
            return AttaRelationAssertion
        if test_class == cls.CLASS_RESULT:
            return AttaResultAssertion

        print("ERROR: Unhandled test class: %s (assertion: %s)" % (test_class, assertion))
        return None


class EventAssertion(Assertion, AttaEventAssertion):

    def __init__(self, obj, assertion, atta, events):
        super().__init__(obj, assertion, atta)
        self._actual_value = list(map(self._atta.value_to_string, events))
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

    def __init__(self, obj, assertion, atta):
        assertion = [""] * 4
        super().__init__(obj, assertion, atta)

    def run(self):
        info = {}

        properties = {}
        supported_properties = self._atta.get_supported_properties(self._obj)
        for supported_property in supported_properties.keys():
            properties[supported_property] = self._atta.get_property_value(self._obj, supported_property)
        info["properties"] = properties

        supported_methods = self._atta.get_supported_methods(self._obj)
        methods = []
        for function_info_dict in supported_methods.values():
            method = function_info_dict.get("ATK") or function_info_dict.get("ATSPI")
            methods.append(self._atta.value_to_string(method))
        info["supported methods"] = sorted(methods)

        info = self._atta.value_to_string(info)
        log = json.dumps(info, indent=4, sort_keys=True)
        self._status = self.STATUS_FAIL
        return self._status, " ".join(self._messages), log


class AtkAtta(Atta):
    """Accessible Technology Test Adapter to test ATK support."""

    def __init__(self, host, port, name="ATTA for ATK", version="0.1", api="ATK"):
        """Initializes this ATTA."""

        self._api_min_version = "2.20.0"
        self._listener_thread = None
        self._proxy = None
        self._interfaces = []

        try:
            Atspi.get_desktop(0)
        except:
            self._print(self.LOG_ERROR, "Could not get desktop from AT-SPI2.")
            self._enabled = False
            return

        gir = gi.Repository.get_default()
        info = gir.find_by_name("Atspi", "Accessible")
        ifaces = [x.get_name() for x in info.get_interfaces()]
        self._interfaces = list(filter(lambda x: gir.find_by_name("Atk", x), ifaces))

        super().__init__(host, port, name, version, api, Atta.LOG_INFO)

    def start(self, **kwargs):
        """Starts this ATTA (i.e. before running a series of tests)."""

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
        """Shuts down this ATTA (i.e. after all tests have been run)."""

        if not self._enabled:
            return

        self._deregister_listener("document:load-complete", self._on_load_complete)
        if self._listener_thread is not None:
            Atspi.event_quit()
            self._listener_thread.join()
            self._listener_thread = None

        super().shutdown(signum, frame, **kwargs)

    def _get_system_api_version(self, **kwargs):
        """Returns a string with the installed version of the accessibility API."""

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
            self._print(self.LOG_ERROR, self._on_exception())
            return False

        enabled = self._proxy.Get("(ss)", "org.a11y.Status", "IsEnabled")
        return enabled

    def _set_accessibility_enabled(self, enable, **kwargs):
        """Returns True if accessibility support was successfully set."""

        if not self._proxy:
            return False

        should_enable = GLib.Variant("b", enable)
        self._proxy.Set("(ssv)", "org.a11y.Status", "IsEnabled", should_enable)
        success = self._get_accessibility_enabled() == enable

        if success and enable:
            msg = "Accessibility support was just enabled. Browser restart may be needed."
            self._print(self.LOG_WARNING, msg)

        return success

    def _register_listener(self, event_type, callback, **kwargs):
        """Registers an accessible-event listener on the platform."""

        listener = self._listeners.get(callback, Atspi.EventListener.new(callback))
        Atspi.EventListener.register(listener, event_type)
        self._listeners[callback] = listener

    def _deregister_listener(self, event_type, callback, **kwargs):
        """De-registers an accessible-event listener on the platform."""

        listener = self._listeners.get(callback)
        if listener:
            Atspi.EventListener.deregister(listener, event_type)

    def _create_platform_assertions(self, assertions, **kwargs):
        """Performs platform-specific changes needed to harness assertions."""

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

        if obj:
            Atspi.Accessible.clear_cache(obj)

        if test_class is None:
            result_value = Assertion.STATUS_FAIL
            messages = "ERROR: %s is not a valid assertion" % assertion
            log = messages
        elif test_class == EventAssertion:
            test = test_class(obj, assertion, self, self._event_history)
            result_value, messages, log = test.run()
        else:
            test = test_class(obj, assertion, self)
            result_value, messages, log = test.run()

        return {"result": result_value, "message": str(messages), "log": log}

    def _get_id(self, obj, **kwargs):
        """Returns the element id associated with obj or an empty string upon failure."""

        if obj is None:
            return ""

        try:
            attrs = Atspi.Accessible.get_attributes(obj) or {}
        except:
            return ""

        # Gecko and WebKitGtk respectively
        return attrs.get("id") or attrs.get("html-id") or ""

    def _get_uri(self, document, **kwargs):
        """Returns the URI associated with document or an empty string upon failure."""

        if document is None:
            return ""

        try:
            Atspi.Accessible.clear_cache(document)
        except:
            self._print(self.LOG_ERROR, self._on_exception())
            return ""

        # Gecko and WebKitGtk respectively
        for name in ("DocURL", "URI"):
            try:
                uri = Atspi.Document.get_document_attribute_value(document, name)
            except:
                return ""
            if uri:
                return uri

        return ""

    def _get_children(self, obj, **kwargs):
        """Returns the children of obj or [] upon failure or absence of children."""

        try:
            count = Atspi.Accessible.get_child_count(obj)
        except:
            print(self._on_exception())
            return []

        return [Atspi.Accessible.get_child_at_index(obj, i) for i in range(count)]

    def _get_parent(self, obj, **kwargs):
        """Returns the parent of obj or None upon failure."""

        try:
            parent = Atspi.Accessible.get_parent(obj)
        except:
            print(self._on_exception())
            return None

        return parent

    @staticmethod
    def _find_matching_symbol(atk_symbol, atspi_symbols):
        """Returns the symbol in atspi_symbols which is equivalent to atk_symbol."""

        # Things which are unique or hard to reliably map via heuristic.
        mappings = {
            "atk_selection_add_selection": "atspi_selection_select_child",
            "atk_selection_ref_selection": "atspi_selection_get_selected_child",
        }

        mapped = mappings.get(atk_symbol)
        if mapped in atspi_symbols:
            return atspi_symbols[atspi_symbols.index(mapped)]

        # Ideally, the symbols are the same, not counting the API name.
        candidate_symbol = atk_symbol.replace("atk", "atspi")
        if candidate_symbol in atspi_symbols:
            return atspi_symbols[atspi_symbols.index(candidate_symbol)]

        # AT-SPI2 tends to use "get" when ATK uses "ref".
        replaced = candidate_symbol.replace("_ref_", "_get_")
        if replaced in atspi_symbols:
            return atspi_symbols[atspi_symbols.index(replaced)]

        replaced = candidate_symbol.replace("_ref_at", "_get_accessible_at")
        if replaced in atspi_symbols:
            return atspi_symbols[atspi_symbols.index(replaced)]

        # They sometimes split words differently ("key_binding", "keybinding").
        collapsed = candidate_symbol.replace("_", "")
        matches = list(map(lambda x: x.replace("_", ""), atspi_symbols))
        if collapsed in matches:
            return atspi_symbols[matches.index(collapsed)]

        # AT-SPI2 tends to use "get n" when ATK uses "get ... count".
        if "_get_" in atk_symbol and "_count" in atk_symbol:
            matches = list(filter(lambda x: "get_n_" in x, atspi_symbols))
            if len(matches) == 1:
                return atspi_symbols[atspi_symbols.index(matches[0])]

        return None

    def get_relation_targets(self, obj, relation_type, **kwargs):
        """Returns the elements of pointed to by relation_type for obj."""

        if not obj and property_name != "accessible":
            raise AttributeError("Object not found")

        for relation in Atspi.Accessible.get_relation_set(obj):
            r_type = Atspi.Relation.get_relation_type(relation)
            n_targets = Atspi.Relation.get_n_targets(relation)
            if self.value_to_string(r_type) == relation_type and n_targets:
                return [Atspi.Relation.get_target(relation, i) for i in range(n_targets)]

        return []

    def get_supported_methods(self, obj=None, **kwargs):
        """Returns a name:callable dict of supported platform methods."""

        if obj is None:
            obj_interfaces = self._interfaces
        else:
            obj_interfaces = self.get_property_value(obj, "interfaces")

        def _include(info_dict):
            method = info_dict.get("ATK") or info_dict.get("ATSPI")
            interface = method.get_container().get_name()
            return interface in obj_interfaces

        if self._supported_methods:
            return {k: v for k, v in self._supported_methods.items() if _include(v)}
            return self._supported_methods

        # These setters have corresponding getters in ATK, which we can test
        # via a matching AT-SPI2 getter. More importantly, they lack matching
        # setters in AT-SPI2, so we cannot set these property values via ATTA.
        implementor_only = [
            "atk_action_set_description",
            "atk_document_set_attribute_value",
            "atk_image_set_image_description",
            "atk_table_set_caption",
            "atk_table_set_column_description",
            "atk_table_set_column_header",
            "atk_table_set_row_description",
            "atk_table_set_row_header",
            "atk_table_set_summary",
        ]

        gir = gi.Repository.get_default()

        for iface in self._interfaces:
            atk_info = gir.find_by_name("Atk", iface)
            atspi_info = gir.find_by_name("Atspi", iface)
            atk_methods = {m.get_symbol(): m for m in atk_info.get_methods()}
            atspi_methods = {m.get_symbol(): m for m in atspi_info.get_methods()}

            for atk_symbol, atk_function_info in atk_methods.items():
                if atk_symbol in implementor_only or atk_function_info.is_deprecated():
                    continue

                value = {"ATK": atk_function_info}
                atspi_symbols = list(atspi_methods.keys())
                atspi_symbol = self._find_matching_symbol(atk_symbol, atspi_symbols)
                if atspi_symbol:
                    value["ATSPI"] = atspi_methods.pop(atspi_symbol)
                self._supported_methods[atk_symbol] = value

            # These items weren't matched cleanly with an ATK method, but we may need
            # to use them. Example: To test atk_table_cell_get_position() we need both
            # atspi_table_cell_get_row_index() and atspi_table_cell_get_column_index().
            for symbol, function_info in atspi_methods.items():
                if not function_info.is_deprecated():
                    self._supported_methods[symbol] = {"ATSPI": function_info}

        return {k: v for k, v in self._supported_methods.items() if _include(v)}

    def string_to_method_and_arguments(self, callable_as_string, **kwargs):
        """Converts callable_as_string into the appropriate callable platform method
        and list of arguments with the appropriate types."""

        try:
            method_string, args_string = re.split("\(", callable_as_string, maxsplit=1)
            args_string = args_string[:-1]
        except ValueError:
            method_string = callable_as_string
            args_list = []
        else:
            args_list = list(filter(lambda x: x != "", args_string.split(",")))

        supported_methods = self.get_supported_methods()
        info_dict = supported_methods.get(method_string, {})
        method = info_dict.get(kwargs.get("api", "ATSPI"))
        if not method:
            raise NameError("No known platform method for %s" % method_name)

        in_args = filter(lambda x: x.get_direction() == Direction.IN, method.get_arguments())
        arg_types = list(map(lambda x: TypeTag(x.get_type().get_tag()), in_args))
        if len(arg_types) != len(args_list):
            string = self._atta.value_to_string(method)
            raise TypeError("Incorrect argument count for %s" % string)

        args = [arg_types[i](args_list[i]) for i in range(len(arg_types))]
        return method, args

    def get_result(self, method, arguments, **kwargs):
        """Returns the result of calling method with the specified arguments."""

        arguments.insert(0, kwargs.get("obj"))
        return method.invoke(*arguments)

    def get_supported_properties(self, obj=None, **kwargs):
        """Returns a name:callable dict of supported platform properties."""

        if self._supported_properties:
            return self._supported_properties

        self._supported_properties = {
            "accessible": lambda x: x is not None,
            "childCount": Atspi.Accessible.get_child_count,
            "description": Atspi.Accessible.get_description,
            "name": Atspi.Accessible.get_name,
            "interfaces": Atspi.Accessible.get_interfaces,
            "objectAttributes": Atspi.Accessible.get_attributes_as_array,
            "parent": Atspi.Accessible.get_parent,
            "relations": Atspi.Accessible.get_relation_set,
            "role": Atspi.Accessible.get_role,
            "states": Atspi.Accessible.get_state_set,
        }

        return self._supported_properties

    def type_to_string(self, value, **kwargs):
        """Returns the type of value as a harness-compliant string."""

        value_type = type(value)
        if value_type in (Atspi.Accessible, Atspi.Relation):
            return "Object"

        if value_type == Atspi.StateSet:
            return "List"

        if value_type in (Atspi.Role, Atspi.RelationType, Atspi.StateType):
            return "Constant"

        return super().type_to_string(value, **kwargs)

    def value_to_string(self, value, **kwargs):
        """Returns value (e.g. a platform contstant) as a string."""

        value_type = type(value)
        if value_type == Atspi.Accessible:
            return self._get_id(value, **kwargs)

        if value_type == Atspi.Relation:
            return self.value_to_string(Atspi.Relation.get_relation_type(value))

        if value_type == Atspi.StateSet:
            all_states = [Atspi.StateType(i) for i in range(Atspi.StateType.LAST_DEFINED)]
            states = [s for s in all_states if value.contains(s)]
            return list(map(self.value_to_string, states))

        if value_type in (Atspi.Role, Atspi.RelationType, Atspi.StateType):
            value_name = value.value_name.replace("ATSPI_", "")
            if value_type == Atspi.Role:
                # ATK (which we're testing) has ROLE_STATUSBAR; AT-SPI (which we're using)
                # has ROLE_STATUS_BAR. ATKify the latter so we can verify the former.
                value_name = value_name.replace("ROLE_STATUS_BAR", "ROLE_STATUSBAR")
            return value_name

        if value_type == Atspi.Event:
            role = self.value_to_string(Atspi.Accessible.get_role(value.source))
            objid = "(id=%s)" % (self.value_to_string(value.source) or "")
            detail1 = value.detail1
            detail2 = value.detail2
            any_data = self.value_to_string(value.any_data)
            return "%s(%i,%i,%s) by %s %s" % (value.type, detail1, detail2, any_data, role, objid)

        if value_type == FunctionInfo:
            method_args = []
            for arg in value.get_arguments():
                arg_name = arg.get_name()
                arg_type = TypeTag(arg.get_type().get_tag())
                method_args.append("%s %s" % (arg_type.__name__, arg_name))

            string = "%s(%s)" % (value.get_symbol(), ", ".join(method_args))
            if value.is_deprecated():
                string = "DEPRECATED: %s" % string
            return string

        return super().value_to_string(value, **kwargs)

    def _on_load_complete(self, data, **kwargs):
        """Callback for the platform's signal that a document has loaded."""

        if self.is_ready(data.source):
            application = Atspi.Accessible.get_application(data.source)
            Atspi.Accessible.set_cache_mask(application, Atspi.Cache.DEFAULT)

    def _on_test_event(self, data, **kwargs):
        """Callback for platform accessibility events the ATTA is testing."""

        if self._in_current_document(data.source):
            self._event_history.append(data)


def get_cmdline_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", action="store")
    parser.add_argument("--port", action="store")
    return vars(parser.parse_args())

if __name__ == "__main__":
    options = get_cmdline_options()
    atta_host = options.get("host") or "localhost"
    atta_port = options.get("port") or "4119"

    print("Attempting to start AtkAtta")
    atk_atta = AtkAtta(atta_host, atta_port)
    if not atk_atta.is_enabled():
        sys.exit(1)

    atk_atta.start()
