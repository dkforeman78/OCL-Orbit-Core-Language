import unittest

from compiler.driver import compile_source
from compiler.diagnostics import DiagnosticError
from compiler.lexer import lex, TokenKind
from native_support import build_and_run


class LiteralTests(unittest.TestCase):
    def diagnostic(self, source, code):
        with self.assertRaises(DiagnosticError) as caught:
            compile_source(source)
        self.assertEqual(caught.exception.code, code)

    def test_equivalent_spellings(self):
        baseline = compile_source('fn main() -> i32 { return 42; }')[1]
        for value in ('0x2a', '0x2A', '0b0010_1010', '4_2', '00042'):
            self.assertEqual(compile_source(f'fn main() -> i32 {{ return {value}; }}')[1], baseline)

    def test_boundaries_in_all_radices(self):
        for spelling in (str, lambda n: hex(n), lambda n: bin(n)):
            for value in (0, 2147483647, -2147483648):
                ir = compile_source(f'fn main() -> i32 {{ return {spelling(value)}; }}')[1]
                self.assertIn(f'ret i32 {value}', ir)
            for value in (2147483648, -2147483649):
                self.diagnostic(f'fn main() -> i32 {{ return {spelling(value)}; }}', 'E0203')
        self.diagnostic('fn main() -> i32 { return 0xffff_ffff; }', 'E0203')

    def test_malformed_numeric_words(self):
        for value in ('0x', '0b', '0X2a', '0B10', '0b102', '0xfg', '42u8',
                      '1_', '1__0', '0x_ff', '0b_1', '0x1_', '12é'):
            with self.subTest(value=value):
                self.diagnostic(f'fn main() -> i32 {{ return {value}; }}', 'E0002')

    def test_huge_literals_and_leading_zeroes(self):
        for prefix, digit in (('', '9'), ('0x', 'f'), ('0b', '1')):
            self.diagnostic(f'fn main() -> i32 {{ return {prefix + digit * 10000}; }}', 'E0203')
            ir = compile_source(f'fn main() -> i32 {{ return {prefix + "0" * 10000}1; }}')[1]
            self.assertIn('ret i32 1', ir)

    def test_array_lengths(self):
        for length in ('2', '0x2', '0b10', '0_2'):
            compile_source(f'fn main() -> i32 {{ let a: [i32; {length}] = [20,22]; return a[0]+a[1]; }}')
        for length in ('0', '0x101', '0b100000001', '9' * 10000):
            self.diagnostic(f'fn main() -> i32 {{ let a: [i32; {length}] = [42]; return 42; }}', 'E0219')
        values = ','.join(['0'] * 256)
        compile_source(f'fn main() -> i32 {{ let a: [i32; 0x100] = [{values}]; return 42; }}')

    def test_comment_insertion_and_division(self):
        plain = 'fn main() -> i32 { return 84 / 2; }'
        commented = '// start\r\nfn/*α*/ main() -> i32 { return 84 / /* / ** */ 2; }// EOF'
        self.assertEqual(compile_source(plain)[1], compile_source(commented)[1])
        self.assertEqual([t.lexeme for t in lex('ab/*x*/cd </*x*/<')][:-1], ['ab', 'cd', '<', '<'])

    def test_comments_do_not_nest(self):
        tokens = lex('/* outer /* inner */42')
        self.assertEqual(tokens[0].lexeme, '42')
        self.assertEqual(tokens[0].kind, TokenKind.INTEGER)

    def test_locations_after_comments(self):
        source = '// hi\r\n/* α\r\nβ */42'
        token = lex(source)[0]
        self.assertEqual((token.location.offset, token.location.line, token.location.column),
                         (source.index('42'), 3, 5))
        with self.assertRaises(DiagnosticError) as caught:
            lex(' \n/* unfinished')
        self.assertEqual(caught.exception.code, 'E0003')
        self.assertEqual(caught.exception.location.line, 2)
        self.assertEqual(caught.exception.location.column, 1)

    def test_a_block_comment_cannot_close_on_its_opening_star(self):
        # `/*/` is an opener whose star is still the opener's own. Searching for
        # the terminator from the start of the delimiter rather than past it
        # would let three characters be a complete comment.
        for source in ('/*/', 'a /*/ b', '/*', '/*x'):
            with self.subTest(source=source):
                with self.assertRaises(DiagnosticError) as caught:
                    lex(source)
                self.assertEqual(caught.exception.code, 'E0003')
        self.assertEqual([t.lexeme for t in lex('/**/42')][:-1], ['42'])
        self.assertEqual([t.lexeme for t in lex('/***/42')][:-1], ['42'])

    def test_columns_advance_past_a_same_line_comment(self):
        # A line comment is always followed by a newline or EOF, so only a block
        # comment leaves a token behind it on the same line to measure.
        for source in ('a /*note*/ b', 'a /**/ b', 'a /* é中 */ b'):
            with self.subTest(source=source):
                first, second = lex(source)[:2]
                self.assertEqual((first.location.line, first.location.column), (1, 1))
                self.assertEqual((second.location.line, second.location.column),
                                 (1, source.index('b') + 1))

    def test_columns_advance_by_the_width_of_a_numeric_spelling(self):
        # The column must step over the literal as it was written, not over the
        # value it denotes, so radix prefixes, separators and leading zeroes all
        # have to count.
        for spelling in ('42', '0x2a', '0b10_1010', '1_000', '00042', '0x7fff_ffff'):
            with self.subTest(spelling=spelling):
                source = f'{spelling} b'
                token = lex(source)[1]
                self.assertEqual((token.lexeme, token.location.line, token.location.column),
                                 ('b', 1, len(spelling) + 2))

    def test_an_over_large_array_length_names_the_element_cap(self):
        # The parser's bounded decode and the semantic cap are two different
        # numbers. If they drift apart, an over-long length is still rejected
        # but stops naming the limit the programmer actually exceeded.
        for length in ('257', '0x101', '0b1_0000_0001', '9' * 400):
            with self.subTest(length=length[:16]):
                with self.assertRaises(DiagnosticError) as caught:
                    compile_source(f'fn main() -> i32 {{ let a: [i32; {length}] = [42]; return 42; }}')
                self.assertEqual(caught.exception.code, 'E0219')
                self.assertIn('at most 256 elements', str(caught.exception))

    def test_native_constants_conversions_and_comments(self):
        for type_name in ('i8','u8','i16','u16','i32','u32','i64','u64'):
            source = (f'const N: {type_name} = 0x2a as {type_name}; '
                      f'fn id(x: {type_name}) -> {type_name} {{ return x; }} '
                      f'fn main() -> i32 {{ /* compare */ return if id(0b10_1010 as {type_name}) == N {{ 42 }} else {{ 1 }}; }}')
            self.assertEqual(build_and_run(self, source, 'literal_' + type_name, release=False), 42)
