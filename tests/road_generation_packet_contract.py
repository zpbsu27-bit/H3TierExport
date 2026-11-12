import gzip,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import road_generation_packet as packet

def ref(path):return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

class RoadGenerationPacketContract(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.geometry=self.root/'geometry.png';self.style=self.root/'style.png'
  Image.new('RGBA',(8,8),(3,4,5,255)).save(self.geometry);Image.new('RGBA',(8,8),(9,8,7,255)).save(self.style)
  self.standard=self.root/'standard.json';self.standard.write_text(json.dumps({'version':'test-v1'}),encoding='utf-8')
  self.inventory=self.root/'inventory.json.gz';rows=[{'kind':'def','identity':name,'group':0,'frame':frame,'canvas':[32,32],'crop':[32,32],'anchor':[0,0]} for name,frame in [('COBBRD',1),('DIRTRD',2)]]
  self.inventory.write_bytes(gzip.compress(json.dumps({'rows':rows}).encode('utf-8')))
  self.key=['def','COBBRD',0,1];self.profile={'schema':packet.common.PROFILE,'replaces_canonical_art_standard':False,
   'art_standard':{**ref(self.standard),'version':'test-v1'},'native_inventory':ref(self.inventory),
   'preset':'road-connection','expected_slots':[self.key],
   'road_authoring_brief_revision':'road-seam-lessons-20261102-v1',
   'road_drawing_guides':[{'key':self.key,'role':'geometry','source':ref(self.geometry)},
                          {'key':self.key,'role':'style','source':ref(self.style)}]}
  self.path=self.root/'profile.json'
 def bind(self):
  self.path.write_text(json.dumps(self.profile),encoding='utf-8');return ref(self.path)
 def test_packet_has_one_task_and_pins_the_two_references_in_order(self):
  result=packet.make_packet(self.bind(),self.root/'output');self.assertEqual(result['status'],'PREPARED_NOT_SUBMITTED');self.assertFalse(result['art_approved'])
  task=result['tasks'][0];self.assertEqual(task['reference_images'],[str(self.geometry),str(self.style)])
  self.assertEqual(task['expected_reference_sha256'],[ref(self.geometry)['sha256'],ref(self.style)['sha256']])
  self.assertIn('COBBRD碎石路风格锁定',task['prompt']);self.assertEqual(task['background_mode'],'transparent')
 def test_missing_or_drifted_guide_is_blocked(self):
  self.profile['road_drawing_guides'].pop()
  with self.assertRaisesRegex(ValueError,'ROAD_PACKET_SLOT_GUIDES_REQUIRED'):packet.make_packet(self.bind(),self.root/'output')
  self.profile['road_drawing_guides'].append({'key':self.key,'role':'style','source':ref(self.style)})
  Image.new('RGBA',(9,9)).save(self.style)
  with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):packet.make_packet(self.bind(),self.root/'output')
 def test_other_road_family_does_not_get_cobblestone_style_lock(self):
  key=['def','DIRTRD',0,2];self.profile['expected_slots']=[key]
  for guide in self.profile['road_drawing_guides']:guide['key']=key
  result=packet.make_packet(self.bind(),self.root/'output');self.assertNotIn('COBBRD碎石路风格锁定',result['tasks'][0]['prompt'])
 def test_dispatcher_renderer_pin_is_accepted_and_checked(self):
  binding=self.bind();renderer=self.root/'renderer.exe';renderer.write_bytes(b'test renderer')
  args=['--profile',binding['path'],'--profile-sha256',binding['sha256'],
        '--output-root',str(self.root/'output'),'--out',str(self.root/'tasks.json'),
        '--renderer',str(renderer),'--renderer-sha256',ref(renderer)['sha256']]
  self.assertEqual(packet.main(args),0)
  self.assertEqual(json.loads((self.root/'tasks.json').read_text(encoding='utf-8'))['status'],'PREPARED_NOT_SUBMITTED')
 def test_standard_inventory_and_slot_are_verified(self):
  binding=self.bind();self.standard.write_text(json.dumps({'version':'changed'}),encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):packet.make_packet(binding,self.root/'output')
  self.profile['art_standard']= {**ref(self.standard),'version':'wrong-version'}
  with self.assertRaisesRegex(ValueError,'ART_STANDARD_VERSION_DRIFT'):packet.make_packet(self.bind(),self.root/'output')
  self.profile['art_standard']['version']='changed'
  self.inventory.write_bytes(gzip.compress(json.dumps({'rows':[]}).encode('utf-8')))
  with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):packet.make_packet(self.bind(),self.root/'output')
  self.profile['native_inventory']=ref(self.inventory)
  with self.assertRaisesRegex(ValueError,'ROAD_PACKET_NATIVE_SLOT_NOT_FOUND'):packet.make_packet(self.bind(),self.root/'output')

if __name__=='__main__':unittest.main()
