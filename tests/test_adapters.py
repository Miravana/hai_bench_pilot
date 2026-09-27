import json
import hashlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from haibench.adapters import BackendError, OllamaBackend, ReplayBackend


class AdapterTests(unittest.TestCase):
    def test_replay_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "responses.json"
            path.write_text(json.dumps({"a": ["raw", {"error": "fixture_failure"}]}))
            backend = ReplayBackend(path)
            self.assertEqual(backend.metadata()["fixture_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            backend.start_case("a")
            self.assertEqual(backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)["text"], "raw")
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
            self.assertEqual(error.exception.code, "fixture_failure")
            backend.start_case("b")
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
            self.assertEqual(error.exception.code, "replay_missing")
            path.write_text('{"a": [42]}')
            with self.assertRaises(BackendError):
                ReplayBackend(path)
            for bad in ('{"a":[],"a":[]}', '{"a":[NaN]}', '{"a":[1e999]}'):
                path.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(BackendError):
                    ReplayBackend(path)

    def test_endpoint_validation(self):
        for url in ("https://localhost:11434", "http://example.com", "http://user:pass@localhost",
                    "http://localhost:11434/extra", "http://localhost?q=x", "http://localhost#x",
                    "http://localhost.evil", "http://127.0.0.2:11434"):
            with self.subTest(url=url), self.assertRaises(BackendError):
                OllamaBackend("local", url)
        with self.assertRaises(BackendError):
            OllamaBackend("gpt-oss:cloud")

    def test_http_fixture(self):
        class Handler(BaseHTTPRequestHandler):
            response_status = 200
            response_body = b""
            extra_headers = {}
            delay = 0
            request_body = None

            def do_POST(self):
                self.server.request_path = self.path
                self.server.request_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.delay:
                    time.sleep(self.delay)
                self.send_response(self.response_status)
                if self.response_status == 302:
                    self.send_header("Location", "http://example.com/")
                for key, value in self.extra_headers.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(self.response_body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            backend = OllamaBackend("qwen3:4b", f"http://127.0.0.1:{server.server_port}")
            Handler.response_body = json.dumps({"model": "qwen3:4b", "message": {"content": "{}"},
                                                "done": True, "done_reason": "stop", "eval_count": 3}).encode()
            result = backend.generate([{"role": "user", "content": "hi"}], seed=7, temperature=0,
                                      max_tokens=21, timeout=1)
            self.assertEqual((result["text"], result["usage"], result["finish_reason"]),
                             ("{}", {"eval_count": 3}, "stop"))
            self.assertEqual(server.request_path, "/api/chat")
            request = server.request_body
            self.assertEqual(request["options"], {"seed": 7, "temperature": 0, "num_predict": 21})
            self.assertEqual(request["stream"], False)
            self.assertEqual(request["format"]["required"], ["answer", "explanation", "optional_next_step"])
            self.assertNotIn("think", request)
            for raw in (b"bad json", b'{"message":{"content":"ok"},"model":"x","done":false}'):
                Handler.response_body = raw
                with self.assertRaises(BackendError) as error:
                    backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
                self.assertEqual(error.exception.code, "invalid_envelope")
            for raw in (b'{"model":"x","model":"y","message":{"content":"ok"},"done":true}',
                        b'{"model":"x","message":{"content":"ok"},"done":true,"eval_count":NaN}',
                        b'{"model":"x","message":{"content":"ok"},"done":true,"eval_count":"3"}'):
                Handler.response_body = raw
                with self.subTest(raw=raw), self.assertRaises(BackendError) as error:
                    backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
                self.assertEqual(error.exception.code, "invalid_envelope")
            Handler.response_body = b"x" * 100
            with patch("haibench.adapters._MAX_RESPONSE_BYTES", 16):
                with self.assertRaises(BackendError) as error:
                    backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
                self.assertEqual(error.exception.code, "invalid_envelope")
            Handler.response_body = b"{}"
            Handler.extra_headers = {"Content-Length": "100"}
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
            self.assertEqual(error.exception.code, "connection_error")
            Handler.extra_headers = {}
            Handler.response_status = 302
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
            self.assertEqual(error.exception.code, "http_error")
            self.assertNotIn("example.com", str(error.exception))
            Handler.response_status = 401
            Handler.response_body = b"secret-access-token"
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=1)
            self.assertEqual(str(error.exception), "http_error")
            Handler.response_status = 200
            Handler.delay = 0.1
            with self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=1, timeout=0.01)
            self.assertEqual(error.exception.code, "timeout")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


