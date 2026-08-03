import unittest

from src.reporting_prompts import (
    AUDIT_ANALYSIS_SYSTEM_PROMPT_EN,
    AUDIT_ANALYSIS_SYSTEM_PROMPT_ES,
    REPORT_WRITER_SYSTEM_PROMPT_EN,
    REPORT_WRITER_SYSTEM_PROMPT_ES,
    get_prompt_bundle,
)


class ReportingPromptsTest(unittest.TestCase):
    def test_get_prompt_bundle_defaults_to_spanish_executive(self):
        bundle = get_prompt_bundle(None, None)

        self.assertEqual(bundle['language'], 'es-ES')
        self.assertEqual(bundle['tone'], 'executive')
        self.assertEqual(bundle['audit_analysis_system_prompt'], AUDIT_ANALYSIS_SYSTEM_PROMPT_ES)
        self.assertEqual(bundle['report_writer_system_prompt'], REPORT_WRITER_SYSTEM_PROMPT_ES)
        self.assertIn('prioriza', bundle['language_prompt'].lower())
        self.assertIn('dirección', bundle['tone_prompt'].lower())

    def test_get_prompt_bundle_selects_english_technical(self):
        bundle = get_prompt_bundle('en', 'technical')

        self.assertEqual(bundle['language'], 'en')
        self.assertEqual(bundle['tone'], 'technical')
        self.assertEqual(bundle['audit_analysis_system_prompt'], AUDIT_ANALYSIS_SYSTEM_PROMPT_EN)
        self.assertEqual(bundle['report_writer_system_prompt'], REPORT_WRITER_SYSTEM_PROMPT_EN)
        self.assertIn('english', bundle['language_prompt'].lower())
        self.assertIn('technical', bundle['tone_prompt'].lower())
        self.assertIn('independent reviewer', bundle['quality_review_system_prompt'].lower())
        self.assertIn('one minimal repair', bundle['repair_system_prompt'].lower())

    def test_spanish_products_and_quality_prompts_are_fully_localized(self):
        bundle = get_prompt_bundle('es-ES', 'technical')

        self.assertIn('producto técnico', bundle['tone_prompt'].lower())
        self.assertIn('criterios de aceptación', bundle['tone_prompt'].lower())
        self.assertIn('revisor independiente', bundle['quality_review_system_prompt'].lower())
        self.assertIn('una única reparación', bundle['repair_system_prompt'].lower())

    def test_analysis_prompt_requires_pattern_synthesis_instead_of_one_finding_per_url(self):
        bundle = get_prompt_bundle('es-ES', 'executive', 'existing_client')

        self.assertIn('entre 5 y 12 hallazgos', bundle['audit_analysis_system_prompt'])
        self.assertIn('No generes un hallazgo por URL', bundle['audit_analysis_system_prompt'])

    def test_get_prompt_bundle_falls_back_for_unknown_options(self):
        bundle = get_prompt_bundle('fr', 'casual')

        self.assertEqual(bundle['language'], 'es-ES')
        self.assertEqual(bundle['tone'], 'executive')


if __name__ == '__main__':
    unittest.main()
