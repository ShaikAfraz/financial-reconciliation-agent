"""
Unit tests for the BenchmarkEvaluator and ErrorPropagationAnalyzer.
"""

import unittest
from evaluation.evaluator import BenchmarkEvaluator
from evaluation.error_propagation import ErrorPropagationAnalyzer

class TestEvaluation(unittest.TestCase):

    def setUp(self):
        self.evaluator = BenchmarkEvaluator()
        self.error_analyzer = ErrorPropagationAnalyzer()

    def test_benchmark_metrics_calculation(self):
        results = self.evaluator.run_all()
        metrics = results["metrics"]

        # Ensure all metrics are present and within valid bounds [0.0, 1.0]
        self.assertIn("plan_sequence_correctness", metrics)
        self.assertIn("tool_selection_accuracy", metrics)
        self.assertIn("recommendation_groundedness", metrics)

        self.assertGreaterEqual(metrics["tool_selection_accuracy"], 0.85)
        self.assertGreaterEqual(metrics["plan_sequence_correctness"], 0.70)
        self.assertGreaterEqual(metrics["recommendation_groundedness"], 0.85)

    def test_error_propagation_detection(self):
        results = self.error_analyzer.run_all()
        self.assertIn("overall_error_propagation_rate", results)
        self.assertGreater(results["total_scenarios_evaluated"], 0)
        self.assertGreater(results["overall_error_propagation_rate"], 0.50)

if __name__ == "__main__":
    unittest.main()
