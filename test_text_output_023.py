#!/usr/bin/env python3
"""0.1.23 80-column formatter, streaming, and structural protection."""
from io import StringIO
import unittest

from text_output import ColumnWriter, wrap_line, wrap_text


class ColumnTests(unittest.TestCase):
    def test_long_field_hanging_indent(self):
        output = wrap_line('  next: ' + 'Long engineering explanation ' * 9)
        self.assertTrue(all(len(line) <= 80 for line in output.splitlines()))
        self.assertTrue(output.splitlines()[1].startswith('        '))
        self.assertIn('next:', output)

    def test_streaming_writes_and_newlines(self):
        underlying = StringIO()
        writer = ColumnWriter(underlying)
        writer.write('  next: ' + 'Validation words ' * 6)
        writer.write('continued into another fragment\n')
        writer.write('Short\n')
        writer.flush()
        result = underlying.getvalue()
        self.assertTrue(all(len(line) <= 80 for line in result.splitlines()))
        self.assertTrue(result.endswith('Short\n'))

    def test_terminal_keeps_complete_absolute_paths(self):
        for label in ('Planning results', 'Results directory',
                      'Results archive', 'ICC report', 'Plan'):
            value = '/home/sdunnaga/projects/rhel-223619/backport-plans/backport_plan_02'
            original = f'{label}: {value}\n'
            underlying = StringIO()
            writer = ColumnWriter(underlying)
            # Split across writes to test the streaming path too.
            writer.write(original[:26])
            writer.write(original[26:])
            writer.flush()
            self.assertEqual(underlying.getvalue(), original)

    def test_terminal_keeps_url_and_nested_paths(self):
        examples = [
            'Ticket: https://issues.redhat.com/browse/RHEL-223619',
            'Conflict: arch/arm64/include/asm/pgtable.h',
            'Source: ./backport-plans/backport_plan_02/report.txt',
        ]
        for value in examples:
            with self.subTest(value=value):
                text = value + ' extra-context ' * 8 + '\n'
                target = StringIO()
                writer = ColumnWriter(target)
                writer.write(text)
                writer.flush()
                self.assertEqual(target.getvalue(), text)

    def test_terminal_wraps_ordinary_prose(self):
        input_text = '  next: ' + 'Engineering explanation needs careful attention ' * 6
        target = StringIO()
        writer = ColumnWriter(target)
        writer.write(input_text + '\n')
        writer.flush()
        self.assertTrue(all(len(line) <= 80 for line in target.getvalue().splitlines()))
        self.assertIn('\n', target.getvalue())

    def test_indivisible_path_is_display_only(self):
        path = '/home/person/' + ('very-long-directory/' * 12)
        wrapped = wrap_line('  report: ' + path)
        self.assertTrue(all(len(line) <= 80 for line in wrapped.splitlines()))
        self.assertEqual(path, '/home/person/' + ('very-long-directory/' * 12))

    def test_protected_lines_remain_byte_for_byte_unchanged(self):
        text = 'a' * 120 + '\nnext: ' + 'words ' * 40 + '\n'
        wrapped = wrap_text(text, protected=lambda l: l.startswith('a'))
        self.assertEqual(wrapped.splitlines()[0], 'a' * 120)
        self.assertTrue(all(len(l) <= 80 for l in wrapped.splitlines()[1:]))


if __name__ == '__main__':
    unittest.main()