class ThinkingTests(unittest.TestCase):
    def test_cli_http_payloads_and_artifacts(self):
        import subprocess
        import sys
        from tests.test_core import GOOD, case, suite
        from haibench.adapters import _RESPONSE_SCHEMA

        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply({'version': 'fixture-version'} if self.path == '/api/version' else
                           {'models': [{'name': 'qwen3:4b', 'digest': 'fixture-digest'}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/show':
                    self.reply({'thinking': {'values': [True, False], 'default': True}})
                else:
                    requests.append(body)
                    self.reply({'model': 'qwen3:4b', 'done': True, 'done_reason': 'stop',
                                'message': {'content': GOOD, 'thinking': '' if body.get('think') is False else 'separate reasoning'},
                                'eval_count': 30})

            def reply(self, body):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'suite.json'
                source.write_text(json.dumps(suite(case(turns=[
                    {'id': 't1', 'user': 'first', 'expected_answer': '4', 'review_metrics': ['goal_fidelity']},
                    {'id': 't2', 'user': 'second', 'expected_answer': '4', 'review_metrics': ['goal_fidelity']},
                ]))))
                for mode in (None, 'default', 'on', 'off'):
                    with self.subTest(mode=mode):
                        requests.clear()
                        output = root / str(mode)
                        command = [sys.executable, '-m', 'haibench', 'run', '--suite', str(source),
                                   '--backend', 'ollama', '--model', 'qwen3:4b', '--out', str(output),
                                   '--base-url', f'http://127.0.0.1:{server.server_port}']
                        if mode:
                            command.extend(['--thinking', mode])
                        result = subprocess.run(command, capture_output=True, text=True,
                                                cwd=Path(__file__).resolve().parents[1])
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn('Starting ollama/qwen3:4b', result.stderr)
                        self.assertIn('a/t1', result.stderr)
                        self.assertIn('Completed', result.stderr)
                        self.assertEqual(json.loads(result.stdout)['ok'], 2)
                        expected = {'model': 'qwen3:4b', 'messages': [
                            {'role': 'system', 'content': 'Respond with the required JSON object.'},
                            {'role': 'user', 'content': 'first'}], 'stream': False,
                            'format': _RESPONSE_SCHEMA,
                            'options': {'seed': 0, 'temperature': 0.0, 'num_predict': 512}}
                        if mode in ('on', 'off'):
                            expected['think'] = mode == 'on'
                        self.assertEqual(requests[0], expected)
                        self.assertNotIn('think', requests[1]['options'])
                        self.assertNotIn('separate reasoning', json.dumps(requests[1]['messages']))
                        traces = [json.loads(line) for line in (output / 'traces.jsonl').read_text().splitlines()]
                        trace = traces[0]
                        thinking = '' if mode == 'off' else 'separate reasoning'
                        self.assertEqual(trace['raw_response'], GOOD)
                        self.assertEqual(trace['raw_thinking'], thinking)
                        self.assertTrue(trace['fixture_answer_match'])
                        self.assertEqual(trace['generation_diagnostics'], {
                            'requested_thinking_mode': mode or 'default', 'thinking_returned': bool(thinking),
                            'thinking_characters': len(thinking), 'content_characters': len(GOOD),
                            'thinking_tokens': None, 'content_tokens': None, 'generated_tokens': 30})
                        metadata = json.loads((output / 'manifest.json').read_text())['backend_metadata']
                        self.assertEqual(metadata['requested_thinking_mode'], mode or 'default')
                        self.assertEqual(metadata['server_version'], 'fixture-version')
                        self.assertEqual(metadata['model_digest'], 'fixture-digest')
                        self.assertEqual(metadata['resolved_model'], 'qwen3:4b')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_unsupported_and_unverified_modes_never_generate(self):
        for mode, capability, code in (
            ('on', {'values': [False]}, 'thinking_mode_unsupported'),
            ('off', {'values': [True]}, 'thinking_mode_unsupported'),
            ('on', {'values': ['low', 'high']}, 'thinking_mode_unsupported'),
            ('off', None, 'thinking_support_unverified'),
            ('on', {'values': [1, 0]}, 'thinking_support_unverified'),
        ):
            with self.subTest(mode=mode, capability=capability):
                backend = OllamaBackend('qwen3:4b', thinking=mode)
                def response(path, body, **kwargs):
                    self.assertNotEqual(path, '/api/chat')
                    return {'thinking': capability} if path == '/api/show' else {}
                with patch.object(backend, '_request', side_effect=response):
                    with self.assertRaises(BackendError) as error:
                        backend.generate([], seed=0, temperature=0, max_tokens=32, timeout=1)
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual(backend.metadata()['thinking_control_error'], code)
        backend = OllamaBackend('qwen3:4b')
        with patch.object(backend, '_request', side_effect=BackendError('timeout')):
            self.assertEqual(backend.metadata()['metadata_errors'], dict.fromkeys(('version', 'show', 'tags'), 'timeout'))
        # Failed discovery must not prevent default-mode generation.
        with patch.object(backend, '_request', return_value={
            'message': {'content': '{}'}, 'model': 'qwen3:4b', 'done': True}):
            self.assertEqual(backend.generate([], seed=0, temperature=0, max_tokens=32, timeout=1)['text'], '{}')

    def test_thinking_truncation_and_control_failure_artifacts(self):
        from tests.test_core import GOOD, case, suite
        from haibench.core import run_suite
        turns = [dict(case()['turns'][0]), {**case()['turns'][0], 'id': 't2'}]
        for content, mode, reason, status in (
            ('', 'on', 'length', 'truncated'),
            ('{"answer":', 'on', 'length', 'truncated'),
            (GOOD, 'off', 'stop', 'backend_error'),
        ):
            backend = OllamaBackend('qwen3:4b', thinking=mode)
            chats = []
            def response(path, body, **kwargs):
                if path == '/api/show':
                    return {'thinking': {'values': [True, False]}}
                if path != '/api/chat':
                    return {}
                chats.append(body)
                return {'message': {'content': content, 'thinking': 'reasoning' * 50},
                        'model': 'qwen3:4b', 'done': True, 'done_reason': reason, 'eval_count': 512}
            with self.subTest(content=content, mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'run'
                with patch.object(backend, '_request', side_effect=response):
                    report = run_suite(suite(case(turns=turns)), backend, output)
                traces = [json.loads(line) for line in (output / 'traces.jsonl').read_text().splitlines()]
                self.assertEqual(traces[0]['status'], status)
                self.assertEqual(traces[0]['raw_thinking'], 'reasoning' * 50)
                self.assertEqual(traces[0]['raw_response'], content)
                self.assertIsNone(traces[0]['fixture_answer_match'])
                self.assertEqual(report['by_family']['correction']['eligible_correction_checks'], 0)
                if mode == 'off':
                    self.assertEqual(traces[0]['error_code'], 'thinking_mode_not_honored')
                    self.assertEqual(traces[1]['status'], 'blocked')
                    self.assertEqual(len(chats), 1)
                else:
                    self.assertEqual(len(chats), 2)
                    self.assertEqual(chats[1]['messages'][-2], {'role': 'assistant', 'content': content})
                    self.assertNotIn('reasoning', json.dumps(chats[1]['messages']))

    def test_support_failure_precedes_cases_and_bad_thinking_is_rejected(self):
        from tests.test_core import case, suite
        from haibench.core import run_suite
        backend = OllamaBackend('qwen3:4b', thinking='off')
        turns = [dict(case()['turns'][0]), {**case()['turns'][0], 'id': 't2'}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            with patch.object(backend, '_request', return_value={}) as request, \
                    patch.object(backend, 'start_case') as start_case, \
                    self.assertRaises(BackendError) as error:
                run_suite(suite(case('a', turns=turns), case('b')), backend, output)
            self.assertEqual(error.exception.code, 'thinking_support_unverified')
            self.assertEqual(request.call_count, 3)
            start_case.assert_not_called()
            self.assertFalse(output.exists())
            metadata = backend.metadata()
            self.assertEqual(metadata['requested_thinking_mode'], 'off')
            self.assertEqual(metadata['thinking_control_error'], 'thinking_support_unverified')
        backend = OllamaBackend('qwen3:4b')
        for thinking in (123, {}, []):
            with self.subTest(thinking=thinking), patch.object(backend, '_request', return_value={
                'message': {'content': '{}', 'thinking': thinking}, 'model': 'qwen3:4b', 'done': True
            }), self.assertRaises(BackendError) as error:
                backend.generate([], seed=0, temperature=0, max_tokens=32, timeout=1)
            self.assertEqual(error.exception.code, 'invalid_envelope')

    def test_installed_qwen_top_level_capabilities(self):
        from tests.test_core import GOOD
        for mode in ('default', 'on', 'off'):
            with self.subTest(mode=mode):
                backend = OllamaBackend('qwen3.5:4b', thinking=mode)
                chats = []
                def response(path, body, **kwargs):
                    if path == '/api/show':
                        return {'capabilities': ['completion', 'vision', 'tools', 'thinking']}
                    if path != '/api/chat':
                        return {}
                    chats.append(body)
                    return {'model': 'qwen3.5:4b', 'done': True,
                            'message': {'content': GOOD, 'thinking': 'reasoning' if mode == 'on' else ''}}
                with patch.object(backend, '_request', side_effect=response) as request:
                    backend.preflight()
                    result = backend.generate([], seed=0, temperature=0, max_tokens=512, timeout=1)
                    metadata = backend.metadata()
                self.assertEqual(sum(c.args[0] == '/api/show' for c in request.call_args_list), 1)
                self.assertIsNone(metadata['thinking_control_error'])
                self.assertEqual(metadata['thinking_capabilities']['source'], 'capabilities')
                self.assertEqual(result['text'], GOOD)
                self.assertEqual(result['thinking'], 'reasoning' if mode == 'on' else '')
                self.assertNotIn('think', chats[0]['options'])
                if mode == 'default':
                    self.assertNotIn('think', chats[0])
                else:
                    self.assertIs(chats[0]['think'], mode == 'on')

    def test_unadvertised_top_level_capabilities_remain_unverified(self):
        for capabilities in (None, [], ['completion'], 'thinking'):
            with self.subTest(capabilities=capabilities):
                backend = OllamaBackend('qwen3.5:4b', thinking='off')
                with patch.object(backend, '_request', return_value={'capabilities': capabilities}), \
                        self.assertRaises(BackendError) as error:
                    backend.preflight()
                self.assertEqual(error.exception.code, 'thinking_support_unverified')


if __name__ == "__main__":
    unittest.main()
