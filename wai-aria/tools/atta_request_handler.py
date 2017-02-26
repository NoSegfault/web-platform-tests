#!/usr/bin/env python3
#
# Sharable Request Handler for Accessible Technology Test Adapters
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2017 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import json
import sys
import traceback

from http.server import BaseHTTPRequestHandler


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
