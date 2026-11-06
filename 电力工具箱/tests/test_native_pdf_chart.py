import os, sys, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '电力工具箱'))
sys.path.insert(0, str(ROOT / '广东运行日报PDF解析'))
from gd_pdf_tool import parser as p
from toolbox_native_domain import invoke
from test_daily_pdf_parser import sample_chart

class PdfNativeDomainTests(unittest.TestCase):
    def test_strict_domain_registered(self):
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            self.assertEqual([0.1, 0.2], invoke('pdf_chart','scale_values', {'values':[1,2], 'target':0.3}))

    def compare_chart(self, image, declaration=None):
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python', 'TOOLBOX_COMPUTE_BACKEND':'python'}):
            expected = p.analyze_chart_image(image, 1234.5, declaration)
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            actual = p.analyze_chart_image(image, 1234.5, declaration)
        self.assertEqual(expected.rows, actual.rows)
        np.testing.assert_allclose(expected.pixel_counts, actual.pixel_counts, atol=1e-8)
        for key in ('category_count','category_geometry_source','detected_x_cluster_count','plot_top_y','plot_baseline_y'):
            self.assertEqual(expected.diagnostics[key], actual.diagnostics[key])

    def test_chart_missing_tiny_and_overlapping_colors(self):
        self.compare_chart(sample_chart([(20,10,50),(0,0,0),(0.4,0.7,0),(12,0,0),(0,2,0),(0,0,5),(3,4,5)], grid_overlay=True))

    def test_gray_and_declared_totals(self):
        parts = [(10,12,30)] + [(2,3,4)] * 29
        declaration = p.DeclarationInfo(p.DeclarationRow(100,10.15,20.25,69.6),p.DeclarationRow(60,10.3,20.4,29.3))
        self.compare_chart(sample_chart(parts,gray=True), declaration)

    def test_normal_parser_uses_single_compact_native_chart(self):
        image = sample_chart([(10,20,30)]*6)
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}), patch.object(p,'_make_color_masks',side_effect=AssertionError('Python image kernel invoked')), patch.object(p, '_invoke_native_domain', wraps=p._invoke_native_domain) as native:
            self.assertEqual(6, len(p.analyze_chart_image(image, 600).rows))
        self.assertEqual(1, native.call_count)
        domain, action, payload = native.call_args.args
        self.assertEqual(('pdf_chart', 'analyze_chart'), (domain, action))
        self.assertIsInstance(payload['pixels'], str)
        self.assertLess(len(payload['pixels']), image.width * image.height * 4 + 4)

    def test_declaration_reordered_and_missing_fields(self):
        tables = [[[ '类别','新能源容量','气机容量','申报容量','煤机容量'],['零价及以下','30 (3%)','20','60','10'],['报价600以上','3','2','6','1']]]
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
            expected = p._read_declaration_tables(tables,require_header=True)
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            self.assertEqual(expected,p._read_declaration_tables(tables,require_header=True))
            self.assertEqual(p.DeclarationInfo(None,None),p._read_declaration_tables([[['零价','1']]]))

    def test_regular_axis_missing_labels_and_fallback(self):
        image = sample_chart([(20,10,30)]*10)
        array = np.array(image)
        array[575:635,56+4*32-3:56+4*32+3] = 255
        self.compare_chart(Image.fromarray(array))
        array[575:635] = 255
        self.compare_chart(Image.fromarray(array))

    def test_scale_and_row_order_parity(self):
        cases = [([1,1,1],0.2),([0,0],0),([],0),([0.00001,100,0.00003],0.1),([1,2,3],12.25)]
        for values,target in cases:
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected = p._scale_values_to_total(values,target)
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                self.assertEqual(expected,p._scale_values_to_total(values,target))
        counts = [(1.5,2.5,3.5)]*30
        for baseline in (0,1.25,12.35,1234.5):
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected = p._rows_from_pixels(counts,baseline/7.5,baseline,None)
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                self.assertEqual(expected,p._rows_from_pixels(counts,baseline/7.5,baseline,None))

    def test_baseline_text_preserves_marker_and_capacity_rules(self):
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            for text,expected in [('无汇总数据',None),('零价及以下 12.5 999 12345.67 报价600以上 99999',12345.67),('零价及以下 -5 1000 报价600以上 20000',None)]:
                self.assertEqual({'value':expected},invoke('pdf_chart','baseline_text',{'text':text}))

    def test_candidate_scoring_parity(self):
        for image in (sample_chart([(30,20,10)]*12),Image.new('RGB',(600,300),'white'),Image.new('RGB',(10,10),'white')):
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected = p._score_quote_chart_candidate(image)
            with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                self.assertEqual(expected,p._score_quote_chart_candidate(image))

    def test_native_errors_are_not_swallowed_by_pdf_engine_fallbacks(self):
        from types import SimpleNamespace
        from contextlib import contextmanager
        @contextmanager
        def fake_open(path):
            yield SimpleNamespace(pages=[SimpleNamespace(extract_tables=lambda:[])])
        with patch.object(p,'_open_pdf',fake_open), patch.object(p,'_invoke_native_domain',side_effect=RuntimeError('missing native backend')):
            with self.assertRaisesRegex(RuntimeError,'missing native backend'):
                p.extract_declaration_info(Path('unused.pdf'))
        with patch.object(p,'_open_pdf',side_effect=OSError('no embedded image')), patch.object(p,'render_quote_chart_crop',return_value=sample_chart([(10,20,30)]*6)), patch.object(p,'_invoke_native_domain',side_effect=ValueError('native scoring failed')):
            with self.assertRaisesRegex(p.ParseError,'native scoring failed'):
                p.extract_quote_chart_image(Path('unused.pdf'))

    def test_empty_chart_and_unallocatable_capacity(self):
        with patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            with self.assertRaises((p.ParseError,ValueError)):
                p.analyze_chart_image(Image.new('RGB',(200,200),'white'), 100)
            with self.assertRaises(ValueError):
                invoke('pdf_chart','scale_values',{'values':[0,0],'target':2})

if __name__ == '__main__': unittest.main()

