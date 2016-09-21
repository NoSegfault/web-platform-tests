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


class Assertion():

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"

    EXPECTATION_IS = "is"
    EXPECTATION_IS_NOT = "isNot"
    EXPECTATION_CONTAINS = "contains"
    EXPECTATION_DOES_NOT_CONTAIN = "doesNotContain"
    EXPECTATION_IS_ANY = "isAny"
    EXPECTATION_IS_TYPE = "isType"

    TEST_EVENT = "event"
    TEST_PROPERTY = "property"
    TEST_RESULT = "result"

    PROPERTIES = ["id",
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
    INTERFACES = ["Component",
                  "Document",
                  "Hyperlink",
                  "Hypertext",
                  "Image",
                  "Selection",
                  "Table",
                  "TableCell",
                  "Text",
                  "Value"]

    def __init__(self, obj, assertion):
        self._obj = obj
        self._test_string = assertion[1]
        self._expectation = assertion[2]
        self._value = assertion[3]
        self._msgs = []

    @classmethod
    def get_test_class(cls, assertion):
        test_class = assertion[0]
        if test_class == cls.TEST_PROPERTY:
            return PropertyAssertion
        if test_class == cls.TEST_EVENT:
            return EventAssertion
        if test_class == cls.TEST_RESULT:
            return ResultAssertion

        print("ERROR: Unhandled test class: %s" % test_class)
        return None

    @staticmethod
    def _get_object_attributes(obj):
        try:
            attrs = dict([attr.split(':', 1) for attr in obj.getAttributes()])
        except:
            return {}

        return attrs

    def _get_arg_info(self, arg):
        name = arg.get_name()
        typeinfo = arg.get_type()
        typetag = typeinfo.get_tag()

        # TODO: This is private, but I'm not finding API to ask for the type in
        # the form we want it (e.g. int). And getting the type string ("guint32")
        # which we convert to the type, while not private, is teh suck. Look for
        # API to do it right later.
        argtype = gi._gi.TypeTag(typetag)
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
            print("ERROR: Unable to get interface info for %s" % interface_name)
            return []

        return info.get_methods()

    def _get_interfaces(self, obj):
        interfaces = {}
        for iface in pyatspi.utils.listInterfaces(obj):
            if iface not in self.INTERFACES:
                continue
            methods = self._get_interface_methods(iface)
            interfaces[iface] = list(map(self._get_method_details, methods))

        return interfaces

    def _get_relations(self, obj):
        relations = {}
        for r in obj.getRelationSet():
            relation = r.getRelationType().value_name.replace("ATSPI_", "")
            targets = [r.getTarget(i) for i in range(r.getNTargets())]
            relations[relation] = list(map(self._get_id, targets))

        return relations

    @staticmethod
    def _get_states(obj):
        states = []
        for s in obj.getState().getStates():
            states.append(s.value_name.replace("ATSPI_", ""))

        return states

    @staticmethod
    def _get_role(obj):
        role = str(pyatspi.Role(obj.getRole()))

        # ATK (which we're testing) has ROLE_STATUSBAR; AT-SPI (which we're using)
        # has ROLE_STATUS_BAR. ATKify the latter so we can verify the former.
        return role.replace("STATUS_BAR", "STATUSBAR")

    def _get_id(self, obj):
        attrs = self._get_object_attributes(obj)
        return attrs.get("id") or attrs.get("html-id")

    def _get_property(self, prop):
        if prop not in self.PROPERTIES:
            print("ERROR: Unknown property: %s" % prop)
            return None

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
            return self._get_object_attributes(self._obj)

        if prop == "interfaces":
            return self._get_interfaces(self._obj)

        if prop == "states":
            return self._get_states(self._obj)

        if prop == "relations":
            return self._get_relations(self._obj)

        if prop == "parentID":
            return self._get_id(self._obj.parent)

        print("ERROR: Unhandled property: %s" % prop)
        return None

    def _get_value(self):
        pass

    def _get_result(self):
        value = self._get_value()
        if self._expectation == self.EXPECTATION_IS:
            return self._value == value
        if self._expectation == self.EXPECTATION_IS_NOT:
            return self._value != value
        if self._expectation == self.EXPECTATION_CONTAINS:
            return self._value in value
        if self._expectation == self.EXPECTATION_DOES_NOT_CONTAIN:
            return self._value not in value
        if self._expectation == self.EXPECTATION_IS_ANY:
            return value in self._value
        if self._expectation == self.EXPECTATION_IS_TYPE:
            return type(value).__name__ == self._value

        self._msgs.append("ERROR: Unhandled assertion type: %s" % self._expectation)
        return False

    def run(self):
        result, msgs = self._get_result(), "\n".join(self._msgs)
        if result == True:
            return self.PASS, msgs
        if result == False:
            return self.FAIL, msgs
        return self.ERROR, msgs


class DumpInfoAssertion(Assertion):

    def __init__(self, obj, assertion=None):
        assertion = [""] * 4
        super().__init__(obj, assertion)

    def _get_result(self):
        self._msgs.append("DRY RUN")

        info = {}
        info["properties"] = {}
        for prop in self.PROPERTIES:
            info["properties"][prop] = self._get_property(prop)

        print(json.dumps(info, indent=4, sort_keys=True))
        return True


class PropertyAssertion(Assertion):

    def _get_value(self):
        return self._get_property(self._test_string)


class ResultAssertion(Assertion):

    def _get_value(self):
        iface_string, callable_string = re.split("\.", self._test_string, maxsplit=1)
        try:
            interface = eval("pyatspi.Atspi.%s" % iface_string)
        except:
            self._msgs.append("ERROR: Invalid interface: %s" % iface_string)
            return None

        function_string = re.split("\(", callable_string, maxsplit=1)[0]
        if function_string not in dir(interface):
            self._msgs.append("ERROR: %s not in %s" % (function_string, interface))
            return None

        try:
            to_call = "pyatspi.Atspi.%s.%s" % (iface_string, callable_string)
            return eval(to_call)
        except:
            self._msgs.append("ERROR: Exception calling %s" % to_call)
            return None


class EventAssertion(Assertion):

    def __init__(self, obj, assertion):
        pass


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

    def __init__(self, verify_dependencies=True, dry_run=False):
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
        self._dry_run = dry_run

        if verify_dependencies and not self._check_environment():
            return

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except:
            print("ERROR: Exception getting accessible desktop from pyatspi")
        else:
            self._enabled = True

        if self._dry_run:
            print("DRY RUN ONLY: No assertions will be tested.")

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

        if self._dry_run:
            test_class = DumpInfoAssertion
        else:
            test_class = Assertion.get_test_class(assertion)

        test = test_class(obj, assertion)
        result_value, status = test.run()

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

        try:
            attrs = dict([attr.split(':', 1) for attr in obj.getAttributes()])
        except:
            return "", self.FAILURE_EXCEPTION

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
    parser.add_argument("--dry-run", action="store_true")
    return vars(parser.parse_args())

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    args = get_cmdline_options()
    verify_dependencies = not args.get("ignore_dependencies")
    dry_run = args.get("dry_run")
    print("Starting AtkAtspiAtta")
    atta = AtkAtspiAtta(verify_dependencies, dry_run)
    atta.start()

    host = args.get("host") or "localhost"
    port = args.get("port") or "4119"
    print("Starting server on http://%s:%s/" % (host, port))
    server = HTTPServer((host, int(port)), AttaRequestHandler)
    server.serve_forever()
