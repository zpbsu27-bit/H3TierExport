"""Source parser and neighborhood axes regression; real source proof is separate."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import map_source_oracle as oracle

class SourceRules(unittest.TestCase):
    def test_turn_neighborhood_flips_xy_independently(self):
        p={'cells':list('?-?-++?+?'),'road_frames':[0,1],'river_frames':[-1,-1],'allow_h':True,'allow_v':True}
        for mask,flip in ((80,0),(72,1),(18,2),(10,3)):
            self.assertEqual(oracle.select_pattern(mask,[p]),[0,flip,0,1])
        self.assertEqual(oracle.select_pattern(16,[p]),[-1,0,-1,-1])
        self.assertEqual(oracle.select_pattern(80,[p],'river'),[-1,0,-1,-1])

    def test_optional_diagonal_changes_do_not_break_question_mark_rules(self):
        p={'cells':list('?-?-++?+?'),'road_frames':[0,1],'river_frames':[-1,-1],'allow_h':True,'allow_v':True}
        for diagonal in (0,1,4,32,128,165):
            self.assertEqual(oracle.select_pattern(80|diagonal,[p]),[0,0,0,1])

    def test_incomplete_source_pattern_table_is_rejected(self):
        row='{{"?","-","?","-","+","+","?","+","?"},{0,1},{-1,-1},true,true}'
        header='const std::vector<typename CDrawLinesOperation<T>::LinePattern> CDrawLinesOperation<T>::patterns = {'
        with self.assertRaisesRegex(ValueError,'TABLE_CHANGED'):oracle.parse_patterns(header+','.join([row]*9)+'};')
        patterns,source=oracle.parse_patterns(header+','.join([row]*10)+'};')
        self.assertEqual(len(patterns),10);self.assertIn('template <typename T>',source)
        self.assertEqual(patterns[0]['road_frames'],[0,1])

    def test_extracted_function_is_preserved_with_nested_scope(self):
        text='prefix\nvoid sample(){ if(true){a();} else {b();} }\nsuffix'
        self.assertEqual(oracle.extract_function(text,'void sample('),'void sample(){ if(true){a();} else {b();} }')
        with self.assertRaisesRegex(ValueError,'NOT_CLOSED'):oracle.extract_function('void sample(){','void sample(')

if __name__=='__main__':unittest.main()
