"""Turn-scoped coordination contract; inference is now owned by Codex."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_provider import plan_schema, validate_plan

class ContractTests(unittest.TestCase):
    def source(self, kind='user'):
        return {'trigger': {'kind': kind}, 'releasableJobs': ['job-1'], 'sessions': [{'id':'worker'}], 'dispatchBudget': 2}
    def test_result_turn_cannot_request_release_even_if_previous_jobs_exist(self):
        schema = plan_schema(self.source('result'))
        self.assertEqual(schema['properties']['release']['maxItems'], 0)
    def test_user_release_and_dispatch_ids_are_scoped_to_current_snapshot(self):
        schema = plan_schema(self.source())
        self.assertEqual(schema['properties']['release']['items']['enum'], ['job-1'])
        self.assertEqual(schema['properties']['dispatches']['items']['properties']['session']['enum'], ['worker'])
        self.assertEqual(schema['properties']['dispatches']['maxItems'], 2)
    def test_no_targets_or_budget_disables_dispatch_without_mutating_schema(self):
        source = self.source(); source.update(sessions=[], dispatchBudget=0, releasableJobs=[])
        schema = plan_schema(source)
        self.assertEqual(schema['properties']['dispatches']['maxItems'], 0)
        self.assertEqual(schema['properties']['release']['maxItems'], 0)
        self.assertNotIn('maxItems', plan_schema(self.source())['properties']['release'])
    def test_active_worker_cannot_be_released_and_dispatched_in_one_plan(self):
        source=self.source(); source['jobs']=[{'id':'job-1','session':'worker','state':'running'}]
        schema=plan_schema(source)
        self.assertEqual(schema['properties']['dispatches']['maxItems'],0)
        self.assertEqual(schema['properties']['release']['items']['enum'],['job-1'])
    def test_invalid_commands_and_duplicate_targets_fail_closed(self):
        for plan in [dict(reply='ok',release=[],dispatches=[],shell='command'),
                     dict(reply='ok',release=[],dispatches=[{'session':'worker','instruction':'a'}]*2)]:
            with self.assertRaises(ValueError): validate_plan(plan)

if __name__ == '__main__': unittest.main()
