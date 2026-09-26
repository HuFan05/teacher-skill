"""Synthetic regressions; no archived research material or protected cases."""
import json
from pathlib import Path
import tempfile
import unittest

from crs_formula import Presentation, excerpt, safe_label, validate, TICK
from crs_model import CRSError

class FormulaPresentationTests(unittest.TestCase):
    def test_plain_text_notation_cannot_pass_as_zero(self):
        for text in ('softmax(z)<1', 'int_0^3 f(t) dt', 'x_t>=4'):
            with self.subTest(text=text), self.assertRaises(CRSError): validate(text)

    def test_broken_formula_blocked(self):
        for text in (r'$\frac{x}{y$', r'$\frac{x}{y}…$', r'$\\frac{x}{y}$', r'\[x+y\]', '$x+y', '&#91;x&#93;'):
            with self.subTest(text=text), self.assertRaises(CRSError): validate(text)

    def test_code_and_prose_are_not_formulas(self):
        self.assertEqual(validate('Plain prose.')['formulas'],0)
        self.assertEqual(validate(TICK+'softmax(z)<1'+TICK)['formulas'],0)

    def test_formula_transport(self):
        value=r'Loss $\mathcal{L}=-\sum_i y_i \log p_i$ and text.'
        self.assertEqual(safe_label(value),value)
        self.assertEqual(validate(value)['formulas'],1)

    def test_formula_is_atomic_in_summary(self):
        value=r'$O(n \log n)$'
        self.assertEqual(excerpt(value+' words',3),value+'…')
        self.assertNotIn('$',excerpt('Start '+value+' words',8))

    def test_replacements_never_touch_existing_formulas_or_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'presentation.json'
            p.write_text(json.dumps([{'source':'softmax(z)_i','tex':r'\mathrm{softmax}(z)_i'}]),encoding='utf-8')
            obj=Presentation(p)
            self.assertEqual(obj.apply('softmax(z)_i'),r'$\mathrm{softmax}(z)_i$')
            literal=TICK+'softmax(z)_i'+TICK
            self.assertEqual(obj.apply(literal),literal)
            self.assertEqual(obj.apply(r'$\mathrm{softmax}(z)_i$'),r'$\mathrm{softmax}(z)_i$')

    def test_raw_prose_replacement_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'presentation.json'
            p.write_text(json.dumps([{'source':'unverified','tex':'verified'}]),encoding='utf-8')
            with self.assertRaises(CRSError):Presentation(p)

    def test_readback_binds_exact_bytes_and_no_render_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'map.md';p.write_text('$x+y$',encoding='utf-8')
            report=Presentation().readback(p,'$x+y$')
            self.assertEqual(report['rendering'],'not_checked')
            self.assertFalse(report['live_obsidian_checked'])
            with self.assertRaises(CRSError):Presentation().readback(p,'$x-y$')

if __name__=='__main__':unittest.main()
