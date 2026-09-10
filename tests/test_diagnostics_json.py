import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from compiler import cli
from compiler.diagnostics import DiagnosticError, InternalCompilerError, SourceLocation


class JsonDiagnosticsTests(unittest.TestCase):
    def test_real_cli_emits_one_json_error_without_overwriting_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'broken.ocl'
            output = Path(directory) / 'keep.ll'
            source.write_text('fn main() -> i32 { return @; }', encoding='utf-8')
            output.write_text('preserve me', encoding='utf-8')
            result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'oclc.py'),
                                     'emit-ir', str(source), '-o', str(output), '--diagnostic-format=json'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertEqual(self.record(result.stderr)['code'], 'E0001')
            self.assertEqual(output.read_text(encoding='utf-8'), 'preserve me')

    def invoke(self, args):
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                code = cli.main(args)
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def record(self, stderr):
        self.assertEqual(len(stderr.splitlines()), 1)
        value = json.loads(stderr)
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['severity'], 'error')
        self.assertEqual(set(value), {'schema_version', 'severity', 'kind', 'code', 'message', 'file', 'location', 'source_line'})
        return value

    def test_source_errors_across_all_commands(self):
        for source, expected in [('fn main() -> i32 { return @; }', 'E0001'),
                                 ('fn main() -> i32 { return ; }', 'E0100'),
                                 ('fn main() -> i32 { return false; }', 'E0214')]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'source é.ocl'
                path.write_text(source, encoding='utf-8')
                for command in ('check', 'emit-ir', 'build'):
                    with self.subTest(command=command, expected=expected):
                        code, out, err = self.invoke([command, str(path), '--diagnostic-format=json'])
                        self.assertEqual(code, 1)
                        self.assertEqual(out, '')
                        data = self.record(err)
                        self.assertEqual(data['code'], expected)
                        self.assertEqual(data['file'], str(path))
                        self.assertEqual(data['kind'], 'source')
                        self.assertEqual(data['source_line'], source)

    def test_usage_error_is_json_without_source_access(self):
        for args in (['--diagnostic-format=json'], ['wat', 'missing.ocl', '--diagnostic-format', 'json'],
                     ['check', 'missing.ocl', '--release', '--diagnostic-format=json'],
                     ['check', 'missing.ocl', '--unknown', '--diagnostic-format=json']):
            with mock.patch.object(cli, '_read_and_compile') as read:
                code, out, err = self.invoke(args)
            self.assertEqual(code, 2)
            self.assertEqual(out, '')
            self.assertEqual(self.record(err)['kind'], 'usage')
            read.assert_not_called()

    def test_format_preselection_respects_last_option_and_end_marker(self):
        code, _, err = self.invoke(['--diagnostic-format=json', '--diagnostic-format=text'])
        self.assertEqual(code, 2)
        self.assertIn('usage:', err)
        _, _, err = self.invoke(['--', '--diagnostic-format=json'])
        self.assertIn('usage:', err)

    def test_non_source_errors_have_null_locations(self):
        cases = [(OSError('cannot read "file"\nsecond line'), 1, 'input-output'),
                 (ValueError('invalid encoding'), 1, 'input-output'),
                 (InternalCompilerError('broken invariant'), 70, 'internal')]
        for error, expected_code, kind in cases:
            with mock.patch.object(cli, '_read_and_compile', side_effect=error):
                code, out, err = self.invoke(['check', 'file.ocl', '--diagnostic-format=json'])
            self.assertEqual(code, expected_code)
            self.assertEqual(out, '')
            data = self.record(err)
            self.assertEqual(data['kind'], kind)
            self.assertEqual(data['message'], str(error))
            self.assertIsNone(data['location'])
            self.assertIsNone(data['code'])

    def test_missing_clang_and_external_failure_are_json(self):
        with mock.patch.object(cli, '_read_and_compile', return_value='IR'):
            with mock.patch.object(cli, '_clang', return_value=None):
                code, _, err = self.invoke(['build', 'file.ocl', '--diagnostic-format=json'])
            self.assertEqual(code, 2)
            self.assertEqual(self.record(err)['kind'], 'toolchain')
            with mock.patch.object(cli, '_clang', return_value='clang'), mock.patch.object(cli.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 70, '', 'line one\nline two\n')):
                code, _, err = self.invoke(['build', 'file.ocl', '--release', '--diagnostic-format=json'])
            self.assertEqual(code, 1)
            self.assertEqual(self.record(err)['message'], 'line one\nline two\n')

    def test_json_does_not_change_success_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'good.ocl'
            path.write_text('fn main() -> i32 { return 42; }', encoding='utf-8')
            for command in ('check', 'emit-ir'):
                plain = self.invoke([command, str(path)])
                structured = self.invoke([command, str(path), '--diagnostic-format=json'])
                self.assertEqual(plain, structured)
                self.assertEqual(structured[0], 0)
                self.assertEqual(structured[2], '')

    def test_raw_location_and_tab_rendering(self):
        source = '\treturn @;'
        error = DiagnosticError('E0001', 'invalid token', source, SourceLocation(8, 1, 9))
        data = error.as_dict('a.ocl')
        self.assertEqual(data['location'], {'offset': 8, 'line': 1, 'column': 9})
        rendered = error.render('a.ocl').splitlines()
        self.assertEqual(rendered[-1].index('^'), rendered[-2].index('@'))
        self.assertEqual(data['source_line'], source)

    def test_unicode_comment_separator_and_eof_lines(self):
        source = '/* a\u2028b */ @'
        offset = source.index('@')
        error = DiagnosticError('E0001', 'invalid token', source, SourceLocation(offset, 1, offset+1))
        self.assertEqual(error.source_line(), source)
        self.assertEqual(error.as_dict('a.ocl')['location']['offset'], offset)
        eof = DiagnosticError('E0100', 'expected function', 'abc\n', SourceLocation(4, 2, 1))
        self.assertEqual(eof.source_line(), '')

    def test_bom_and_crlf_locations_use_decoded_normalized_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'crlf.ocl'
            path.write_bytes(b'\xef\xbb\xbf// heading\r\n\t@')
            code, _, err = self.invoke(['check', str(path), '--diagnostic-format=json'])
        self.assertEqual(code, 1)
        data = self.record(err)
        self.assertEqual(data['location'], {'offset': 12, 'line': 2, 'column': 2})
        self.assertEqual(data['source_line'], '\t@')
