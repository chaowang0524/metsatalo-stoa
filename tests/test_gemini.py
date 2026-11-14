import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from endpoints import gemini
from stoa import pipeline, verda
from stoa.answer import Answer
from stoa.inspect_file import FileInfo
from stoa.tools import vision_tools


class GeminiTests(unittest.TestCase):
    def test_client_uses_google_url_and_accepts_mixed_case_key(self):
        with patch.object(verda, 'os', SimpleNamespace(environ={'Gemini_API_KEY': 'unit-test-key'})), \
             patch.dict(verda._clients, {}, clear=True), patch.object(verda, 'OpenAI') as constructor:
            verda.client('gemini')
        constructor.assert_called_once_with(base_url=gemini.BASE_URL, api_key='unit-test-key')

    def test_missing_key_reports_variable_name(self):
        with patch.object(verda, 'os', SimpleNamespace(environ={})), \
             patch.dict(verda._clients, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'GEMINI_API_KEY'):
                verda.client('gemini')

    def test_raster_profile_calls_gemini(self):
        path = Path('plan.png')
        info = FileInfo(path, 'raster', image=path)
        with patch.object(pipeline, 'inspect_file', return_value=info), \
             patch.object(vision_tools, 'ask_vision_model', return_value=Answer(3)) as vision:
            result = pipeline.answer(path, 'How many windows?', router='hybrid_gemini')
        self.assertEqual(vision.call_args.kwargs['endpoint'], 'gemini')
        self.assertEqual(result.answer.value, 3)

    def test_default_profile_uses_gemini_only_for_cubicasa(self):
        cases = [
            ('junction-dataset-preview/data/cubicasa5k/plan.png', 'raster', 'gemini'),
            ('data/CUBICASA5K/plan.png', 'raster', 'gemini'),
            ('data/finna/scan.jpg', 'raster', 'kimi'),
            ('data/opening_design/scanned.pdf', 'raster', 'kimi'),
        ]
        for name, kind, expected in cases:
            path = Path(name)
            with self.subTest(path=name), patch.object(pipeline, 'ROUTER', 'hybrid'), \
                 patch.object(pipeline, 'inspect_file', return_value=FileInfo(path, kind, image=path)), \
                 patch.object(vision_tools, 'ask_vision_model', return_value=Answer(3)) as vision:
                pipeline.answer(path, 'How many windows?')
            self.assertEqual(vision.call_args.kwargs['endpoint'], expected)

    def test_explicit_kimi_profile_keeps_kimi_for_cubicasa(self):
        path = Path('data/cubicasa5k/plan.png')
        with patch.object(pipeline, 'inspect_file', return_value=FileInfo(path, 'raster', image=path)), \
             patch.object(vision_tools, 'ask_vision_model', return_value=Answer(3)) as vision:
            pipeline.answer(path, 'How many windows?', router='kimi')
        self.assertEqual(vision.call_args.kwargs['endpoint'], 'kimi')

    def test_pdf_vision_and_table_models_remain_kimi(self):
        info = FileInfo(Path('plan.pdf'), 'vector_pdf', image=Path('plan.pdf'))
        fn = Mock(return_value=Answer(1))
        tool = SimpleNamespace(name='ask_vision_model', source='image', fn=fn)
        for name in ('hybrid', 'hybrid_gemini'):
            profile = pipeline.PROFILES[name]
            pipeline._run(tool, info, {'question': 'Count doors'}, {}, profile)
            self.assertEqual(fn.call_args.kwargs['endpoint'], 'kimi')
            self.assertEqual(profile.route, 'deepseek')
            self.assertEqual(profile.table, 'kimi')

    def test_google_does_not_receive_kimi_thinking_body(self):
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"answer": 3, "unit": "count"}'))])
        for endpoint in ('gemini', 'kimi'):
            with self.subTest(endpoint=endpoint), \
                 patch.object(vision_tools, '_png_bytes', return_value=b'fixture'), \
                 patch.object(verda, 'chat', return_value=resp) as chat, \
                 patch.object(verda, 'usage_of', return_value={}):
                vision_tools.ask_vision_model('plan.png', 'How many windows?', endpoint=endpoint, effort='low')
                kwargs = chat.call_args.kwargs
                self.assertEqual(kwargs['reasoning_effort'], 'low')
                self.assertEqual(kwargs['max_tokens'], 4096)
                if endpoint == 'gemini':
                    self.assertNotIn('extra_body', kwargs)
                else:
                    self.assertEqual(kwargs['extra_body'], {'thinking': {'type': 'enabled'}})

    def test_usage_estimate_uses_gemini_prices(self):
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100))
        self.assertAlmostEqual(verda.usage_of(resp, 'gemini')['cost_usd'], 0.00225)

    def test_google_output_gap_includes_thinking_without_double_counting(self):
        for reported in (80, 491):
            response = SimpleNamespace(usage=SimpleNamespace(
                prompt_tokens=1208, completion_tokens=reported, total_tokens=1699))
            usage = verda.usage_of(response, 'gemini')
            self.assertEqual(usage['completion_tokens'], 491)
            self.assertEqual(usage['reported_completion_tokens'], reported)
            self.assertEqual(usage['unattributed_output_tokens'], 491-reported)
            self.assertAlmostEqual(usage['cost_usd'], 0.0054945)

    def test_other_provider_token_accounting_is_unchanged(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=1000, completion_tokens=100, total_tokens=1500))
        self.assertEqual(verda.usage_of(response, 'kimi')['completion_tokens'], 100)


if __name__ == '__main__':
    unittest.main()
