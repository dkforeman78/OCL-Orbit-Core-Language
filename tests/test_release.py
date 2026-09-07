import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from compiler import cli
from native_support import build_and_run, assert_deterministic_trap, ROOT


class ReleaseCliTests(unittest.TestCase):
    def test_optimization_flag_forwarding_and_argument_placements(self):
        for args, expected in (
            (['build', 'source file.ocl'], '-O0'),
            (['build', '--release', 'source file.ocl'], '-O2'),
            (['build', 'source file.ocl', '--release', '-o', 'output file.exe'], '-O2'),
        ):
            with self.subTest(args=args), mock.patch.object(cli, '_read_and_compile', return_value='IR'), \
                    mock.patch.object(cli, '_clang', return_value='clang'), \
                    mock.patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
                command = run.call_args.args[0]
                self.assertEqual([a for a in command if a.startswith('-O')], [expected])
                self.assertEqual(command[0], 'clang')
                if '-o' in args:
                    self.assertEqual(command[-2:], ['-o', 'output file.exe'])

    def test_release_rejected_before_reading_or_writing(self):
        for command in ('check', 'emit-ir'):
            with self.subTest(command=command), mock.patch.object(cli, '_read_and_compile') as read, \
                    mock.patch.object(Path, 'write_text') as write, contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as caught:
                    cli.main([command, 'missing.ocl', '--release', '-o', 'untouched'])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('--release is only valid with build', error.getvalue())
                read.assert_not_called()
                write.assert_not_called()


class ReleaseExecutionTests(unittest.TestCase):
    def both(self, source, *, trap=False):
        for release in (False, True):
            with self.subTest(release=release):
                result = build_and_run(self, source, 'release case', release=release)
                if trap:
                    assert_deterministic_trap(self, result)
                else:
                    self.assertEqual(result, 42)

    def test_exact_width_arithmetic_and_conversions(self):
        for width in (8, 16, 32, 64):
            for sign in ('i', 'u'):
                t = f'{sign}{width}'
                signed = sign == 'i'
                top = f'((1 as {t}) << ({width-1} as {t}))' if signed else f'~(0 as {t})'
                comparison = f'a < (0 as {t})' if signed else f'a > (0 as {t})'
                div = f'((-7) as {t}) / (2 as {t}) == ((-3) as {t})' if signed else f'(7 as {t}) / (2 as {t}) == (3 as {t})'
                rem = f'((-7) as {t}) % (2 as {t}) == ((-1) as {t})' if signed else f'(7 as {t}) % (2 as {t}) == (1 as {t})'
                right = f'((a >> ({width-1} as {t})) == ((-1) as {t}))' if signed else f'((a >> ({width-1} as {t})) == (1 as {t}))'
                with self.subTest(type=t):
                    self.both(f'fn f(a: {t}) -> bool {{ '
                              f'var x: {t} = a; x = x + (1 as {t}); '
                              f'return (x - (1 as {t})) == a && (a * (2 as {t})) == (a + a) '
                              f'&& {comparison} && {div} && {rem} && {right} '
                              f'&& (a / (1 as {t})) == a && (a << (0 as {t})) == a '
                              f'&& ((42 as {t}) as i32) == 42; }} '
                              f'fn main() -> i32 {{ return if f({top}) {{42}} else {{1}}; }}')

    def test_invalid_division_remainder_and_shifts_at_every_width(self):
        for width in (8, 16, 32, 64):
            for sign in ('i', 'u'):
                t = f'{sign}{width}'
                cases = [('a / b', f'1 as {t}', f'0 as {t}'),
                         ('a % b', f'1 as {t}', f'0 as {t}'),
                         ('a << b', f'1 as {t}', f'{width} as {t}'),
                         ('a >> b', f'1 as {t}', f'(-1) as {t}')]
                if sign == 'i':
                    for operator in ('/', '%'):
                        cases.append((f'a {operator} b', f'(1 as {t}) << ({width-1} as {t})', f'(-1) as {t}'))
                for expression, a, b in cases:
                    with self.subTest(type=t, expression=expression, b=b):
                        self.both(f'fn f(a: {t}, b: {t}) -> {t} {{ return {expression}; }} '
                                  f'fn main() -> i32 {{ return f({a}, {b}) as i32; }}', trap=True)

    def test_bounds_trap_and_valid_boundary(self):
        for index in (-1, 2, -2147483648, 2147483647):
            self.both(f'fn f(i: i32) -> i32 {{ let a: [i32; 2] = [20,42]; return a[i]; }} '
                      f'fn main() -> i32 {{ return f({index}); }}', trap=True)
        self.both('fn f(i: i32) -> i32 { let a: [i32; 2] = [20,42]; return a[i]; } '
                  'fn main() -> i32 { return f(1); }')

    def test_unselected_traps_are_lazy(self):
        self.both('enum E { A, B } fn bad(z: i32) -> i32 { return 1 / z; } '
                  'fn main() -> i32 { '
                  'let x: i32 = if true {42} else {bad(0)}; '
                  'let y: bool = false && bad(0) == 0; '
                  'let z: bool = true || bad(0) == 0; '
                  'return match E.A { E.A => if z && !y {x} else {1}, E.B => bad(0) }; }')

    def test_all_acceptance_programs_in_both_modes(self):
        for name in ('hello', 'add', 'local', 'decisions', 'repeat', 'loop_control',
                     'arrays', 'structures', 'enums', 'constants', 'integers', 'bitwise', 'literals', 'release'):
            with self.subTest(program=name):
                self.both((ROOT / 'examples' / f'{name}.ocl').read_text(encoding='utf-8'))
