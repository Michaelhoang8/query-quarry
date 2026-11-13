import unittest
from engine import compare,experiment,EXAMPLES,validate


class EngineTests(unittest.TestCase):
    def test_customer_index_changes_plan_and_preserves_results(self):
        result=compare(EXAMPLES['customer'])
        self.assertIsNone(result['baseline']['error'])
        self.assertIsNone(result['indexed']['error'])
        self.assertTrue(result['comparison']['ordered_preview_equal'])
        self.assertTrue(result['comparison']['complete_unclipped_results'])
        self.assertTrue(any('SCAN orders' in p['detail'] for p in result['baseline']['plan']))
        self.assertTrue(any('idx_orders_customer' in p['detail'] for p in result['indexed']['plan']))
        self.assertLess(result['indexed']['vm_steps_estimate'],result['baseline']['vm_steps_estimate'])
        self.assertGreater(result['indexed']['allocated_index_bytes'],0)
        self.assertEqual(len(result['indexed']['samples']),3)

    def test_every_example_has_equal_complete_results(self):
        for request in EXAMPLES.values():
            result=compare(request)
            self.assertIsNone(result['baseline']['error'])
            self.assertIsNone(result['indexed']['error'])
            self.assertTrue(result['comparison']['ordered_preview_equal'])
            self.assertTrue(result['comparison']['complete_unclipped_results'])

    def test_preview_limit_is_explicit(self):
        result=experiment('SELECT id FROM orders ORDER BY id',{},[])
        self.assertEqual(len(result['rows']),100)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['rows'][0],[1])

    def test_writes_attachments_pragmas_and_extension_functions_are_denied(self):
        for sql in ['DELETE FROM orders','UPDATE orders SET amount_cents=0','DROP TABLE orders',"ATTACH DATABASE ':memory:' AS other",'PRAGMA database_list',"SELECT load_extension('x')",'SELECT * FROM sqlite_master']:
            with self.subTest(sql=sql):
                self.assertIsNotNone(experiment(sql,{},[])['error'])

    def test_recursive_query_hits_budget_and_large_strings_hit_limits(self):
        infinite='WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT SUM(x) FROM n'
        self.assertIn('budget',experiment(infinite,{},[],step_limit=10000)['error'])
        huge="WITH RECURSIVE n(x) AS (SELECT 'x' UNION ALL SELECT x||x FROM n WHERE length(x)<100000) SELECT max(length(x)) FROM n"
        self.assertIsNotNone(experiment(huge,{},[])['error'])

    def test_named_parameters_are_bound_and_not_interpolated(self):
        result=experiment('SELECT COUNT(*) FROM orders WHERE status=:status',{'status':"paid' OR 1=1 --"},[])
        self.assertEqual(result['rows'],[[0]])
        self.assertIsNotNone(experiment('SELECT :missing',{},[])['error'])

    def test_read_only_ctes_window_functions_and_blobs(self):
        result=experiment('WITH x AS (SELECT id FROM orders WHERE id<4) SELECT id, row_number() OVER (ORDER BY id) AS n FROM x',{},[])
        self.assertEqual(result['rows'],[[1,1],[2,2],[3,3]])
        self.assertEqual(experiment("SELECT X'ABCD'",{},[])['rows'],[['0xabcd']])

    def test_multiple_statements_and_nonfinite_output_are_rejected(self):
        self.assertIsNotNone(experiment('SELECT 1; SELECT 2',{},[])['error'])
        self.assertIn('nonfinite',experiment('SELECT :x*:x',{'x':1e308},[])['error'])

    def test_strict_request_validation(self):
        for data in [None,{}, {**EXAMPLES['customer'],'extra':1},{**EXAMPLES['customer'],'indexes':['unknown']},
                     {**EXAMPLES['customer'],'indexes':['customer','customer']},{**EXAMPLES['customer'],'parameters':{'x':True}},
                     {**EXAMPLES['customer'],'parameters':{'x':2**64}},{**EXAMPLES['customer'],'parameters':{'x':float('inf')}},
                     {**EXAMPLES['customer'],'parameters':{'x':[]}}]:
            with self.assertRaises(ValueError): validate(data)


if __name__=='__main__': unittest.main()

