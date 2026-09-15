import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from compiler.diagnostics import DiagnosticError
from compiler.driver import compile_file, compile_source
from compiler import modules
from native_support import ROOT, require_clang, run_executable


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ocl modules ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, source):
        path = self.root / name
        path.write_text(source, encoding='utf-8')
        return path

    def diagnostic(self, code, filename=None):
        with self.assertRaises(DiagnosticError) as caught:
            compile_file(self.root / 'main.ocl')
        self.assertEqual(caught.exception.code, code)
        if filename:
            expected = self.root / filename
            self.assertEqual(caught.exception.filename, str(expected if filename == 'main.ocl' else expected.resolve()))
        return caught.exception

    def test_cross_file_functions_constants_enums_and_structures(self):
        self.write('main.ocl', 'import math; fn main() -> i32 { let p: Point = Point {x: N}; return choose(E.Yes, p.x); }')
        self.write('math.ocl', 'import types; const N: i32 = 42; fn choose(e: E, x: i32) -> i32 { return match e {E.Yes => x, E.No => 0}; }')
        self.write('types.ocl', 'enum E {Yes, No} struct Point {x: i32}')
        program, ir = compile_file(self.root / 'main.ocl')
        self.assertEqual(len(program.functions), 2)
        self.assertIn('define i32 @choose', ir)
        self.run_both(self.root / 'main.ocl')

    def run_both(self, source):
        require_clang()
        for release in (False, True):
            output = self.root / ('output.exe' if os.name == 'nt' else 'output')
            command = [sys.executable, str(ROOT / 'oclc.py'), 'build', str(source), '-o', str(output)]
            if release:
                command.append('--release')
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(output), 42)

    def test_diamond_loads_each_file_once_and_ignores_unreachable_files(self):
        self.write('main.ocl', 'import a; import b; fn main() -> i32 {return n();}')
        self.write('a.ocl', 'import shared;')
        self.write('b.ocl', 'import shared;')
        self.write('shared.ocl', 'fn n() -> i32 {return 42;}')
        self.write('unused.ocl', '@ invalid')
        with mock.patch.object(modules, '_load', wraps=modules._load) as load:
            program, first = compile_file(self.root / 'main.ocl')
        self.assertEqual(load.call_count, 4)
        self.assertEqual(len(program.functions), 2)
        self.assertEqual(first, compile_file(self.root / 'main.ocl')[1])

    def test_import_errors(self):
        cases = [('import missing; fn main() -> i32 {return 42;}', 'E0301'),
                 ('import a; import a; fn main() -> i32 {return 42;}', 'E0302'),
                 ('fn main() -> i32 {return 42;} import a;', 'E0300'),
                 ('import a.b; fn main() -> i32 {return 42;}', 'E0300'),
                 ('import a', 'E0300'),
                 ('fn main() -> i32 {import a; return 42;}', 'E0300')]
        self.write('a.ocl', '')
        for source, code in cases:
            with self.subTest(source=source):
                self.write('main.ocl', source)
                self.diagnostic(code, 'main.ocl')

    def test_cycle_diagnostic_names_chain_and_importing_file(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {return 42;}')
        self.write('a.ocl', 'import b;')
        self.write('b.ocl', 'import a;')
        # A removed cycle guard otherwise loops forever: the file bound counts
        # unique files, not traversal steps. Isolate and reap the child on timeout.
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'oclc.py'), 'check',
                 str(self.root / 'main.ocl'), '--diagnostic-format=json'],
                cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
        except subprocess.TimeoutExpired:
            self.fail('cycle detection failed to terminate within 10 seconds')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        error = json.loads(result.stderr)
        self.assertEqual(error['code'], 'E0303')
        self.assertEqual(error['file'], str((self.root / 'b.ocl').resolve()))
        self.assertIn('main.ocl -> a.ocl -> b.ocl -> a.ocl', error['message'])

    def test_module_count_boundary_and_long_chain(self):
        self.write('main.ocl', 'import m1; fn main() -> i32 {return 42;}')
        for index in range(1, 256):
            self.write(f'm{index}.ocl', f'import m{index+1};' if index < 255 else '')
        compile_file(self.root / 'main.ocl')
        self.write('m255.ocl', 'import m256;')
        self.write('m256.ocl', '')
        self.diagnostic('E0304', 'm255.ocl')

    def test_duplicate_declarations_and_imported_main(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {return 42;} fn f() -> i32 {return 1;}')
        self.write('a.ocl', 'fn f() -> i32 {return 2;}')
        self.diagnostic('E0201', 'a.ocl')
        self.write('a.ocl', 'fn main() -> i32 {return 42;}')
        self.diagnostic('E0306', 'a.ocl')

    def test_files_cannot_share_partial_declarations(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {')
        self.write('a.ocl', 'return 42; }')
        self.diagnostic('E0100', 'main.ocl')

    def test_missing_entry_main_is_reported_in_entry_source(self):
        self.write('main.ocl', 'import a;')
        self.write('a.ocl', 'fn f() -> i32 {return 42;}')
        self.diagnostic('E0204', 'main.ocl')

    def test_type_and_constant_collisions_remain_errors(self):
        for declaration, code in [('enum E {A}', 'E0232'), ('struct S {x: i32}', 'E0225'),
                                  ('const N: i32 = 1;', 'E0238')]:
            with self.subTest(declaration=declaration):
                self.write('main.ocl', f'import a; {declaration} fn main() -> i32 {{return 42;}}')
                self.write('a.ocl', declaration)
                self.diagnostic(code, 'a.ocl')

    def test_nominal_enum_types_across_modules_do_not_convert(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {let x: A = B.One; return 42;}')
        self.write('a.ocl', 'enum A {One} enum B {One}')
        self.diagnostic('E0214', 'main.ocl')

    def test_dependency_diagnostics_have_local_source_coordinates(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {return f();}')
        for body, code in [('return @;', 'E0001'), ('return ;', 'E0100'), ('return false;', 'E0214')]:
            source = '// é\u2028comment\nfn f() -> i32 { ' + body + ' }'
            self.write('a.ocl', source)
            error = self.diagnostic(code, 'a.ocl')
            data = error.as_dict('incorrect.ocl')
            self.assertEqual(data['location']['line'], 2)
            self.assertEqual(data['source_line'], source.split('\n')[1])
            self.assertIn(str((self.root / 'a.ocl').resolve()), error.render('incorrect.ocl'))

    def test_json_cli_dependency_failure_preserves_output(self):
        source = self.write('main.ocl', 'import a; fn main() -> i32 {return 42;}')
        (self.root / 'a.ocl').write_bytes(b'\xef\xbb\xbf// comment\r\n@')
        output = self.write('keep.ll', 'keep me')
        result = subprocess.run([sys.executable, str(ROOT / 'oclc.py'), 'emit-ir', str(source), '-o', str(output), '--diagnostic-format=json'],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        data = json.loads(result.stderr)
        self.assertEqual(data['file'], str((self.root / 'a.ocl').resolve()))
        self.assertEqual(data['location'], {'line': 2, 'column': 1, 'offset': 11})
        self.assertEqual(output.read_text(), 'keep me')

    def test_invalid_dependency_encoding_identifies_import_site(self):
        self.write('main.ocl', 'import a; fn main() -> i32 {return 42;}')
        (self.root / 'a.ocl').write_bytes(b'\xff')
        error = self.diagnostic('E0301', 'main.ocl')
        self.assertIn('a.ocl', error.message)

    def test_source_api_never_loads_imports(self):
        with mock.patch.object(Path, 'read_text') as read:
            with self.assertRaises(DiagnosticError) as caught:
                compile_source('import a; fn main() -> i32 {return 42;}')
        self.assertEqual(caught.exception.code, 'E0300')
        read.assert_not_called()

    def test_a_module_resolving_outside_the_entry_directory_is_rejected(self):
        # The symlink test above is the only other cover for this check and it
        # skips wherever the host denies symlink creation, so on those hosts the
        # containment rule can be deleted without a single test noticing.
        # Redirecting resolution keeps the invariant pinned on every host.
        self.write('main.ocl', 'import a; fn main() -> i32 {return 42;}')
        self.write('a.ocl', '')
        with tempfile.TemporaryDirectory(prefix='ocl outside ') as outside:
            escaped = Path(outside) / 'a.ocl'
            escaped.write_text('', encoding='utf-8')
            real_resolve = Path.resolve

            def redirect(self, *args, **kwargs):
                resolved = real_resolve(self, *args, **kwargs)
                return real_resolve(escaped) if resolved.name == 'a.ocl' else resolved

            with mock.patch.object(Path, 'resolve', redirect):
                error = self.diagnostic('E0305')
            self.assertIn('outside entry directory', error.message)

    def test_a_diamond_is_admitted_at_the_file_bound(self):
        # The bound counts unique files, so revisiting an already-compiled
        # module must not consume budget. A pure chain never revisits anything,
        # so only a diamond reaching the limit exercises the ordering between
        # the already-finished short circuit and the limit check.
        padding = 252
        imports = ''.join(f'import p{index}; ' for index in range(padding))
        # entry + padding + a + b + shared == 256 unique files
        self.write('main.ocl', imports + 'import a; import b; fn main() -> i32 {return n();}')
        for index in range(padding):
            self.write(f'p{index}.ocl', '')
        self.write('a.ocl', 'import shared;')
        self.write('b.ocl', 'import shared;')
        self.write('shared.ocl', 'fn n() -> i32 {return 42;}')
        program, _ = compile_file(self.root / 'main.ocl')
        self.assertEqual(len(program.functions), 2)
        self.write('over.ocl', '')
        self.write('main.ocl', imports + 'import a; import b; import over; fn main() -> i32 {return n();}')
        self.diagnostic('E0304')

    def test_a_module_name_differing_only_in_case_is_one_file(self):
        # On a case-insensitive host both spellings name the same file, so the
        # second import is a duplicate rather than a second copy of the
        # declarations. On a case-sensitive host they are separate files.
        self.write('math.ocl', 'fn add(a: i32, b: i32) -> i32 {return a + b;}')
        self.write('main.ocl', 'import math; import Math; fn main() -> i32 {return add(20, 22);}')
        insensitive = (self.root / 'MATH.ocl').exists()
        if insensitive:
            self.diagnostic('E0302')
            self.write('main.ocl', 'import Math; fn main() -> i32 {return add(20, 22);}')
            program, _ = compile_file(self.root / 'main.ocl')
            self.assertEqual(len(program.functions), 2)
        else:
            self.diagnostic('E0301')

    def test_symlink_outside_root_is_rejected(self):
        self.write('main.ocl', 'import alias; fn main() -> i32 {return 42;}')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'outside.ocl'
            target.write_text('', encoding='utf-8')
            try:
                (self.root / 'alias.ocl').symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest('host does not permit creation of symlinks')
            self.diagnostic('E0305', 'main.ocl')

    def test_module_acceptance_in_all_commands_and_modes(self):
        source = ROOT / 'examples' / 'modules' / 'main.ocl'
        for command in ('check', 'emit-ir'):
            result = subprocess.run([sys.executable, str(ROOT / 'oclc.py'), command, str(source)],
                                    cwd=self.root, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.run_both(source)
