import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def serve(answer, models=("fake-7b",), report_usage=True):
    """answer(prompt:str, n_call:int)->str. Returns (server, base_url). Call server.shutdown() when done."""
    state = {"calls": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            self._send({"data": [{"id": m} for m in models]}) if self.path.endswith("/models") else self._send({}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            state["calls"] += 1
            prompt = req["messages"][-1]["content"]
            text = answer(prompt, state["calls"])
            out = {"model": req["model"], "choices": [{"message": {"role": "assistant", "content": text}}]}
            if report_usage:
                out["usage"] = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(text) // 4)}
            self._send(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1", state
