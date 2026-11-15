"""Bounded 0.17 mutation checks. Replacements execute in memory, never on disk.

Run: python tools/check_namespace_mutations.py
Each mutation runs selected regression tests in a fresh, time-bounded process.
A malformed mutation is a harness error, not a caught defect.
"""
import importlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

CASES = [
    ('qualified privacy', 'namespaces', 'Binder',
     'if not declaration.public:', 'if False:',
     ['test_private_qualified_and_unqualified_calls']),
    ('unqualified privacy', 'namespaces', 'Binder',
     'if declaration.public:', 'if True:',
     ['test_private_qualified_and_unqualified_calls']),
    ('ambiguity guard', 'namespaces', 'Binder',
     'if len(candidates) > 1:', 'if False:', ['test_ambiguity_order_is_stable']),
    ('own-name precedence', 'namespaces', 'Binder',
     'if own is not None:', 'if False:',
     ['test_own_declarations_win_and_wrong_kind_does_not_fall_back']),
    ('public type exposure', 'namespaces', 'Binder',
     'if exposed and not declaration.public:', 'if False:',
     ['test_private_type_cannot_leak_through_public_interface']),
    ('declaration owner identity', 'namespaces', 'Binder',
     '{owner.encode("utf-8").hex()}', '{"shared"}',
     ['test_duplicate_names_are_legal_between_modules']),
    ('index-assignment index binding', 'namespaces', 'Binder',
     "for field in ('expression', 'initializer', 'index'):",
     "for field in ('expression', 'initializer'):",
     ['test_constant_names_in_index_assignment_indices']),
    ('block scope isolation', 'namespaces', 'Binder',
     'frames.append((iter(statement.statements), set(local), [], statement))',
     'frames.append((iter(statement.statements), local, [], statement))',
     ['test_block_constant_shadow_does_not_escape']),
    ('loop scope isolation', 'namespaces', 'Binder',
     'frames.append((iter(statement.body.statements), set(local), [], statement))',
     'frames.append((iter(statement.body.statements), local, [], statement))',
     ['test_block_constant_shadow_does_not_escape']),
    ('private candidate ordering', 'namespaces', 'Binder',
     'for qualifier, target in sorted(unit.imports.items()):',
     'for qualifier, target in unit.imports.items():',
     ['test_private_candidate_message_order_is_stable']),
    ('legacy qualification parse guard', 'parser', 'Parser',
     'if self.namespaces and self._match(TokenKind.DOUBLE_COLON):',
     'if self._match(TokenKind.DOUBLE_COLON):',
     ['test_legacy_qualification_is_a_parse_error']),
    ('struct-as-enum diagnostic guard', 'namespaces', 'Binder',
     'if not isinstance(declaration, n.EnumDeclaration):\n                        self.error',
     'if False:\n                        self.error',
     ['test_struct_type_used_as_enum_reports_source_name']),
    ('duplicate own declaration', 'namespaces', 'Binder',
     'if declaration.name in table[category]:', 'if False:',
     ['test_duplicates_within_module_still_fail']),
    ('wrong-kind fallback', 'namespaces', 'Binder',
     'if not isinstance(declaration, expected):', 'if False:',
     ['test_own_declarations_win_and_wrong_kind_does_not_fall_back']),
    ('mixed-mode boundary', 'modules', 'load_program',
     'if child.namespaces != module.namespaces:', 'if False:',
     ['test_both_mixed_mode_directions', 'test_transitive_mixed_mode_location']),
    ('late/duplicate header', 'namespaces', 'prepare',
     "if (token.kind is TokenKind.IDENTIFIER and token.lexeme == 'module'",
     "if (False and token.kind is TokenKind.IDENTIFIER and token.lexeme == 'module'",
     ['test_invalid_headers']),
    ('global module cap', 'modules', 'load_program',
     'if len(modules) >= MAX_MODULES:', 'if False:', ['test_module_cap_remains_global']),
    ('constant binding owner', 'namespaces', 'Binder',
     "changes['name'] = self.value(owner, node.name, node.location, n.ConstDeclaration)",
     "changes['name'] = node.name",
     ['test_duplicate_names_are_legal_between_modules']),
]


def child(index):
    from test_namespaces import NamespaceTests
    label, module_name, attribute, old, new, methods = CASES[index]
    module = importlib.import_module('compiler.' + module_name)
    source = Path(module.__file__).read_text(encoding='utf-8')
    if source.count(old) != 1:
        raise RuntimeError(f'{label}: expected one replacement, got {source.count(old)}')
    scope = {'__name__': module.__name__, '__package__': 'compiler'}
    exec(compile(source.replace(old, new), module.__file__, 'exec'), scope)
    suite = unittest.TestSuite(NamespaceTests(method) for method in methods)
    stream = io.StringIO()
    with mock.patch.object(module, attribute, scope[attribute]):
        result = unittest.TextTestRunner(stream=stream).run(suite)
    if result.wasSuccessful():
        print(f'SURVIVED: {label}')
        return 1
    print(f'CAUGHT: {label} ({len(result.failures)} failures, {len(result.errors)} errors)')
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--case':
        return child(int(sys.argv[2]))
    baseline = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                               '-p', 'test_namespaces.py'], cwd=ROOT, timeout=120)
    if baseline.returncode:
        return baseline.returncode
    failed = False
    for i in range(len(CASES)):
        result = subprocess.run([sys.executable, __file__, '--case', str(i)], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, file=sys.stderr, end='')
        failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
