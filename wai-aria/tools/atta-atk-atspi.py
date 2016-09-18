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
import subprocess
import sys
import threading
import traceback

from http.server import BaseHTTPRequestHandler, HTTPServer


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
    FAILURE_EXCEPTION = "Exception"
    FAILURE_INVALID_REQUEST = "Invalid request"
    FAILURE_NOT_FOUND = "Not found"
    FAILURE_NOT_IMPLEMENTED = "Not implemented"
    FAILURE_RESULTS = "Expected result does not match actual result"
    SUCCESS = "Success"

    INTERFACES = ["Action",
                  "Application",
                  "Collection",
                  "Component",
                  "Document",
                  "Hyperlink",
                  "Hypertext",
                  "Image",
                  "Selection",
                  "Table",
                  "TableCell",
                  "Text",
                  "Value"]

    def __init__(self, verify_dependencies=True):
        """Initializes this ATTA.

        Arguments:
        - verify_dependencies: Boolean reflecting if we should verify that the
          client environment meets the minimum requirements needed for reliable
          test results. Note: If verify_dependencies is False, the installed
          versions of the accessibility libraries will not be obtained and thus
          will not be reported in the results. DEFAULT: True
        """

        self._atta_name = "WPT ATK/AT-SPI2 ATTA"
        self._atta_version = "0.1"
        self._api_name = "ATK"
        self._api_version = ""
        self._minimum_api_version = "2.20"
        self._enabled = False
        self._ready = False
        self._next_test = None, ""
        self._current_document = None
        self._current_element = None
        self._callbacks = {"document:load-complete": self._on_load_complete}
        self._listener_thread = None

        if verify_dependencies and not self._check_environment():
            return

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except:
            print("ERROR: Exception getting accessible desktop from pyatspi")
        else:
            self._enabled = True

    def _check_environment(self):
        """Returns True if the client environment has all expected dependencies."""

        can_enable = True

        atk_version, has_error = self._check_version("atk")
        if has_error:
            can_enable = False

        bridge_version, has_error = self._check_version("atk-bridge-2.0")
        if has_error:
            can_enable = False

        atspi_version, has_error = self._check_version("atspi-2")
        if has_error:
            can_enable = False

        # For now, don't spit up on this. Just discourage it.
        if atk_version != bridge_version != atspi_version:
            print("WARNING: A11y libraries are from different release cycles." \
                  "\natk: %s, at-spi2-atk: %s, at-spi2-core: %s" % \
                  (atk_version, bridge_version, atspi_version))
        else:
            self._api_version = atk_version

        pygobject_version, has_error = self._check_version("pygobject-3.0", "3.10")
        if has_error:
            can_enable = False

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

        return can_enable

    def _check_version(self, module, minimum_version=None):
        """Checks that the version of module is at least the specified version.

        Arguments:
        - module: A string containing the pkg-config-style module name
        - minimum_version: A string containing the mimimum required version.
          If minimum_version is None, use the class' _minimum_api_version.

        Returns: A (string, bool) tuple with the actual version and result.
        The actual version is reported in terms of the stable release cycle,
        with the micro version removed so that this information can be used
        to verify and report the library's API version.
        """

        if minimum_version is None:
            minimum_version = self._minimum_api_version

        get_version = "pkg-config %s --modversion --silence-errors"
        version = subprocess.getoutput(get_version % module) or "0.0.0"
        major, minor = list(map(int, version.split(".")))[:-1]
        minor += (minor & 1)
        api_version = "%i.%i" % (major, minor)

        check_version = "pkg-config %s --atleast-version=%s --print-errors"
        error = subprocess.getoutput(check_version % (module, minimum_version))
        if error:
            print("ERROR: %s" % error)

        return api_version, bool(error)

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

    def _parse_assertion(self, assertion):
        """Parses a single test assertion, such as the platform role being
        ROLE_CHECK_BOX. The conventions established for writing testable
        ARIA statements for this harness are relied upon to identify what
        is to be tested.

        Arguments:
        - assertion: A tokenized list containing the components of the property
          or other condition being tested. Note that this is a consequence of
          what we receive from the ARIA test harness and not an indication of
          what is desired or required by this ATTA.

        Returns:
        - A (function, value, expected_result) tuple. The ATTA function is
          specific to what is being tested (role, name, state, etc.). The
          value and expected result reflect what the test author provided
          in the testable statement.
        - A string indicating success, or the cause of failure
        """

        try:
            test_type = assertion[0].lower()
            test_value = assertion[1]
        except:
            return None, self.FAILURE_INVALID_REQUEST

        try:
            expected_result = assertion[2]
        except IndexError:
            expected_result = True
        else:
            if isinstance(expected_result, str):
                if expected_result.lower() == "true":
                    expected_result = True
                elif expected_result.lower() == "false":
                    expected_result = False

        if test_type == "name":
            return (self._has_name, test_value, expected_result), self.SUCCESS
        if test_type == "description":
            return (self._has_description, test_value, expected_result), self.SUCCESS
        if test_type == "role":
            return (self._has_role, test_value, expected_result), self.SUCCESS
        if test_type == "state":
            return (self._has_state, test_value, expected_result), self.SUCCESS

        # TODO: This only covers the "is it implemented at all?" tests. We need
        # a means to test specific interface calls.
        if test_type == "interface":
            return (self._has_interface, test_value, expected_result), self.SUCCESS

        # TODO: We also need to decide what the asertions for specific interface
        # calls should look like. In order to get the platform support in place,
        # for now we'll treat assertions starting with "interface" as the "is it
        # implemented at all?" test, and assertions starting with a specific
        # interface (e.g. "Table") as a function call test.
        if test_type in map(str.lower, self.INTERFACES):
            function, status = self._get_interface_function(assertion[0], test_value)
            return (self._has_result, function, expected_result), self.SUCCESS

        if test_type == "object":
            return (self._has_attribute_value, test_value, expected_result), self.SUCCESS

        if test_type == "relation":
            return (self._has_relation, test_value, expected_result), self.SUCCESS

        print("ERROR: Unhandled assertion type: %s" % test_type)
        return None, self.FAILURE_INVALID_REQUEST

    def _run_test(self, obj, assertion):
        """Runs a single assertion on the specified object.

        Arguments:
        - obj: The AtspiAccessible being tested
        - assertion: A tokenized list containing the components of the property
          or other condition being tested. Note that this is a consequence of
          what we receive from the ARIA test harness and not an indication of
          what is desired or required by this ATTA.

        Returns:
        - A dict containing the result (e.g. "PASS" or "FAIL") and a message
          reflecting the status, such as details in the case of failure.
        """

        parsed_assertion, status = self._parse_assertion(assertion)
        if status != self.SUCCESS:
            return {"result": self.RESULT_ERROR, "message": status}

        function, test_value, expected_result = parsed_assertion
        result, status, actual_result = function(obj, test_value, expected_result)
        if result == True:
            result_value = self.RESULT_PASS
        if result == False:
            result_value = self.RESULT_FAIL
        if status == self.FAILURE_RESULTS:
            status = "(Found: %s)" % actual_result

        return {"result": result_value, "message": status}

    def run_tests(self, obj_id, assertions):
        """Runs the provided assertions on the object with the specified id.

        Arguments:
        - obj_id: A string containing the id of the host-language element
        - assertions: A list of tokenized lists containing the components of
          the property or other condition being tested. Note that this is a
          consequence of what we receive from the ARIA test harness and not
          an indication of what is desired or required by this ATTA.

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

        obj, status = self._get_element_with_id(self._current_document, obj_id)
        if not obj:
            return {"status": self.STATUS_ERROR,
                    "message": status,
                    "results": []}

        response = {"status": self.STATUS_OK}

        print("RUNNING TESTS: id: '%s' obj: %s" % (obj_id, obj))

        results = []
        for i, assertion in enumerate(assertions):
            result = self._run_test(obj, assertion)
            print("%i. %s %s" % (i, assertion, result))
            results.append(result)

        response["results"] = results
        if not results:
            response["status"] = "ERROR"

        return response

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

    def _get_attributes_dict(self, obj):
        """Returns the accessible object attributes associated with obj.

        Arguments:
        - obj: The AtspiAccessible whose attributes are sought

        Returns:
        - A dict of name, value pairs
        - A string indicating success, or the cause of failure
        """

        try:
            attrs = obj.getAttributes()
        except:
            return {}, self.FAILURE_EXCEPTION

        return dict([attr.split(':', 1) for attr in attrs]), self.SUCCESS

    def _get_document_uri(self, obj):
        """Returns the URI associated with obj.

        Arguments:
        - obj: The AtspiAccessible which implements AtspiDocument

        Returns:
        - A string containing the URI or an empty string upon failure
        - A string indicating success, or the cause of failure
        """

        try:
            document = obj.queryDocument()
            uri = document.getAttributeValue("DocURL")
        except NotImplementedError:
            return "", self.FAILURE_NOT_IMPLEMENTED
        except:
            return "", self.FAILURE_EXCEPTION

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

        attrs, status = self._get_attributes_dict(obj)
        if status != self.SUCCESS:
            return "", status

        result = attrs.get("id") or attrs.get("html-id")
        if not result:
            return "", self.FAILURE_NOT_FOUND

        return result, self.SUCCESS

    def _get_element_with_id(self, root, element_id):
        """Returns the descendent of root which has the specified id.

        Arguments:
        - root: An AtspiAccessible, typically a document object
        - element_id: A string containing the id to look for.

        Returns:
        - The AtspiAccessible if found and valid or None upon failure
        - A string indicating success, or the cause of failure
        """

        if not element_id:
            return None, self.FAILURE_INVALID_REQUEST

        for child in root:
            self._get_element_id(child)

        pred = lambda x: self._get_element_id(x)[0] == element_id
        obj = pyatspi.utils.findDescendant(root, pred)
        if not obj:
            return None, self.FAILURE_NOT_FOUND

        # Quick-and-dirty trick: AT-SPI2 tends to raise an exception if you
        # ask for the name of a defunct, invalid, or otherwise bogus object.
        # Checking the element to be tested here means we shouldn't have to
        # add sanity checks in all of the other methods used during testing.
        try:
            name = obj.name
        except:
            return None, self.FAILURE_EXCEPTION

        return obj, self.SUCCESS

    def _get_interface_function(self, interface_string, function_string):
        """Returns the specified AT-SPI function from the specified interface.

        Arguments:
        - interface_string: The AT-SPI interface name as a string (e.g. 'Table')
        - function_string: The AT-SPI function as a string (e.g. 'get_n_rows')

        Returns:
        - The callable function described, or None if it doesn't exist
        - A string indicating success, or the cause of failure
        """

        try:
            interface = eval("pyatspi.Atspi.%s" % interface_string)
        except:
            return None, self.FAILURE_INVALID_REQUEST

        if function_string not in dir(interface):
            return None, self.FAILURE_INVALID_REQUEST

        return eval("pyatspi.Atspi.%s.%s" % (interface_string, function_string)), self.SUCCESS

    def _has_attribute_value(self, obj, name, value, expected_result=True):
        """Checks if obj has an attribute with the specified name and value.

        Arguments:
        - obj: The AtspiAccessible to check
        - name: A string containing the attribute name
        - value: A string containing the attribute value
        - expected_result: A boolean reflecting if the values should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the actual value
        """

        attrs, status = self._get_attributes_dict(obj)
        actual_value = attrs.get(name, "")
        result = actual_value == value
        success = result == expected_result

        if not attrs:
            return success, status, actual_value

        if name not in attrs:
            return success, self.FAILURE_NOT_FOUND, actual_value

        if success == False:
            return success, self.FAILURE_RESULTS, actual_value

        return success, self.SUCCESS, actual_value

    def _has_description(self, obj, description_string, expected_result):
        """Checks if the accessible description of obj is description_string.

        Arguments:
        - obj: The AtspiAccessible to check
        - description_string: A string containing the description to compare
        - expected_result: A boolean reflecting if the descriptions should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the actual description
        """

        actual_description = obj.description
        result = actual_description == description_string
        success = result == expected_result

        if success == False:
            return success, self.FAILURE_RESULTS, actual_description

        return success, self.SUCCESS, actual_description

    def _has_interface(self, obj, interface_string, expected_result):
        """Checks if obj implements the specified Atspi interface.

        Arguments:
        - obj: The AtspiAccessible to check
        - interface_string: The interface name as a string (e.g. 'TableCell')
        - expected_result: A boolean reflecting if the interface is expected

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing all interfaces found
        """

        actual_interfaces = pyatspi.utils.listInterfaces(obj)
        result = interface_string in actual_interfaces
        success = result == expected_result

        interfaces_string = ", ".join(actual_interfaces)
        if success == False:
            return success, self.FAILURE_RESULTS, interfaces_string

        return success, self.SUCCESS, interfaces_string

    def _has_name(self, obj, name_string, expected_result):
        """Checks if the accessible name of obj is name_string.

        Arguments:
        - obj: The AtspiAccessible to check
        - name_string: A string containing the name to compare
        - expected_result: A boolean reflecting if the names should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the actual name
        """

        actual_name = obj.name
        result = actual_name == name_string
        success = result == expected_result

        if success == False:
            return success, self.FAILURE_RESULTS, actual_name

        return success, self.SUCCESS, actual_name

    def _has_relation(self, obj, type_string, target_ids, expected_result=True):
        """Checks if the obj has the specified accessible relation type pointing
        to the element(s) with the specified id(s). Test writers may provide all
        of the targets in a single assertion or create multiple assertions, each
        of which contains a subset of the targets.

        Arguments:
        - obj: The AtspiAccessible to check
        - type_string: A string containing the AtspiRelationType being checked
        - target_ids: A string containing the id(s) of the referenced element(s)
        - expected_result: A boolean reflecting if the names should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the id(s) of the target(s)
        """

        targets = []
        relations = obj.getRelationSet()
        for r in relations:
            string = r.getRelationType().value_name.replace("ATSPI_", "")
            if string == type_string:
                targets = [r.getTarget(i) for i in range(r.getNTargets())]
                break

        desired_ids = re.compile("\W+").split(target_ids)
        actual_ids = list(map(lambda x: self._get_element_id(x)[0], targets))
        not_found = list(filter(lambda x: x not in actual_ids, desired_ids))

        result = not not_found
        success = result == expected_result

        if success == False:
            return success, self.FAILURE_RESULTS, actual_ids

        return success, self.SUCCESS, actual_ids

    def _has_result(self, obj, function, value, expected_result=True):
        """Checks the result of performing the specified function on obj.

        Arguments:
        - obj: The AtspiAccessible to check
        - function: The callable AT-SPI interface function to check
        - value: The return value of the function to check, written to reflect
          the expected type (e.g. "2" is a string; 2 is an int)
        - expected_result: A boolean reflecting if the results should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the actual return value
        """

        if function is None:
            return False, self.FAILURE_INVALID_REQUEST, None

        actual_value = function(obj)
        result = actual_value == value
        success = result == expected_result

        if success == False:
            return success, self.FAILURE_RESULTS, actual_value

        return success, self.SUCCESS, actual_value

    def _has_role(self, obj, role_string, expected_result):
        """Checks if the accessible role of obj is role_string.

        Arguments:
        - obj: The AtspiAccessible to check
        - role_string: A string containing a role constant (e.g. 'ROLE_LIST_BOX')
        - expected_result: A boolean reflecting if the roles should match

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing the actual role
        """

        role = obj.getRole()
        if not isinstance(role, pyatspi.Role):
            role = pyatspi.Role(role)

        actual_role = str(role)
        result = actual_role == role_string
        success = result == expected_result

        if success == False:
            return success, self.FAILURE_RESULTS, actual_role

        return success, self.SUCCESS, actual_role

    def _has_state(self, obj, state_string, expected_result):
        """Checks if the accessible state set of obj contains the specified state.

        Arguments:
        - obj: The AtspiAccessible to check
        - state_string: A string containing a state constant (e.g. 'STATE_CHECKED')
        - expected_result: A boolean reflecting if the state should be in the set

        Returns:
        - A boolean reflecting if the actual result is the expected_result
        - A string indicating success, or the cause of failure
        - A string containing all states found
        """

        state_set = obj.getState()
        state_strings = map(str, state_set.getStates())
        result = state_string in state_strings
        success = result == expected_result

        actual_states = ", ".join(state_strings)
        if success == False:
            return success, self.FAILURE_RESULTS, actual_states

        return success, self.SUCCESS, actual_states

    def _on_load_complete(self, event):
        """Callback for the document:load-complete AtspiEvent. We are interested
        in this event because it greatly simplifies locating the document which
        contains the elements which will be tested. In order for this to work,
        the ATTA must be loaded before the test starts.

        Arguments:
        - event: The AtspiEvent which was emitted
        """

        test_name, test_uri = self._next_test
        uri, status = self._get_document_uri(event.source)
        self._ready = uri == test_uri

        if self._ready:
            print("READY: Next test is '%s' (%s)" % (test_name, test_uri))
            self._current_document = event.source
            return

        if not uri:
            print("ERROR: No URI for %s (%s)" % (event.source, status))
            return


# TODO: The code in this class was largely lifted from atta-example.py.
# This should probably be a shared tool, which calls ATTA-provided
# API (e.g. to verify the ATTA is ready to proceed with a test run).
class AttaRequestHandler(BaseHTTPRequestHandler):

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
        else:
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
        self.wfile.write(bytes(self.dump_json(response), "utf-8"))

    def start_test(self):
        print("==================================")
        response = atta.get_info()
        params = self.get_params("test", "url")
        error = params.get("error")
        if error:
            response["status"] = "ERROR"
            response["statusText"] = error
            self._send_response(response)
            return

        # HACK to give us sufficient time to receive the document:load-complete
        # accessibility event and compare it to the URL we set above.
        atta.set_next_test(name=params.get("test"), url=params.get("url"))
        while not atta.is_ready():
            pass

        response["status"] = "READY"
        self._send_response(response)

    def run_tests(self):
        params = self.get_params("title", "id", "data")
        response = atta.run_tests(params.get("id"), params.get("data", {}))
        if not response.get("results"):
            response["statusText"] = params.get("error")

        self._send_response(response)

    def end_test(self):
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
    return vars(parser.parse_args())

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    args = get_cmdline_options()
    verify_dependencies = not args.get("ignore_dependencies")
    print("Starting AtkAtspiAtta")
    atta = AtkAtspiAtta(verify_dependencies)
    atta.start()

    host = args.get("host") or "localhost"
    port = args.get("port") or "4119"
    print("Starting server on http://%s:%s/" % (host, port))
    server = HTTPServer((host, int(port)), AttaRequestHandler)
    server.serve_forever()
