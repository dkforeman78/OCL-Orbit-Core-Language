"""0.17 source activation, semantic boundaries, and native binding witnesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from compiler.driver import compile_file, compile_source
from compiler.diagnostics import DiagnosticError
from compiler import namespaces
from native_support import ROOT, require_clang, run_executable

HEADER = 'module namespaces;\n'
MAIN = 'fn main() -> i32 {return 42;}'


class NamespaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ocl namespaces ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, source, namespace=True):
        path = self.root / (name + '.ocl')
        path.write_text((HEADER if namespace else '') + source, encoding='utf-8')
        return path

    def compile(self):
        return compile_file(self.root / 'main.ocl')

    def error(self, code):
        with self.assertRaises(DiagnosticError) as caught:
            self.compile()
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    def native(self, expected=42):
        require_clang()
        for release in (False, True):
            output = self.root / ('result.exe' if os.name == 'nt' else 'result')
            command = [sys.executable, str(ROOT / 'oclc.py'), 'build', str(self.root / 'main.ocl'), '-o', str(output)]
            if release:
                command.append('--release')
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_executable(output), expected)

    def test_source_api_header_and_private_helpers(self):
        _, ir = compile_source(HEADER + 'fn helper()->i32{return 42;} ' + 'fn main()->i32{return helper();}')
        self.assertIn('@ocl.ns.', ir)
        self.assertIn('define i32 @main()', ir)

    def test_header_comments_bom_and_whitespace(self):
        self.write('main', '\ufeff// comment\nmodule /* comment */ namespaces ;\n' + MAIN, False)
        self.compile()

    def test_legacy_identifiers_remain_legal(self):
        source = 'const namespaces:i32=40; fn module(pub:i32)->i32{return pub+namespaces;} fn main()->i32{return module(2);}'
        _, ir = compile_source(source)
        self.assertIn('@module', ir)
        self.assertNotIn('ocl.ns.', ir)

    def test_invalid_headers(self):
        for source in ('module other; ' + MAIN, 'module namespaces ' + MAIN,
                       HEADER + HEADER + MAIN, MAIN + HEADER,
                       'import a; ' + HEADER + MAIN):
            with self.subTest(source=source):
                self.write('main', source, False)
                self.error('E0310')

    def test_namespace_api_does_not_load_imports(self):
        with self.assertRaises(DiagnosticError) as caught:
            compile_source(HEADER + 'import missing; ' + MAIN)
        self.assertEqual(caught.exception.code, 'E0300')

    def test_no_implicit_activation_by_pub_or_qualification(self):
        for source in ('pub ' + MAIN, 'fn main()->i32{return a::f();}'):
            with self.subTest(source=source), self.assertRaises(DiagnosticError):
                compile_source(source)

    def test_pub_only_on_top_level_declarations(self):
        for source in ('pub pub ' + MAIN, 'pub import a; ' + MAIN,
                       'fn main()->i32{pub let x:i32=42; return x;}',
                       'struct S {pub x:i32} ' + MAIN,
                       'enum E {pub A} ' + MAIN,
                       'fn pub()->i32{return 1;} ' + MAIN):
            with self.subTest(source=source), self.assertRaises(DiagnosticError):
                compile_source(HEADER + source)

    def test_both_mixed_mode_directions(self):
        for mode in (False, True):
            with self.subTest(mode=mode):
                self.write('main', 'import a; ' + MAIN, mode)
                self.write('a', 'fn f()->i32{return 42;}', not mode)
                error = self.error('E0311')
                self.assertEqual(error.location.line, 2 if mode else 1)
                self.assertIn('a.ocl', error.message)

    def test_transitive_mixed_mode_location(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', 'import b;')
        self.write('b', '', False)
        error = self.error('E0311')
        self.assertEqual(error.filename, str((self.root / 'a.ocl').resolve()))

    def test_public_function_can_call_private_helper(self):
        self.write('main', 'import a; fn main()->i32{return a::f();}')
        self.write('a', 'pub fn f()->i32{return helper();} fn helper()->i32{return 42;}')
        self.native()

    def test_private_qualified_and_unqualified_calls(self):
        self.write('a', 'fn f()->i32{return 42;}')
        for reference in ('a::f()', 'f()'):
            with self.subTest(reference=reference):
                self.write('main', f'import a; fn main()->i32{{return {reference};}}')
                self.error('E0314')

    def test_private_constants_types_literals_and_patterns(self):
        self.write('a', 'const N:i32=42; enum E {A} struct S{x:i32} pub enum Public{A}')
        cases = ('return a::N;', 'let e:a::E = a::E.A; return 42;',
                 'let s:a::S = a::S{x:42}; return s.x;',
                 'let e:a::Public=a::Public.A; return match e {a::E.A=>42};')
        for body in cases:
            with self.subTest(body=body):
                self.write('main', f'import a; fn main()->i32{{{body}}}')
                self.error('E0314')

    def test_unselected_branch_does_not_bypass_privacy(self):
        self.write('a', 'fn secret()->i32{return 1;}')
        self.write('main', 'import a; fn main()->i32{return if true {42} else {a::secret()};}')
        self.error('E0314')

    def test_private_type_cannot_leak_through_public_interface(self):
        for declaration in ('pub fn f(x:E)->i32{return 42;}',
                            'pub fn f()->E{return E.A;}', 'pub const C:E=E.A;'):
            with self.subTest(declaration=declaration):
                self.write('main', 'enum E{A} ' + declaration + MAIN)
                self.error('E0318')

    def test_public_type_can_be_forward_declared_in_interface(self):
        self.write('a', 'pub fn f(x:E)->E{return x;} pub const C:E=E.A; pub enum E{A}')
        self.write('main', 'import a; fn main()->i32{return match a::f(a::C){a::E.A=>42};}')
        self.native()

    def test_duplicate_names_are_legal_between_modules(self):
        self.write('a', 'pub const N:i32=20; pub fn f()->i32{return N;}')
        self.write('b', 'pub const N:i32=22; pub fn f()->i32{return N;}')
        self.write('main', 'import a; import b; fn main()->i32{return a::f()+b::f();}')
        program, ir = self.compile()
        names = [f.name for f in program.functions]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('@ocl.ns.612e6f636c.f()', ir)
        self.assertIn('@ocl.ns.622e6f636c.f()', ir)
        self.native()

    def test_ambiguity_order_is_stable(self):
        self.write('a', 'pub fn f()->i32{return 20;}')
        self.write('b', 'pub fn f()->i32{return 22;}')
        messages = []
        for imports in ('import a; import b;', 'import b; import a;'):
            self.write('main', imports + 'fn main()->i32{return f();}')
            messages.append(self.error('E0315').message)
        self.assertEqual(messages[0], messages[1])
        self.assertIn('a::f, b::f', messages[0])

    def test_wrong_kind_is_not_filtered_out_of_ambiguity(self):
        self.write('a', 'pub const f:i32=42;')
        self.write('b', 'pub fn f()->i32{return 42;}')
        self.write('main', 'import a; import b; fn main()->i32{return f();}')
        self.error('E0315')

    def test_own_declarations_win_and_wrong_kind_does_not_fall_back(self):
        self.write('a', 'pub fn f()->i32{return 9;}')
        self.write('main', 'import a; fn f()->i32{return 42;} fn main()->i32{return f();}')
        self.assertIn('call i32 @ocl.ns.6d61696e2e6f636c.f()', self.compile()[1])
        self.native()
        self.write('main', 'import a; const f:i32=42; fn main()->i32{return f();}')
        self.error('E0317')

    def test_local_value_and_qualifier_are_separate(self):
        self.write('a', 'pub const N:i32=1; pub fn f()->i32{return 20;}')
        self.write('main', 'import a; const N:i32=2; fn main()->i32{let a:i32=10; let N:i32=12; let f:i32=99; return a+N+f();}')
        self.native()
        self.write('main', 'import a; fn main()->i32{let a:i32=22;return a+a::f();}')
        self.native()

    def test_private_candidate_does_not_make_public_candidate_ambiguous(self):
        self.write('a', 'const N:i32=1;')
        self.write('b', 'pub const N:i32=42;')
        self.write('main', 'import a; import b; fn main()->i32{return N;}')
        self.native()

    def test_transitive_names_and_qualifiers_are_not_visible(self):
        self.write('a', 'import b; pub fn f()->i32{return b::f();}')
        self.write('b', 'pub fn f()->i32{return 42;} pub const N:i32=42;')
        for expression, code in (('N', 'E0313'), ('b::f()', 'E0312')):
            self.write('main', f'import a; fn main()->i32{{return {expression};}}')
            self.error(code)
        self.write('main', 'import a; fn main()->i32{return a::f();}')
        self.native()

    def test_diamond_single_ownership(self):
        self.write('shared', 'pub enum E{A} pub fn f()->E{return E.A;}')
        self.write('a', 'import shared; pub fn f()->shared::E{return shared::f();}')
        self.write('b', 'import shared; pub fn f()->shared::E{return shared::f();}')
        self.write('main', 'import a; import b; fn main()->i32{return if a::f()==b::f(){42}else{0};}')
        program, _ = self.compile()
        self.assertEqual(len(program.enumerations), 1)
        self.native()

    def test_nominal_enums_with_equal_names_remain_distinct(self):
        for name in ('a', 'b'):
            self.write(name, 'pub enum E{A}')
        self.write('main', 'import a; import b; fn main()->i32{return if a::E.A==b::E.A{42}else{0};}')
        self.error('E0211')

    def test_qualified_structure_types_literals_and_fields(self):
        self.write('a', 'pub struct S{x:i32}')
        self.write('b', 'pub struct S{x:bool,y:i32}')
        self.write('main', 'import a; import b; fn main()->i32{var p:a::S=a::S{x:20}; let q:b::S=b::S{y:22,x:true}; p.x=p.x+q.y;return p.x;}')
        self.native()

    def test_local_field_base_wins_over_enum_type(self):
        self.write('main', 'enum E{A} struct S{A:i32} fn main()->i32{let E:S=S{A:42};return E.A;}')
        self.native()

    def test_qualified_constant_assignment_is_diagnostic(self):
        self.write('a', 'pub const N:i32=42;')
        self.write('main', 'import a; fn main()->i32{a::N=1;return 42;}')
        self.error('E0215')

    def test_private_assignment_and_wrong_kind_qualified_call(self):
        self.write('a', 'const N:i32=42; pub const C:i32=42;')
        self.write('main', 'import a; fn main()->i32{a::N=1;return 42;}')
        self.error('E0314')
        self.write('main', 'import a; fn main()->i32{return a::C();}')
        self.error('E0317')

    def test_duplicates_within_module_still_fail(self):
        for declarations in ('fn f()->i32{return 1;} const f:i32=1;',
                             'enum E{A} struct E{x:i32}'):
            self.write('main', declarations + MAIN)
            self.error('E0316')

    def test_duplicate_diagnostic_marks_second_source_declaration(self):
        for first, second in (('const f:i32=1;', 'fn f()->i32{return 1;}'),
                              ('fn f()->i32{return 1;}', 'const f:i32=1;'),
                              ('enum E{A}', 'struct E{x:i32}'),
                              ('struct E{x:i32}', 'enum E{A}')):
            with self.subTest(first=first):
                self.write('main', first + '\n' + second + '\n' + MAIN)
                error = self.error('E0316')
                self.assertEqual(error.location.line, 3)
                self.assertIn('first declared at 2:1', error.message)

    def test_existing_no_shadowing_and_declaration_order(self):
        self.write('main', 'fn main()->i32{{let x:i32=1;}{let x:i32=2;}return 42;}')
        self.error('E0210')
        self.write('main', 'fn main()->i32{let x:i32=y;let y:i32=42;return x;}')
        self.error('E0313')

    def test_entry_main_restrictions(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', MAIN)
        self.error('E0306')

    def test_namespace_cycles_remain_bounded(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', 'import main;')
        result = subprocess.run([sys.executable, str(ROOT / 'oclc.py'), 'check', str(self.root / 'main.ocl')],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('E0303', result.stderr)

    def test_long_expression_binding_is_iterative(self):
        source = HEADER + 'const N:i32=1; fn main()->i32{return ' + '+'.join(['N'] * 20000) + ';}'
        _, ir = compile_source(source)
        self.assertEqual(ir.count('add i32'), 19999)

    def test_deep_blocks_from_deep_caller(self):
        source = HEADER + 'fn main()->i32{' + '{' * 254 + 'return 42;' + '}' * 255
        def deep(n):
            return deep(n - 1) if n else compile_source(source)
        before = sys.getrecursionlimit()
        deep(750)
        self.assertEqual(sys.getrecursionlimit(), before)

    def test_deterministic_ir_across_root_relocation(self):
        self.write('a', 'pub fn f()->i32{return 42;}')
        self.write('main', 'import a; fn main()->i32{return a::f();}')
        first = self.compile()[1]
        self.assertEqual(first, self.compile()[1])
        with tempfile.TemporaryDirectory() as directory:
            for file in self.root.glob('*.ocl'):
                (Path(directory) / file.name).write_bytes(file.read_bytes())
            self.assertEqual(first, compile_file(Path(directory) / 'main.ocl')[1])

    def test_private_member_location_and_json(self):
        self.write('a', 'fn hidden()->i32{return 42;}')
        self.write('main', 'import a;\nfn main()->i32{return a::hidden();}')
        error = self.error('E0314')
        self.assertEqual((error.location.line, error.location.column), (3, 26))
        record = error.as_dict('ignored')
        self.assertEqual(record['schema_version'], 1)
        self.assertEqual(record['code'], 'E0314')
        self.assertEqual(record['file'], str(self.root / 'main.ocl'))

    def test_unknown_qualifier_and_member_distinct(self):
        self.write('a', '')
        for expression, code in (('missing::f()', 'E0312'), ('a::f()', 'E0313')):
            self.write('main', f'import a; fn main()->i32{{return {expression};}}')
            self.error(code)

    def test_contiguous_qualification_token(self):
        self.write('a', 'pub fn f()->i32{return 42;}')
        self.write('main', 'import a; fn main()->i32{return a: :f();}')
        self.error('E0100')

    def test_type_exposure_location_is_type_token(self):
        self.write('main', 'enum E{A}\npub fn f()->E{return E.A;}\n' + MAIN)
        error = self.error('E0318')
        self.assertEqual((error.location.line, error.location.column), (3, 13))

    def test_dependency_diagnostic_in_all_cli_commands(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', 'import b; pub fn f()->i32{return b::secret();}')
        self.write('b', 'fn secret()->i32{return 42;}')
        for command in ('check', 'emit-ir', 'build'):
            result = subprocess.run([sys.executable, str(ROOT / 'oclc.py'), command,
                                     str(self.root / 'main.ocl'), '--diagnostic-format=json'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stderr)
            record = json.loads(result.stderr)
            self.assertEqual(record['code'], 'E0314')
            self.assertEqual(record['file'], str((self.root / 'a.ocl').resolve()))
            self.assertEqual(record['schema_version'], 1)

    def test_no_llvm_lowering_after_private_access(self):
        self.write('main', 'import a; fn main()->i32{return a::secret();}')
        self.write('a', 'fn secret()->i32{return 42;}')
        with mock.patch('compiler.driver.generate_llvm_ir') as lowering:
            self.error('E0314')
        lowering.assert_not_called()

    def test_namespace_acceptance_all_commands_and_modes(self):
        for source in (ROOT / 'examples' / 'namespaces').glob('*.ocl'):
            (self.root / source.name).write_bytes(source.read_bytes())
        for command in ('check', 'emit-ir'):
            result = subprocess.run([sys.executable, str(ROOT / 'oclc.py'), command, str(self.root / 'main.ocl')],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.native()

    def test_module_cap_remains_global(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', '')
        with mock.patch('compiler.modules.MAX_MODULES', 1):
            self.error('E0304')

    def test_constant_cap_remains_global(self):
        self.write('a', ' '.join(f'const C{i}:i32={i};' for i in range(129)))
        self.write('b', ' '.join(f'const C{i}:i32={i};' for i in range(128)))
        self.write('main', 'import a; import b; ' + MAIN)
        self.error('E0238')

    def test_namespace_type_ambiguity_and_own_type_precedence(self):
        self.write('a', 'pub enum E{A}')
        self.write('b', 'pub enum E{A}')
        self.write('main', 'import a; import b; fn main()->i32{let e:E=E.A;return 42;}')
        self.error('E0315')
        self.write('main', 'import a; import b; enum E{A} fn main()->i32{let e:E=E.A;return 42;}')
        self.compile()

    def test_namespace_expression_boundary_from_deep_caller(self):
        def deep(n, source):
            return deep(n - 1, source) if n else compile_source(source)
        before = sys.getrecursionlimit()
        deep(700, HEADER + 'fn main()->i32{return ' + '(' * 255 + '42' + ')' * 255 + ';}')
        with self.assertRaises(DiagnosticError) as caught:
            deep(700, HEADER + 'fn main()->i32{return ' + '(' * 256 + '42' + ')' * 256 + ';}')
        self.assertEqual(caught.exception.code, 'E0101')
        self.assertEqual(sys.getrecursionlimit(), before)

    def test_real_case_distinct_module_identities(self):
        from test_modules import ModuleTests
        self.root = ModuleTests.case_sensitive_directory(self)
        self.write('math', 'pub fn f()->i32{return 20;}')
        self.write('Math', 'pub fn f()->i32{return 22;}')
        self.assertFalse((self.root / 'math.ocl').samefile(self.root / 'Math.ocl'))
        self.write('main', 'import math; import Math; fn main()->i32{return math::f()+Math::f();}')
        program, _ = self.compile()
        self.assertEqual(len({f.name for f in program.functions}), 3)
        self.native()

    def test_constant_names_in_index_assignment_indices(self):
        self.write('indices', 'pub const K:i32=3;')
        for index in ('K', 'indices::K'):
            with self.subTest(index=index):
                self.write('main', 'import indices; const K:i32=3; '
                           'fn main()->i32{var a:[i32;4]=[0,0,0,0]; '
                           f'a[{index}]=42; return a[3];}}')
                # In-process compilation makes this witness visible to the
                # in-memory mutation runner; native runs verify the stored slot.
                self.compile()
                self.native()

    def test_block_constant_shadow_does_not_escape(self):
        for block in ('{let x:i32=9;}', 'while false {let x:i32=9;}'):
            with self.subTest(block=block):
                self.write('main', 'const x:i32=42; fn main()->i32{' + block + 'return x;}')
                _, ir = self.compile()
                self.assertIn('ret i32 42', ir)
                self.native()

    def test_private_candidate_message_order_is_stable(self):
        self.write('a', 'const N:i32=1;')
        self.write('b', 'const N:i32=2;')
        messages = []
        for imports in ('import a; import b;', 'import b; import a;'):
            self.write('main', imports + 'fn main()->i32{return N;}')
            messages.append(self.error('E0314').message)
        self.assertEqual(messages[0], messages[1])
        self.assertEqual(messages[0], "'N' is private in a, b; use a public API")

    def test_legacy_qualification_is_a_parse_error(self):
        for source in ('fn main()->i32{return a::b();}',
                       'fn main()->i32{let x:a::T=1;return 42;}',
                       'fn main()->i32{return a::N;}'):
            with self.subTest(source=source):
                with self.assertRaises(DiagnosticError) as caught:
                    compile_source(source)
                self.assertEqual(caught.exception.code, 'E0100')

    def test_struct_type_used_as_enum_reports_source_name(self):
        self.write('a', 'pub struct S{x:i32}')
        self.write('main', 'import a; fn main()->i32{return a::S.x;}')
        error = self.error('E0317')
        self.assertEqual(error.message, "'a::S' is not an enum")
        self.assertNotIn('ocl.ns.', error.message)

    def test_containment_remains_enforced_in_namespace_graph(self):
        self.write('main', 'import a; ' + MAIN)
        self.write('a', 'pub fn f()->i32{return 42;}')
        original = Path.resolve
        def escape(path, *args, **kwargs):
            if path.name == 'a.ocl':
                return self.root.parent / 'a.ocl'
            return original(path, *args, **kwargs)
        # Resolution simulation: this does not claim host symlink support.
        with mock.patch.object(Path, 'resolve', escape):
            self.error('E0305')


if __name__ == '__main__':
    unittest.main()
