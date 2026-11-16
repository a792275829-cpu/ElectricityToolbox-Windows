"""Windows topology OCR keeps tiled coordinates and the configured engine."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import toolbox_section_prelabel as prelabel

TOOLBOX_TEST_TAGS = ()


class WindowsOcrTests(unittest.TestCase):
    def test_ppocr_predictions_keep_original_coordinates_and_view_evidence(self):
        prediction = SimpleNamespace(json={'res': {
            'rec_texts': ['测试站'], 'rec_scores': [.96], 'rec_boxes': [[2, 3, 20, 15]],
        }})
        paddle = Mock()
        paddle.predict.return_value = [prediction]
        constructor = Mock(return_value=paddle)
        with Image.new('RGB', (40, 30), 'white') as image, patch.dict(
            'sys.modules', {'paddleocr': SimpleNamespace(PaddleOCR=constructor)}
        ), patch.object(prelabel.subprocess, 'run') as launch:
            records = prelabel.recognize(image, enhanced=False)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['text'], '测试站')
        self.assertEqual(records[0]['box'], dict(x=2, y=3, width=18, height=12))
        self.assertEqual(records[0]['views'][0]['bounds'], [0, 0, 40, 30])
        self.assertEqual(records[0]['views'][0]['provenance'], 'actual-inference')
        self.assertEqual(constructor.call_args.kwargs['text_detection_model_name'], 'PP-OCRv6_medium_det')
        launch.assert_not_called()

    def test_unknown_engine_is_rejected_before_starting_an_external_program(self):
        with Image.new('RGB', (10, 10)) as image, patch.object(prelabel.subprocess, 'run') as launch:
            with self.assertRaisesRegex(ValueError, '仅支持 PP-OCRv6'):
                prelabel.recognize(image, engine='unsupported')
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
